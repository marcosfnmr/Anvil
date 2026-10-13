"""Compile signal bindings without changing public devices or old DB offsets."""
import hashlib
from ipaddress import ip_network

from devices import DEVICE_REGISTRY

FIELD_NODE_ID = "__anvil_field__"
FIELD_RUNTIME_CONTRACT_VERSION = 1


def add_field_runtime(payload, devices):
    if not payload.field_objects:
        return devices
    connected_fields = {
        (link.source if link.source.kind == "field" else link.target).object_id
        for link in payload.signal_connections
    }
    linked_field_zones = {item.zone_id for item in payload.field_objects if item.object_id in connected_fields}
    if len(linked_field_zones) > 1:
        raise ValueError("connected field objects span multiple Purdue zones; one field runtime zone is required")
    zone_id = next(iter(linked_field_zones), payload.field_objects[0].zone_id)
    zone = next((item for item in payload.network.zones if item.zone_id == zone_id), None)
    network = ip_network(zone.subnet if zone else payload.network.subnet)
    used = {item["ip"] for item in devices if item.get("zone_id", "default") == zone_id}
    if any(device["node_id"] == FIELD_NODE_ID for device in devices):
        raise ValueError("reserved internal field runtime node ID")
    address = next((str(network.broadcast_address - offset) for offset in range(1, len(used) + 3)
                    if network.broadcast_address - offset > network.network_address + 1
                    and str(network.broadcast_address - offset) not in used), None)
    if address is None:
        raise ValueError("the scenario subnet needs one free IP for the field runtime")
    field = {"node_id": FIELD_NODE_ID, "device_type": "field-runtime", "name": "Simulación de campo",
             "ip": address, "network_mode": None, "zone_id": zone_id,
             "zone_subnet": str(network), "zoned": bool(payload.network.zones), "config": {
                 "contract_version": FIELD_RUNTIME_CONTRACT_VERSION,
                 "objects": [item.model_dump(mode="json") for item in payload.field_objects],
                 "connections": [item.model_dump(mode="json") for item in payload.signal_connections],
                 "process_connections": [item.model_dump(mode="json") for item in payload.process_connections],
             }}
    runtime_devices = [field, *devices]
    generated_gateways = []
    for device in devices:
        bindings = []
        points = {point["address"]: point for point in DEVICE_REGISTRY[device["device_type"]]().build_io_map(device)}
        for link in payload.signal_connections:
            endpoint = link.source if link.source.kind == "device" else link.target
            if endpoint.node_id != device["node_id"]:
                continue
            point = points[endpoint.address]
            field_endpoint = link.target if link.target.kind == 'field' else link.source
            binding = {"connection_id": link.connection_id, "address": endpoint.address,
                       "direction": "output" if link.source.kind == "device" else "input",
                       "data_type": point["data_type"], "field_id": field_endpoint.object_id, "field_port": field_endpoint.port}
            if device["device_type"] == "plc-s7":
                from services.plc_operations import _component_address
                component = next(c for c in device["config"]["components"] if _component_address(c) == endpoint.address)
                binding.update(area=component.get("area", "DB"), db=component["db"] if component.get("area", "DB") == "DB" else 0, byte_offset=component["byte_offset"], bit_offset=component.get("bit_offset", 0))
            bindings.append(binding)
        if bindings:
            source_zone_id = device.get("zone_id", "default")
            source_zone = next((item for item in payload.network.zones if item.zone_id == source_zone_id), None)
            source_network = ip_network(source_zone.subnet if source_zone else payload.network.subnet)
            field_route_enabled = any(
                link.route_enabled is True
                and (link.source.node_id if link.source.kind == "device" else link.target.node_id) == device["node_id"]
                for link in payload.signal_connections
            )
            if source_zone_id == zone_id or not field_route_enabled:
                bridge_host, bridge_port = address, 8765
            else:
                route_id = f"field-{device['node_id']}"
                route_port = 20000 + int(hashlib.sha256(route_id.encode()).hexdigest()[:4], 16) % 40000
                used_ports = {int(route.get("port", 0)) for route in device.get("config", {}).get("network_routes", {}).values()}
                for _ in range(40000):
                    if route_port not in used_ports:
                        break
                    route_port = 20000 + (route_port - 19999) % 40000
                else:
                    raise ValueError(f"no free TCP route port for field link from '{device['node_id']}'")
                occupied_source_ips = {item["ip"] for item in runtime_devices + generated_gateways
                                       if item.get("zone_id") == source_zone_id}
                source_route_ip = next((str(source_network.network_address + offset)
                                        for offset in range(2, source_network.num_addresses - 1)
                                        if str(source_network.network_address + offset) not in occupied_source_ips), None)
                if source_route_ip is None:
                    raise ValueError(f"zone '{source_zone_id}' has no free address for a field route")
                occupied_target_ips = set()
                for item in runtime_devices + generated_gateways:
                    if item.get("zone_id") == zone_id:
                        occupied_target_ips.add(item["ip"])
                    config = item.get("config", {})
                    if config.get("target_zone_id") == zone_id and config.get("target_interface_ip"):
                        occupied_target_ips.add(config["target_interface_ip"])
                target_route_ip = next((str(network.network_address + offset)
                                        for offset in range(2, network.num_addresses - 1)
                                        if str(network.network_address + offset) not in occupied_target_ips), None)
                if target_route_ip is None:
                    raise ValueError(f"zone '{zone_id}' has no free address for a field route")
                gateway_id = f"__anvil_field_route_{device['node_id']}"
                if any(item["node_id"] == gateway_id for item in runtime_devices + generated_gateways):
                    raise ValueError("reserved internal field route node ID")
                generated_gateways.append({
                    "node_id": gateway_id, "device_type": "tcp-gateway", "name": f"Ruta de campo {device['node_id']}",
                    "ip": source_route_ip,
                    "hostname": f"field-route-{hashlib.sha256(route_id.encode()).hexdigest()[:12]}",
                    "target_ip": address, "external_access": False, "external_port": None,
                    "network_mode": None, "zone_id": source_zone_id,
                    "zone_subnet": str(source_network), "zoned": bool(payload.network.zones),
                    "config": {"role": "tcp-gateway", "summary": "field HTTP route",
                               "listen_port": route_port, "source_node_id": device["node_id"],
                               "source_ip": device["ip"], "target_ip": address,
                               "target_port": 8765, "target_interface_ip": target_route_ip,
                               "target_zone_id": zone_id, "target_zone_subnet": str(network),
                               "target_hostname": "field-runtime", "target_node_id": FIELD_NODE_ID,
                               "protocol": "field-http"},
                })
                bridge_host, bridge_port = source_route_ip, route_port
            device["config"]["field_bridge"] = {
                "url": f"http://{bridge_host}:{bridge_port}/exchange",
                "node_id": device["node_id"], "bindings": bindings,
            }
    return [*runtime_devices, *generated_gateways]
