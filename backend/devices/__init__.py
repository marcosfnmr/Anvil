"""Device registry and automatic module discovery."""

from __future__ import annotations

from importlib import import_module
from pkgutil import iter_modules
from typing import TYPE_CHECKING, Callable, TypeVar

if TYPE_CHECKING:
    from .base import Device


DeviceType = TypeVar("DeviceType", bound="Device")
DEVICE_REGISTRY: dict[str, type[Device]] = {}


def register_device(type_name: str) -> Callable[[type[DeviceType]], type[DeviceType]]:
    if not type_name:
        raise ValueError("Device type name must not be empty")

    def decorator(device_class: type[DeviceType]) -> type[DeviceType]:
        if type_name in DEVICE_REGISTRY:
            raise ValueError(f"A device is already registered for type '{type_name}'")
        DEVICE_REGISTRY[type_name] = device_class
        return device_class

    return decorator


def _load_device_modules() -> None:
    for module in iter_modules(__path__):
        if not module.ispkg and module.name != "base":
            import_module(f"{__name__}.{module.name}")


_load_device_modules()
