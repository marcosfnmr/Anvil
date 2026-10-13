from __future__ import annotations

import sys
import socket
import time
import random
import logging
import unittest
from pathlib import Path
from types import SimpleNamespace

PLC_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLC_DIR))

from snap7.client import Client  # noqa: E402
from snap7.server import Server  # noqa: E402
from snap7.type import SrvArea  # noqa: E402
from protocol_capture import EVENT_AREA_DB, EVENT_READ, EVENT_WRITE, ProtocolCapture  # noqa: E402


class ProtocolCaptureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.buffer = bytearray([0b00000101, 0x42])
        self.capture = ProtocolCapture({1: self.buffer})

    def event(self, code: int, *, area: int = EVENT_AREA_DB, index: int = 1, offset: int = 0, count: int = 1):
        return SimpleNamespace(
            EvtCode=code, EvtParam1=area, EvtParam2=index, EvtParam3=offset,
            EvtParam4=count, EvtRetCode=0, EvtSender=0xAC1E000A,
        )

    def test_records_db_reads_and_writes_with_bytes_and_source(self) -> None:
        self.capture._on_read(self.event(EVENT_READ))
        self.capture._on_event(self.event(EVENT_WRITE, offset=1))
        events = self.capture.snapshot()
        self.assertEqual([event["operation"] for event in events], ["read", "write"])
        self.assertEqual(events[0]["raw_hex"], "05")
        self.assertEqual(events[1]["raw_hex"], "42")
        self.assertEqual(events[0]["source"], "172.30.0.10")
        self.assertEqual(events[0]["db"], 1)

    def test_emits_safe_semantic_s7_audit_line(self):
        with self.assertLogs("anvil.plc.protocol", level="INFO") as captured:
            self.capture._on_event(self.event(EVENT_WRITE, offset=1))
        audit_line = next(line for line in captured.output if "S7 audit operation=" in line)
        self.assertIn("operation=write", audit_line)
        self.assertIn("byte_offset=1", audit_line)
        self.assertNotIn("data=", audit_line)

    def test_durable_queue_records_only_writes_and_failures_without_payload(self):
        from protocol_audit import ProtocolAuditQueue

        queue = ProtocolAuditQueue(":memory:")
        capture = ProtocolCapture({1: self.buffer}, protocol_audit_queue=queue)
        capture._on_read(self.event(EVENT_READ))
        capture._on_event(self.event(EVENT_WRITE, offset=1))
        failure = self.event(EVENT_READ)
        failure.EvtRetCode = 5
        capture._on_read(failure)
        events = queue.pending()["events"]
        self.assertEqual(len(events), 2)
        self.assertIn("operation=write", events[0]["line"])
        self.assertIn("result=error", events[1]["line"])
        self.assertNotIn("42", events[0]["line"])
        queue.close()

    def test_ignores_non_db_and_bounds_history(self) -> None:
        self.capture._on_read(self.event(EVENT_READ, area=0x83))
        self.assertEqual(self.capture.snapshot(), [])
        bounded = ProtocolCapture({1: self.buffer}, capacity=1)
        bounded._on_read(self.event(EVENT_READ))
        bounded._on_event(self.event(EVENT_WRITE, offset=1))
        self.assertEqual(len(bounded.snapshot()), 1)
        self.assertEqual(bounded.snapshot()[0]["operation"], "write")

    def test_response_delay_profile_applies_to_s7_reads_and_writes(self):
        import time
        delayed = ProtocolCapture({1: self.buffer}, response_delay_ms=35)
        for code in (EVENT_READ, EVENT_WRITE):
            started=time.monotonic(); delayed._on_event(self.event(code))
            self.assertGreaterEqual(time.monotonic()-started, .03)

    def test_response_jitter_is_bounded_and_repeatable(self):
        from unittest.mock import patch
        first = ProtocolCapture({1: self.buffer}, response_jitter_ms=50)
        second = ProtocolCapture({1: self.buffer}, response_jitter_ms=50)
        observed = []
        for capture in (first, second):
            with patch("protocol_capture.time.sleep") as sleep:
                for _ in range(8):
                    capture._on_event(self.event(EVENT_READ))
            samples = [call.args[0] * 1000 for call in sleep.call_args_list]
            observed.append(samples)
        self.assertEqual(observed[0], observed[1])
        self.assertTrue(any(sample > 0 for sample in observed[0]))
        self.assertTrue(all(0 <= sample <= 51 for sample in observed[0]))

    def test_normal_response_jitter_uses_configured_seed(self):
        from unittest.mock import patch
        captures = [
            ProtocolCapture({1: self.buffer}, response_jitter_ms=60,
                            response_jitter_distribution="normal", response_jitter_seed=7)
            for _ in range(2)
        ]
        observed = []
        for capture in captures:
            with patch("protocol_capture.time.sleep") as sleep:
                for _ in range(20):
                    capture._on_event(self.event(EVENT_READ))
            observed.append([round(call.args[0] * 1000) for call in sleep.call_args_list])
        self.assertEqual(observed[0], observed[1])
        self.assertTrue(all(0 <= sample <= 60 for sample in observed[0]))
        self.assertGreater(len(set(observed[0])), 5)

    def test_injected_timeout_suppresses_s7_data_responses_but_keeps_setup(self):
        server = Server(log=False)
        original = lambda request, address: b"normal-response"
        server._process_request = original
        server._parse_request = lambda request: {"parameters": {"function_code": request[0]}}
        capture = ProtocolCapture({1: self.buffer}, fault_mode="timeout")
        capture.attach(server)
        self.assertIsNone(server._process_request(b"\x04", ("127.0.0.1", 1234)))
        self.assertIsNone(server._process_request(b"\x05", ("127.0.0.1", 1234)))
        self.assertEqual(server._process_request(b"\xf0", ("127.0.0.1", 1234)), b"normal-response")

    def test_s7_timeout_probability_is_seeded_and_does_not_consume_session_setup_draws(self):
        server = Server(log=False)
        server._process_request = lambda request, address: b"normal-response"
        server._parse_request = lambda request: {"parameters": {"function_code": request[0]}}
        capture = ProtocolCapture({1: self.buffer}, fault_mode="timeout", fault_probability=0.5, fault_seed=23)
        capture.attach(server)
        address = ("127.0.0.1", 1234)
        self.assertEqual(server._process_request(b"\xf0", address), b"normal-response")
        expected_rng = random.Random(23)
        expected = [expected_rng.random() < 0.5 for _ in range(20)]
        observed = [server._process_request(b"\x04", address) is None for _ in range(20)]
        self.assertEqual(observed, expected)

    def test_injected_timeout_is_observed_by_snap7_client_and_does_not_apply_write(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        server = Server(log=False)
        memory = bytearray(b"\x00")
        server.register_area(SrvArea.DB, 1, memory)
        ProtocolCapture({1: memory}, fault_mode="timeout").attach(server)
        client = Client()
        try:
            server.start_to("127.0.0.1", port)
            deadline = time.monotonic() + 2
            last_error = None
            while time.monotonic() < deadline:
                try:
                    client.connect("127.0.0.1", 0, 1, tcp_port=port)
                    if client.get_connected():
                        break
                except Exception as error:
                    last_error = error
                    time.sleep(.02)
            else:
                self.fail(f"Snap7 client could not connect: {last_error}")
            client.connection.timeout = .15
            client.connection.socket.settimeout(.15)

            started = time.monotonic()
            with self.assertRaises(Exception):
                client.db_read(1, 0, 1)
            self.assertGreaterEqual(time.monotonic() - started, .10)

            client.disconnect()
            client.connect("127.0.0.1", 0, 1, tcp_port=port)
            client.connection.timeout = .15
            client.connection.socket.settimeout(.15)
            started = time.monotonic()
            with self.assertRaises(Exception):
                client.db_write(1, 0, bytearray(b"\x01"))
            self.assertGreaterEqual(time.monotonic() - started, .10)
            self.assertEqual(memory, bytearray(b"\x00"))
        finally:
            client.disconnect()
            server.stop()

    def test_captures_m_i_q_process_image_operations_with_separate_areas(self):
        marker = bytearray([0x04, 0x2A])
        inputs = bytearray([0x01, 0x00])
        outputs = bytearray([0x08, 0xFF])
        capture = ProtocolCapture({}, io_buffers={"M": marker, "I": inputs, "Q": outputs})
        capture._on_read(self.event(EVENT_READ, area=int(SrvArea.MK), index=0, offset=1))
        capture._on_read(self.event(EVENT_READ, area=int(SrvArea.PE), index=0))
        capture._on_event(self.event(EVENT_WRITE, area=int(SrvArea.PA), index=0, offset=1))
        events = capture.snapshot()
        self.assertEqual([(item["area"], item["db"], item["byte_offset"], item["raw_hex"])
                          for item in events], [("M", None, 1, "2a"), ("I", None, 0, "01"), ("Q", None, 1, "ff")])


if __name__ == "__main__":
    unittest.main()
