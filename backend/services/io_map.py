"""Build public I/O maps from private normalized runtime configuration."""

from __future__ import annotations

from typing import Any

from devices import DEVICE_REGISTRY
from models.scenario import ScenarioIoMapRead


def build_scenario_io_map(
    scenario_id: str,
    runtime_devices: list[dict[str, Any]],
) -> ScenarioIoMapRead:
    points: list[dict[str, Any]] = []
    for runtime_device in runtime_devices:
        device_type = runtime_device["device_type"]
        device_class = DEVICE_REGISTRY.get(device_type)
        if device_class is None:
            raise ValueError(f"Unsupported device type '{device_type}'")
        points.extend(device_class().build_io_map(runtime_device))
    return ScenarioIoMapRead.model_validate(
        {"scenario_id": scenario_id, "points": points}
    )
