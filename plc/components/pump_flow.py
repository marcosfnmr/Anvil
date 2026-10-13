"""Flow sensor whose output depends on its pump command bit."""

from __future__ import annotations

import struct

from config_schema import PumpFlowComponentConfig

from . import register_component
from .base import Component


@register_component("pump_flow", PumpFlowComponentConfig)
class PumpFlow(Component):
    config: PumpFlowComponentConfig

    def initialize(self, db_buffer: bytearray) -> None:
        self.simulate(db_buffer, 0.0)

    def simulate(self, db_buffer: bytearray, dt: float) -> None:
        del dt
        command_mask = 1 << self.config.command_bit_offset
        running = bool(db_buffer[self.config.command_byte_offset] & command_mask)
        value = (self.config.flow_min + self.config.flow_max) / 2 if running else 0.0
        value = min(self.config.flow_max, max(self.config.flow_min if running else 0.0, value))
        struct.pack_into(">f", db_buffer, self.config.byte_offset, value)
