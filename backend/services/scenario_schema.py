"""Versioned migrations for scenario interchange payloads."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable


CURRENT_SCENARIO_SCHEMA_VERSION = 2


def _migrate_v1_to_v2(payload: dict[str, Any]) -> dict[str, Any]:
    """Make the explicit Ethernet topology field part of the v2 contract."""
    migrated = deepcopy(payload)
    migrated.setdefault("network_connections", [])
    return migrated


_MIGRATIONS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {
    1: _migrate_v1_to_v2,
}


def migrate_scenario_payload(version: int, payload: dict[str, Any]) -> dict[str, Any]:
    """Upgrade a supported export payload to the current scenario schema.

    The source object is never mutated. Each transition is an explicit function
    so a later version adds a new adjacent migration instead of silently
    interpreting an older document with today's defaults.
    """
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ValueError("Scenario schema version must be a positive integer")
    if version > CURRENT_SCENARIO_SCHEMA_VERSION:
        raise ValueError(f"Unsupported Anvil scenario schema version {version}")
    if not isinstance(payload, dict):
        raise ValueError("Scenario payload must be an object")

    migrated = deepcopy(payload)
    while version < CURRENT_SCENARIO_SCHEMA_VERSION:
        migration = _MIGRATIONS.get(version)
        if migration is None:
            raise ValueError(f"No migration exists for Anvil scenario schema version {version}")
        migrated = migration(migrated)
        version += 1
    return migrated
