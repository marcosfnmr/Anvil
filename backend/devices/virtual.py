"""Explicitly limited auxiliary cyber-range nodes."""

from __future__ import annotations

import json
import os
import re
import secrets
from typing import Any
from typing import ClassVar

from models.virtual_devices import VirtualDeviceConfig

from . import register_device
from .base import Device


class VirtualNode(Device):
    config_model: ClassVar[type] = VirtualDeviceConfig
    _image: ClassVar[str] = os.environ.get("ANVIL_VIRTUAL_IMAGE", os.environ.get("ANVIL_PLC_IMAGE", "anvil/plc-s7:dev"))
    _role = "virtual"

    def build_runtime_device(self, device: Any) -> dict[str, Any]:
        return {
            "node_id": device.node_id,
            "device_type": device.device_type,
            "name": device.name,
            "ip": device.ip,
            "external_access": False,
            "external_port": None,
            "config": device.config.model_dump(mode="json"),
        }

    def network_mode(self, runtime_device: dict[str, Any]) -> str | None:
        config = runtime_device["config"]
        if self._role == "attacker" and not config.get("participant_access_enabled", False):
            return "none"
        if self._role == "engineering" and not config.get("engineering_access_enabled", False):
            return "none"
        return None

    def build_container_config(self, scenario_id: str, runtime_device: dict[str, Any]) -> dict[str, Any]:
        safe = re.sub(r"[^a-zA-Z0-9_.-]+", "-", runtime_device["node_id"]).strip("-._").lower() or "node"
        payload = json.dumps({"node_id": runtime_device["node_id"], **runtime_device["config"]})
        terminal_access = (
            self._role == "attacker" and runtime_device["config"].get("participant_access_enabled") is True
        ) or (
            self._role == "engineering" and runtime_device["config"].get("engineering_access_enabled") is True
        )
        proxy_url = runtime_device.get("egress_proxy_url")
        command = (
            "import json,time; "
            "c=json.loads(%r); "
            "print(json.dumps({'role':c['role'],'node_id':c['node_id'],"
            "'summary':c['summary'],'mode':'simulated auxiliary node'}),flush=True); "
            "time.sleep(31536000)"
        ) % payload
        return {
            "image": self._image,
            "healthcheck": {"test": ["NONE"]},
            "name": f"anvil-{scenario_id[:8]}-{self._role}-{safe}"[:63],
            "command": ["python", "-u", "-c", command],
            "detach": True,
            "read_only": True,
            "network_disabled": False,
            **({"environment": {
                "HTTP_PROXY": proxy_url, "HTTPS_PROXY": proxy_url,
                "http_proxy": proxy_url, "https_proxy": proxy_url,
                "NO_PROXY": "localhost,127.0.0.1,::1", "no_proxy": "localhost,127.0.0.1,::1",
            }} if proxy_url else {}),
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "user": "65534:65534",
            "mem_limit": "64m",
            "pids_limit": 32,
            "labels": {
                "anvil.managed": "true",
                "anvil.scenario_id": scenario_id,
                "anvil.node_id": runtime_device["node_id"],
                "anvil.role": self._role,
                "anvil.terminal_access_enabled": str(terminal_access).lower(),
                "anvil.simulated": "true",
            },
        }

@register_device("hmi")
class HmiDevice(VirtualNode):
    label, description, icon, _role = "HMI", "Panel de lectura S7 y Modbus desde Anvil", "HMI", "hmi"
    protocols = ("s7comm-client", "modbus-tcp-client")
    capabilities = ("operator-view", "selected-tag-read", "authorized-write", "alarms", "synoptic")
    _image = os.environ.get("ANVIL_HMI_IMAGE", os.environ.get("ANVIL_SCADA_IMAGE", "anvil/scada:dev"))


@register_device("gateway")
class GatewayDevice(VirtualNode):
    label, description, icon, _role = "Gateway", "Gateway representativo; no enruta redes", "GW", "gateway"


@register_device("tcp-gateway")
class TcpGatewayDevice(VirtualNode):
    """Un proxy TCP de flujo único; no habilita IP forwarding ni NET_ADMIN."""
    label, description, icon, _role = "Gateway TCP", "Reenvía un único flujo de protocolo permitido", "TCP", "tcp-gateway"
    internal = True

    def build_container_config(self, scenario_id: str, runtime_device: dict[str, Any]) -> dict[str, Any]:
        cfg = runtime_device["config"]
        target = str(cfg["target_ip"])
        target_port = int(cfg["target_port"])
        allowed_source_ip = str(cfg["source_ip"])
        latency_ms = int(cfg.get("latency_ms", 0))
        listener = int(cfg["listen_port"])
        control_token = secrets.token_urlsafe(32)
        gateway_config = json.dumps({
            "route_id": str(runtime_device["node_id"]).removeprefix("__anvil_route_"),
            "listen_ip": str(runtime_device["ip"]),
            "listen_port": int(cfg["listen_port"]),
            "source_ip": str(cfg["source_ip"]),
            "target_ip": str(cfg["target_ip"]),
            "target_port": int(cfg["target_port"]),
            "latency_ms": int(cfg.get("latency_ms", 0)),
            "inspect_protocol": cfg.get("inspect_protocol"),
            "control_token": control_token,
        }, separators=(",", ":"))
        return {
            "image": self._image, "healthcheck": {"test": ["NONE"]},
            "name": f"anvil-{scenario_id[:8]}-route-{runtime_device['node_id'][-24:]}"[:63],
            "command": ["python", "-u", "tcp_gateway.py"],
            "environment": {"ANVIL_TCP_GATEWAY_CONFIG": gateway_config,
                            "ANVIL_TCP_GATEWAY_CONTROL_TOKEN": control_token},
            "detach": True,
            "read_only": True, "network_disabled": False, "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"], "user": "65534:65534",
            "mem_limit": "64m", "pids_limit": 32,
            "labels": {"anvil.managed": "true", "anvil.scenario_id": scenario_id,
                       "anvil.node_id": runtime_device["node_id"], "anvil.role": "tcp-gateway",
                       "anvil.simulated": "true", "anvil.route_target_ip": target,
                       "anvil.route_target_port": str(target_port),
                       "anvil.route_source_node_id": str(cfg["source_node_id"]),
                       "anvil.route_source_ip": allowed_source_ip,
            "anvil.route_target_node_id": str(cfg["target_node_id"]),
                       "anvil.route_protocol": str(cfg["protocol"]),
                       "anvil.route_latency_ms": str(latency_ms),
                       "anvil.route_listen_ip": str(runtime_device["ip"]),
                       "anvil.route_inspect_protocol": str(cfg.get("inspect_protocol") or "none"),
                       "anvil.route_source_zone": str(runtime_device["zone_id"]),
                       "anvil.route_target_zone": str(cfg["target_zone_id"]),
                       "anvil.route_listen_port": str(listener),
                       "anvil.hostname": str(runtime_device.get("hostname") or runtime_device["node_id"])},
        }


@register_device("egress-gateway")
class EgressGatewayDevice(VirtualNode):
    """HTTP CONNECT gateway with one workstation source and exact destinations."""
    label, description, icon, _role = "Salida controlada", "Proxy HTTPS hacia destinos TCP declarados", "EGRESS", "egress-gateway"
    internal = True
    _image = os.environ.get("ANVIL_EGRESS_IMAGE", "anvil/egress-proxy:dev")

    def build_container_config(self, scenario_id: str, runtime_device: dict[str, Any]) -> dict[str, Any]:
        config = runtime_device["config"]
        payload = json.dumps({
            "listen_ip": runtime_device["ip"],
            "source_ip": config["source_ip"],
            "rules": config["rules"],
        }, separators=(",", ":"))
        rule_json = json.dumps(config["rules"], sort_keys=True, separators=(",", ":"))
        return {
            "image": self._image,
            "name": f"anvil-egress-{runtime_device['node_id'][-24:]}"[:63],
            "command": ["python", "-u", "egress_proxy.py"],
            "environment": {"ANVIL_EGRESS_CONFIG": payload},
            "detach": True,
            "read_only": True,
            "network_disabled": False,
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "user": "65534:65534",
            "mem_limit": "64m",
            "nano_cpus": 250_000_000,
            "pids_limit": 32,
            "healthcheck": {"test": ["CMD", "python", "-c",
                "import socket,sys; s=socket.create_connection((sys.argv[1],3128),1); s.close()",
                runtime_device["ip"]], "interval": 5_000_000_000, "timeout": 2_000_000_000,
                "retries": 5, "start_period": 2_000_000_000},
            "labels": {
                "anvil.managed": "true", "anvil.scenario_id": scenario_id,
                "anvil.node_id": runtime_device["node_id"], "anvil.role": "egress-gateway",
                "anvil.simulated": "true", "anvil.egress_source_node_id": config["source_node_id"],
                "anvil.egress_source_ip": config["source_ip"], "anvil.egress_source_zone": runtime_device["zone_id"],
                "anvil.egress_allowlist": rule_json,
                "anvil.hostname": runtime_device["hostname"],
            },
        }


@register_device("firewall")
class FirewallDevice(VirtualNode):
    label, description, icon, _role = "Política de consola", "Permite puertos en la API; no filtra la red", "FW", "firewall"


@register_device("attacker-station")
class AttackerStationDevice(VirtualNode):
    label, description, icon, _role = "Estación de participante", "Cliente Snap7/PyModbus opcional en su zona Purdue", "PART", "attacker"
    _image = os.environ.get(
        "ANVIL_PARTICIPANT_IMAGE",
        os.environ.get("ANVIL_HMI_IMAGE", os.environ.get("ANVIL_SCADA_IMAGE", "anvil/scada:dev")),
    )


@register_device("engineering-station")
class EngineeringStationDevice(VirtualNode):
    label, description, icon, _role = (
        "Estación de ingeniería", "Host OT para herramientas de ingeniería; acceso de red y terminal opt-in", "ENG", "engineering",
    )
    _image = os.environ.get(
        "ANVIL_ENGINEERING_IMAGE",
        os.environ.get("ANVIL_PARTICIPANT_IMAGE", os.environ.get("ANVIL_HMI_IMAGE", os.environ.get("ANVIL_SCADA_IMAGE", "anvil/scada:dev"))),
    )


@register_device("ethernet-switch")
class EthernetSwitchDevice(VirtualNode):
    """Logical switch represented by its zone's managed Docker bridge."""
    label, description, icon, _role = "Switch Ethernet", "Conmutación L2 mediante el bridge Docker interno de su zona", "SW", "switch"
    network_resource = True

    def build_container_config(self, scenario_id: str, runtime_device: dict[str, Any]) -> dict[str, Any]:
        del scenario_id, runtime_device
        raise RuntimeError("Ethernet switches are implemented by their managed Docker zone bridge")
