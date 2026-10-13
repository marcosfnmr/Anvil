"""Docker adapter for a configurable Modbus TCP field device."""

from __future__ import annotations

import json
import os
import re
from typing import Any

from models.modbus import ModbusTcpConfigInput

from . import register_device
from .base import Device


def _safe_name(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip("-._")
    return normalized.lower() or "device"


@register_device("modbus-tcp")
class ModbusTcpDevice(Device):
    label = "Dispositivo Modbus TCP/RTU"
    description = "Servidor Modbus TCP o RTU en serie virtual"
    icon = "MB"
    image = os.environ.get("ANVIL_MODBUS_IMAGE", "anvil/modbus-tcp:dev")
    config_model = ModbusTcpConfigInput
    protocols = ("modbus-tcp", "modbus-rtu-virtual")
    capabilities = ("server", "coils", "discrete-inputs", "holding-registers", "input-registers", "optional-host-forwarding")
    component_types = frozenset({"coil", "holding_register", "input_register"})

    @classmethod
    def external_port(cls, config: Any) -> int:
        return config.port if config.transport == "tcp" else None

    def build_runtime_device(self, device: Any) -> dict[str, Any]:
        config = ModbusTcpConfigInput.model_validate(device.config)
        cursors = {"coil": 0, "discrete_input": 0, "holding_register": 0, "input_register": 0}
        used: dict[str, set[int]] = {
            key: {item.address for item in config.points if item.type == key and item.address is not None}
            for key in cursors
        }
        points = []
        for item in config.points:
            point = item.model_dump(mode="json")
            kind = item.type
            if item.address is not None:
                address = item.address
            else:
                address = cursors[kind]
                while address in used[kind]:
                    address += 1
                if address > 65535:
                    raise ValueError(f"No Modbus {kind} address available")
            point["address"] = address
            used[kind].add(address)
            if item.address is None:
                cursors[kind] = address + 1
            points.append(point)
        return {
            "node_id": device.node_id,
            "device_type": device.device_type,
            "name": device.name,
            "ip": device.ip,
            "external_access": device.external_access,
            "external_port": device.external_port,
            "external_host_ip": device.external_host_ip,
            "config": {
                "transport": config.transport,
                "unit_id": config.unit_id,
                "port": config.port,
                "baudrate": config.baudrate,
                "bytesize": config.bytesize,
                "parity": config.parity,
                "stopbits": config.stopbits,
                "response_delay_ms": config.response_delay_ms,
                "response_jitter_ms": config.response_jitter_ms,
                "response_jitter_distribution": config.response_jitter_distribution,
                "response_jitter_seed": config.response_jitter_seed,
                "fault_mode": config.fault_mode,
                "fault_probability": config.fault_probability,
                "fault_seed": config.fault_seed,
                "points": points,
            },
        }

    def build_container_config(self, scenario_id: str, runtime_device: dict[str, Any]) -> dict[str, Any]:
        port = runtime_device["config"]["port"]
        config = {
            "image": self.image,
            "name": (f"anvil-{scenario_id[:8]}-{_safe_name(runtime_device['name'])}-{_safe_name(runtime_device['node_id'])}")[:63],
            "environment": {"MODBUS_CONFIG": json.dumps(runtime_device["config"])},
            "healthcheck": {
                "test": ["CMD", "python", "-c", f"import socket; s=socket.create_connection(('127.0.0.1',{port}),3); s.close()"],
                "interval": 5_000_000_000,
                "timeout": 4_000_000_000,
                "retries": 3,
                "start_period": 5_000_000_000,
            },
            "detach": True,
            "labels": {
                "anvil.managed": "true",
                "anvil.scenario_id": scenario_id,
                "anvil.node_id": runtime_device["node_id"],
            },
        }
        return config

    def build_io_map(self, runtime_device: dict[str, Any]) -> list[dict[str, Any]]:
        points = []
        for item in runtime_device["config"]["points"]:
            kind = item["type"]
            if kind == "coil":
                address, data_type, access = f"coil:{item['address']}", "BOOL", "read_write"
            elif kind == "discrete_input":
                address, data_type, access = f"discrete_input:{item['address']}", "BOOL", "read"
            elif kind == "holding_register":
                address, data_type, access = f"holding_register:{item['address']}", "UINT16", "read_write"
            else:
                address, data_type, access = f"input_register:{item['address']}", "UINT16", "read"
            points.append({
                "node_id": runtime_device["node_id"],
                "device_name": runtime_device["name"],
                "component_name": item["name"],
                "component_type": kind,
                "db": 0,
                "address": address,
                "data_type": data_type,
                "access": access,
                "unit": item.get("unit") or None,
                **({"min_value": item["min_value"], "max_value": item["max_value"]}
                   if item.get("min_value") is not None and item.get("max_value") is not None else {}),
            })
        owned = {binding["address"] for binding in runtime_device["config"].get("field_bridge", {}).get("bindings", []) if binding["direction"] == "input"}
        for point in points:
            if point["address"] in owned:
                point["access"] = "read"
                binding = next(b for b in runtime_device['config']['field_bridge']['bindings'] if b['address'] == point['address'] and b['direction'] == 'input')
                point.update(source_node_id=binding['field_id'], source_address=binding['field_port'])
        return points

    def network_services(self, runtime_device: dict[str, Any]) -> list[dict[str, Any]]:
        config = runtime_device["config"]
        if config["transport"] == "rtu":
            return [{
                "service": "modbus-rtu-raw-tunnel",
                "protocol": "modbus-rtu",
                "transport": "tcp-raw",
                "port": config["port"],
                "role": "server",
                "description": "Túnel TCP raw hacia el puerto serie RTU virtual; no usa cabecera MBAP",
            }]
        return [{
            "service": "modbus-tcp",
            "protocol": "modbus-tcp",
            "transport": "tcp",
            "port": config["port"],
            "role": "server",
            "description": "Servidor Modbus TCP con cabecera MBAP",
        }]
