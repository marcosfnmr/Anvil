"""Bounded semantic capture of DB reads and writes handled by Snap7."""

from __future__ import annotations

import logging
import random
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Any

from snap7.server import Server
from snap7.type import SrvArea, SrvEvent
from timing import DeterministicJitter


LOGGER = logging.getLogger("anvil.plc.protocol")
EVENT_READ = 0x00020000
EVENT_WRITE = 0x00040000
EVENT_CLIENT_ADDED = 0x00000008
EVENT_CLIENT_DISCONNECTED = 0x00000080
EVENT_AREA_DB = int(SrvArea.DB)
AREA_NAMES = {
    int(SrvArea.PE): "I",
    int(SrvArea.PA): "Q",
    int(SrvArea.MK): "M",
    EVENT_AREA_DB: "DB",
}


@dataclass(frozen=True)
class ProtocolOperation:
    timestamp: str
    operation: str
    source: str
    area: str
    db: int | None
    byte_offset: int
    byte_count: int
    raw_hex: str
    result: str
    result_code: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "operation": self.operation,
            "source": self.source,
            "area": self.area,
            "db": self.db,
            "byte_offset": self.byte_offset,
            "byte_count": self.byte_count,
            "raw_hex": self.raw_hex,
            "result": self.result,
            "result_code": self.result_code,
        }


class ProtocolCapture:
    """Collect a bounded history of successful DB reads/writes and bytes."""

    def __init__(self, buffers: dict[int, bytearray], *, io_buffers: dict[str, bytearray] | None = None,
                 capacity: int = 200, response_delay_ms: int = 0, response_jitter_ms: int = 0,
                 response_jitter_distribution: str = "deterministic", response_jitter_seed: int = 0,
                 fault_mode: str = "none", fault_probability: float = 1.0, fault_seed: int = 0,
                 protocol_audit_queue=None) -> None:
        self.buffers = buffers
        self.io_buffers = io_buffers or {}
        self.response_delay_ms = response_delay_ms
        self.response_jitter = DeterministicJitter(
            response_jitter_ms, seed=response_jitter_seed, distribution=response_jitter_distribution
        )
        if fault_mode not in {"none", "timeout"}:
            raise ValueError(f"unsupported S7 fault mode: {fault_mode}")
        self.fault_mode = fault_mode
        self.fault_probability = fault_probability
        self._fault_rng = random.Random(fault_seed)
        self._fault_lock = Lock()
        self._events: deque[ProtocolOperation] = deque(maxlen=capacity)
        self._lock = Lock()
        self.protocol_audit_queue = protocol_audit_queue

    def attach(self, server: Server) -> None:
        # python-snap7 invokes both callbacks for reads. Consume reads only in
        # the dedicated callback and writes/client events in the general one.
        server.set_read_events_callback(self._on_read)
        server.set_events_callback(self._on_event)
        if self.fault_mode == "timeout":
            original = server._process_request

            def suppress_data_responses(request_data: bytes, client_address: tuple[str, int]):
                try:
                    request = server._parse_request(request_data)
                    function_code = (request.get("parameters") or {}).get("function_code")
                except Exception:
                    return original(request_data, client_address)
                # S7 READ_AREA/WRITE_AREA are function codes 0x04/0x05. Leave
                # session setup and other services available while simulating
                # a seeded, probabilistic silent timeout on process data.
                if function_code in {0x04, 0x05}:
                    with self._fault_lock:
                        inject_fault = self._fault_rng.random() < self.fault_probability
                    if inject_fault:
                        LOGGER.warning("S7 injected timeout: suppressing data response from %s", client_address)
                        return None
                return original(request_data, client_address)

            server._process_request = suppress_data_responses

    def snapshot(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            return [operation.as_dict() for operation in list(self._events)[-limit:]]

    def _on_read(self, event: SrvEvent) -> None:
        if int(event.EvtCode) == EVENT_READ:
            self._capture(event, "read")

    def _on_event(self, event: SrvEvent) -> None:
        code = int(event.EvtCode)
        if code in (EVENT_READ, EVENT_WRITE):
            delay_ms = self.response_delay_ms + self.response_jitter.next_ms()
            if delay_ms:
                time.sleep(delay_ms / 1000)
        if code == EVENT_CLIENT_ADDED:
            LOGGER.info("S7 client connected source=%s", _format_ipv4(int(event.EvtSender)))
        elif code == EVENT_CLIENT_DISCONNECTED:
            LOGGER.info("S7 client disconnected source=%s", _format_ipv4(int(event.EvtSender)))
        elif code == EVENT_WRITE:
            self._capture(event, "write")

    def _capture(self, event: SrvEvent, operation: str) -> None:
        area_code = int(event.EvtParam1)
        area = AREA_NAMES.get(area_code)
        if area is None:
            return
        index = int(event.EvtParam2)
        db = index if area == "DB" else None
        offset = int(event.EvtParam3)
        count = int(event.EvtParam4)
        result_code = int(event.EvtRetCode)
        raw = b""
        if result_code == 0:
            buffer = self.buffers.get(index) if area == "DB" else self.io_buffers.get(area)
            if buffer is not None:
                raw = bytes(buffer[offset : offset + count])
        source = _format_ipv4(int(event.EvtSender))
        success = result_code == 0
        captured = ProtocolOperation(
            timestamp=datetime.now(timezone.utc).isoformat(),
            operation=operation,
            source=source,
            area=area,
            db=db,
            byte_offset=offset,
            byte_count=count,
            raw_hex=raw.hex(" "),
            result="ok" if success else "error",
            result_code=result_code,
        )
        with self._lock:
            self._events.append(captured)
        area_label = f"DB{index}" if area == "DB" else area
        # Safe semantic trace for durable audit collection. Keep the legacy
        # data-bearing line for the existing protocol inspector, but never
        # copy its payload into the audit database.
        LOGGER.info(
            "S7 audit operation=%s source=%s area=%s db=%s byte_offset=%d byte_count=%d result=%s result_code=%d",
            operation, source, area, db if db is not None else "-", offset, count,
            "ok" if success else "error", result_code,
        )
        if self.protocol_audit_queue is not None and (operation == "write" or not success):
            audit_timestamp = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
            line = (
                f"{audit_timestamp} anvil.plc.protocol: S7 audit operation={operation} source={source} "
                f"area={area} db={db if db is not None else '-'} byte_offset={offset} "
                f"byte_count={count} result={'ok' if success else 'error'} result_code={result_code}"
            )
            try:
                self.protocol_audit_queue.append(line)
            except Exception:
                LOGGER.exception("Could not queue durable S7 protocol audit metadata")
        LOGGER.info(
            "S7 %s source=%s %s bytes[%d:%d] data=%s result=%s code=%d",
            operation.upper(), source, area_label, offset, offset + count,
            raw.hex(" ") or "-", "ok" if success else "error", result_code,
        )


def _format_ipv4(value: int) -> str:
    return ".".join(str((value >> shift) & 0xFF) for shift in (24, 16, 8, 0))
