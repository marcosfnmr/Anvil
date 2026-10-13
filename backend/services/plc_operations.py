"""Validated S7 value access through a managed PLC container."""

from __future__ import annotations

import json
import struct
from typing import Any

from docker.errors import DockerException

from services.docker_manager import DockerManagerError


_S7_SCRIPT = r'''import json, struct, sys
from snap7.client import Client
from snap7.type import Area
action, rack, slot, area, db, offset, bit, value = sys.argv[1:]
client = Client()
client.connect("127.0.0.1", int(rack), int(slot), tcp_port=102)
try:
    size = 1 if action in ("bool", "write_bool") else 4
    def read(size): return bytearray(client.db_read(int(db), int(offset), size)) if area == "DB" else bytearray(client.read_area(getattr(Area, {"I":"PE", "Q":"PA", "M":"MK"}[area]), 0, int(offset), size))
    def write(raw): return client.db_write(int(db), int(offset), raw) if area == "DB" else client.write_area(getattr(Area, {"I":"PE", "Q":"PA", "M":"MK"}[area]), 0, int(offset), raw)
    raw = read(size)
    if action == "write_bool":
        mask = 1 << int(bit)
        raw[0] = raw[0] | mask if value == "true" else raw[0] & ~mask
        write(raw)
        raw = read(1)
        result = bool(raw[0] & mask)
        data_type = "BOOL"
    elif action == "write_real":
        raw = bytearray(struct.pack(">f", float(value)))
        write(raw)
        raw = read(4)
        result = float(struct.unpack(">f", raw)[0])
        data_type = "REAL"
    elif action == "bool":
        result = bool(raw[0] & (1 << int(bit)))
        data_type = "BOOL"
    else:
        result = float(struct.unpack(">f", raw)[0])
        data_type = "REAL"
    response = {"value": result, "data_type": data_type, "raw_hex": raw.hex(" ")}
    try:
        from field_bridge import quality_for
        address = (f"DB{db}.DBX{offset}.{bit}" if area == "DB" else f"{area}{offset}.{bit}") if data_type == "BOOL" else (f"DB{db}.DBD{offset}" if area == "DB" else f"{area}D{offset}")
        communication = quality_for(address)
        if communication is not None: response['communication'] = communication
    except ImportError:
        pass
    print(json.dumps(response))
finally:
    if client.get_connected():
        client.disconnect()
'''


def _managed_container(manager: Any, scenario_id: str, node_id: str) -> Any:
    try:
        containers = manager.client.containers.list(
            all=False,
            filters={"label": f"anvil.scenario_id={scenario_id}"},
        )
        for container in containers:
            if container.labels.get("anvil.node_id") == node_id:
                return container
    except DockerException as error:
        raise DockerManagerError(f"Unable to locate PLC container: {error}") from error
    raise LookupError(f"Running PLC '{node_id}' was not found in this scenario")


def _exec_point(
    manager: Any,
    scenario_id: str,
    runtime_device: dict[str, Any],
    component: dict[str, Any],
    *,
    write_value: bool | None = None,
) -> dict[str, Any]:
    is_bool = component["type"] in {"valve", "motor", "digital_input", "alarm", "pump_command"} or component["type"] in {"remote_input", "io_channel"} and component["data_type"] == "BOOL"
    action = ("write_bool" if is_bool else "write_real") if write_value is not None else ("bool" if is_bool else "real")
    args = [
        "python", "-c", _S7_SCRIPT, action,
        str(runtime_device["config"]["rack"]),
        str(runtime_device["config"]["slot"]),
        str(component.get("area", "DB")),
        str(component["db"]),
        str(component["byte_offset"]),
        str(component.get("bit_offset", 0)),
        "true" if write_value is True else "false" if write_value is False else str(write_value),
    ]
    container = _managed_container(manager, scenario_id, runtime_device["node_id"])
    try:
        result = container.exec_run(args, stdout=True, stderr=True, demux=False)
    except DockerException as error:
        raise DockerManagerError(f"Unable to communicate with PLC: {error}") from error
    output = (
        result.output.decode("utf-8", errors="replace")
        if isinstance(result.output, bytes)
        else str(result.output)
    )
    if result.exit_code != 0:
        raise DockerManagerError(f"S7 operation failed: {output.strip()}")
    try:
        response = json.loads(output.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as error:
        raise DockerManagerError(
            f"PLC returned an invalid S7 response: {output.strip()}"
        ) from error
    return {
        "node_id": runtime_device["node_id"],
        "address": _component_address(component),
        "component_name": component["name"],
        "unit": component.get("unit") or None,
        **response,
    }


def read_device_values(
    manager: Any,
    scenario_id: str,
    runtime_device: dict[str, Any],
) -> dict[str, Any]:
    values = [
        _exec_point(manager, scenario_id, runtime_device, component)
        for component in runtime_device["config"]["components"]
    ]
    if any(item["type"] == "remote_input" for item in runtime_device["config"]["components"]):
        container = _managed_container(manager, scenario_id, runtime_device["node_id"])
        result = container.exec_run(["python", "-c", _LINK_STATUS_SCRIPT])
        if result.exit_code != 0:
            raise DockerManagerError("Unable to read S7 link diagnostics")
        try:
            diagnostics = json.loads(result.output)
        except (ValueError, TypeError) as error:
            raise DockerManagerError("Invalid S7 link diagnostics") from error
        for item in values:
            if item["address"] in diagnostics:
                item["communication"] = diagnostics[item["address"]]
    return {"scenario_id": scenario_id, "node_id": runtime_device["node_id"], "values": values}


def write_device_point(
    manager: Any,
    scenario_id: str,
    runtime_device: dict[str, Any],
    component: dict[str, Any],
    value: bool | float,
) -> dict[str, Any]:
    result = _exec_point(
        manager, scenario_id, runtime_device, component, write_value=value
    )
    return {"scenario_id": scenario_id, **result}


def _component_address(component: dict[str, Any]) -> str:
    area = component.get("area", "DB")
    if component["type"] in {"valve", "motor", "digital_input", "alarm", "pump_command"} or component["type"] in {"remote_input", "io_channel"} and component["data_type"] == "BOOL":
        return f"DB{component['db']}.DBX{component['byte_offset']}.{component['bit_offset']}" if area == "DB" else f"{area}{component['byte_offset']}.{component['bit_offset']}"
    return f"DB{component['db']}.DBD{component['byte_offset']}" if area == "DB" else f"{area}D{component['byte_offset']}"


_LINK_STATUS_SCRIPT = r'''import json, time
try:
    with open('/tmp/anvil-s7-links.json') as source:
        links = json.load(source)
except FileNotFoundError:
    links = {}
for item in links.values():
    last = item.pop('last_success_epoch', None)
    age = max(0, (time.time() - last) * 1000) if last is not None else None
    item['age_ms'] = round(age) if age is not None else None
    item['quality'] = 'waiting' if last is None else 'stale' if age > item['stale_after_ms'] else 'good'
print(json.dumps(links))
'''
