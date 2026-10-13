"""Boolean valve component backed by a single S7 DB bit."""

from __future__ import annotations

from config_schema import AlarmComponentConfig, DigitalInputComponentConfig, MotorComponentConfig, PumpCommandComponentConfig, ValveComponentConfig

from . import register_component
from .base import Component


@register_component("valve", ValveComponentConfig)
class Valve(Component):
    """Set an initial boolean state and then leave remote writes untouched."""

    config: ValveComponentConfig

    def __init__(self, config: ValveComponentConfig) -> None:
        super().__init__(config)

    def initialize(self, db_buffer: bytearray) -> None:
        mask = 1 << self.config.bit_offset
        if self.config.initial_state:
            db_buffer[self.config.byte_offset] |= mask
        else:
            db_buffer[self.config.byte_offset] &= ~mask

    def simulate(self, db_buffer: bytearray, dt: float) -> None:
        """Valves are writable outputs; their state is managed by S7 clients."""


@register_component("motor", MotorComponentConfig)
class Motor(Valve):
    """A writable motor run/stop BOOL output."""


@register_component("digital_input", DigitalInputComponentConfig)
class DigitalInput(Valve):
    """A simulated read-only discrete input."""


@register_component("alarm", AlarmComponentConfig)
class Alarm(Valve):
    """A read-only alarm state represented by a BOOL."""


@register_component("pump_command", PumpCommandComponentConfig)
class PumpCommand(Valve):
    """A writable pump run/stop command bit."""
