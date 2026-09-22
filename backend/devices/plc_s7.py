"""Siemens S7 simulator device adapter."""

from __future__ import annotations

import json
import os
import re
from typing import Any

from models.scenario import AnalogSensorInput, DeviceInput, ValveInput

from . import register_device
from .base import Device


def _safe_name(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip("-._")
    return normalized.lower() or "device"


@register_device("plc-s7")
class PlcS7Device(Device):
    image = os.environ.get("ANVIL_PLC_IMAGE", "anvil/plc-s7:dev")

    def build_runtime_device(self, device: DeviceInput) -> dict[str, Any]:
        cursors: dict[int, tuple[int, int]] = {}
        runtime_components: list[dict[str, Any]] = []

        for component in device.config.components:
            byte_offset, bit_offset = cursors.get(component.db, (0, 0))
            component_data = component.model_dump(mode="json")

            if isinstance(component, ValveInput):
                component_data["byte_offset"] = byte_offset
                component_data["bit_offset"] = bit_offset
                bit_offset += 1
                if bit_offset == 8:
                    byte_offset += 1
                    bit_offset = 0
                required_end = component_data["byte_offset"] + 1
            elif isinstance(component, AnalogSensorInput):
                if bit_offset:
                    byte_offset += 1
                    bit_offset = 0
                if byte_offset % 2:
                    byte_offset += 1
                component_data["byte_offset"] = byte_offset
                required_end = byte_offset + 4
                byte_offset = required_end
            else:
                raise ValueError(f"Unsupported component '{component.type}'")

            if required_end > device.config.db_size:
                raise ValueError(
                    f"component '{component.name}' exceeds DB{component.db} size "
                    f"({device.config.db_size} bytes)"
                )

            cursors[component.db] = (byte_offset, bit_offset)
            runtime_components.append(component_data)

        return {
            "node_id": device.node_id,
            "device_type": device.device_type,
            "name": device.name,
            "ip": device.ip,
            "config": {
                "plc_id": device.node_id,
                "rack": device.config.rack,
                "slot": device.config.slot,
                "db_size": device.config.db_size,
                "components": runtime_components,
            },
        }

    def build_container_config(
        self,
        scenario_id: str,
        runtime_device: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "image": self.image,
            "name": (
                f"anvil-{scenario_id[:8]}-"
                f"{_safe_name(runtime_device['name'])}-{_safe_name(runtime_device['node_id'])}"
            )[:63],
            "environment": {
                "PLC_CONFIG": json.dumps(runtime_device["config"], separators=(",", ":"))
            },
            "detach": True,
            "labels": {
                "anvil.managed": "true",
                "anvil.scenario_id": scenario_id,
                "anvil.node_id": runtime_device["node_id"],
            },
        }
