"""PLC memory channel; physical behaviour belongs to the field runtime."""
import struct

from components import register_component
from components.base import Component
from config_schema import IoChannelConfig


@register_component("io_channel", IoChannelConfig)
class IoChannel(Component):
    def initialize(self, db_buffer):
        if self.config.direction == "output" and self.config.data_type == "REAL":
            struct.pack_into(">f", db_buffer, self.config.byte_offset, self.config.min_value)

    def simulate(self, db_buffer, dt):
        pass  # Updated by field wiring or by a command client.
