from __future__ import annotations

import tempfile
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scada_history import DurableHistory


class DurableHistoryTests(unittest.TestCase):
    def test_unacknowledged_samples_survive_reopen_and_ack_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "history.sqlite3")
            store = DurableHistory(path, 10)
            store.append({"timestamp": "t1", "values": [{"value": 1}]})
            batch = store.pending(10)
            self.assertEqual(batch["samples"][0]["timestamp"], "t1")
            store.close()

            restored = DurableHistory(path, 10)
            self.assertEqual(restored.pending(10)["samples"], batch["samples"])
            restored.acknowledge(batch["cursor"])
            self.assertEqual(restored.pending(10)["samples"], [])
            restored.acknowledge(batch["cursor"])
            restored.close()

    def test_acknowledged_rows_are_removed_and_queue_overflow_is_counted(self) -> None:
        store = DurableHistory(None, 2)
        first = store.append({"timestamp": "t1", "values": []})
        batch = store.pending(1)
        store.acknowledge(batch["cursor"])
        store.append({"timestamp": "t2", "values": []})
        store.append({"timestamp": "t3", "values": []})
        overflow = store.append({"timestamp": "t4", "values": []})
        pending = store.pending(10)
        self.assertEqual(overflow["pending_count"], 2)
        self.assertEqual(overflow["dropped_samples"], 1)
        self.assertEqual([item["timestamp"] for item in pending["samples"]], ["t3", "t4"])
        self.assertEqual(first["pending_count"], 1)
        with self.assertRaisesRegex(ValueError, "outside"):
            store.acknowledge(pending["cursor"] + 1)
        store.close()

    def test_oversized_sample_is_counted_and_not_written(self) -> None:
        store = DurableHistory(None, 10, max_bytes=32)
        stats = store.append({"timestamp": "t1", "values": [{"text": "x" * 100}]})
        self.assertEqual(stats, {"pending_count": 0, "dropped_samples": 1})
        store.close()


if __name__ == "__main__":
    unittest.main()
