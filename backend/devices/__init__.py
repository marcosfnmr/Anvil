"""Device registry and automatic module discovery."""

from __future__ import annotations

from importlib import import_module
from inspect import isabstract
from pkgutil import iter_modules
import re
from typing import TYPE_CHECKING, Callable, TypeVar

if TYPE_CHECKING:
    from .base import Device


DeviceType = TypeVar("DeviceType", bound="Device")
DEVICE_REGISTRY: dict[str, type[Device]] = {}


def register_device(type_name: str) -> Callable[[type[DeviceType]], type[DeviceType]]:
    if (not isinstance(type_name, str) or len(type_name) > 64
            or not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", type_name)
            or not type_name.isascii() or type_name != type_name.lower()):
        raise ValueError("Device type name must be a lowercase identifier with optional hyphens")

    def decorator(device_class: type[DeviceType]) -> type[DeviceType]:
        if type_name in DEVICE_REGISTRY:
            raise ValueError(f"A device is already registered for type '{type_name}'")
        from .base import Device

        if not isinstance(device_class, type) or not issubclass(device_class, Device):
            raise TypeError(f"Device adapter '{type_name}' must inherit from Device")
        if isabstract(device_class):
            raise TypeError(f"Device adapter '{type_name}' has unimplemented abstract methods")
        for flag in ("internal", "network_resource"):
            value = getattr(device_class, flag, False)
            if type(value) is not bool:
                raise TypeError(f"Device adapter '{type_name}' field '{flag}' must be a boolean")
        if not getattr(device_class, "internal", False):
            config_model = getattr(device_class, "config_model", None)
            from pydantic import BaseModel

            if not isinstance(config_model, type) or not issubclass(config_model, BaseModel):
                raise TypeError(f"Device adapter '{type_name}' must define a Pydantic config_model")
            for field in ("label", "description", "icon"):
                value = getattr(device_class, field, None)
                if not isinstance(value, str) or not value.strip():
                    raise TypeError(f"Device adapter '{type_name}' must define a non-empty {field}")
        for field in ("protocols", "capabilities"):
            values = getattr(device_class, field, ())
            if not isinstance(values, (tuple, list)) or any(not isinstance(value, str) or not value.strip() for value in values):
                raise TypeError(f"Device adapter '{type_name}' field '{field}' must be a sequence of non-empty strings")
        components = getattr(device_class, "component_types", ())
        if (not isinstance(components, (set, frozenset, tuple, list))
                or any(not isinstance(value, str) or not value.strip() for value in components)):
            raise TypeError(f"Device adapter '{type_name}' field 'component_types' must be a collection of non-empty strings")
        DEVICE_REGISTRY[type_name] = device_class
        return device_class

    return decorator


def unregister_device(type_name: str) -> None:
    """Remove a plugin registry entry (used by isolated extension tests)."""
    DEVICE_REGISTRY.pop(type_name, None)


def _load_device_modules() -> None:
    for module in iter_modules(__path__):
        if not module.ispkg and module.name != "base":
            import_module(f"{__name__}.{module.name}")


def get_device_config_model(type_name: str):
    """Resolve a device config model from the registry."""
    device_class = DEVICE_REGISTRY.get(type_name)
    return getattr(device_class, "config_model", None) if device_class else None


def validate_device_config(type_name: str, raw_config: dict):
    """Validate a raw scenario config with the registered device adapter."""
    device_class = DEVICE_REGISTRY.get(type_name)
    if device_class is None:
        raise ValueError(f"Unsupported device type '{type_name}'")
    return device_class.validate_config(raw_config)


def get_device_component_config(type_name: str):
    """Return the discriminated component schema exposed by a device plugin."""
    config_model = get_device_config_model(type_name)
    return getattr(config_model, "component_config_model", None)


def device_supports_component(type_name: str, component_type: str) -> bool:
    device_class = DEVICE_REGISTRY.get(type_name)
    return component_type in getattr(device_class, "component_types", ())


def list_device_catalog() -> list[dict[str, object]]:
    """Expose the device labels used by the scenario builder palette."""
    return [
        adapter.catalog_entry(name)
        for name, adapter in DEVICE_REGISTRY.items()
        if not getattr(adapter, "internal", False)
    ]


def get_device_class(type_name: str):
    """Look up a device adapter by its scenario type."""
    return DEVICE_REGISTRY.get(type_name)


_load_device_modules()
