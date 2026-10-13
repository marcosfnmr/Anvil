from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from field_bridge import FieldBridge


class FieldBridgeCycleTests(unittest.TestCase):
    def test_bridge_uses_configured_scan_interval(self):
        self.assertEqual(FieldBridge({"scan_cycle_ms": 250}, None).interval, .25)

    def test_legacy_bridge_defaults_to_100_ms(self):
        self.assertEqual(FieldBridge({}, None).interval, .1)


if __name__ == "__main__":
    unittest.main()
