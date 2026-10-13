from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
from services.modbus_operations import read_device_values, write_device_point  # noqa: E402


class ModbusOperationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.container = Mock()
        self.container.labels = {"anvil.node_id": "mb-1"}
        self.manager = SimpleNamespace(client=SimpleNamespace(containers=SimpleNamespace(list=Mock(return_value=[self.container]))))
        self.runtime = {"node_id": "mb-1", "config": {"unit_id": 3, "port": 1502, "points": [
            {"type": "coil", "name": "pump", "address": 0},
            {"type": "discrete_input", "name": "switch", "address": 2},
            {"type": "holding_register", "name": "speed", "address": 4},
        ]}}

    def respond(self, value: bool | int, raw_hex: str) -> None:
        self.container.exec_run.return_value = SimpleNamespace(exit_code=0, output=json.dumps({"value": value, "raw_hex": raw_hex}).encode())

    def test_reads_all_configured_points_from_managed_container(self) -> None:
        self.respond(123, "007b")
        response = read_device_values(self.manager, "scenario", self.runtime)
        self.assertEqual(response["values"][0]["address"], "coil:0")
        command = self.container.exec_run.call_args.args[0]
        self.assertIn("ModbusTcpClient", command[2])
        self.assertEqual(command[3:9], ["read", "tcp", "1502", "3", "holding_register", "4"])

    def test_writes_point_and_reads_back_value(self) -> None:
        self.respond(123, "007b")
        response = write_device_point(self.manager, "scenario", self.runtime, self.runtime["config"]["points"][1], 123)
        self.assertEqual(response["value"], 123)
        self.assertEqual(self.container.exec_run.call_args.args[0][3], "write")

    def test_discrete_input_is_read_only_boolean(self) -> None:
        self.respond(True, "01")
        point = self.runtime["config"]["points"][1]
        result = read_device_values(self.manager, "scenario", {**self.runtime, "config": {**self.runtime["config"], "transport": "tcp", "points": [point]}})
        self.assertEqual(result["values"][0]["address"], "discrete_input:2")
        command = self.container.exec_run.call_args.args[0]
        self.assertIn("read_discrete_inputs", command[2])

    def test_rtu_operations_use_pyserial_socket_tunnel(self) -> None:
        self.runtime["config"].update({"transport": "rtu", "baudrate": 9600, "parity": "N"})
        self.container.exec_run.return_value = SimpleNamespace(exit_code=0, output=json.dumps({"values": [
            {"type": "coil", "address": 0, "name": "pump", "value": False, "raw_hex": "00", "unit": None},
            {"type": "holding_register", "address": 4, "name": "speed", "value": 12, "raw_hex": "000c", "unit": None},
        ]}).encode())
        read_device_values(self.manager, "scenario", self.runtime)
        command = self.container.exec_run.call_args.args[0]
        self.assertIn("ModbusSerialClient", command[2])
        config = json.loads(command[3])
        self.assertEqual((config["transport"], config["baudrate"], len(config["points"])), ("rtu", 9600, 3))


if __name__ == "__main__":
    unittest.main()
