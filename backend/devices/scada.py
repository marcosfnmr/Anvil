"""Container adapter for the native-protocol SCADA collector."""

from __future__ import annotations

import json
import os
import re
from typing import Any

from models.scada import ScadaConfigInput
from . import register_device
from .base import Device


@register_device("scada")
class ScadaDevice(Device):
    label = "SCADA"
    description = "Adquisición cíclica S7/Modbus con tendencias y alarmas"
    icon = "SCADA"
    config_model = ScadaConfigInput
    protocols = ("s7comm-client", "modbus-tcp-client")
    capabilities = ("polling", "history", "threshold-alarms")
    image = os.environ.get("ANVIL_SCADA_IMAGE", "anvil/scada:dev")

    def build_runtime_device(self, device: Any) -> dict[str, Any]:
        config = ScadaConfigInput.model_validate(device.config)
        return {
            "node_id": device.node_id, "device_type": "scada", "name": device.name,
            "ip": device.ip, "external_access": False, "external_port": None,
            "config": config.model_dump(mode="json"),
        }

    def build_container_config(self, scenario_id: str, runtime_device: dict[str, Any]) -> dict[str, Any]:
        safe = re.sub(r"[^a-zA-Z0-9_.-]+", "-", runtime_device["node_id"]).strip("-._").lower() or "scada"
        config = ScadaConfigInput.model_validate(runtime_device["config"]).model_dump(mode="json")
        # `targets` is deployment-derived protocol metadata, not a public user
        # setting in ScadaConfigInput. Preserve it after public schema parsing.
        targets = runtime_device["config"].get("targets")
        if not isinstance(targets, list) or not targets:
            raise ValueError("SCADA runtime requires deployment-derived protocol targets")
        config["targets"] = targets
        return {
            "image": self.image,
            "name": f"anvil-{scenario_id[:8]}-scada-{safe}"[:63],
            "environment": {"SCADA_CONFIG": json.dumps(config)},
            "healthcheck": {
                "test": ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8766/health', timeout=3)"],
                "interval": 5_000_000_000, "timeout": 4_000_000_000,
                "retries": 3, "start_period": 5_000_000_000,
            },
            "detach": True,
            "labels": {
                "anvil.managed": "true", "anvil.scenario_id": scenario_id,
                "anvil.node_id": runtime_device["node_id"],
            },
        }

    def network_services(self, runtime_device: dict[str, Any]) -> list[dict[str, Any]]:
        del runtime_device
        return [{
            "service": "anvil-scada-runtime-api",
            "protocol": "http",
            "transport": "http",
            "port": 8766,
            "role": "server",
            "scope": "internal",
            "description": "API interna de salud, muestras, histórico y alarmas del runtime SCADA",
        }]
