"""Verify series pipe losses and conserved parallel flow in Docker."""

from __future__ import annotations

import argparse
import time
from ipaddress import ip_network

import docker

from platform_smoke import request


def free_subnet(client: docker.DockerClient) -> str:
    used = [
        ip_network(entry["Subnet"])
        for network in client.networks.list()
        for entry in ((network.attrs.get("IPAM") or {}).get("Config") or [])
        if entry.get("Subnet")
    ]
    for third in range(20, 240):
        candidate = ip_network(f"10.248.{third}.0/24")
        if all(not candidate.overlaps(existing) for existing in used):
            return str(candidate)
    raise RuntimeError("No free subnet in 10.248.0.0/16 for the pipe smoke")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8004")
    args = parser.parse_args()
    client = docker.from_env()
    scenario_id = None
    try:
        subnet = ip_network(free_subnet(client))
        payload = {
            "name": "Process pipe integration smoke",
            "network": {"subnet": str(subnet)},
            "devices": [],
            "field_objects": [
                {"object_id": "pump", "type": "pump", "name": "Feed pump",
                 "initial_state": True, "response_seconds": 0,
                 "flow_min": 0, "flow_max": 80, "max_head_kpa": 100, "unit": "L/min"},
                {"object_id": "pipe", "type": "pipe", "name": "Feed line",
                 "max_flow_lpm": 40, "pressure_drop_kpa": 16},
                {"object_id": "tank", "type": "tank", "name": "Receiver",
                 "capacity_liters": 100, "initial_level_liters": 0,
                 "outlet_flow_lpm": 0},
                {"object_id": "pressure", "type": "analog_sensor", "name": "Line pressure",
                 "min_value": 0, "max_value": 100, "unit": "kPa",
                 "simulation": {"mode": "process"}},
                {"object_id": "restricted-pump", "type": "pump", "name": "Restricted pump",
                 "initial_state": True, "response_seconds": 0, "flow_min": 0,
                 "flow_max": 80, "max_head_kpa": 100, "unit": "L/min"},
                {"object_id": "restricted-pipe", "type": "pipe", "name": "Restrictive line",
                 "max_flow_lpm": 40, "pressure_drop_kpa": 400},
                {"object_id": "restricted-tank", "type": "tank", "name": "Restricted receiver",
                 "capacity_liters": 100, "initial_level_liters": 0,
                 "outlet_flow_lpm": 0},
                {"object_id": "branch-pump", "type": "pump", "name": "Branch pump",
                 "initial_state": True, "response_seconds": 0, "flow_min": 0,
                 "flow_max": 100, "max_head_kpa": 100, "unit": "L/min"},
                {"object_id": "branch-pipe-a", "type": "pipe", "name": "Branch A",
                 "max_flow_lpm": 60, "pressure_drop_kpa": 100},
                {"object_id": "branch-pipe-b", "type": "pipe", "name": "Branch B",
                 "max_flow_lpm": 60, "pressure_drop_kpa": 400},
                {"object_id": "branch-tank-a", "type": "tank", "name": "Receiver A",
                 "capacity_liters": 1000, "initial_level_liters": 0,
                 "outlet_flow_lpm": 0},
                {"object_id": "branch-tank-b", "type": "tank", "name": "Receiver B",
                 "capacity_liters": 1000, "initial_level_liters": 0,
                 "outlet_flow_lpm": 0},
                {"object_id": "manifold-pump", "type": "pump", "name": "Manifold pump",
                 "initial_state": True, "response_seconds": 0, "flow_min": 0,
                 "flow_max": 100, "unit": "L/min"},
                {"object_id": "splitter", "type": "flow_splitter", "name": "Distributor",
                 "split_ratio_pct": 30},
                {"object_id": "split-pipe-a", "type": "pipe", "name": "Split branch A",
                 "max_flow_lpm": 100},
                {"object_id": "split-pipe-b", "type": "pipe", "name": "Split branch B",
                 "max_flow_lpm": 100},
                {"object_id": "mixer", "type": "flow_mixer", "name": "Collector"},
                {"object_id": "mixed-tank", "type": "tank", "name": "Mixed receiver",
                 "capacity_liters": 1000, "initial_level_liters": 0,
                 "outlet_flow_lpm": 0},
                {"object_id": "limited-pump", "type": "pump", "name": "Capacity-limited pump",
                 "initial_state": True, "response_seconds": 0, "flow_min": 0,
                 "flow_max": 100, "unit": "L/min"},
                {"object_id": "limited-splitter", "type": "flow_splitter", "name": "Limited distributor",
                 "split_ratio_pct": 30},
                {"object_id": "limited-pipe-a", "type": "pipe", "name": "Limited branch A",
                 "max_flow_lpm": 10},
                {"object_id": "limited-pipe-b", "type": "pipe", "name": "Limited branch B",
                 "max_flow_lpm": 20},
                {"object_id": "limited-mixer", "type": "flow_mixer", "name": "Limited collector"},
                {"object_id": "limited-tank", "type": "tank", "name": "Limited receiver",
                 "capacity_liters": 1000, "initial_level_liters": 0,
                 "outlet_flow_lpm": 0},
            ],
            "signal_connections": [],
            "process_connections": [
                {"connection_id": "pump-to-pipe",
                 "source": {"object_id": "pump", "port": "flow"},
                 "target": {"object_id": "pipe", "port": "process_in"}},
                {"connection_id": "pipe-to-tank",
                 "source": {"object_id": "pipe", "port": "process_out"},
                 "target": {"object_id": "tank", "port": "inlet"}},
                {"connection_id": "pipe-pressure",
                 "source": {"object_id": "pipe", "port": "pressure"},
                 "target": {"object_id": "pressure", "port": "measurement"}},
                {"connection_id": "restricted-pump-pipe",
                 "source": {"object_id": "restricted-pump", "port": "flow"},
                 "target": {"object_id": "restricted-pipe", "port": "process_in"}},
                {"connection_id": "restricted-pipe-tank",
                 "source": {"object_id": "restricted-pipe", "port": "process_out"},
                 "target": {"object_id": "restricted-tank", "port": "inlet"}},
                {"connection_id": "branch-pump-a",
                 "source": {"object_id": "branch-pump", "port": "flow"},
                 "target": {"object_id": "branch-pipe-a", "port": "process_in"}},
                {"connection_id": "branch-pump-b",
                 "source": {"object_id": "branch-pump", "port": "flow"},
                 "target": {"object_id": "branch-pipe-b", "port": "process_in"}},
                {"connection_id": "branch-a-tank",
                 "source": {"object_id": "branch-pipe-a", "port": "process_out"},
                 "target": {"object_id": "branch-tank-a", "port": "inlet"}},
                {"connection_id": "branch-b-tank",
                 "source": {"object_id": "branch-pipe-b", "port": "process_out"},
                 "target": {"object_id": "branch-tank-b", "port": "inlet"}},
                {"connection_id": "manifold-feed",
                 "source": {"object_id": "manifold-pump", "port": "flow"},
                 "target": {"object_id": "splitter", "port": "process_in"}},
                {"connection_id": "split-a",
                 "source": {"object_id": "splitter", "port": "process_out_a"},
                 "target": {"object_id": "split-pipe-a", "port": "process_in"}},
                {"connection_id": "split-b",
                 "source": {"object_id": "splitter", "port": "process_out_b"},
                 "target": {"object_id": "split-pipe-b", "port": "process_in"}},
                {"connection_id": "mix-a",
                 "source": {"object_id": "split-pipe-a", "port": "process_out"},
                 "target": {"object_id": "mixer", "port": "process_in_a"}},
                {"connection_id": "mix-b",
                 "source": {"object_id": "split-pipe-b", "port": "process_out"},
                 "target": {"object_id": "mixer", "port": "process_in_b"}},
                {"connection_id": "mix-tank",
                 "source": {"object_id": "mixer", "port": "process_out"},
                 "target": {"object_id": "mixed-tank", "port": "inlet"}},
                {"connection_id": "limited-feed",
                 "source": {"object_id": "limited-pump", "port": "flow"},
                 "target": {"object_id": "limited-splitter", "port": "process_in"}},
                {"connection_id": "limited-a",
                 "source": {"object_id": "limited-splitter", "port": "process_out_a"},
                 "target": {"object_id": "limited-pipe-a", "port": "process_in"}},
                {"connection_id": "limited-b",
                 "source": {"object_id": "limited-splitter", "port": "process_out_b"},
                 "target": {"object_id": "limited-pipe-b", "port": "process_in"}},
                {"connection_id": "limited-mix-a",
                 "source": {"object_id": "limited-pipe-a", "port": "process_out"},
                 "target": {"object_id": "limited-mixer", "port": "process_in_a"}},
                {"connection_id": "limited-mix-b",
                 "source": {"object_id": "limited-pipe-b", "port": "process_out"},
                 "target": {"object_id": "limited-mixer", "port": "process_in_b"}},
                {"connection_id": "limited-mix-tank",
                 "source": {"object_id": "limited-mixer", "port": "process_out"},
                 "target": {"object_id": "limited-tank", "port": "inlet"}},
            ],
        }
        created = request(args.base_url, "/api/scenarios", "POST", payload)
        scenario_id = created["id"]
        root = f"/api/scenarios/{scenario_id}"
        deployed = request(args.base_url, f"{root}/deploy", "POST")
        if deployed["status"] != "running":
            raise AssertionError(f"Field-only pipe scenario failed to deploy: {deployed}")
        time.sleep(1.1)
        snapshot = request(args.base_url, f"{root}/field/values")
        objects = {item["object_id"]: item for item in snapshot["objects"]}
        if objects["pump"]["flow"] != 40:
            raise AssertionError(f"Unexpected pump model output: {objects['pump']}")
        if objects["pipe"]["process_in"] != 40 or objects["pipe"]["flow"] != 40:
            raise AssertionError(f"Pipe flow differs from its 40 L/min configured limit: {objects['pipe']}")
        if objects["pipe"]["pressure_kpa"] != 84 or objects["pressure"]["value"] != 84:
            raise AssertionError(f"Pipe head loss was not visible on the connected pressure sensor: {objects}")
        if objects["tank"]["inflow_lpm"] != 40 or objects["tank"]["volume_liters"] <= 0:
            raise AssertionError(f"Pipe-limited flow did not reach the tank: {objects['tank']}")
        if objects["restricted-pump"]["flow"] != 20 or objects["restricted-pipe"]["flow"] != 20:
            raise AssertionError(f"Restricted series path did not reduce source flow to 20 L/min: {objects}")
        if objects["restricted-tank"]["inflow_lpm"] != 20:
            raise AssertionError(f"Reduced flow did not reach the restricted-path tank: {objects['restricted-tank']}")
        branch_a, branch_b = objects["branch-pipe-a"], objects["branch-pipe-b"]
        if abs(objects["branch-pump"]["flow"] - 50) > 1e-6:
            raise AssertionError(f"Parallel pump did not report 50 L/min total: {objects['branch-pump']}")
        if abs(branch_a["flow"] - 100 / 3) > 1e-6 or abs(branch_b["flow"] - 50 / 3) > 1e-6:
            raise AssertionError(f"Parallel paths did not split flow by hydraulic capacity: {branch_a}, {branch_b}")
        if abs(branch_a["flow"] + branch_b["flow"] - objects["branch-pump"]["flow"]) > 1e-6:
            raise AssertionError("Parallel branches did not conserve source flow")
        if abs(branch_a["pressure_drop_kpa"] - branch_b["pressure_drop_kpa"]) > 1e-6:
            raise AssertionError("Parallel paths did not reach a common calculated pressure loss")
        if abs(objects["branch-tank-a"]["inflow_lpm"] + objects["branch-tank-b"]["inflow_lpm"] - 50) > 1e-6:
            raise AssertionError("Tank inflows do not conserve branch source flow")
        splitter, mixer = objects["splitter"], objects["mixer"]
        if abs(splitter["process_out_a"] - 15) > 1e-6 or abs(splitter["process_out_b"] - 35) > 1e-6:
            raise AssertionError(f"Configured manifold ratio was not applied: {splitter}")
        if abs(mixer["flow"] - 50) > 1e-6 or abs(objects["mixed-tank"]["inflow_lpm"] - 50) > 1e-6:
            raise AssertionError(f"Manifold merge did not conserve flow to the receiver: {mixer}, {objects['mixed-tank']}")
        limited = objects["limited-splitter"]
        if abs(objects["limited-pump"]["flow"] - 30) > 1e-6:
            raise AssertionError(f"Source did not reduce flow to the sum of branch capacities: {objects['limited-pump']}")
        if abs(limited["process_out_a"] - 10) > 1e-6 or abs(limited["process_out_b"] - 20) > 1e-6:
            raise AssertionError(f"Configured split did not reroute flow into remaining branch capacity: {limited}")
        if (abs(objects["limited-mixer"]["flow"] - 30) > 1e-6 or
                abs(objects["limited-tank"]["inflow_lpm"] - 30) > 1e-6):
            raise AssertionError(f"Capacity-limited manifold did not conserve flow to receiver: {objects['limited-mixer']}, {objects['limited-tank']}")
        print("PASS: Docker field runtime verified series pressure loss, parallel capacity split, configured splitter/mixer flow, and capacity-limited rerouting")
        return 0
    finally:
        if scenario_id:
            try:
                request(args.base_url, f"/api/scenarios/{scenario_id}/stop", "POST")
            finally:
                request(args.base_url, f"/api/scenarios/{scenario_id}", "DELETE")
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
