"""Base contract for dockerizable device types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Device(ABC):
    label: str = "OT device"
    description: str = ""
    icon: str = "OT"
    config_model: type | None = None
    component_types: frozenset[str] = frozenset()
    network_resource: bool = False
    protocols: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()

    @classmethod
    def external_port(cls, config: Any) -> int | None:
        """Return a protocol's container port for optional loopback forwarding."""
        del config
        return None

    def network_mode(self, runtime_device: dict[str, Any]) -> str | None:
        del runtime_device
        return None

    @abstractmethod
    def build_runtime_device(self, device: Any) -> dict[str, Any]:
        """Produce private runtime data, including automatically assigned offsets."""

    @abstractmethod
    def build_container_config(
        self,
        scenario_id: str,
        runtime_device: dict[str, Any],
    ) -> dict[str, Any]:
        """Produce keyword arguments for docker-py's container creation API."""

    @classmethod
    def validate_config(cls, raw_config: dict[str, Any]) -> Any:
        """Validate device-specific scenario configuration."""
        if cls.config_model is None:
            raise ValueError(f"Device adapter '{cls.__name__}' has no config_model")
        return cls.config_model.model_validate(raw_config)

    def build_io_map(self, runtime_device: dict[str, Any]) -> list[dict[str, Any]]:
        """Expose read-only protocol addresses derived from private runtime data."""

        del runtime_device
        return []

    def network_services(self, runtime_device: dict[str, Any]) -> list[dict[str, Any]]:
        """Describe listening services actually provided by this runtime node."""

        del runtime_device
        return []

    @classmethod
    def catalog_entry(cls, type_name: str) -> dict[str, Any]:
        """Describe implemented adapter capabilities for API/UI discovery."""
        return {
            "type": type_name,
            "label": cls.label,
            "description": cls.description,
            "icon": cls.icon,
            "protocols": list(cls.protocols),
            "capabilities": list(cls.capabilities),
        }
