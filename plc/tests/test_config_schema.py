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
    def test_accepts_the_mvp_configuration(self) -> None:
        config = PLCConfig.model_validate(valid_config())

        self.assertEqual(config.plc_id, "plc-01")
        self.assertEqual(len(config.components), 2)

    def test_rejects_unknown_component_types(self) -> None:
        config = valid_config()
        config["components"][0]["type"] = "motor"

        with self.assertRaises(ValidationError):
            PLCConfig.model_validate(config)

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
