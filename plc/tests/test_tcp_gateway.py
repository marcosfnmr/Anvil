from __future__ import annotations

import socket
import threading
import unittest
import json
import os
import tempfile
from unittest.mock import patch

from tcp_gateway import Gateway
from modbus_interceptor import RewriteRule
from protocol_audit import ProtocolAuditQueue


def _read_exact(stream: socket.socket, size: int) -> bytes:
    result = bytearray()
    while len(result) < size:
        chunk = stream.recv(size - len(result))
        if not chunk:
            raise RuntimeError("socket closed before complete Modbus frame")
        result.extend(chunk)
    return bytes(result)


class TcpGatewayTests(unittest.TestCase):
    def test_client_sees_rewritten_value_while_upstream_keeps_original(self) -> None:
        upstream_listener = socket.socket()
        upstream_listener.bind(("127.0.0.1", 0))
        upstream_listener.listen(1)
        upstream_port = upstream_listener.getsockname()[1]
        request = b"\x00\x0c\x00\x00\x00\x06\x01\x03\x00\x0a\x00\x01"
        response = b"\x00\x0c\x00\x00\x00\x05\x01\x03\x02\x00\x15"
        observed: list[bytes] = []

        def server() -> None:
            peer, _ = upstream_listener.accept()
            with peer:
                observed.append(_read_exact(peer, len(request)))
                peer.sendall(response)

        server_thread = threading.Thread(target=server, daemon=True)
        server_thread.start()
        temp_dir = tempfile.TemporaryDirectory()
        queue_path = os.path.join(temp_dir.name, "events.sqlite3")
        with patch.dict(os.environ, {
            "ANVIL_PROTOCOL_AUDIT_ACK_TOKEN": "a" * 43,
            "ANVIL_PROTOCOL_AUDIT_PATH": queue_path,
        }):
            gateway = Gateway({
                "route_id": "unit-test",
                "listen_ip": "127.0.0.1",
                "listen_port": 25001,
                "source_ip": "127.0.0.1",
                "target_ip": "127.0.0.1",
                "target_port": upstream_port,
                "latency_ms": 0,
                "inspect_protocol": "modbus-tcp",
                "control_token": "x" * 43,
            })
        gateway.set_rules((RewriteRule("pressure", 1, 3, 10, 900, "exercise-1", "event-1"),))
        client, relay = socket.socketpair()
        client.settimeout(2)
        relay_thread = threading.Thread(target=gateway._serve, args=(relay,), daemon=True)
        relay_thread.start()
        try:
            client.sendall(request)
            rewritten = _read_exact(client, len(response))
            self.assertEqual(observed, [request])
            self.assertEqual(int.from_bytes(response[-2:], "big"), 21)
            self.assertEqual(int.from_bytes(rewritten[-2:], "big"), 900)
            self.assertEqual(rewritten[:9], response[:9])
            queue = ProtocolAuditQueue(queue_path)
            try:
                [queued] = queue.pending()["events"]
                metadata = json.loads(queued["line"])
                self.assertEqual(metadata["exercise_id"], "exercise-1")
                self.assertEqual(metadata["event_id"], "event-1")
                self.assertEqual(metadata["rule_ids"], ["pressure"])
                self.assertNotIn("replacement_value", metadata)
            finally:
                queue.close()
                gateway._audit_queue.close()
                temp_dir.cleanup()
        finally:
            client.close()
            upstream_listener.close()
            relay_thread.join(timeout=2)
            server_thread.join(timeout=2)

    def test_other_route_protocol_remains_byte_transparent(self) -> None:
        upstream_listener = socket.socket()
        upstream_listener.bind(("127.0.0.1", 0))
        upstream_listener.listen(1)
        upstream_port = upstream_listener.getsockname()[1]
        request = b"opaque request bytes"
        response = b"opaque response bytes"

        def server() -> None:
            peer, _ = upstream_listener.accept()
            with peer:
                self.assertEqual(_read_exact(peer, len(request)), request)
                peer.sendall(response)

        server_thread = threading.Thread(target=server, daemon=True)
        server_thread.start()
        gateway = Gateway({
            "route_id": "opaque-test", "listen_ip": "127.0.0.1", "listen_port": 25002,
            "source_ip": "127.0.0.1", "target_ip": "127.0.0.1", "target_port": upstream_port,
            "latency_ms": 0, "inspect_protocol": None, "control_token": "y" * 43,
        })
        client, relay = socket.socketpair()
        client.settimeout(2)
        relay_thread = threading.Thread(target=gateway._serve, args=(relay,), daemon=True)
        relay_thread.start()
        try:
            client.sendall(request)
            self.assertEqual(_read_exact(client, len(response)), response)
        finally:
            client.close()
            upstream_listener.close()
            relay_thread.join(timeout=2)
            server_thread.join(timeout=2)

    def test_gateway_rewrites_fc06_before_upstream_and_queues_match(self) -> None:
        upstream_listener = socket.socket()
        upstream_listener.bind(("127.0.0.1", 0))
        upstream_listener.listen(1)
        upstream_port = upstream_listener.getsockname()[1]
        request = b"\x00\x21\x00\x00\x00\x06\x01\x06\x00\x0a\x00\x2a"
        rewritten_request = request[:10] + (65000).to_bytes(2, "big")
        observed: list[bytes] = []

        def server() -> None:
            peer, _ = upstream_listener.accept()
            with peer:
                observed.append(_read_exact(peer, len(request)))
                peer.sendall(observed[0])

        server_thread = threading.Thread(target=server, daemon=True)
        server_thread.start()
        temp_dir = tempfile.TemporaryDirectory()
        queue_path = os.path.join(temp_dir.name, "events.sqlite3")
        with patch.dict(os.environ, {
            "ANVIL_PROTOCOL_AUDIT_ACK_TOKEN": "a" * 43,
            "ANVIL_PROTOCOL_AUDIT_PATH": queue_path,
        }):
            gateway = Gateway({
                "route_id": "write-test", "listen_ip": "127.0.0.1", "listen_port": 25004,
                "source_ip": "127.0.0.1", "target_ip": "127.0.0.1", "target_port": upstream_port,
                "latency_ms": 0, "inspect_protocol": "modbus-tcp", "control_token": "w" * 43,
            })
        gateway.set_rules((RewriteRule("setpoint", 1, 6, 10, 65000, "exercise-w", "event-w"),))
        client, relay = socket.socketpair()
        client.settimeout(2)
        relay_thread = threading.Thread(target=gateway._serve, args=(relay,), daemon=True)
        relay_thread.start()
        try:
            client.sendall(request)
            response = _read_exact(client, len(request))
            self.assertEqual(observed, [rewritten_request])
            self.assertEqual(response, rewritten_request)
            queue = ProtocolAuditQueue(queue_path)
            try:
                [queued] = queue.pending()["events"]
                metadata = json.loads(queued["line"])
                self.assertEqual(metadata["event"], "modbus_request_rewritten")
                self.assertEqual(metadata["direction"], "request")
                self.assertEqual(metadata["function_code"], 6)
                self.assertNotIn("replacement_value", metadata)
            finally:
                queue.close()
                gateway._audit_queue.close()
                temp_dir.cleanup()
        finally:
            client.close()
            upstream_listener.close()
            relay_thread.join(timeout=2)
            server_thread.join(timeout=2)

    def test_unix_control_handler_authenticates_and_updates_rules(self) -> None:
        token = "z" * 43
        gateway = Gateway({
            "route_id": "control-test", "listen_ip": "127.0.0.1", "listen_port": 25003,
            "source_ip": "127.0.0.1", "target_ip": "127.0.0.1", "target_port": 502,
            "latency_ms": 0, "inspect_protocol": "modbus-tcp", "control_token": token,
        })

        def control(request: dict) -> dict:
            client, worker = socket.socketpair()
            thread = threading.Thread(target=gateway._handle_control, args=(worker,), daemon=True)
            thread.start()
            client.sendall(json.dumps(request).encode() + b"\n")
            raw = bytearray()
            while b"\n" not in raw:
                raw.extend(client.recv(1024))
            client.close()
            thread.join(timeout=1)
            return json.loads(bytes(raw).split(b"\n", 1)[0])

        valid_rule = {"rule_id": "pressure", "unit_id": 1, "function_code": 3,
                      "address": 10, "replacement_value": 900}
        self.assertEqual(control({"token": token, "action": "set", "rules": [valid_rule]}),
                         {"ok": True, "active_rules": 1})
        self.assertEqual(len(gateway.current_rules()), 1)
        self.assertFalse(control({"token": "x" * 43, "action": "clear"})["ok"])
        self.assertEqual(len(gateway.current_rules()), 1)
        self.assertEqual(control({"token": token, "action": "clear"}),
                         {"ok": True, "active_rules": 0})
        self.assertEqual(gateway.current_rules(), ())


if __name__ == "__main__":
    unittest.main()
