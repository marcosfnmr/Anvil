"""Boolean valve component backed by a single S7 DB bit."""

from __future__ import annotations

from config_schema import ValveComponentConfig

from . import register_component
from .base import Component


@register_component("valve")
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
