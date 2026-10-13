from __future__ import annotations

import sys
import unittest
from pathlib import Path

PLC_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLC_DIR))

from modbus_schema import ModbusConfig  # noqa: E402


class ModbusContainerSchemaTests(unittest.TestCase):
    def test_rejects_duplicate_addresses_per_space(self) -> None:
        with self.assertRaises(ValueError):
            ModbusConfig.model_validate({"points": [
                {"type": "holding_register", "name": "a", "address": 3},
                {"type": "holding_register", "name": "b", "address": 3},
            ]})

    def test_keeps_address_zero_and_initial_values(self) -> None:
        config = ModbusConfig.model_validate({"unit_id": 7, "points": [
            {"type": "coil", "name": "c", "address": 0, "initial_value": True},
            {"type": "holding_register", "name": "r", "address": 0, "initial_value": 125},
        ]})
        self.assertEqual(config.unit_id, 7)
        self.assertEqual([point.address for point in config.points], [0, 0])

    def test_rejects_boolean_and_fractional_register_initial_values(self) -> None:
        for value in (True, 12.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ModbusConfig.model_validate({"points": [
                {"type": "holding_register", "name": "reg", "address": 0, "initial_value": value},
                ]})

    def test_requires_non_empty_point_name_and_points(self) -> None:
        for payload in ({"points": []}, {"points": [{"type": "coil", "name": "", "address": 0}]}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                ModbusConfig.model_validate(payload)

    def test_discrete_inputs_require_boolean_initial_values(self) -> None:
        for value in (0, 1, "true"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ModbusConfig.model_validate({"points": [
                    {"type": "discrete_input", "name": "switch", "address": 1, "initial_value": value},
                ]})

    def test_behavior_options_have_safe_defaults_and_strict_ranges(self) -> None:
        config = ModbusConfig.model_validate({"points": [{"type": "coil", "name": "c", "address": 0}]})
        self.assertEqual(config.response_delay_ms, 0)
        self.assertEqual(config.response_jitter_ms, 0)
        self.assertEqual(config.fault_mode, "none")
        self.assertEqual((config.fault_probability, config.fault_seed), (1.0, 0))
        for payload in (
            {"response_delay_ms": -1},
            {"response_delay_ms": 10001},
            {"response_delay_ms": True},
            {"response_jitter_ms": -1},
            {"response_jitter_ms": 10001},
            {"response_jitter_ms": True},
            {"fault_mode": "timeout"},
            {"fault_probability": True},
            {"fault_probability": 10**1000},
            {"fault_probability": -0.1},
            {"fault_probability": 1.1},
            {"fault_seed": True},
        ):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                ModbusConfig.model_validate({**payload, "points": [{"type": "coil", "name": "c", "address": 0}]})

    def test_rtu_rejects_tcp_jitter_profile(self):
        with self.assertRaises(ValueError):
            ModbusConfig.model_validate({"transport": "rtu", "response_jitter_ms": 1,
                                         "points": [{"type": "coil", "name": "c", "address": 0}]})

    def test_rtu_rejects_tcp_fault_probability(self):
        with self.assertRaises(ValueError):
            ModbusConfig.model_validate({"transport": "rtu", "fault_probability": 0.5,
                                         "points": [{"type": "coil", "name": "c", "address": 0}]})

    def test_tcp_jitter_distributions_and_seed_are_validated(self):
        base = {"points": [{"type": "coil", "name": "c", "address": 0}]}
        for distribution in ("deterministic", "uniform", "normal"):
            config = ModbusConfig.model_validate({**base, "response_jitter_ms": 40,
                "response_jitter_distribution": distribution, "response_jitter_seed": 23})
            self.assertEqual(config.response_jitter_distribution, distribution)
            self.assertEqual(config.response_jitter_seed, 23)
        for values in ({"response_jitter_distribution": "exponential"}, {"response_jitter_seed": -1},
                       {"response_jitter_seed": True}, {"response_jitter_seed": 2147483648}):
            with self.assertRaises(ValueError):
                ModbusConfig.model_validate({**base, **values})
        with self.assertRaises(ValueError):
            ModbusConfig.model_validate({**base, "transport": "rtu", "response_jitter_distribution": "uniform"})


if __name__ == "__main__":
    unittest.main()
