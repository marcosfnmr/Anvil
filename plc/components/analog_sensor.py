"""Analog sensor component stored as a big-endian S7 REAL."""

from __future__ import annotations

import math
import random
import struct

from config_schema import AnalogSensorComponentConfig

from . import register_component
from .base import Component


@register_component("analog_sensor")
class AnalogSensor(Component):
    """Write a constant or sinusoidal value to the configured DB offset."""

    config: AnalogSensorComponentConfig

    def __init__(self, config: AnalogSensorComponentConfig) -> None:
        super().__init__(config)
        self._elapsed_seconds = 0.0

    def initialize(self, db_buffer: bytearray) -> None:
        self._elapsed_seconds = 0.0
        self._write_value(db_buffer)

    def simulate(self, db_buffer: bytearray, dt: float) -> None:
        self._elapsed_seconds += dt
        self._write_value(db_buffer)

    def _write_value(self, db_buffer: bytearray) -> None:
        value = self._simulated_value()
        struct.pack_into(">f", db_buffer, self.config.byte_offset, value)

    def _simulated_value(self) -> float:
        value_range = self.config.max_value - self.config.min_value
        midpoint = self.config.min_value + (value_range / 2)

        if self.config.simulation.mode == "sine_wave":
            period = self.config.simulation.period_seconds
            assert period is not None  # Guaranteed by SimulationConfig validation.
            value = midpoint + (value_range / 2) * math.sin(
                2 * math.pi * self._elapsed_seconds / period
            )
        else:
            value = midpoint

        if self.config.simulation.noise:
            value += random.gauss(0.0, self.config.simulation.noise * value_range)

        return min(self.config.max_value, max(self.config.min_value, value))
