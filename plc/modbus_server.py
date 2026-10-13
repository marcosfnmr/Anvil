"""Configurable Modbus TCP server for Anvil OT scenarios."""

from __future__ import annotations

import json
import logging
import os
import random
import threading
import asyncio
import os
import pty
import tty
from datetime import datetime, timezone
from field_bridge import FieldBridge
from protocol_audit import ProtocolAuditQueue

from pydantic import ValidationError
from pymodbus.constants import ExcCodes
from pymodbus.datastore import (
    ModbusDeviceContext,
    ModbusServerContext,
    ModbusSparseDataBlock,
)
from pymodbus.pdu import ExceptionResponse
from pymodbus.framer import FramerType
from pymodbus.server import ModbusSerialServer, ModbusTcpServer
from pymodbus.server.requesthandler import ServerRequestHandler

from modbus_schema import ModbusConfig
from timing import DeterministicJitter


LOGGER = logging.getLogger("anvil.modbus")


class BehaviorRequestHandler(ServerRequestHandler):
    """Apply configured behavior and emit value-free semantic request traces."""

    def __init__(self, *args):
        super().__init__(*args)
        self.behavior_lock = asyncio.Lock()

    async def handle_request(self):
        request = self.last_pdu
        address = self.last_addr
        if request is None:
            return
        async with self.behavior_lock:
            delay_ms = self.server.response_delay_ms + self.server.response_jitter.next_ms()
            if delay_ms:
                await asyncio.sleep(delay_ms / 1000)
            inject_fault = (self.server.fault_mode != "none"
                            and self.server.fault_rng.random() < self.server.fault_probability)
            if inject_fault and self.server.fault_mode == "disconnect":
                summary = _request_summary(request, address, self._peer_address())
                LOGGER.info("Modbus request %s result=disconnect", summary)
                self._record_audit(request, address, summary, "disconnect")
                if self.transport:
                    self.transport.close()
                return
            if inject_fault and self.server.fault_mode == "device_failure":
                response = ExceptionResponse(request.function_code, exception_code=ExcCodes.DEVICE_FAILURE)
                response.transaction_id = request.transaction_id
                response.dev_id = request.dev_id
                self.server_send(response, address)
                return
            self.last_pdu = request
            self.last_addr = address
            await super().handle_request()

    def server_send(self, pdu, addr):
        """Log protocol operation metadata without recording point values."""
        request = self.last_pdu
        if request is not None:
            response_code = int(getattr(pdu, "function_code", -1))
            if response_code == int(request.function_code) + 0x80:
                result = f"exception code={getattr(pdu, 'exception_code', '?')}"
            elif response_code == int(request.function_code):
                result = "ok"
            else:
                result = f"protocol_error response_function={response_code}"
            LOGGER.info("Modbus request %s result=%s",
                        _request_summary(request, addr, self._peer_address()), result)
            self._record_audit(
                request, addr, _request_summary(request, addr, self._peer_address()), result,
            )
        return super().server_send(pdu, addr)

    def _record_audit(self, request, address, summary: str, result: str) -> None:
        queue = getattr(getattr(self, "server", None), "protocol_audit_queue", None)
        function_code = int(getattr(request, "function_code", 0))
        if queue is None or (function_code not in {5, 6, 15, 16, 22, 23} and result == "ok"):
            return
        timestamp = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
        line = f"{timestamp} anvil.modbus: Modbus request {summary} result={result}"
        try:
            queue.append(line)
        except Exception:
            LOGGER.exception("Could not queue durable Modbus protocol audit metadata")

    def _peer_address(self):
        if isinstance(getattr(self, "server", None), ModbusSerialServer):
            return "rtu-serial"
        if self.transport:
            peer = self.transport.get_extra_info("peername")
            if isinstance(peer, (list, tuple)) and peer:
                return peer[0]
        return None


def _request_summary(request, address, source_fallback=None) -> str:
    """Return bounded, value-free metadata for a decoded Modbus request."""
    function_code = int(getattr(request, "function_code", 0))
    operation = {
        1: "read_coils", 2: "read_discrete_inputs", 3: "read_holding_registers",
        4: "read_input_registers", 5: "write_coil", 6: "write_holding_register",
        15: "write_coils", 16: "write_holding_registers", 22: "mask_write_register",
        23: "read_write_registers",
    }.get(function_code, "function")
    space = {
        1: "coil", 2: "discrete_input", 3: "holding_register", 4: "input_register",
        5: "coil", 6: "holding_register", 15: "coil", 16: "holding_register",
        22: "holding_register", 23: "holding_register",
    }.get(function_code, "unknown")
    point_address = getattr(request, "address", None)
    count = getattr(request, "count", 0)
    if not count:
        if function_code == 23:
            count = getattr(request, "read_count", 1)
        elif function_code in {5, 6, 22}:
            count = 1
        else:
            collection = (getattr(request, "bits", None) or getattr(request, "registers", None)
                          or getattr(request, "values", None))
            count = len(collection) if isinstance(collection, (list, tuple)) else 1
    peer = source_fallback or (address[0] if isinstance(address, (list, tuple)) and address else None)
    peer = peer or "unknown"
    return (f"source={peer} unit_id={getattr(request, 'dev_id', '?')} "
            f"function={function_code} operation={operation} space={space} "
            f"address={point_address if point_address is not None else '?'} count={count}")


class BehaviorModbusTcpServer(ModbusTcpServer):
    def __init__(self, *args, response_delay_ms: int = 0, response_jitter_ms: int = 0,
                 response_jitter_distribution: str = "deterministic", response_jitter_seed: int = 0,
                 fault_mode: str = "none", fault_probability: float = 1.0, fault_seed: int = 0,
                 protocol_audit_queue: ProtocolAuditQueue | None = None, **kwargs):
        self.response_delay_ms = response_delay_ms
        self.response_jitter = DeterministicJitter(
            response_jitter_ms, seed=response_jitter_seed, distribution=response_jitter_distribution
        )
        self.fault_mode = fault_mode
        self.fault_probability = fault_probability
        self.fault_rng = random.Random(fault_seed)
        self.protocol_audit_queue = protocol_audit_queue
        super().__init__(*args, **kwargs)

    def callback_new_connection(self):
        return BehaviorRequestHandler(
            self,
            self.trace_packet,
            self.trace_pdu,
            self.trace_connect,
        )


class BehaviorModbusSerialServer(ModbusSerialServer):
    """Attach the same value-free audit handler to the virtual RTU bus."""

    def __init__(self, *args, protocol_audit_queue: ProtocolAuditQueue | None = None, **kwargs):
        self.protocol_audit_queue = protocol_audit_queue
        self.response_delay_ms = 0
        self.response_jitter = DeterministicJitter(0, seed=0)
        self.fault_mode = "none"
        self.fault_probability = 0.0
        self.fault_rng = random.Random(0)
        super().__init__(*args, **kwargs)

    def callback_new_connection(self):
        return BehaviorRequestHandler(
            self,
            self.trace_packet,
            self.trace_pdu,
            self.trace_connect,
        )


def load_config() -> ModbusConfig:
    raw = os.environ.get("MODBUS_CONFIG")
    if raw is None:
        raise ValueError("MODBUS_CONFIG environment variable is required")
    try:
        return ModbusConfig.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as error:
        raise ValueError(f"MODBUS_CONFIG validation failed: {error}") from error



class ZeroBasedModbusDeviceContext(ModbusDeviceContext):
    """Use protocol register addresses as configured (without pymodbus offset)."""

    def getValues(self, func_code, address, count=1):
        return self.store[self.decode(func_code)].getValues(address, count)

    def setValues(self, func_code, address, values):
        return self.store[self.decode(func_code)].setValues(address, values)

def create_context(config: ModbusConfig) -> ModbusServerContext:
    coils: dict[int, bool] = {}
    discrete_inputs: dict[int, bool] = {}
    holding: dict[int, int] = {}
    inputs: dict[int, int] = {}
    for point in config.points:
        if point.type == "coil":
            coils[point.address] = point.initial_value
        elif point.type == "discrete_input":
            discrete_inputs[point.address] = point.initial_value
        elif point.type == "holding_register":
            holding[point.address] = point.initial_value
        else:
            inputs[point.address] = point.initial_value
    device = ZeroBasedModbusDeviceContext(
        co=ModbusSparseDataBlock(coils),
        di=ModbusSparseDataBlock(discrete_inputs),
        hr=ModbusSparseDataBlock(holding),
        ir=ModbusSparseDataBlock(inputs),
    )
    return ModbusServerContext(devices={config.unit_id: device}, single=False)


async def serve(context: ModbusServerContext, config: ModbusConfig,
                protocol_audit_queue: ProtocolAuditQueue | None = None) -> None:
    server = BehaviorModbusTcpServer(
        context,
        address=("0.0.0.0", config.port),
        response_delay_ms=config.response_delay_ms,
        response_jitter_ms=config.response_jitter_ms,
        response_jitter_distribution=config.response_jitter_distribution,
        response_jitter_seed=config.response_jitter_seed,
        fault_mode=config.fault_mode,
        fault_probability=config.fault_probability,
        fault_seed=config.fault_seed,
        protocol_audit_queue=protocol_audit_queue,
    )
    await server.serve_forever()


async def _read_fd(loop: asyncio.AbstractEventLoop, fd: int) -> bytes:
    ready = loop.create_future()

    def on_readable() -> None:
        if ready.done():
            return
        try:
            data = os.read(fd, 4096)
        except OSError:
            data = b""
        ready.set_result(data)

    loop.add_reader(fd, on_readable)
    try:
        return await ready
    finally:
        loop.remove_reader(fd)


async def _write_fd(loop: asyncio.AbstractEventLoop, fd: int, data: bytes) -> None:
    remaining = memoryview(data)
    while remaining:
        try:
            written = os.write(fd, remaining)
            remaining = remaining[written:]
        except BlockingIOError:
            ready = loop.create_future()
            loop.add_writer(fd, lambda: ready.set_result(None) if not ready.done() else None)
            try:
                await ready
            finally:
                loop.remove_writer(fd)


async def serve_rtu(context: ModbusServerContext, config: ModbusConfig,
                    protocol_audit_queue: ProtocolAuditQueue | None = None) -> None:
    """Serve RTU frames on a virtual serial bus with a raw TCP serial tunnel.

    TCP carries the RTU byte stream without MBAP framing. A standard serial
    client can reach it with pySerial's socket:// URL handler. The PTY models
    the serial endpoint; it is not an RS-485 electrical-layer simulation.
    """
    master_fd, slave_fd = pty.openpty()
    tty.setraw(slave_fd)
    serial_port = os.ttyname(slave_fd)
    os.close(slave_fd)
    os.set_blocking(master_fd, False)
    serial_server = BehaviorModbusSerialServer(
        context,
        framer=FramerType.RTU,
        port=serial_port,
        baudrate=config.baudrate,
        bytesize=config.bytesize,
        parity=config.parity,
        stopbits=config.stopbits,
        timeout=1,
        protocol_audit_queue=protocol_audit_queue,
        trace_packet=lambda sending, data: (LOGGER.debug("RTU %s %s", "tx" if sending else "rx", data.hex()), data)[1],
    )
    server_task = asyncio.create_task(serial_server.serve_forever())
    await asyncio.sleep(0.1)
    if server_task.done():
        server_task.result()
    if serial_server.transport is None:
        raise RuntimeError("Modbus RTU serial listener did not open its virtual port")
    client_lock = asyncio.Lock()
    loop = asyncio.get_running_loop()

    async def bridge(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        async with client_lock:
            async def to_serial() -> None:
                while data := await reader.read(4096):
                    await _write_fd(loop, master_fd, data)

            async def to_client() -> None:
                while data := await _read_fd(loop, master_fd):
                    writer.write(data)
                    await writer.drain()

            tasks = [asyncio.create_task(to_serial()), asyncio.create_task(to_client())]
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass

    listener = await asyncio.start_server(bridge, host="0.0.0.0", port=config.port)
    LOGGER.info("Modbus RTU unit %d on virtual %d %s %s%d serial bus; raw tunnel TCP/%d",
                config.unit_id, config.baudrate, config.parity, config.bytesize, config.stopbits, config.port)
    try:
        async with listener:
            await listener.serve_forever()
    finally:
        listener.close()
        await listener.wait_closed()
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        os.close(master_fd)


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        config = load_config()
    except ValueError as error:
        LOGGER.error("Unable to start Modbus simulator: %s", error)
        return 1
    LOGGER.info("Modbus %s unit %d listening on port %d with %d points",
                "RTU raw serial tunnel" if config.transport == "rtu" else "TCP",
                config.unit_id, config.port, len(config.points))
    context = create_context(config)
    raw_config = json.loads(os.environ["MODBUS_CONFIG"])
    wiring = raw_config.get("field_bridge")
    if wiring:
        stop = threading.Event()
        bridge = FieldBridge(wiring, stop)
        device = context[config.unit_id]
        def wire_cycle():
            bridge.start()
            while not stop.is_set():
                outputs = {}
                for binding in wiring["bindings"]:
                    kind, address = binding["address"].split(":")
                    code = {"coil": 1, "discrete_input": 2, "holding_register": 3, "input_register": 4}[kind]
                    if binding["direction"] == "output":
                        raw_value = device.getValues(code, int(address), 1)[0]
                        outputs[binding["connection_id"]] = bool(raw_value) if binding["data_type"] == "BOOL" else int(raw_value)
                values = bridge.exchange(outputs)
                for binding in wiring["bindings"]:
                    if binding["direction"] == "input" and binding["connection_id"] in values:
                        kind, address = binding["address"].split(":")
                        code = {"coil": 1, "discrete_input": 2, "holding_register": 3, "input_register": 4}[kind]
                        device.setValues(code, int(address), [values[binding["connection_id"]]])
                stop.wait(.1)
        threading.Thread(target=wire_cycle, daemon=True).start()
    audit_queue = ProtocolAuditQueue()
    try:
        asyncio.run(serve_rtu(context, config, audit_queue) if config.transport == "rtu"
                    else serve(context, config, audit_queue))
        LOGGER.info("Modbus server stopped")
    finally:
        if audit_queue is not None:
            audit_queue.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
