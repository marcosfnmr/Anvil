"""A communication input, updated exclusively by the S7 polling worker."""
from config_schema import RemoteInputComponentConfig
from . import register_component
from .base import Component


@register_component("remote_input", RemoteInputComponentConfig)
class RemoteInput(Component):
    def initialize(self, db_buffer: bytearray) -> None:
        if self.config.data_type == "BOOL":
            db_buffer[self.config.byte_offset] &= ~(1 << self.config.bit_offset)
        else:
            db_buffer[self.config.byte_offset:self.config.byte_offset + 4] = bytes(4)

    def simulate(self, db_buffer: bytearray, dt: float) -> None:
        pass
