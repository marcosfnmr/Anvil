"""Component registry used by the PLC simulation runtime."""

from __future__ import annotations

from importlib import import_module
from pkgutil import iter_modules
from typing import TYPE_CHECKING, Callable, TypeVar

if TYPE_CHECKING:
    from .base import Component


ComponentType = TypeVar("ComponentType", bound="Component")

COMPONENT_REGISTRY: dict[str, type[Component]] = {}


def register_component(type_name: str) -> Callable[[type[ComponentType]], type[ComponentType]]:
    """Register a component class under its PLC_CONFIG ``type`` value."""
    if not type_name:
        raise ValueError("Component type name must not be empty")

    def decorator(component_class: type[ComponentType]) -> type[ComponentType]:
        if type_name in COMPONENT_REGISTRY:
            raise ValueError(f"A component is already registered for type '{type_name}'")
        COMPONENT_REGISTRY[type_name] = component_class
        return component_class

    return decorator


def _load_component_modules() -> None:
    """Import sibling component modules so their decorators populate the registry."""
    for module in iter_modules(__path__):
        if not module.ispkg and module.name != "base":
            import_module(f"{__name__}.{module.name}")


_load_component_modules()
