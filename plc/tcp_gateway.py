"""Single-flow TCP route relay with optional, exact Modbus register rewrites.

The control socket is Unix-only inside the gateway container. The Docker API
policy must restrict the helper command which can reach it; no network control
listener is created.
"""

from __future__ import annotations

import json
import hmac
import os
import socket
import threading
import time
from typing import Any

from modbus_interceptor import (
    ModbusFrameError,
    ModbusTransactionCorrelator,
    RewriteRule,
    split_mbap_frame,
)
from protocol_audit import ProtocolAuditQueue


CONTROL_SOCKET = "/tmp/anvil-route-control.sock"
MAX_CONTROL_BYTES = 8192
MAX_BUFFER_BYTES = 260


def _rules_from_json(value: Any) -> tuple[RewriteRule, ...]:
    if not isinstance(value, list) or len(value) > 32:
        raise ValueError("rules must be a list with at most 32 entries")
    rules: list[RewriteRule] = []
    rule_ids: set[str] = set()
    for item in value:
        fields = {"rule_id", "unit_id", "function_code", "address", "replacement_value"}
        optional = {"phase"} if isinstance(item, dict) and item.get("function_code") == 23 else set()
        valid_fields = (fields, fields | {"exercise_id", "event_id"},
                        fields | optional, fields | optional | {"exercise_id", "event_id"})
        if not isinstance(item, dict) or set(item) not in valid_fields:
            raise ValueError("each rule must contain exactly the supported fields")
        if ("exercise_id" in item) != ("event_id" in item):
            raise ValueError("exercise rewrite metadata must include both IDs")
        for key in ("exercise_id", "event_id"):
            if key in item and (not isinstance(item[key], str) or not item[key]
                                or len(item[key]) > 48
                                or any(not (ch.isalnum() or ch in "_.-") for ch in item[key])):
                raise ValueError("invalid exercise rewrite metadata")
        rules.append(RewriteRule(**item))
        if item["rule_id"] in rule_ids:
            raise ValueError("rewrite rule IDs must be unique")
        rule_ids.add(item["rule_id"])
    # Reuse correlator validation, including exact integer checks.
    ModbusTransactionCorrelator(rules).replace_rules(rules)
    keys = [(rule.unit_id, rule.function_code, rule.phase, rule.address) for rule in rules]
    if len(keys) != len(set(keys)):
        raise ValueError("rules cannot target the same register more than once")
    return tuple(rules)


class Gateway:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.token = config["control_token"]
        if not isinstance(self.token, str) or len(self.token) != 43:
            raise ValueError("invalid route control token")
        self._lock = threading.RLock()
        self._rules: tuple[RewriteRule, ...] = ()
        self._correlators: set[ModbusTransactionCorrelator] = set()
        self._stopping = threading.Event()
        self._control_listener: socket.socket | None = None
        self._audit_queue = (
            ProtocolAuditQueue()
            if os.environ.get("ANVIL_PROTOCOL_AUDIT_ACK_TOKEN")
            else None
        )

    def current_rules(self) -> tuple[RewriteRule, ...]:
        with self._lock:
            return self._rules

    def set_rules(self, rules: tuple[RewriteRule, ...]) -> None:
        with self._lock:
            self._rules = rules
            correlators = tuple(self._correlators)
        for correlator in correlators:
            correlator.replace_rules(rules)

    def run(self) -> None:
        control = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._control_listener = control
        try:
            try:
                os.unlink(CONTROL_SOCKET)
            except FileNotFoundError:
                pass
            control.bind(CONTROL_SOCKET)
            os.chmod(CONTROL_SOCKET, 0o600)
            control.listen(4)
            control.settimeout(0.5)
            threading.Thread(target=self._control_loop, daemon=True).start()

            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((self.config["listen_ip"], self.config["listen_port"]))
            listener.listen(32)
            listener.settimeout(0.5)
            while not self._stopping.is_set():
                try:
                    client, peer = listener.accept()
                except TimeoutError:
                    continue
                if peer[0] != self.config["source_ip"]:
                    client.close()
                    continue
                threading.Thread(target=self._serve, args=(client,), daemon=True).start()
        finally:
            self._stopping.set()
            control.close()
            try:
                os.unlink(CONTROL_SOCKET)
            except FileNotFoundError:
                pass

    def _control_loop(self) -> None:
        assert self._control_listener is not None
        while not self._stopping.is_set():
            try:
                client, _ = self._control_listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            threading.Thread(target=self._handle_control, args=(client,), daemon=True).start()

    def _handle_control(self, client: socket.socket) -> None:
        with client:
            client.settimeout(1.0)
            raw = bytearray()
            try:
                while len(raw) <= MAX_CONTROL_BYTES:
                    chunk = client.recv(min(1024, MAX_CONTROL_BYTES + 1 - len(raw)))
                    if not chunk:
                        break
                    raw.extend(chunk)
                    if b"\n" in raw:
                        break
                if len(raw) > MAX_CONTROL_BYTES or b"\n" not in raw:
                    raise ValueError("control request is missing or too large")
                request = json.loads(bytes(raw).split(b"\n", 1)[0])
                if not isinstance(request, dict) or set(request) - {"token", "action", "rules"}:
                    raise ValueError("invalid control request")
                if not isinstance(request.get("token"), str) or not hmac.compare_digest(request["token"], self.token):
                    raise PermissionError("invalid route control token")
                action = request.get("action")
                if action == "clear" and set(request) == {"token", "action"}:
                    self.set_rules(())
                    response = {"ok": True, "active_rules": 0}
                elif action == "set" and set(request) == {"token", "action", "rules"}:
                    rules = _rules_from_json(request["rules"])
                    self.set_rules(rules)
                    response = {"ok": True, "active_rules": len(rules)}
                else:
                    raise ValueError("unsupported route control action")
            except (ValueError, TypeError, PermissionError) as error:
                response = {"ok": False, "error": str(error)[:160]}
            client.sendall(json.dumps(response, separators=(",", ":")).encode() + b"\n")

    def _serve(self, client: socket.socket) -> None:
        try:
            upstream = socket.create_connection(
                (self.config["target_ip"], self.config["target_port"]), timeout=5,
            )
        except OSError:
            client.close()
            return
        client.settimeout(None)
        upstream.settimeout(None)
        inspect_modbus = self.config.get("inspect_protocol") == "modbus-tcp"
        correlator = ModbusTransactionCorrelator(self.current_rules()) if inspect_modbus else None
        if correlator:
            with self._lock:
                self._correlators.add(correlator)

        def record_match(matched: tuple[str, ...], direction: str) -> None:
            if not matched:
                return
            rules = {rule.rule_id: rule for rule in self.current_rules()}
            for rule_id in matched:
                rule = rules.get(rule_id)
                if rule is None:
                    continue
                audit_line = json.dumps({
                    "event": "modbus_request_rewritten" if direction == "request" else "modbus_response_rewritten",
                    "route_id": self.config["route_id"],
                    "rule_ids": [rule.rule_id],
                    "count": 1,
                    "exercise_id": rule.exercise_id,
                    "event_id": rule.event_id,
                    "unit_id": rule.unit_id,
                    "function_code": rule.function_code,
                    "address": rule.address,
                    "direction": direction,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z",
                }, separators=(",", ":"))
                if self._audit_queue is not None:
                    self._audit_queue.append(audit_line)
                print(audit_line, flush=True)

        def pump(source: socket.socket, destination: socket.socket, *, requests: bool) -> None:
            buffer = bytearray()
            try:
                while True:
                    chunk = source.recv(4096)
                    if not chunk:
                        break
                    if not correlator:
                        self._send(destination, chunk)
                        continue
                    buffer.extend(chunk)
                    while buffer:
                        frame_parts = split_mbap_frame(buffer)
                        if frame_parts is None:
                            if len(buffer) > MAX_BUFFER_BYTES:
                                raise ModbusFrameError("incomplete Modbus frame exceeds its buffer limit")
                            break
                        frame, remainder = frame_parts
                        buffer = bytearray(remainder)
                        if requests:
                            forwarded, matched = correlator.forward_request_with_matches(frame)
                        else:
                            forwarded, matched = correlator.forward_response(frame)
                        self._send(destination, forwarded)
                        record_match(matched, "request" if requests else "response")
            except (OSError, ModbusFrameError, ValueError):
                # Malformed framing or ambiguous transaction state fails closed
                # for this flow; never scan arbitrary bytes for a new boundary.
                try:
                    source.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            finally:
                try:
                    destination.shutdown(socket.SHUT_WR)
                except OSError:
                    pass

        threading.Thread(target=pump, args=(client, upstream), kwargs={"requests": True}, daemon=True).start()
        pump(upstream, client, requests=False)
        for stream in (client, upstream):
            try:
                stream.close()
            except OSError:
                pass
        if correlator:
            with self._lock:
                self._correlators.discard(correlator)

    def _send(self, destination: socket.socket, data: bytes) -> None:
        latency_ms = int(self.config.get("latency_ms", 0))
        if latency_ms:
            time.sleep(latency_ms / 1000)
        destination.sendall(data)


def main() -> None:
    raw = os.environ.get("ANVIL_TCP_GATEWAY_CONFIG", "")
    config = json.loads(raw)
    environment_token = os.environ.get("ANVIL_TCP_GATEWAY_CONTROL_TOKEN", "")
    if not hmac.compare_digest(str(config.get("control_token", "")), environment_token):
        raise SystemExit("route control token configuration mismatch")
    Gateway(config).run()


if __name__ == "__main__":
    main()
