from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from services.plc_operations import read_device_values, write_device_point  # noqa: E402


class PlcOperationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.container = Mock()
        self.container.labels = {"anvil.node_id": "plc-1"}
        self.manager = SimpleNamespace(client=SimpleNamespace(containers=SimpleNamespace(list=Mock(return_value=[self.container]))))
        self.runtime = {
            "node_id": "plc-1",
            "config": {
                "rack": 0,
                "slot": 1,
                "components": [
                    {"type": "valve", "name": "V1", "db": 1, "byte_offset": 0, "bit_offset": 2},
                    {"type": "analog_sensor", "name": "P1", "db": 1, "byte_offset": 2, "unit": "bar"},
                ],
            },
        }

    def respond(self, value: bool | float, data_type: str, raw_hex: str) -> None:
        self.container.exec_run.return_value = SimpleNamespace(exit_code=0, output=json.dumps({"value": value, "data_type": data_type, "raw_hex": raw_hex}).encode())

    def test_reads_each_point_by_running_snap7_client_in_managed_plc(self) -> None:
        self.respond(False, "BOOL", "00")
        result = read_device_values(self.manager, "scenario", self.runtime)
        self.assertEqual([item["address"] for item in result["values"]], ["DB1.DBX0.2", "DB1.DBD2"])
        args = self.container.exec_run.call_args.args[0]
        self.assertIn("from snap7.client import Client", args[2])
        self.assertIn("client.db_read(int(db), int(offset)", args[2])
        self.assertEqual(args[3], "real")

    def test_writes_only_selected_boolean_point_and_returns_value(self) -> None:
        self.respond(True, "BOOL", "04")
        result = write_device_point(self.manager, "scenario", self.runtime, self.runtime["config"]["components"][0], True)
        self.assertEqual(result["address"], "DB1.DBX0.2")
        self.assertTrue(result["value"])
        args = self.container.exec_run.call_args.args[0]
        self.assertEqual(args[3], "write_bool")
        self.assertEqual(args[-2], "2")
        self.assertEqual(args[-1], "true")

    def test_writes_real_output_channel_without_boolean_coercion(self) -> None:
        component = {"type": "io_channel", "name": "Setpoint", "db": 1,
                     "byte_offset": 4, "bit_offset": 0, "data_type": "REAL", "direction": "output"}
        self.respond(47.5, "REAL", "42 3e 00 00")
        result = write_device_point(self.manager, "scenario", self.runtime, component, 47.5)
        self.assertEqual(result["address"], "DB1.DBD4")
        self.assertEqual(result["value"], 47.5)
        args = self.container.exec_run.call_args.args[0]
        self.assertEqual(args[3], "write_real")
        self.assertEqual(args[-1], "47.5")

    def test_s7_memory_area_is_passed_to_snap7_client_and_reported_in_address(self):
        component = {"type": "io_channel", "name": "Marker", "area": "M", "db": 1,
                     "byte_offset": 3, "bit_offset": 2, "data_type": "BOOL", "direction": "output"}
        self.respond(True, "BOOL", "04")
        result = write_device_point(self.manager, "scenario", self.runtime, component, True)
        self.assertEqual(result["address"], "M3.2")
        args = self.container.exec_run.call_args.args[0]
        self.assertEqual(args[6], "M")


if __name__ == "__main__":
    unittest.main()
