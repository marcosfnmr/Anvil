"""Read-only extraction preview; does not change PLC execution or memory layout."""

import json
from hashlib import sha256
import re

from devices import DEVICE_REGISTRY
from models.field import FieldPosition
from models.scenario import ScenarioCreate


def preview_field_extraction(scenario, runtime_devices):
    objects, connections, positions = [], [], {}
    reserved = {item.object_id for item in scenario.field_objects} | {item.node_id for item in scenario.devices}
    reserved_connections = {item.connection_id for item in scenario.signal_connections}

    def connection_id(candidate):
        while candidate in reserved_connections:
            candidate += "-copy"
        reserved_connections.add(candidate)
        return candidate
    runtime = {item["node_id"]: item for item in runtime_devices}
    warnings = [
        "Preview only: no existing scenario or PLC configuration has been modified.",
        "To migrate, use POST /api/scenarios/{id}/field-migration-copy. It creates a stopped copy with independent field objects and I/O channels; the source scenario remains unchanged.",
    ]
    for device in scenario.devices:
        if device.device_type != "plc-s7":
            continue
        points = DEVICE_REGISTRY[device.device_type]().build_io_map(runtime[device.node_id])
        cursor = 0
        occurrences = {}
        for index, component in enumerate(device.config.components):
            width = 2 if component.type == "pump" else 1
            component_points = points[cursor:cursor + width]
            cursor += width
            if component.type == "remote_input":
                continue
            addresses = {point["address"] for point in component_points}
            if any(
                endpoint.kind == "device" and endpoint.node_id == device.node_id and endpoint.address in addresses
                for link in scenario.signal_connections
                for endpoint in (link.source, link.target)
            ):
                warnings.append(f"'{device.node_id}/{component.name}' already has a field connection; left unchanged in preview.")
                continue
            digest = sha256(f"{device.node_id}:{index}".encode()).hexdigest()[:16]
            object_id = f"field-{digest}"
            while object_id in reserved:
                object_id += "-copy"
            reserved.add(object_id)
            value = component.model_dump(mode="json")
            value.pop("db", None)
            value["object_id"] = object_id
            objects.append(value)
            actuator = component.type in {"valve", "motor", "pump"}
            field = {"kind": "field", "object_id": object_id, "port": "command" if actuator else "value" if component.type == "analog_sensor" else "state"}
            endpoint = {"kind": "device", "node_id": device.node_id, "address": component_points[0]["address"]}
            connections.append({"connection_id": connection_id(f"link-{digest}"), "source": endpoint if actuator else field, "target": field if actuator else endpoint})
            if component.type == "pump":
                connections.append({
                    "connection_id": connection_id(f"flow-{digest}"),
                    "source": {"kind": "field", "object_id": object_id, "port": "flow"},
                    "target": {"kind": "device", "node_id": device.node_id, "address": component_points[1]["address"]},
                })
            identity = (component.type, component.name)
            occurrence = occurrences.get(identity, 0)
            occurrences[identity] = occurrence + 1
            # Matches the identity used by the existing process-map overlay.
            key = json.dumps([device.node_id, component.type, component.name, occurrence], ensure_ascii=False, separators=(",", ":"))
            position = scenario.canvas.get("field_positions", {}).get(key)
            if position:
                try:
                    position = FieldPosition.model_validate(position).model_dump(mode="json")
                except ValueError:
                    warnings.append(f"Invalid saved position for '{device.node_id}/{component.name}'; omitted from preview.")
                else:
                    positions[object_id] = position
                    value["position"] = position
    return {
        "scenario_id": str(scenario.id), "applied": False,
        "field_objects": [item.model_dump(mode="json") for item in scenario.field_objects] + objects,
        "signal_connections": [item.model_dump(mode="json") for item in scenario.signal_connections] + connections,
        "process_connections": [item.model_dump(mode="json") for item in scenario.process_connections],
        "suggested_positions": positions, "warnings": warnings,
    }


def build_field_migration_copy(scenario, runtime_devices, *, name: str | None = None) -> ScenarioCreate:
    """Build a stopped scenario copy with legacy PLC components as field objects.

    The source is never changed. Existing DB byte/bit addresses and PLC logic
    references are retained through explicit `io_channel` components.
    """
    preview = preview_field_extraction(scenario, runtime_devices)
    runtime_by_id = {item["node_id"]: item for item in runtime_devices}
    network_model = scenario.network
    zone_by_id = {item.zone_id: item for item in network_model.zones}
    existing_linked_zones = {
        item.zone_id
        for item in scenario.field_objects
        if any(
            (link.source.kind == "field" and link.source.object_id == item.object_id)
            or (link.target.kind == "field" and link.target.object_id == item.object_id)
            for link in scenario.signal_connections
        )
    }
    legacy_devices = [
        device for device in scenario.devices
        if device.device_type == "plc-s7"
        and any(component.type in {"valve", "motor", "digital_input", "alarm", "pump", "analog_sensor"}
                for component in device.config.components)
    ]
    if not legacy_devices:
        raise ValueError("This scenario has no legacy PLC field components to migrate")

    if len(existing_linked_zones) > 1:
        raise ValueError("Connected field objects span multiple zones and cannot share one field runtime")
    field_zone = next(iter(existing_linked_zones), None)
    if field_zone is None:
        configured_field_zones = {item.zone_id for item in scenario.field_objects}
        if configured_field_zones:
            if len(configured_field_zones) > 1:
                raise ValueError("Field objects span multiple zones; select one field runtime zone before migration")
            field_zone = next(iter(configured_field_zones))
        else:
            legacy_zones = {device.zone_id for device in legacy_devices}
            if len(legacy_zones) > 1:
                raise ValueError("Legacy PLC components span multiple zones; migrate them in same-zone copies")
            field_zone = next(iter(legacy_zones), "default")

    for device in legacy_devices:
        if device.zone_id != field_zone:
            raise ValueError(
                f"PLC '{device.name}' is in zone '{device.zone_id}' but the field runtime will be in "
                f"'{field_zone}'; move the PLC/field objects into one zone or connect them manually after migration"
            )

    channel_ids = {
        component.channel_id
        for device in scenario.devices if device.device_type == "plc-s7"
        for component in device.config.components
        if getattr(component, "channel_id", None)
    }
    original_field_ids = {item.object_id for item in scenario.field_objects}
    used_generated_field_ids: set[str] = set()
    migrated_components: dict[str, list[dict]] = {}
    for device in scenario.devices:
        serialized = [item.model_dump(mode="json") for item in device.config.components]
        if device.device_type != "plc-s7":
            migrated_components[device.node_id] = serialized
            continue
        runtime_device = runtime_by_id[device.node_id]
        points = DEVICE_REGISTRY[device.device_type]().build_io_map(runtime_device)
        cursor = 0
        converted: list[dict] = []
        for index, component in enumerate(device.config.components):
            component_type = component.type
            width = 2 if component_type == "pump" else 1
            component_points = points[cursor:cursor + width]
            cursor += width
            if component_type not in {"valve", "motor", "digital_input", "alarm", "pump", "analog_sensor"}:
                converted.append(component.model_dump(mode="json"))
                continue
            if len(component_points) != width:
                raise ValueError(f"Could not resolve all S7 addresses for '{device.node_id}/{component.name}'")
            digest = sha256(f"{scenario.id}:{device.node_id}:{index}".encode()).hexdigest()[:16]
            for point_index, point in enumerate(component_points):
                address = point["address"]
                match = re.fullmatch(r"DB([1-9]\d*)\.DBX(\d+)\.([0-7])", address)
                data_type = point["data_type"]
                if data_type == "BOOL":
                    if match is None:
                        raise ValueError(f"Unsupported legacy BOOL address '{address}'")
                    db, byte_offset, bit_offset = map(int, match.groups())
                else:
                    match = re.fullmatch(r"DB([1-9]\d*)\.DBD(\d+)", address)
                    if match is None:
                        raise ValueError(f"Unsupported legacy REAL address '{address}'")
                    db, byte_offset = map(int, match.groups())
                    bit_offset = None
                is_command = component_type in {"valve", "motor"} or component_type == "pump" and point_index == 0
                channel_name = component.name
                channel_id = f"migrated-{digest}-{point_index}"
                while channel_id in channel_ids:
                    channel_id += "-copy"
                channel_ids.add(channel_id)
                channel = {
                    "type": "io_channel", "name": channel_name, "db": db, "area": "DB",
                    "byte_offset": byte_offset, "direction": "output" if is_command else "input",
                    "data_type": data_type, "unit": point.get("unit") or "", "channel_id": channel_id,
                }
                if bit_offset is not None:
                    channel["bit_offset"] = bit_offset
                if "min_value" in point and "max_value" in point:
                    channel["min_value"] = point["min_value"]
                    channel["max_value"] = point["max_value"]
                converted.append(channel)
            # Keep an existing field connection bound to the same address. For
            # an unconnected legacy component, use the deterministic proposal.
            addresses = {point["address"] for point in component_points}
            has_existing_link = any(
                endpoint.kind == "device" and endpoint.node_id == device.node_id and endpoint.address in addresses
                for link in scenario.signal_connections
                for endpoint in (link.source, link.target)
            )
            if not has_existing_link:
                proposed = next((item for item in preview["field_objects"]
                                 if item["object_id"] not in original_field_ids
                                 and item["object_id"] not in used_generated_field_ids
                                 and item["name"] == component.name
                                 and item["type"] == component_type), None)
                if proposed is None:
                    raise ValueError(f"Could not create a field object for '{device.node_id}/{component.name}'")
                used_generated_field_ids.add(proposed["object_id"])
                proposed["zone_id"] = field_zone
        migrated_components[device.node_id] = converted

    devices = []
    for device in scenario.devices:
        item = device.model_dump(mode="json")
        item["config"]["components"] = migrated_components[device.node_id]
        devices.append(item)
    default_suffix = " (campo independiente)"
    payload = {
        "name": name or f"{scenario.name[:80 - len(default_suffix)]}{default_suffix}",
        "network": network_model.model_dump(mode="json"),
        "devices": devices,
        "canvas": scenario.canvas,
        "field_objects": preview["field_objects"],
        "signal_connections": preview["signal_connections"],
        "process_connections": preview["process_connections"],
        "network_routes": [item.model_dump(mode="json") for item in scenario.network_routes],
        "network_connections": [item.model_dump(mode="json") for item in scenario.network_connections],
    }
    return ScenarioCreate.model_validate(payload)
