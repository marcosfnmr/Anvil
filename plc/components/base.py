"""Base contract for every simulated PLC component."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Component(ABC):
    """A component that initializes and optionally updates an S7 DB buffer."""

    def __init__(self, config: Any) -> None:
        self.config = config

    @abstractmethod
    def initialize(self, db_buffer: bytearray) -> None:
        """Write the component's initial state into its registered DB buffer."""

    @abstractmethod
    def simulate(self, db_buffer: bytearray, dt: float) -> None:
        """Advance the component simulation by ``dt`` seconds."""
