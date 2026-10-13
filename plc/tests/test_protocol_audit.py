from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from protocol_audit import ProtocolAuditQueue, main


class ProtocolAuditQueueTests(unittest.TestCase):
    def test_pending_queue_survives_reopen_and_acknowledges_only_after_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "events.sqlite3")
            queue = ProtocolAuditQueue(path)
            queue.append("2026-10-09T00:00:00Z anvil.modbus: write")
            queue.append("2026-10-09T00:00:01Z anvil.plc.protocol: write")
            pending = queue.pending(limit=1)
            self.assertEqual(pending["pending_count"], 2)
            self.assertEqual(pending["cursor"], 1)
            queue.close()

            reopened = ProtocolAuditQueue(path)
            pending = reopened.pending()
            self.assertEqual([event["id"] for event in pending["events"]], [1, 2])
            self.assertEqual(pending["cursor"], 2)
            reopened.acknowledge(pending["cursor"])
            self.assertEqual(reopened.pending()["events"], [])
            reopened.close()

    def test_queue_bounds_count_and_reports_dropped_oldest_events(self):
        queue = ProtocolAuditQueue(":memory:", capacity=2, max_bytes=4096)
        queue.append("first")
        queue.append("second")
        stats = queue.append("third")
        pending = queue.pending()
        self.assertEqual([item["line"] for item in pending["events"]], ["second", "third"])
        self.assertEqual(stats["dropped_events"], 1)
        self.assertEqual(pending["dropped_events"], 1)
        queue.close()

    def test_queue_bounds_payload_bytes_and_rejects_multiline_events(self):
        queue = ProtocolAuditQueue(":memory:", capacity=10, max_bytes=1024)
        queue.append("a" * 700)
        stats = queue.append("b" * 700)
        self.assertEqual(stats["pending_count"], 1)
        self.assertEqual(stats["dropped_events"], 1)
        with self.assertRaisesRegex(ValueError, "one non-empty line"):
            queue.append("one\ntwo")
        queue.close()

    def test_ack_command_requires_runtime_token_and_commits_a_valid_cursor(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "events.sqlite3")
            queue = ProtocolAuditQueue(path)
            queue.append("audit")
            queue.close()
            output = io.StringIO()
            with patch.dict(os.environ, {
                "ANVIL_PROTOCOL_AUDIT_PATH": path,
                "ANVIL_PROTOCOL_AUDIT_ACK_TOKEN": "x" * 43,
                "ANVIL_PROTOCOL_AUDIT_EXEC_TOKEN": "wrong",
            }), contextlib.redirect_stdout(output):
                self.assertEqual(main(["ack", "1"]), 3)
            with patch.dict(os.environ, {
                "ANVIL_PROTOCOL_AUDIT_PATH": path,
                "ANVIL_PROTOCOL_AUDIT_ACK_TOKEN": "x" * 43,
                "ANVIL_PROTOCOL_AUDIT_EXEC_TOKEN": "x" * 43,
            }), contextlib.redirect_stdout(output):
                self.assertEqual(main(["ack", "1"]), 0)
            result = ProtocolAuditQueue(path)
            self.assertEqual(result.pending()["events"], [])
            result.close()

    def test_pending_cli_outputs_json_and_clamps_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "events.sqlite3")
            queue = ProtocolAuditQueue(path)
            queue.append("audit")
            queue.close()
            output = io.StringIO()
            with patch.dict(os.environ, {"ANVIL_PROTOCOL_AUDIT_PATH": path}), contextlib.redirect_stdout(output):
                self.assertEqual(main(["pending", "50000"]), 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["events"][0]["line"], "audit")


if __name__ == "__main__":
    unittest.main()
