"""Private scenario-to-host protocol relay used only for opted-in devices."""

from __future__ import annotations

import json
import os
import re
from typing import Any

from . import register_device
from .base import Device


@register_device("protocol-ingress")
class ProtocolIngressDevice(Device):
    label = "Ingreso protocolario"
    description = "Proxy privado de un único destino S7 o Modbus TCP"
    internal = True
    _image = os.environ.get("ANVIL_INGRESS_IMAGE", "anvil/protocol-ingress:dev")
    network_resource = False

    @classmethod
    def external_port(cls, config: Any) -> int | None:
        del config
        return 25000

    def build_runtime_device(self, device: Any) -> dict[str, Any]:
        return device

    def build_container_config(self, scenario_id: str, runtime_device: dict[str, Any]) -> dict[str, Any]:
        cfg = runtime_device["config"]
        safe = re.sub(r"[^a-zA-Z0-9_.-]+", "-", runtime_device["node_id"]).strip("-._").lower()
        payload = {key: cfg[key] for key in ("listen_ip", "listen_port", "target_ip", "target_port")}
        # Published ports are DNATed to the container's primary bridge address,
        # while listen_ip is its secondary, private scenario-zone address.
        # Bind all container interfaces and enforce the sole accepted peer in
        # ingress_proxy.py; the host bind itself remains explicitly scoped.
        payload["bind_ip"] = "0.0.0.0"
        return {
            "image": self._image,
            "name": f"anvil-{scenario_id[:8]}-ingress-{safe}"[:63],
            "command": ["python", "-u", "ingress_proxy.py"],
            "environment": {"ANVIL_INGRESS_CONFIG": json.dumps(payload, separators=(",", ":"))},
            "ports": {f"{cfg['listen_port']}/tcp": (runtime_device["external_host_ip"], runtime_device["external_port"])},
            "healthcheck": {"test": ["CMD", "python", "-c",
                            f"import socket; s=socket.create_connection(({cfg['listen_ip']!r},{cfg['listen_port']}),1); s.close()"],
                            "interval": 5_000_000_000, "timeout": 4_000_000_000,
                            "retries": 3, "start_period": 5_000_000_000},
            "detach": True,
            "labels": {
                "anvil.managed": "true", "anvil.scenario_id": scenario_id,
                "anvil.node_id": runtime_device["node_id"], "anvil.role": "protocol-ingress",
                "anvil.external_access": "true",
                "anvil.external_host_ip": runtime_device["external_host_ip"],
                "anvil.external_port": str(runtime_device["external_port"]),
                "anvil.external_container_port": str(cfg["listen_port"]),
                "anvil.ingress_target_node_id": cfg["target_node_id"],
                "anvil.ingress_target_port": str(cfg["target_port"]),
                "anvil.ingress_protocol": cfg["protocol"],
            },
        }
