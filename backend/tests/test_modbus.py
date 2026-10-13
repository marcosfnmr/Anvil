from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from devices import DEVICE_REGISTRY  # noqa: E402
from devices.modbus_tcp import ModbusTcpDevice  # noqa: E402
from models.modbus import ModbusTcpConfigInput  # noqa: E402
from models.scenario import DeviceInput  # noqa: E402


class ModbusSchemaTests(unittest.TestCase):
    def test_automatic_address_uses_free_space_after_explicit_last_address(self) -> None:
        device = DeviceInput.model_validate({
            "node_id": "mb", "device_type": "modbus-tcp", "name": "meter", "ip": "172.30.10.11",
            "config": {"points": [
                {"type": "coil", "name": "last", "address": 65535},
                {"type": "coil", "name": "auto"},
            ]},
        })
        runtime = ModbusTcpDevice().build_runtime_device(device)
        self.assertEqual([point["address"] for point in runtime["config"]["points"]], [65535, 0])

    def test_rejects_invalid_ranges_types_and_duplicate_addresses(self) -> None:
        for payload in (
            {"unit_id": True, "points": [{"type": "coil", "name": "c"}]},
            {"points": [{"type": "coil", "name": "c", "address": "12"}]},
            {"unit_id": 256, "points": [{"type": "coil", "name": "c"}]},
            {"points": [{"type": "coil", "name": "c", "address": 65536}]},
            {"points": [{"type": "coil", "name": "c", "initial_value": 1}]},
            {"points": [{"type": "holding_register", "name": "r", "initial_value": 1.5}]},
            {"points": [{"type": "holding_register", "name": "r", "initial_value": 9, "min_value": 10, "max_value": 20}]},
            {"points": [{"type": "input_register", "name": "r", "min_value": 0}]},
            {"points": [{"type": "input_register", "name": "r", "min_value": 10, "max_value": 10}]},
            {"points": [{"type": "coil", "name": "a", "address": 1}, {"type": "coil", "name": "b", "address": 1}]},
            {"points": [{"type": "discrete_input", "name": "a", "initial_value": 1}]},
            {"points": [{"type": "discrete_input", "name": "a", "address": 2}, {"type": "discrete_input", "name": "b", "address": 2}]},
            {"response_delay_ms": True, "points": [{"type": "coil", "name": "c"}]},
            {"response_delay_ms": 10001, "points": [{"type": "coil", "name": "c"}]},
            {"response_jitter_ms": True, "points": [{"type": "coil", "name": "c"}]},
            {"response_jitter_ms": 10001, "points": [{"type": "coil", "name": "c"}]},
            {"fault_mode": "silent", "points": [{"type": "coil", "name": "c"}]},
            {"fault_probability": True, "points": [{"type": "coil", "name": "c"}]},
            {"fault_probability": 10**1000, "points": [{"type": "coil", "name": "c"}]},
            {"fault_probability": -0.01, "points": [{"type": "coil", "name": "c"}]},
            {"fault_probability": 1.01, "points": [{"type": "coil", "name": "c"}]},
            {"fault_seed": True, "points": [{"type": "coil", "name": "c"}]},
        ):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                ModbusTcpConfigInput.model_validate(payload)

    def test_auto_addresses_are_sequential_and_per_space(self) -> None:
        device = DeviceInput.model_validate({
            "node_id": "mb-1", "device_type": "modbus-tcp", "name": "meter", "ip": "172.30.10.11",
            "config": {"points": [
                {"type": "coil", "name": "c0"},
                {"type": "coil", "name": "c1"},
                {"type": "coil", "name": "c2", "address": 1},
                {"type": "discrete_input", "name": "di0"},
                {"type": "discrete_input", "name": "di1", "address": 1},
                {"type": "holding_register", "name": "h0"},
                {"type": "input_register", "name": "i0"},
            ]},
        })
        runtime = ModbusTcpDevice().build_runtime_device(device)
        self.assertEqual([point["address"] for point in runtime["config"]["points"]], [0, 2, 1, 0, 1, 0, 0])

    def test_adapter_registers_and_never_publishes_host_ports(self) -> None:
        self.assertIn("modbus-tcp", DEVICE_REGISTRY)
        device = DeviceInput.model_validate({
            "node_id": "mb-1", "device_type": "modbus-tcp", "name": "meter", "ip": "172.30.10.11",
            "config": {"points": [{"type": "coil", "name": "pump"}, {"type": "discrete_input", "name": "switch"}, {"type": "input_register", "name": "pressure"}]},
        })
        runtime = ModbusTcpDevice().build_runtime_device(device)
        container = ModbusTcpDevice().build_container_config("scenario-id", runtime)
        self.assertNotIn("ports", container)
        self.assertNotIn("ports", container.get("host_config", {}))
        self.assertEqual(json.loads(container["environment"]["MODBUS_CONFIG"]), runtime["config"])
        self.assertEqual(runtime["config"]["response_delay_ms"], 0)
        self.assertEqual(runtime["config"]["response_jitter_ms"], 0)
        self.assertEqual(runtime["config"]["fault_mode"], "none")
        io = ModbusTcpDevice().build_io_map(runtime)
        self.assertEqual([point["access"] for point in io], ["read_write", "read", "read"])

    def test_modbus_register_range_is_exposed_in_io_map(self) -> None:
        device = DeviceInput.model_validate({
            "node_id": "mb-range", "device_type": "modbus-tcp", "name": "meter", "ip": "172.30.10.21",
            "config": {"points": [{"type": "input_register", "name": "pressure", "unit": "kPa",
                                    "initial_value": 20, "min_value": 10, "max_value": 40}]},
        })
        runtime = ModbusTcpDevice().build_runtime_device(device)
        point = ModbusTcpDevice().build_io_map(runtime)[0]
        self.assertEqual((point["min_value"], point["max_value"], point["unit"]), (10, 40, "kPa"))

    def test_behavior_profile_is_passed_to_the_modbus_container(self) -> None:
        device = DeviceInput.model_validate({
            "node_id": "mb-1", "device_type": "modbus-tcp", "name": "meter", "ip": "172.30.10.11",
            "config": {
                "response_delay_ms": 175,
                "response_jitter_ms": 425,
                "fault_mode": "device_failure",
                "points": [{"type": "coil", "name": "pump"}],
            },
        })
        runtime = ModbusTcpDevice().build_runtime_device(device)
        container = ModbusTcpDevice().build_container_config("scenario-id", runtime)
        config = json.loads(container["environment"]["MODBUS_CONFIG"])
        self.assertEqual(config["response_delay_ms"], 175)
        self.assertEqual(config["response_jitter_ms"], 425)
        self.assertEqual(config["fault_mode"], "device_failure")

    def test_fault_probability_and_seed_reach_modbus_runtime(self) -> None:
        device = DeviceInput.model_validate({
            "node_id": "mb-fault-prob", "device_type": "modbus-tcp", "name": "meter",
            "ip": "172.30.10.12", "config": {"fault_mode": "device_failure",
                "fault_probability": 0.35, "fault_seed": 17,
                "points": [{"type": "coil", "name": "pump"}]},
        })
        runtime = ModbusTcpDevice().build_runtime_device(device)["config"]
        self.assertEqual((runtime["fault_probability"], runtime["fault_seed"]), (0.35, 17))

    def test_rtu_rejects_tcp_fault_probability_settings(self) -> None:
        with self.assertRaises(ValueError):
            DeviceInput.model_validate({
                "node_id": "mb-rtu-fault", "device_type": "modbus-tcp", "name": "meter",
                "ip": "172.30.10.13", "config": {"transport": "rtu", "fault_probability": 0.5,
                    "points": [{"type": "coil", "name": "pump"}]},
            })

    def test_modbus_jitter_distribution_and_seed_reach_runtime(self):
        for distribution in ("deterministic", "uniform", "normal"):
            device = DeviceInput.model_validate({
                "node_id": "mb-profile", "device_type": "modbus-tcp", "name": "meter", "ip": "172.30.10.12",
                "config": {"response_jitter_ms": 37, "response_jitter_distribution": distribution,
                           "response_jitter_seed": 12, "points": [{"type": "coil", "name": "pump"}]},
            })
            config = ModbusTcpDevice().build_runtime_device(device)["config"]
            self.assertEqual(config["response_jitter_distribution"], distribution)
            self.assertEqual(config["response_jitter_seed"], 12)

    def test_rtu_serial_settings_are_validated_and_passed_to_runtime(self) -> None:
        device = DeviceInput.model_validate({
            "node_id": "mb-rtu", "device_type": "modbus-tcp", "name": "RTU meter", "ip": "172.30.10.11",
            "config": {"transport": "rtu", "unit_id": 7, "port": 1502, "baudrate": 9600,
                       "bytesize": 8, "parity": "N", "stopbits": 1,
                       "points": [{"type": "holding_register", "name": "level"}]},
        })
        runtime = ModbusTcpDevice().build_runtime_device(device)
        self.assertEqual(runtime["config"]["transport"], "rtu")
        self.assertEqual(runtime["config"]["baudrate"], 9600)
        self.assertEqual(runtime["config"]["parity"], "N")
        config = ModbusTcpDevice().build_container_config("scenario-id", runtime)
        self.assertNotIn("ports", config)
        for payload in (
            {"transport": "rtu", "unit_id": 0, "points": [{"type": "coil", "name": "c"}]},
            {"transport": "rtu", "unit_id": 248, "points": [{"type": "coil", "name": "c"}]},
            {"transport": "rtu", "baudrate": 14400, "points": [{"type": "coil", "name": "c"}]},
            {"transport": "rtu", "parity": "E", "points": [{"type": "coil", "name": "c"}]},
            {"transport": "rtu", "response_delay_ms": 1, "points": [{"type": "coil", "name": "c"}]},
            {"transport": "rtu", "response_jitter_ms": 1, "points": [{"type": "coil", "name": "c"}]},
        ):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                ModbusTcpConfigInput.model_validate(payload)

    def test_optional_external_modbus_access_is_configurable_for_ingress_relay(self) -> None:
        device = DeviceInput.model_validate({
            "node_id": "mb-1", "device_type": "modbus-tcp", "name": "meter", "ip": "172.30.10.11",
            "external_access": True, "external_port": 2502,
            "config": {"port": 1502, "points": [{"type": "coil", "name": "pump"}]},
        })
        self.assertTrue(device.external_access)
        self.assertEqual(device.external_port, 2502)


if __name__ == "__main__":
    unittest.main()
