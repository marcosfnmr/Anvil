"""Tests for the component registry and MVP component behavior."""

from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components import COMPONENT_REGISTRY
from config_schema import AnalogSensorComponentConfig, SimulationConfig, ValveComponentConfig


class ComponentTests(unittest.TestCase):
    def test_registry_loads_both_mvp_components(self) -> None:
        self.assertEqual(set(COMPONENT_REGISTRY), {"valve", "analog_sensor"})

    def test_valve_initializes_its_bit_and_preserves_remote_changes(self) -> None:
        valve = COMPONENT_REGISTRY["valve"](
            ValveComponentConfig(
                type="valve",
                name="valvula",
                db=1,
                byte_offset=0,
                bit_offset=2,
                initial_state=True,
            )
        )
        db_buffer = bytearray(32)

        valve.initialize(db_buffer)
        self.assertEqual(db_buffer[0], 0b00000100)

        db_buffer[0] = 0
        valve.simulate(db_buffer, 0.1)
        self.assertEqual(db_buffer[0], 0)

    def test_constant_sensor_writes_the_middle_of_its_range(self) -> None:
        sensor = COMPONENT_REGISTRY["analog_sensor"](
            AnalogSensorComponentConfig(
                type="analog_sensor",
                name="presion",
                db=1,
                byte_offset=2,
                unit="bar",
                min_value=0.0,
                max_value=10.0,
                simulation=SimulationConfig(mode="constant"),
            )
        )
        db_buffer = bytearray(32)

        sensor.initialize(db_buffer)

        self.assertEqual(struct.unpack_from(">f", db_buffer, 2)[0], 5.0)

    def test_sine_sensor_reaches_its_maximum_after_one_quarter_period(self) -> None:
        sensor = COMPONENT_REGISTRY["analog_sensor"](
            AnalogSensorComponentConfig(
                type="analog_sensor",
                name="presion",
                db=1,
                byte_offset=2,
                unit="bar",
                min_value=0.0,
                max_value=10.0,
                simulation=SimulationConfig(mode="sine_wave", period_seconds=20),
            )
        )
        db_buffer = bytearray(32)

        sensor.initialize(db_buffer)
        sensor.simulate(db_buffer, 5.0)

        self.assertEqual(struct.unpack_from(">f", db_buffer, 2)[0], 10.0)

    def test_sensor_noise_is_clamped_to_the_configured_range(self) -> None:
        sensor = COMPONENT_REGISTRY["analog_sensor"](
            AnalogSensorComponentConfig(
                type="analog_sensor",
                name="presion",
                db=1,
                byte_offset=2,
                unit="bar",
                min_value=0.0,
                max_value=10.0,
                simulation=SimulationConfig(mode="constant", noise=1.0),
            )
        )
        db_buffer = bytearray(32)

        with patch("components.analog_sensor.random.gauss", return_value=100.0):
            sensor.initialize(db_buffer)

        self.assertEqual(struct.unpack_from(">f", db_buffer, 2)[0], 10.0)
