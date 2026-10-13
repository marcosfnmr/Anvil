"""Verify valve flow limits and conservation in an isolated Docker field runtime."""
from __future__ import annotations

import argparse
import json
import time
import uuid

import docker


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="anvil/field:rf11-valve-flow")
    args = parser.parse_args()
    client = docker.from_env()
    container = None
    try:
        config = {
            "objects": [
                {"object_id": "closed-pump", "type": "pump", "name": "Closed path pump",
                 "initial_state": True, "response_seconds": 0, "flow_min": 0, "flow_max": 80},
                {"object_id": "closed-valve", "type": "valve", "name": "Closed valve",
                 "initial_state": False, "response_seconds": 0, "flow_rate_lpm": 50},
                {"object_id": "closed-tank", "type": "tank", "name": "Closed path tank",
                 "capacity_liters": 100, "initial_level_liters": 0, "outlet_flow_lpm": 0},
                {"object_id": "proportional-pump", "type": "pump", "name": "Proportional path pump",
                 "initial_state": True, "response_seconds": 0, "flow_min": 0, "flow_max": 60},
                {"object_id": "proportional-valve", "type": "proportional_valve",
                 "name": "Proportional valve", "initial_opening_pct": 0,
                 "response_seconds": 0, "flow_rate_lpm": 60},
                {"object_id": "proportional-tank", "type": "tank", "name": "Proportional path tank",
                 "capacity_liters": 100, "initial_level_liters": 0, "outlet_flow_lpm": 0},
                {"object_id": "branch-pump", "type": "pump", "name": "Parallel branch pump",
                 "initial_state": True, "response_seconds": 0, "flow_min": 0, "flow_max": 100},
                {"object_id": "open-valve", "type": "valve", "name": "Open branch valve",
                 "initial_state": True, "response_seconds": 0, "flow_rate_lpm": 50},
                {"object_id": "branch-closed-valve", "type": "valve", "name": "Closed branch valve",
                 "initial_state": False, "response_seconds": 0, "flow_rate_lpm": 50},
                {"object_id": "branch-tank-a", "type": "tank", "name": "Open branch tank",
                 "capacity_liters": 100, "initial_level_liters": 0, "outlet_flow_lpm": 0},
                {"object_id": "branch-tank-b", "type": "tank", "name": "Closed branch tank",
                 "capacity_liters": 100, "initial_level_liters": 0, "outlet_flow_lpm": 0},
                {"object_id": "disturbance-tank", "type": "tank", "name": "Disturbance tank",
                 "capacity_liters": 100, "initial_level_liters": 20, "outlet_flow_lpm": 0},
            ],
            "connections": [
                {"connection_id": "open-command", "source": {"kind": "device", "node_id": "smoke", "address": "Q0.0"},
                 "target": {"kind": "field", "object_id": "closed-valve", "port": "command"}},
                {"connection_id": "proportional-command", "source": {"kind": "device", "node_id": "smoke", "address": "QD2"},
                 "target": {"kind": "field", "object_id": "proportional-valve", "port": "command"}},
            ],
            "process_connections": [
                {"connection_id": "closed-pump-valve", "source": {"object_id": "closed-pump", "port": "flow"},
                 "target": {"object_id": "closed-valve", "port": "process_in"}},
                {"connection_id": "closed-valve-tank", "source": {"object_id": "closed-valve", "port": "process_out"},
                 "target": {"object_id": "closed-tank", "port": "inlet"}},
                {"connection_id": "proportional-pump-valve", "source": {"object_id": "proportional-pump", "port": "flow"},
                 "target": {"object_id": "proportional-valve", "port": "process_in"}},
                {"connection_id": "proportional-valve-tank", "source": {"object_id": "proportional-valve", "port": "process_out"},
                 "target": {"object_id": "proportional-tank", "port": "inlet"}},
                {"connection_id": "branch-pump-open", "source": {"object_id": "branch-pump", "port": "flow"},
                 "target": {"object_id": "open-valve", "port": "process_in"}},
                {"connection_id": "branch-pump-closed", "source": {"object_id": "branch-pump", "port": "flow"},
                 "target": {"object_id": "branch-closed-valve", "port": "process_in"}},
                {"connection_id": "open-valve-tank", "source": {"object_id": "open-valve", "port": "process_out"},
                 "target": {"object_id": "branch-tank-a", "port": "inlet"}},
                {"connection_id": "closed-valve-tank", "source": {"object_id": "branch-closed-valve", "port": "process_out"},
                 "target": {"object_id": "branch-tank-b", "port": "inlet"}},
            ],
        }
        container = client.containers.run(
            args.image, detach=True, network_mode="none",
            name=f"anvil-field-valve-smoke-{uuid.uuid4().hex[:10]}",
            environment={"FIELD_CONFIG": json.dumps(config)},
        )
        script = r'''import json, time, urllib.request
base = "http://127.0.0.1:8765"
def request(path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(base + path, data=data,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=2) as response:
        return json.load(response)
deadline = time.monotonic() + 15
while True:
    try:
        request("/snapshot")
        break
    except OSError:
        if time.monotonic() >= deadline:
            raise
        time.sleep(.1)
def objects():
    return {item["object_id"]: item for item in request("/snapshot")["objects"]}
def control(payload):
    payload = {"object_id": "disturbance-tank", **payload}
    data = json.dumps(payload).encode()
    req = urllib.request.Request(base + "/control", data=data,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=2) as response:
        return json.load(response)
state = objects()
assert state["closed-pump"]["flow"] == 0, state["closed-pump"]
assert state["closed-valve"]["flow"] == 0, state["closed-valve"]
assert state["closed-tank"]["inflow_lpm"] == 0, state["closed-tank"]
request("/exchange", {"node_id": "smoke", "outputs": {
    "open-command": True, "proportional-command": 40.0}})
state = objects()
assert state["closed-pump"]["flow"] == 40, state["closed-pump"]
assert state["closed-valve"]["flow"] == 40, state["closed-valve"]
assert state["closed-tank"]["inflow_lpm"] == 40, state["closed-tank"]
for key in ("proportional-pump", "proportional-valve", "proportional-tank"):
    assert abs(state[key]["flow"] - 24) < 1e-6 if key != "proportional-tank" else abs(state[key]["inflow_lpm"] - 24) < 1e-6, state[key]
assert state["branch-pump"]["flow"] == 50, state["branch-pump"]
assert state["open-valve"]["flow"] == 50, state["open-valve"]
assert state["branch-closed-valve"]["process_in"] == 0, state["branch-closed-valve"]
assert state["branch-tank-a"]["inflow_lpm"] == 50, state["branch-tank-a"]
assert state["branch-tank-b"]["inflow_lpm"] == 0, state["branch-tank-b"]
initial = state["disturbance-tank"]["volume_liters"]
control({"leak_flow_lpm": 60})
time.sleep(1.1)
state = objects()["disturbance-tank"]
assert state["leak_rate_lpm"] == 60 and state["leak_lpm"] == 60, state
assert state["volume_liters"] < initial, state
control({"leak_flow_lpm": 0})
time.sleep(.5)
state = objects()["disturbance-tank"]
assert state["leak_rate_lpm"] == 0 and state["leak_lpm"] == 0, state
assert state["volume_liters"] <= initial and state["volume_liters"] >= initial - 2, state
print("PASS: closed, proportional and parallel valve flows balance; runtime leak injects and restores in isolated Docker")
'''
        result = container.exec_run(["python", "-c", script], demux=True)
        output = b"".join(part or b"" for part in result.output).decode(errors="replace")
        if result.exit_code != 0:
            raise AssertionError(output[-4000:])
        print(output.strip())
        return 0
    finally:
        if container is not None:
            container.remove(force=True)
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
