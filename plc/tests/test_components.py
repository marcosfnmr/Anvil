"""Tests for the component registry and MVP component behavior."""

from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components import COMPONENT_REGISTRY
from config_schema import AnalogSensorComponentConfig, PLCConfig, PumpCommandComponentConfig, PumpFlowComponentConfig, SimulationConfig, ValveComponentConfig
from config_schema import IoChannelConfig


class ComponentTests(unittest.TestCase):
    def test_registry_loads_both_mvp_components(self) -> None:
        self.assertEqual(set(COMPONENT_REGISTRY), {"valve", "analog_sensor", "motor", "digital_input", "alarm", "pump_command", "pump_flow", "remote_input", "io_channel"})

    def test_pump_flow_tracks_command_state(self) -> None:
        command = COMPONENT_REGISTRY["pump_command"](PumpCommandComponentConfig(
            type="pump_command", name="pump", db=1, byte_offset=0, bit_offset=0,
            flow_byte_offset=2, flow_min=20, flow_max=100,
        ))
        flow = COMPONENT_REGISTRY["pump_flow"](PumpFlowComponentConfig(
            type="pump_flow", name="pump", db=1, byte_offset=2,
            command_byte_offset=0, command_bit_offset=0, flow_min=20, flow_max=100,
        ))
        buffer = bytearray(16)
        command.initialize(buffer)
        flow.initialize(buffer)
        self.assertEqual(struct.unpack_from(">f", buffer, 2)[0], 0)
        buffer[0] = 1
        flow.simulate(buffer, 0.1)
        self.assertEqual(struct.unpack_from(">f", buffer, 2)[0], 60)
        buffer[0] = 0
        flow.simulate(buffer, 0.1)
        self.assertEqual(struct.unpack_from(">f", buffer, 2)[0], 0)

    def test_discrete_outputs_keep_client_writes_and_read_only_inputs_keep_initial_state(self) -> None:
        for kind in ("motor", "digital_input", "alarm"):
            config = PLCConfig.model_validate({
                "plc_id": "p", "rack": 0, "slot": 1, "db_size": 32,
                "components": [{"type": kind, "name": kind, "db": 1, "byte_offset": 0, "bit_offset": 0, "initial_state": True}],
            }).components[0]
            component = COMPONENT_REGISTRY[kind](config)
            buffer = bytearray(32)
            component.initialize(buffer)
            component.simulate(buffer, 0.1)
            self.assertEqual(buffer[0] & 1, 1)

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

    def test_real_output_channel_initializes_inside_its_configured_range(self):
        config = IoChannelConfig(type="io_channel", name="Opening", db=1, byte_offset=2,
                                 data_type="REAL", direction="output", min_value=10, max_value=90)
        channel = COMPONENT_REGISTRY["io_channel"](config)
        buffer = bytearray(8)
        channel.initialize(buffer)
        self.assertEqual(struct.unpack_from(">f", buffer, 2)[0], 10)

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

        with patch.object(sensor._random, "gauss", return_value=100.0):
            sensor.initialize(db_buffer)

        self.assertEqual(struct.unpack_from(">f", db_buffer, 2)[0], 10.0)

    def test_seeded_sensor_noise_repeats_for_the_same_component_identity(self) -> None:
        def sequence(name: str) -> list[float]:
            sensor = COMPONENT_REGISTRY["analog_sensor"](AnalogSensorComponentConfig(
                type="analog_sensor", name=name, db=1, byte_offset=2, unit="bar",
                min_value=0, max_value=100,
                simulation=SimulationConfig(mode="constant", noise=0.2, seed=17),
            ))
            buffer = bytearray(8)
            values = []
            sensor.initialize(buffer)
            values.append(struct.unpack_from(">f", buffer, 2)[0])
            for _ in range(3):
                sensor.simulate(buffer, 1.0)
                values.append(struct.unpack_from(">f", buffer, 2)[0])
            return values

        self.assertEqual(sequence("pressure"), sequence("pressure"))
        self.assertNotEqual(sequence("pressure"), sequence("temperature"))
