"""Base contract for dockerizable device types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from models.scenario import DeviceInput


class Device(ABC):
    @abstractmethod
    def build_runtime_device(self, device: DeviceInput) -> dict[str, Any]:
        """Produce private runtime data, including automatically assigned offsets."""

    @abstractmethod
    def build_container_config(
        self,
        scenario_id: str,
        runtime_device: dict[str, Any],
    ) -> dict[str, Any]:
        """Produce keyword arguments for docker-py's container creation API."""
