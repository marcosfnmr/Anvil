"""Verify a field signal over an explicit HTTP gateway between Purdue zones."""

from __future__ import annotations

import argparse
import json
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import docker


def request(base_url: str, path: str, method: str = "GET", payload: dict | None = None):
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if body else {}
    try:
        with urlopen(Request(f"{base_url}{path}", data=body, headers=headers, method=method), timeout=60) as response:
            raw = response.read()
            return json.loads(raw) if raw else None
    except HTTPError as error:
        raise RuntimeError(f"{method} {path} failed ({error.code}): {error.read().decode(errors='replace')}") from error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8014")
    parser.add_argument("--field-subnet", default="172.30.250.0/24")
    parser.add_argument("--control-subnet", default="172.30.251.0/24")
    parser.add_argument("--route-state", choices=("enabled", "blocked"), default="enabled")
    args = parser.parse_args()
    route_enabled = args.route_state == "enabled"
    payload = {
        "name": "Field route integration smoke",
        "network": {"subnet": "172.30.252.0/24", "zones": [
            {"zone_id": "field", "name": "Field", "purdue_level": "L0", "subnet": args.field_subnet},
            {"zone_id": "control", "name": "Control", "purdue_level": "L1", "subnet": args.control_subnet},
        ]},
        "devices": [{"node_id": "plc", "device_type": "plc-s7", "name": "PLC", "ip": args.control_subnet.rsplit(".", 1)[0] + ".10", "zone_id": "control",
                     "config": {"components": [
                         {"type": "io_channel", "channel_id": "command", "name": "Valve command", "direction": "output", "data_type": "BOOL"},
                         {"type": "io_channel", "channel_id": "feedback", "name": "Valve feedback", "direction": "input", "data_type": "BOOL"},
                         {"type": "io_channel", "channel_id": "measurement", "name": "Measurement", "direction": "input", "data_type": "REAL", "unit": "%"},
                     ]}}],
        "field_objects": [
            {"object_id": "valve", "type": "valve", "name": "Field valve", "zone_id": "field", "response_seconds": 0.1},
            {"object_id": "sensor", "type": "analog_sensor", "name": "Field sensor", "zone_id": "field", "min_value": 0, "max_value": 100, "unit": "%"},
        ],
        "signal_connections": [],
    }
    scenario_id = None
    try:
        request(args.base_url, "/api/ready")
        created = request(args.base_url, "/api/scenarios", "POST", payload)
        scenario_id = created["id"]
        root = f"/api/scenarios/{scenario_id}"
        io_points = request(args.base_url, f"{root}/io-map")["points"]
        addresses = {point["component_name"]: point["address"] for point in io_points}
        payload["signal_connections"] = [
            {"connection_id": "valve-command", "source": {"kind": "device", "node_id": "plc", "address": addresses["Valve command"], "channel_id": "command"},
             "target": {"kind": "field", "object_id": "valve", "port": "command"}, "route_enabled": route_enabled},
            {"connection_id": "valve-feedback", "source": {"kind": "field", "object_id": "valve", "port": "state"},
             "target": {"kind": "device", "node_id": "plc", "address": addresses["Valve feedback"], "channel_id": "feedback"}, "route_enabled": route_enabled},
            {"connection_id": "sensor-value", "source": {"kind": "field", "object_id": "sensor", "port": "value"},
             "target": {"kind": "device", "node_id": "plc", "address": addresses["Measurement"], "channel_id": "measurement"}, "route_enabled": route_enabled},
        ]
        request(args.base_url, root, "PUT", payload)
        deployed = request(args.base_url, f"{root}/deploy", "POST")
        expected_containers = 3 if route_enabled else 2
        if deployed["status"] != "running" or len(deployed["containers"]) != expected_containers:
            raise AssertionError(f"unexpected containers for route state {args.route_state}: {deployed}")
        if route_enabled:
            diagnostics_deadline = time.monotonic() + 15
            diagnostics = None
            while time.monotonic() < diagnostics_deadline:
                diagnostics = request(args.base_url, f"{root}/diagnostics")
                field_routes = [item for item in diagnostics["runtime"].get("routes", [])
                                if item.get("protocol") in {"field-http", "field-binding"}]
                if len(field_routes) >= 2 and all(item.get("status") == "reachable" for item in field_routes):
                    break
                time.sleep(0.25)
            else:
                raise AssertionError(f"active field HTTP route probe failed: {diagnostics}")
            if not diagnostics["runtime"]["protocol_transactions_verified"]:
                raise AssertionError(f"field HTTP route probe result was not reported: {diagnostics}")
            binding_route = next(item for item in field_routes if item["protocol"] == "field-binding")
            if len(binding_route.get("bindings", [])) != len(payload["signal_connections"]) or any(
                item.get("status") != "good" for item in binding_route["bindings"]
            ):
                raise AssertionError(f"not every configured field signal binding is fresh: {binding_route}")

        client = docker.from_env()
        containers = client.containers.list(filters={"label": f"anvil.scenario_id={scenario_id}"})
        by_node = {item.labels["anvil.node_id"]: item for item in containers}
        plc = by_node["plc"]
        field_runtime = by_node["__anvil_field__"]
        plc_zones = {client.networks.get(name).attrs["Labels"]["anvil.zone_id"]
                     for name in plc.attrs["NetworkSettings"]["Networks"]}
        field_zones = {client.networks.get(name).attrs["Labels"]["anvil.zone_id"]
                       for name in field_runtime.attrs["NetworkSettings"]["Networks"]}
        gateways = [item for node, item in by_node.items() if node.startswith("__anvil_field_route_")]
        if plc_zones != {"control"} or field_zones != {"field"} or any(item.attrs["HostConfig"]["PortBindings"] for item in containers):
            raise AssertionError("PLC and field runtime must remain in separate zones without host ports")
        if route_enabled:
            if len(gateways) != 1:
                raise AssertionError("an enabled field route must materialize one gateway")
            gateway = gateways[0]
            networks = gateway.attrs["NetworkSettings"]["Networks"]
            route_zones = {client.networks.get(name).attrs["Labels"]["anvil.zone_id"] for name in networks}
            if len(networks) != 2 or route_zones != {"field", "control"}:
                raise AssertionError("field gateway must connect only its declared two zones")
        elif gateways:
            raise AssertionError("a blocked field route must not materialize a gateway")
        direct = plc.exec_run(["python", "-c", f"import socket; socket.create_connection(('{field_runtime.attrs['NetworkSettings']['Networks'][next(iter(field_runtime.attrs['NetworkSettings']['Networks']))]['IPAddress']}',8765),timeout=1)"])
        if direct.exit_code == 0:
            raise AssertionError("direct PLC-to-field connection unexpectedly crossed zones")

        command_address = addresses["Valve command"]
        write = request(args.base_url, f"{root}/plcs/plc/write", "POST", {"address": command_address, "value": True})
        if write["value"] is not True:
            raise AssertionError(f"PLC command write failed: {write}")
        if not route_enabled:
            time.sleep(1.5)
            state = request(args.base_url, f"{root}/field/values")
            valve = next(item for item in state["objects"] if item["object_id"] == "valve")
            if valve["state"] or any(item["quality"] == "good" for item in state["connections"]):
                raise AssertionError("a blocked route unexpectedly exchanged field signals")
            print(json.dumps({"status": "passed", "route_state": "blocked", "direct_path": "blocked",
                              "gateway": "not created", "command_feedback": "not exchanged",
                              "qualities": [item["quality"] for item in state["connections"]], "host_ports": "none"}))
            return 0
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            field_state = request(args.base_url, f"{root}/field/values")
            valve = next(item for item in field_state["objects"] if item["object_id"] == "valve")
            values = request(args.base_url, f"{root}/plcs/plc/values")["values"]
            feedback = next(item for item in values if item["address"] == addresses["Valve feedback"])
            measure = next(item for item in values if item["address"] == addresses["Measurement"])
            if valve["state"] and feedback["value"] and abs(measure["value"] - 50) < 0.1:
                break
            time.sleep(0.2)
        else:
            raise AssertionError(f"field signal did not round-trip through gateway: valve={valve}, feedback={feedback}, measure={measure}")
        print(json.dumps({"status": "passed", "route_state": "enabled", "field_zone": "field", "controller_zone": "control",
                          "gateway": gateway.name, "direct_path": "blocked", "command_feedback": "passed",
                          "sensor_reading": measure["value"], "field_route_probes": field_routes,
                          "host_ports": "none"}))
        return 0
    finally:
        if scenario_id:
            try:
                request(args.base_url, f"/api/scenarios/{scenario_id}/stop", "POST")
            finally:
                request(args.base_url, f"/api/scenarios/{scenario_id}", "DELETE")


if __name__ == "__main__":
    raise SystemExit(main())
