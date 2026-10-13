"""Resolve configured signals into S7 peer addresses before deployment."""
import re

from devices import DEVICE_REGISTRY


def resolve_s7_links(runtime_devices: list[dict]) -> None:
    by_id = {item["node_id"]: item for item in runtime_devices}
    maps = {node: DEVICE_REGISTRY[item["device_type"]]().build_io_map(item)
            for node, item in by_id.items() if item["device_type"] == "plc-s7"}
    for receiver in runtime_devices:
        if receiver["device_type"] != "plc-s7":
            continue
        for component in receiver["config"]["components"]:
            if component["type"] != "remote_input":
                continue
            source_id = component["source_node_id"]
            source = by_id.get(source_id)
            if source_id == receiver["node_id"] or source_id not in maps:
                raise ValueError("A remote input must reference another S7 PLC in the same scenario")
            point = next((point for point in maps[source_id] if point["address"] == component["source_address"]), None)
            if point is None or point["component_type"] == "remote_input":
                raise ValueError(f"Remote input '{component['name']}' needs an existing local source signal")
            if point["data_type"] != component["data_type"]:
                raise ValueError(f"Remote input '{component['name']}' data type differs from its source")
            if component.get("source_name") is not None and component["source_name"] != point["component_name"]:
                raise ValueError(f"Source signal of '{component['name']}' changed; select its source again")
            match = re.fullmatch(r"DB(\d+)\.(?:DBX(\d+)\.([0-7])|DBD(\d+))", point["address"])
            route = receiver["config"].get("network_routes", {}).get(source_id)
            component.update(source_ip=route["host"] if route else source["ip"],
                             source_port=route["port"] if route else 102,
                             source_rack=source["config"]["rack"],
                             source_slot=source["config"]["slot"], source_db=int(match[1]),
                             source_byte_offset=int(match[2] or match[4]), source_bit_offset=int(match[3] or 0))
