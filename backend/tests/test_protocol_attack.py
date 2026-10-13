from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from services.protocol_attack import execute_protocol_write  # noqa: E402
from services.docker_manager import DockerManagerError  # noqa: E402


class _Container:
    labels = {"anvil.node_id": "participant"}

    def __init__(self, exit_code: int = 0, output: bytes = b'{"value":true}') -> None:
        self.exit_code, self.output, self.args = exit_code, output, None

    def exec_run(self, args, **kwargs):
        self.args = args
        return SimpleNamespace(exit_code=self.exit_code, output=self.output)


class ProtocolAttackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.container = _Container()
        self.manager = SimpleNamespace(client=SimpleNamespace(containers=SimpleNamespace(
            list=lambda **_: [self.container]
        )))
        self.source = {"node_id": "participant"}

    def test_modbus_write_runs_native_client_in_station_container_via_declared_route(self) -> None:
        target = {"node_id": "modbus", "device_type": "modbus-tcp", "ip": "172.30.1.20",
                  "config": {"unit_id": 7, "port": 1502}}
        point = SimpleNamespace(address="coil:3")
        route = {"host": "172.30.2.3", "port": 24003}
        result = execute_protocol_write(self.manager, "scenario-1", self.source, target,
                                        point, None, "BOOL", True, route)
        self.assertEqual(result["protocol"], "modbus-tcp")
        self.assertEqual(result["value"], True)
        self.assertEqual(self.container.args[-6:], ["172.30.2.3", "24003", "7", "coil", "3", "true"])
        self.assertIn("ModbusTcpClient", self.container.args[2])

    def test_s7_write_runs_snap7_client_in_station_container(self) -> None:
        target = {"node_id": "plc", "device_type": "plc-s7", "ip": "172.30.1.20",
                  "config": {"rack": 0, "slot": 1}}
        component = {"type": "io_channel", "data_type": "REAL", "area": "DB", "db": 2,
                     "byte_offset": 4, "bit_offset": 0}
        point = SimpleNamespace(address="DB2.DBD4")
        result = execute_protocol_write(self.manager, "scenario-1", self.source, target,
                                        point, component, "REAL", 45.5)
        self.assertEqual(result["protocol"], "s7")
        self.assertEqual(self.container.args[3:11], ["172.30.1.20", "102", "0", "1", "DB", "2", "4", "0"])
        self.assertIn("from snap7.client import Client", self.container.args[2])

    def test_nonzero_protocol_client_exit_is_reported(self) -> None:
        self.container.exit_code = 1
        self.container.output = b"write rejected"
        target = {"node_id": "modbus", "device_type": "modbus-tcp", "ip": "172.30.1.20",
                  "config": {"unit_id": 1, "port": 502}}
        with self.assertRaisesRegex(DockerManagerError, "write rejected"):
            execute_protocol_write(self.manager, "scenario-1", self.source, target,
                                   SimpleNamespace(address="coil:0"), None, "BOOL", False)


if __name__ == "__main__":
    unittest.main()
