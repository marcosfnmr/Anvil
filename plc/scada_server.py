"""Small polling SCADA runtime with bounded in-memory history and alarms."""

from __future__ import annotations

import json
import hmac
import math
import os
import struct
import threading
import time
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

try:
    from .scada_history import DurableHistory
except ImportError:  # Running the image entrypoint as a script.
    from scada_history import DurableHistory



def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def modbus_client(target: dict[str, Any], timeout: float) -> Any:
    if target.get("transport", "tcp") == "rtu":
        from pymodbus.client import ModbusSerialClient
        return ModbusSerialClient(
            f"socket://{target['host']}:{int(target['port'])}",
            baudrate=int(target.get("baudrate", 19200)),
            bytesize=int(target.get("bytesize", 8)), parity=target.get("parity", "N"),
            stopbits=int(target.get("stopbits", 1)), timeout=timeout,
        )
    from pymodbus.client import ModbusTcpClient
    return ModbusTcpClient(target["host"], port=int(target["port"]), timeout=timeout)


class Collector:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.durable_history = DurableHistory(
            config.get("durable_history_path"), config.get("durable_history_capacity", 100_000),
            config.get("durable_history_max_bytes", 134_217_728),
        )
        recovered = self.durable_history.recent(config["history_capacity"])
        self.history: deque[dict[str, Any]] = deque(recovered, maxlen=config["history_capacity"])
        self.latest: dict[str, Any] = recovered[-1] if recovered else {"timestamp": None, "values": []}
        self.alarms: dict[str, dict[str, Any]] = {}
        self.errors: dict[str, str] = {}
        self.ready = False
        self.lock = threading.RLock()
        self.stopped = threading.Event()
        self.worker = threading.Thread(target=self._poll_loop, name="scada-poller", daemon=True)
        self.worker.start()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {"timestamp": self.latest["timestamp"], "values": list(self.latest["values"]),
                    "errors": dict(self.errors), "alarms": list(self.alarms.values()), "ready": self.ready,
                    **self.durable_history.stats()}

    def read_history(self, limit: int) -> dict[str, Any]:
        with self.lock:
            return {"samples": list(self.history)[-limit:]}

    def read_pending_history(self, limit: int) -> dict[str, Any]:
        return self.durable_history.pending(limit)

    def acknowledge_history(self, cursor: int) -> dict[str, int]:
        return self.durable_history.acknowledge(cursor)

    def acknowledge(self, target_node_id: str, address: str) -> dict[str, Any]:
        key = f"{target_node_id}|{address}"
        with self.lock:
            alarm = self.alarms.get(key)
            if alarm is None:
                raise KeyError("alarm rule was not found")
            alarm["acknowledged"] = True
            alarm["acknowledged_at"] = utc_now()
            return dict(alarm)

    def restore_pending_alarms(self, alarms: list[dict[str, Any]]) -> dict[str, int]:
        """Restore persisted delay starts; the next good poll revalidates conditions."""
        restored = 0
        with self.lock:
            for item in alarms:
                target, address, condition_since = (
                    item.get("target_node_id"), item.get("address"), item.get("condition_since")
                )
                if not all(isinstance(value, str) and value for value in (target, address, condition_since)):
                    continue
                key = f"{target}|{address}"
                current = self.alarms.get(key)
                if current is not None and current.get("active") is True:
                    continue
                if current is None:
                    current = {"target_node_id": target, "address": address, "active": False}
                    self.alarms[key] = current
                current["condition_since"] = condition_since
                current["pending"] = True
                restored += 1
        return {"restored": restored}

    def _poll_loop(self) -> None:
        interval = self.config["poll_interval_ms"] / 1000
        while not self.stopped.is_set():
            started = time.monotonic()
            values: list[dict[str, Any]] = []
            errors: dict[str, str] = {}
            for target in self.config["targets"]:
                try:
                    values.extend(self._read_target(target))
                except Exception as error:
                    errors[target["node_id"]] = str(error)[:240]
                    values.extend({"node_id": target["node_id"], **tag, "value": None, "quality": "bad"}
                                  for tag in target["tags"])
            timestamp = utc_now()
            for value in values:
                value["timestamp"] = timestamp
                value.setdefault("quality", "good")
            sample = {"timestamp": timestamp, "values": values}
            try:
                self.durable_history.append(sample)
            except Exception as error:
                errors["history"] = f"Durable history write failed: {str(error)[:180]}"
            with self.lock:
                self.latest = sample
                self.errors = errors
                self.history.append(sample)
                self._evaluate_alarms(values, timestamp)
                self.ready = True
            self.stopped.wait(max(0.01, interval - (time.monotonic() - started)))

    def _read_target(self, target: dict[str, Any]) -> list[dict[str, Any]]:
        if target["device_type"] == "plc-s7":
            import snap7
            client = snap7.client.Client()
            client.connect(target["host"], target["rack"], target["slot"], tcp_port=target["port"])
            try:
                result = []
                for tag in target["tags"]:
                    address = tag["address"]
                    import re
                    match = re.fullmatch(r"DB(\d+)\.DB([XB])(\d+)(?:\.(\d+))?", address)
                    real_match = re.fullmatch(r"DB(\d+)\.DBD(\d+)", address)
                    area_match = re.fullmatch(r"([MIQ])(\d+)\.([0-7])", address)
                    area_real = re.fullmatch(r"([MIQ])D(\d+)", address)
                    from snap7.type import Area
                    if match:
                        db, offset = int(match[1]), int(match[3])
                        raw = client.db_read(db, offset, 1)
                        value = bool(raw[0] & (1 << int(match[4] or 0)))
                    elif real_match:
                        db, offset = int(real_match[1]), int(real_match[2])
                        raw = client.db_read(db, offset, 4)
                        value = struct.unpack(">f", raw)[0]
                    elif area_match:
                        area, offset, bit = area_match.group(1), int(area_match.group(2)), int(area_match.group(3))
                        raw = client.read_area({"I":Area.PE,"Q":Area.PA,"M":Area.MK}[area],0,offset,1)
                        value = bool(raw[0] & (1 << bit))
                    elif area_real:
                        area, offset = area_real.group(1), int(area_real.group(2))
                        raw = client.read_area({"I":Area.PE,"Q":Area.PA,"M":Area.MK}[area],0,offset,4)
                        value = struct.unpack(">f", raw)[0]
                    else:
                        raise ValueError(f"unsupported S7 address {address}")
                    if type(value) is float and not math.isfinite(value):
                        raise ValueError(f"non-finite S7 value for {address}")
                    result.append({"node_id": target["node_id"], **tag, "value": value, "quality": "good"})
                return result
            finally:
                if client.get_connected():
                    client.disconnect()

        client = modbus_client(target, timeout=1)
        if not client.connect():
            raise ConnectionError("Modbus connection failed")
        result = []
        try:
            for tag in target["tags"]:
                kind, raw_address = tag["address"].split(":", 1)
                address = int(raw_address)
                kwargs = {"device_id": target["unit_id"]}
                if kind == "coil":
                    response = client.read_coils(address, count=1, **kwargs)
                elif kind == "discrete_input":
                    response = client.read_discrete_inputs(address, count=1, **kwargs)
                elif kind == "holding_register":
                    response = client.read_holding_registers(address, count=1, **kwargs)
                else:
                    response = client.read_input_registers(address, count=1, **kwargs)
                if response is None or response.isError():
                    raise IOError(f"Modbus exception for {tag['address']}")
                if kind in {"coil", "discrete_input"}:
                    value = response.bits[0]
                else:
                    value = response.registers[0]
                result.append({"node_id": target["node_id"], **tag, "value": value, "quality": "good"})
        finally:
            client.close()
        return result

    def write_target(self, target_node_id: str, address: str, value: Any) -> dict[str, Any]:
        target = next((item for item in self.config["targets"] if item["node_id"] == target_node_id), None)
        if target is None:
            raise ValueError("target is not selected by this SCADA")
        tag = next((item for item in target["tags"] if item["address"] == address), None)
        if tag is None or tag.get("access") != "read_write":
            raise PermissionError("address is not a writable SCADA output")
        if target["device_type"] == "plc-s7":
            import re
            import snap7
            client = snap7.client.Client()
            client.connect(target["host"], target["rack"], target["slot"], tcp_port=target["port"])
            try:
                match = re.fullmatch(r"DB(\d+)\.DBX(\d+)\.([0-7])", address)
                real_match = re.fullmatch(r"DB(\d+)\.DBD(\d+)", address)
                area_match = re.fullmatch(r"([MQ])(\d+)\.([0-7])", address)
                area_real = re.fullmatch(r"([MQ])D(\d+)", address)
                from snap7.type import Area
                if match:
                    if tag["data_type"] != "BOOL" or type(value) is not bool:
                        raise ValueError("S7 BOOL outputs require a boolean value")
                    db, offset, bit = int(match[1]), int(match[2]), int(match[3])
                    raw = bytearray(client.db_read(db, offset, 1))
                    raw[0] = raw[0] | (1 << bit) if value else raw[0] & ~(1 << bit)
                    client.db_write(db, offset, raw)
                elif area_match:
                    if tag["data_type"] != "BOOL" or type(value) is not bool: raise ValueError("S7 BOOL outputs require a boolean value")
                    area, offset, bit = area_match.group(1), int(area_match.group(2)), int(area_match.group(3))
                    memory_area = {"M":Area.MK,"Q":Area.PA}[area]
                    raw = bytearray(client.read_area(memory_area,0,offset,1)); raw[0] = raw[0] | (1 << bit) if value else raw[0] & ~(1 << bit)
                    client.write_area(memory_area,0,offset,raw)
                elif real_match:
                    if tag["data_type"] != "REAL" or type(value) not in (int, float) or not math.isfinite(value):
                        raise ValueError("S7 REAL outputs require a finite numeric value")
                    db, offset = int(real_match[1]), int(real_match[2])
                    low, high = tag.get("min_value"), tag.get("max_value")
                    if low is not None and value < low or high is not None and value > high:
                        raise ValueError("S7 REAL output is outside its configured range")
                    client.db_write(db, offset, bytearray(struct.pack(">f", float(value))))
                elif area_real:
                    if tag["data_type"] != "REAL" or type(value) not in (int,float) or not math.isfinite(value): raise ValueError("S7 REAL outputs require a finite numeric value")
                    area, offset = area_real.group(1), int(area_real.group(2)); low, high = tag.get("min_value"), tag.get("max_value")
                    if low is not None and value < low or high is not None and value > high: raise ValueError("S7 REAL output is outside its configured range")
                    client.write_area({"M":Area.MK,"Q":Area.PA}[area],0,offset,bytearray(struct.pack(">f",float(value))))
                else:
                    raise ValueError(f"unsupported writable S7 address {address}")
            finally:
                if client.get_connected():
                    client.disconnect()
        else:
            client = modbus_client(target, timeout=2)
            if not client.connect():
                raise ConnectionError("Modbus connection failed")
            try:
                kind, raw_address = address.split(":", 1)
                location = int(raw_address)
                kwargs = {"device_id": target["unit_id"]}
                if kind == "coil" and tag["data_type"] == "BOOL" and type(value) is bool:
                    response = client.write_coil(location, value, **kwargs)
                elif kind == "holding_register" and tag["data_type"] == "UINT16" and type(value) is int and 0 <= value <= 65535:
                    response = client.write_register(location, value, **kwargs)
                else:
                    raise ValueError("only writable Modbus coils and holding registers are supported")
                if response.isError():
                    raise IOError(f"Modbus exception for write to {address}")
            finally:
                client.close()
        return {"node_id": target_node_id, "address": address, "value": value, "quality": "good"}
        return result

    def _evaluate_alarms(self, values: list[dict[str, Any]], timestamp: str) -> None:
        by_key = {(item["node_id"], item["address"]): item for item in values}
        for rule in self.config.get("alarms", []):
            key = f"{rule['target_node_id']}|{rule['address']}"
            point = by_key.get((rule["target_node_id"], rule["address"]))
            quality = point.get("quality", "unknown") if point else "bad"
            if (point is None or point.get("quality") != "good"
                    or type(point.get("value")) not in (int, float)
                    or not math.isfinite(point.get("value"))):
                condition, current_value = False, None
            else:
                current_value = point["value"]
                condition = ((rule.get("above") is not None and current_value > rule["above"])
                             or (rule.get("below") is not None and current_value < rule["below"]))
            previous = self.alarms.get(key, {})
            was_active = previous.get("active") is True
            pending_since = previous.get("condition_since") if condition and not was_active else None
            if condition and not was_active:
                pending_since = pending_since or timestamp
                try:
                    started = datetime.fromisoformat(pending_since)
                    current = datetime.fromisoformat(timestamp)
                    if started.tzinfo is None:
                        started = started.replace(tzinfo=timezone.utc)
                    if current.tzinfo is None:
                        current = current.replace(tzinfo=timezone.utc)
                    elapsed_ms = (current - started).total_seconds() * 1000
                except (TypeError, ValueError):
                    pending_since = timestamp
                    elapsed_ms = 0
                active = elapsed_ms >= rule.get("delay_ms", 0)
            else:
                active = condition
            self.alarms[key] = {
                "target_node_id": rule["target_node_id"], "address": rule["address"],
                "active": active, "value": current_value, "above": rule.get("above"),
                "below": rule.get("below"),
                "quality": quality,
                "delay_ms": rule.get("delay_ms", 0),
                "pending": bool(condition and not active),
                "condition_since": pending_since if condition and not active else None,
                "triggered_at": previous.get("triggered_at") if active and was_active else timestamp if active else None,
                "acknowledged": previous.get("acknowledged", False) if active and was_active else False,
                "acknowledged_at": previous.get("acknowledged_at") if active and was_active else None,
            }

    def close(self) -> None:
        self.stopped.set()
        self.worker.join(timeout=2)
        self.durable_history.close()


def create_handler(collector: Collector, history_ack_token: str | None = None):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/health":
                self._send(200, {"status": "ok"})
            elif parsed.path == "/snapshot":
                self._send(200, collector.snapshot())
            elif parsed.path == "/history":
                try:
                    limit = max(1, min(10_000, int(parsed.query.split("=", 1)[-1]))) if parsed.query else 100
                except ValueError:
                    limit = 100
                self._send(200, collector.read_history(limit))
            elif parsed.path == "/history/pending":
                try:
                    limit = max(1, min(10_000, int(parse_qs(parsed.query).get("limit", ["10000"])[0])))
                except ValueError:
                    limit = 10_000
                self._send(200, collector.read_pending_history(limit))
            elif parsed.path == "/alarms":
                snapshot = collector.snapshot()
                self._send(200, {"alarms": snapshot["alarms"], "ready": snapshot["ready"]})
            else:
                self._send(404, {"detail": "not found"})

        def do_POST(self) -> None:
            if self.path not in {"/alarms/acknowledge", "/alarms/restore", "/write", "/history/acknowledge"}:
                self._send(404, {"detail": "not found"})
                return
            if self.path == "/history/acknowledge":
                supplied = self.headers.get("Authorization", "")
                expected = f"Bearer {history_ack_token}" if history_ack_token else ""
                if not expected or not hmac.compare_digest(supplied, expected):
                    self._send(403, {"detail": "history acknowledgement is not authorized"})
                    return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size > 4096:
                    raise ValueError("request too large")
                payload = json.loads(self.rfile.read(size))
                if self.path == "/history/acknowledge":
                    result = collector.acknowledge_history(payload["cursor"])
                elif self.path == "/write":
                    result = collector.write_target(payload["target_node_id"], payload["address"], payload["value"])
                elif self.path == "/alarms/restore":
                    alarms = payload.get("alarms")
                    if not isinstance(alarms, list) or len(alarms) > 100:
                        raise ValueError("alarms must be a list with at most 100 items")
                    result = collector.restore_pending_alarms(alarms)
                else:
                    result = collector.acknowledge(payload["target_node_id"], payload["address"])
                self._send(200, result)
            except (ValueError, KeyError, TypeError, PermissionError, json.JSONDecodeError, OSError) as error:
                self._send(400, {"detail": str(error)})

        def _send(self, code: int, value: dict[str, Any]) -> None:
            body = json.dumps(value, allow_nan=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_: Any) -> None:
            return
    return Handler


def main() -> None:
    config = json.loads(os.environ["SCADA_CONFIG"])
    collector = Collector(config)
    server = ThreadingHTTPServer(("0.0.0.0", 8766), create_handler(
        collector, os.environ.get("SCADA_HISTORY_ACK_TOKEN"),
    ))
    try:
        server.serve_forever()
    finally:
        server.server_close()
        collector.close()


if __name__ == "__main__":
    main()
