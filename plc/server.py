"""Entrypoint for the parametrizable S7 PLC simulator."""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading

from pydantic import ValidationError
from snap7.server import Server
from snap7.type import SrvArea

from components import COMPONENT_REGISTRY
from components.base import Component
from config_schema import PLCConfig


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
        for db_number in {component.db for component in config.components}
    }


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
) -> None:
    """Update simulated inputs at a fixed 100 ms interval."""
    while not stop_event.is_set():
        for component in components:
            db_buffer = db_buffers[component.config.db]
            component.simulate(db_buffer, SIMULATION_INTERVAL_SECONDS)
        stop_event.wait(SIMULATION_INTERVAL_SECONDS)


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
    server = Server()
    stop_event = threading.Event()
    install_signal_handlers(stop_event)

    try:
        for db_number, db_buffer in db_buffers.items():
            server.register_area(SrvArea.DB, db_number, db_buffer)

        for component in components:
            component.initialize(db_buffers[component.config.db])

        server.start_to("0.0.0.0", 102)
        LOGGER.info(
            "PLC %s listening on port 102 with DBs %s",
            config.plc_id,
            sorted(db_buffers),
        )
        run_simulation(components, db_buffers, stop_event)
        return 0
    except Exception:
        LOGGER.exception("PLC simulator stopped because of an unexpected error")
        return 1
    finally:
        server.stop()
        LOGGER.info("PLC simulator stopped")


if __name__ == "__main__":
    sys.exit(main())
