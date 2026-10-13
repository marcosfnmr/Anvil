from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from retained_state import load_state, save_state


class RetainedStateFileTests(unittest.TestCase):
    def test_atomic_round_trip_and_missing_or_invalid_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "state.json"
            self.assertIsNone(load_state(path))
            save_state(path, {"version": 1, "state": {"counter": 2}})
            self.assertEqual(load_state(path), {"version": 1, "state": {"counter": 2}})
            path.write_text("{truncated", encoding="utf-8")
            self.assertIsNone(load_state(path))
            self.assertEqual([entry.name for entry in path.parent.iterdir()], ["state.json"])

    def test_non_finite_state_is_not_written(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                save_state(Path(directory) / "state.json", {"value": float("nan")})


if __name__ == "__main__":
    unittest.main()
