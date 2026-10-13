"""Tests for PLC_CONFIG validation."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config_schema import PLCConfig


def valid_config() -> dict:
    return {
        "plc_id": "plc-01",
        "rack": 0,
        "slot": 1,
        "db_size": 32,
        "components": [
            {
                "type": "valve",
                "name": "valvula_principal",
                "db": 1,
                "byte_offset": 0,
                "bit_offset": 0,
                "initial_state": False,
            },
            {
                "type": "analog_sensor",
                "name": "presion",
                "db": 1,
                "byte_offset": 2,
                "unit": "bar",
                "min_value": 0.0,
                "max_value": 10.0,
                "simulation": {"mode": "sine_wave", "period_seconds": 30},
            },
        ],
    }


class PLCConfigTests(unittest.TestCase):
    def test_sensor_noise_seed_is_bounded_and_strict(self) -> None:
        config = valid_config()
        config["components"][1]["simulation"].update(noise=0.2, seed=4294967295)
        self.assertEqual(PLCConfig.model_validate(config).components[1].simulation.seed, 4294967295)
        for seed in (-1, 4294967296, 1.5, True):
            invalid = valid_config()
            invalid["components"][1]["simulation"]["seed"] = seed
            with self.subTest(seed=seed), self.assertRaises(ValidationError):
                PLCConfig.model_validate(invalid)

    def test_s7_fault_probability_is_bounded_strict_and_seeded(self) -> None:
        config = valid_config()
        self.assertEqual((PLCConfig.model_validate(config).fault_probability, PLCConfig.model_validate(config).fault_seed), (1.0, 0))
        config.update(fault_mode="timeout", fault_probability=0.25, fault_seed=19)
        self.assertEqual((PLCConfig.model_validate(config).fault_probability, PLCConfig.model_validate(config).fault_seed), (0.25, 19))
        for values in ({"fault_probability": True}, {"fault_probability": 10**1000}, {"fault_probability": -0.1},
                       {"fault_probability": 1.1}, {"fault_seed": True}):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                PLCConfig.model_validate({**valid_config(), **values})

    def test_s7_fault_mode_defaults_to_none_and_accepts_silent_timeout(self) -> None:
        config = valid_config()
        self.assertEqual(PLCConfig.model_validate(config).fault_mode, "none")
        config["fault_mode"] = "timeout"
        self.assertEqual(PLCConfig.model_validate(config).fault_mode, "timeout")
        config["fault_mode"] = "disconnect"
        with self.assertRaises(ValidationError):
            PLCConfig.model_validate(config)

    def test_scan_cycle_defaults_and_accepts_supported_bounds(self) -> None:
        config = valid_config()
        self.assertEqual(PLCConfig.model_validate(config).scan_cycle_ms, 100)
        for value in (10, 5000):
            config["scan_cycle_ms"] = value
            self.assertEqual(PLCConfig.model_validate(config).scan_cycle_ms, value)

    def test_scan_cycle_rejects_out_of_range_and_non_integer_values(self) -> None:
        config = valid_config()
        for value in (9, 5001, True, 100.5):
            with self.subTest(value=value):
                config["scan_cycle_ms"] = value
                with self.assertRaises(ValidationError):
                    PLCConfig.model_validate(config)

    def test_accepts_the_mvp_configuration(self) -> None:
        config = PLCConfig.model_validate(valid_config())

        self.assertEqual(config.plc_id, "plc-01")
        self.assertEqual(len(config.components), 2)

    def test_rejects_unknown_component_types(self) -> None:
        config = valid_config()
        config["components"][0]["type"] = "unknown_component"

        with self.assertRaises(ValidationError):
            PLCConfig.model_validate(config)

    def test_accepts_motor_digital_input_and_alarm(self) -> None:
        config = valid_config()
        config["components"] = [
            {"type": kind, "name": kind, "db": 1, "byte_offset": 0, "bit_offset": offset}
            for offset, kind in enumerate(("motor", "digital_input", "alarm"))
        ]
        self.assertEqual(len(PLCConfig.model_validate(config).components), 3)

    def test_requires_a_period_for_a_sine_wave(self) -> None:
        config = valid_config()
        del config["components"][1]["simulation"]["period_seconds"]

        with self.assertRaises(ValidationError):
            PLCConfig.model_validate(config)

    def test_rejects_a_real_that_does_not_fit_in_the_db(self) -> None:
        config = valid_config()
        config["components"][1]["byte_offset"] = 29

        with self.assertRaises(ValidationError):
            PLCConfig.model_validate(config)

    def test_accepts_sensor_without_a_unit(self) -> None:
        config = valid_config()
        del config["components"][1]["unit"]
        self.assertEqual(PLCConfig.model_validate(config).components[1].unit, "")

    def test_registers_configured_process_image_areas(self):
        from server import create_db_buffers, create_io_buffers
        config = valid_config()
        config["components"] = [
            {"type": "io_channel", "name": "marker", "db": 1, "area": "M", "byte_offset": 0,
             "data_type": "BOOL", "direction": "output"},
            {"type": "io_channel", "name": "input", "db": 1, "area": "I", "byte_offset": 0,
             "data_type": "BOOL", "direction": "input"},
            {"type": "io_channel", "name": "output", "db": 1, "area": "Q", "byte_offset": 0,
             "data_type": "REAL", "direction": "output"},
        ]
        parsed = PLCConfig.model_validate(config)
        self.assertEqual(create_db_buffers(parsed), {})
        self.assertEqual(set(create_io_buffers(parsed)), {"M", "I", "Q"})

    def test_accepts_real_output_channel(self) -> None:
        config = valid_config()
        config["components"] = [{
            "type": "io_channel", "name": "Flow setpoint", "db": 1,
            "byte_offset": 0, "bit_offset": 0, "data_type": "REAL",
            "direction": "output", "unit": "L/min",
        }]
        result = PLCConfig.model_validate(config)
        self.assertEqual(result.components[0].data_type, "REAL")
        self.assertEqual(result.components[0].direction, "output")

    def test_pump_command_uses_one_byte_at_end_of_db(self) -> None:
        config = valid_config()
        config["components"] = [{
            "type": "pump_command", "name": "pump", "db": 1,
            "byte_offset": 31, "bit_offset": 0, "flow_byte_offset": 0,
            "flow_min": 0, "flow_max": 100,
        }]
        PLCConfig.model_validate(config)

    def test_rejects_pump_flow_reference_outside_db(self) -> None:
        config = valid_config()
        config["components"] = [{
            "type": "pump_flow", "name": "flow", "db": 1,
            "byte_offset": 0, "command_byte_offset": 32, "command_bit_offset": 0,
            "flow_min": 0, "flow_max": 100,
        }]
        with self.assertRaises(ValidationError):
            PLCConfig.model_validate(config)
