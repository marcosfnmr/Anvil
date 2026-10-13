"""Entrypoint for the parametrizable S7 PLC simulator."""

from __future__ import annotations

import json
import copy
import logging
import os
import signal
import sys
import threading
import struct
import time

from pydantic import ValidationError
from snap7.server import Server
from snap7.type import SrvArea

from components import COMPONENT_REGISTRY
from components.base import Component
from config_schema import PLCConfig
from protocol_capture import ProtocolCapture
from protocol_audit import ProtocolAuditQueue
from s7_links import S7Links
from field_bridge import FieldBridge
from control_logic import ControlLogic
from retained_state import load_state, save_state


LOGGER = logging.getLogger("anvil.plc")
SIMULATION_INTERVAL_SECONDS = 0.1


def load_config() -> PLCConfig:
    """Read and validate the JSON configuration supplied by the container."""
    raw_config = os.environ.get("PLC_CONFIG")
    if raw_config is None:
        raise ValueError("PLC_CONFIG environment variable is required")
    try:
        config_data = json.loads(raw_config)
    except json.JSONDecodeError as error:
        raise ValueError(f"PLC_CONFIG must contain valid JSON: {error.msg}") from error
    try:
        return PLCConfig.model_validate(config_data)
    except ValidationError as error:
        raise ValueError(f"PLC_CONFIG validation failed: {error}") from error


def create_db_buffers(config: PLCConfig) -> dict[int, bytearray]:
    """Create one shared memory buffer for every DB referenced by a component."""
    return {
        db_number: bytearray(config.db_size)
        for db_number in {component.db for component in config.components if getattr(component, "area", "DB") == "DB"}
    }


def create_io_buffers(config: PLCConfig) -> dict[str, bytearray]:
    """Create shared process-image buffers for configured M/I/Q channels."""
    areas = {component.area for component in config.components if getattr(component, "type", None) == "io_channel" and component.area != "DB"}
    return {area: bytearray(config.db_size) for area in areas}


def _component_buffer(component: Component, db_buffers: dict[int, bytearray], io_buffers: dict[str, bytearray]) -> bytearray:
    area = getattr(component.config, "area", "DB")
    return db_buffers[component.config.db] if area == "DB" else io_buffers[area]


def create_components(config: PLCConfig) -> list[Component]:
    """Instantiate component implementations without knowing concrete types."""
    components: list[Component] = []
    for component_config in config.components:
        component_class = COMPONENT_REGISTRY.get(component_config.type)
        if component_class is None:
            raise ValueError(
                f"No component implementation registered for '{component_config.type}'"
            )
        components.append(component_class(component_config))
    return components


def install_signal_handlers(stop_event: threading.Event) -> None:
    """Turn SIGINT and SIGTERM into a clean simulation-loop shutdown."""
    def request_shutdown(signum: int, _frame: object) -> None:
        LOGGER.info("Received signal %s; stopping PLC simulator", signum)
        stop_event.set()
    signal.signal(signal.SIGINT, request_shutdown)
    signal.signal(signal.SIGTERM, request_shutdown)


def run_simulation(
    components: list[Component],
    db_buffers: dict[int, bytearray],
    stop_event: threading.Event,
    links: S7Links | None = None,
    field: FieldBridge | None = None,
    scan_cycle_seconds: float = SIMULATION_INTERVAL_SECONDS,
    logic: ControlLogic | None = None,
    io_buffers: dict[str, bytearray] | None = None,
    persist_logic_state=None,
) -> None:
    """Update simulated inputs at the configured PLC scan interval."""
    io_buffers = io_buffers or {}
    last_checkpoint = time.monotonic()
    last_logic_state = None
    while not stop_event.is_set():
        for component in components:
            component_buffer = _component_buffer(component, db_buffers, io_buffers)
            component_area = getattr(component.config, "area", "DB")
            if field and any(binding["direction"] == "input" and binding.get("area", "DB") == component_area and (component_area != "DB" or binding.get("db") == component.config.db) and binding["byte_offset"] == component.config.byte_offset and binding.get("bit_offset", 0) == getattr(component.config, "bit_offset", 0) for binding in field.config["bindings"]):
                continue
            component.simulate(component_buffer, scan_cycle_seconds)
        if links:
            links.apply_values()
        if field:
            bindings = field.config["bindings"]
            outputs = {}
            for binding in bindings:
                if binding["direction"] == "output":
                    buffer = db_buffers[binding["db"]] if binding.get("area", "DB") == "DB" else io_buffers[binding["area"]]
                    if binding["data_type"] == "BOOL":
                        outputs[binding["connection_id"]] = bool(buffer[binding["byte_offset"]] & (1 << binding["bit_offset"]))
                    else:
                        outputs[binding["connection_id"]] = float(struct.unpack_from(">f", buffer, binding["byte_offset"])[0])
            values = field.exchange(outputs)
            for binding in bindings:
                if binding["direction"] != "input" or binding["connection_id"] not in values:
                    continue
                buffer, value = (db_buffers[binding["db"]] if binding.get("area", "DB") == "DB" else io_buffers[binding["area"]]), values[binding["connection_id"]]
                if binding["data_type"] == "BOOL":
                    mask = 1 << binding["bit_offset"]
                    offset = binding["byte_offset"]
                    buffer[offset] = buffer[offset] | mask if value else buffer[offset] & ~mask
                else:
                    struct.pack_into(">f", buffer, binding["byte_offset"], value)
        if logic:
            logic.apply()
            now = time.monotonic()
            if persist_logic_state:
                snapshot = logic.export_state()
                discrete_state = {key: value for key, value in snapshot["state"].items()
                                  if key != "timer_elapsed_ms"}
                if last_logic_state != discrete_state or now - last_checkpoint >= 1.0:
                    persist_logic_state(snapshot)
                    last_logic_state = copy.deepcopy(discrete_state)
                    last_checkpoint = now
        stop_event.wait(scan_cycle_seconds)
    if logic and persist_logic_state:
        persist_logic_state(logic.export_state())


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        config = load_config()
        components = create_components(config)
    except ValueError as error:
        LOGGER.error("Unable to start PLC simulator: %s", error)
        return 1

    db_buffers = create_db_buffers(config)
    io_buffers = create_io_buffers(config)
    audit_queue = ProtocolAuditQueue()
    server = Server()
    capture = ProtocolCapture(
        db_buffers,
        io_buffers=io_buffers,
        response_delay_ms=config.response_delay_ms,
        response_jitter_ms=config.response_jitter_ms,
        response_jitter_distribution=config.response_jitter_distribution,
        response_jitter_seed=config.response_jitter_seed,
        fault_mode=config.fault_mode,
        fault_probability=config.fault_probability,
        fault_seed=config.fault_seed,
        protocol_audit_queue=audit_queue,
    )
    capture.attach(server)
    stop_event = threading.Event()
    links = S7Links(config.components, db_buffers, stop_event)
    field = FieldBridge(config.field_bridge, stop_event) if config.field_bridge else None
    logic = ControlLogic(
        [rule.model_dump(mode="json") for rule in config.logic], db_buffers,
        [timer.model_dump(mode="json") for timer in config.timers],
        boolean_rules=[rule.model_dump(mode="json") for rule in config.boolean_logic],
        latches=[latch.model_dump(mode="json") for latch in config.latches],
        counters=[counter.model_dump(mode="json") for counter in config.counters],
        sequences=[sequence.model_dump(mode="json") for sequence in config.sequences],
        safe_off_inputs_provider=links.safe_off_inputs,
        retained_state=(load_state("/var/lib/anvil/retention/state.json")
                        if config.retain_logic_state else None),
    )
    persist_logic_state = (lambda state: save_state("/var/lib/anvil/retention/state.json", state)
                           if config.retain_logic_state else None)
    install_signal_handlers(stop_event)
    try:
        for db_number, db_buffer in db_buffers.items():
            server.register_area(SrvArea.DB, db_number, db_buffer)
        for area, io_buffer in io_buffers.items():
            server.register_area({"I": SrvArea.PE, "Q": SrvArea.PA, "M": SrvArea.MK}[area], 0, io_buffer)
        for component in components:
            component.initialize(_component_buffer(component, db_buffers, io_buffers))
        # Restore retained block outputs after each component writes its configured initial value.
        logic.apply()
        server.start_to("0.0.0.0", 102)
        LOGGER.info("PLC %s listening on port 102 with DBs %s and areas %s", config.plc_id, sorted(db_buffers), sorted(io_buffers))
        links.start()
        if field:
            field.config["scan_cycle_ms"] = config.scan_cycle_ms
            field.start()
        run_simulation(components, db_buffers, stop_event, links, field, config.scan_cycle_ms / 1000,
                       logic, io_buffers, persist_logic_state)
        return 0
    except Exception:
        LOGGER.exception("PLC simulator stopped because of an unexpected error")
        return 1
    finally:
        links.close()
        stop_event.set()
        if field and field.thread.is_alive():
            field.thread.join(timeout=1)
        server.stop()
        audit_queue.close()
        LOGGER.info("PLC simulator stopped")


if __name__ == "__main__":
    sys.exit(main())
