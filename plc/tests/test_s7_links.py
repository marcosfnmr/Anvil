import struct
import sys
import threading
import socket
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config_schema import RemoteInputComponentConfig
from s7_links import S7Links, TimedClient


class S7LinksTests(unittest.TestCase):
    def test_force_off_quality_is_only_active_while_waiting_or_stale(self):
        common = dict(type="remote_input", name="remote", db=1, byte_offset=2,
                      source_node_id="sender", source_ip="172.30.243.10", source_rack=0,
                      source_slot=1, source_db=1, source_byte_offset=0,
                      data_type="REAL", source_address="DB1.DBD0", stale_after_ms=1000)
        forced = RemoteInputComponentConfig(**common, stale_behavior="force_off")
        held = RemoteInputComponentConfig(**{**common, "name": "held", "byte_offset": 6,
                                            "source_address": "DB1.DBD4"})
        links = S7Links([forced, held], {1: bytearray(12)}, threading.Event())
        key = links.address(forced)
        self.assertEqual(links.safe_off_inputs(), {(1, 2, None)})  # waiting is not good quality
        links.status[key]["last_success_epoch"] = time.time()
        self.assertEqual(links.safe_off_inputs(), set())
        links.status[key]["last_success_epoch"] = time.time() - 2
        self.assertEqual(links.safe_off_inputs(), {(1, 2, None)})

    def test_peer_that_accepts_tcp_but_never_replies_respects_timeout(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            done = threading.Event()
            def accept():
                connection, _ = listener.accept()
                with connection:
                    done.wait(2)
            thread = threading.Thread(target=accept, daemon=True)
            thread.start()
            client = TimedClient()
            client.timeout_seconds = 0.2
            started = time.monotonic()
            try:
                with self.assertRaises(Exception) as caught:
                    client.connect("127.0.0.1", 0, 1, tcp_port=listener.getsockname()[1])
                self.assertTrue(any(word in str(caught.exception).lower() for word in ("timeout", "timed out")))
                self.assertGreater(time.monotonic() - started, 0.1)
                self.assertLess(time.monotonic() - started, 1.5)
            finally:
                done.set()
                thread.join(2)
                client.destroy()

    def test_real_and_boolean_apply_without_overwriting_other_bits(self):
        common = dict(type="remote_input", name="remote", db=1, source_node_id="sender",
                      source_ip="172.30.243.10", source_rack=0, source_slot=1, source_db=1, source_byte_offset=0)
        configs = [RemoteInputComponentConfig(**common, byte_offset=0, bit_offset=1, data_type="BOOL", source_address="DB1.DBX0.0"),
                   RemoteInputComponentConfig(**common, byte_offset=2, data_type="REAL", source_address="DB1.DBD2")]
        buffer = bytearray([1, 0, 0, 0, 0, 0])
        links = S7Links(configs, {1: buffer}, threading.Event())
        links.pending = {"DB1.DBX0.1": True, "DB1.DBD2": 42.5}
        with patch("s7_links.STATUS_PATH", "/tmp/anvil-link-unit-status.json"):
            links.apply_values()
            self.assertEqual(buffer[0], 3)
            self.assertEqual(struct.unpack_from(">f", buffer, 2)[0], 42.5)
            # No pending values: last successful readings remain intact.
            links.apply_values()
            self.assertEqual(buffer[0], 3)
            self.assertEqual(struct.unpack_from(">f", buffer, 2)[0], 42.5)
