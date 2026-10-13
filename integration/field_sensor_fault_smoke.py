"""Verify a Docker field runtime freezes and restores a process sensor fault."""
from __future__ import annotations

import argparse
import json
import socket
import time
import urllib.request
import uuid

import docker


def unused_local_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def request(url: str, path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url + path, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=3) as response:
        return json.load(response)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="anvil/field:rf23-sensor-fault")
    args = parser.parse_args()
    client = docker.from_env()
    container = None
    try:
        config = {
            "objects": [
                {"object_id": "tank", "type": "tank", "capacity_liters": 100,
                 "height_m": 2, "initial_level_liters": 50, "outlet_flow_lpm": 0,
                 "leak_flow_lpm": 0, "demand_flow_lpm": 0},
                {"object_id": "level", "type": "analog_sensor", "min_value": 0,
                 "max_value": 100, "unit": "%", "simulation": {"mode": "process"}},
            ],
            "connections": [{"connection_id": "level-to-plc", "source": {
                "kind": "field", "object_id": "level", "port": "value"},
                "target": {"kind": "device", "node_id": "plc-1", "address": "DB1.DBD0"}}],
            "process_connections": [{"connection_id": "tank-level", "source": {
                "object_id": "tank", "port": "level"}, "target": {
                "object_id": "level", "port": "measurement"}}],
        }
        port = unused_local_port()
        container = client.containers.run(
            args.image, detach=True,
            name=f"anvil-field-sensor-fault-{uuid.uuid4().hex[:10]}",
            environment={"FIELD_CONFIG": json.dumps(config)},
            ports={"8765/tcp": ("127.0.0.1", port)},
        )
        url = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            container.reload()
            if container.status == "exited":
                raise AssertionError(container.logs().decode(errors="replace")[-2000:])
            try:
                initial = request(url, "/exchange", {"node_id": "plc-1", "outputs": {}})
                break
            except OSError:
                time.sleep(.2)
        else:
            raise AssertionError("Field runtime did not become reachable")

        original = initial["inputs"]["level-to-plc"]
        request(url, "/control", {"object_id": "tank", "demand_flow_lpm": 60})
        time.sleep(1.1)
        changed = request(url, "/exchange", {"node_id": "plc-1", "outputs": {}})["inputs"]["level-to-plc"]
        if changed >= original:
            raise AssertionError(f"Process sensor did not follow tank level: {original} -> {changed}")

        request(url, "/control", {"object_id": "level", "fault": True})
        time.sleep(1.1)
        frozen = request(url, "/exchange", {"node_id": "plc-1", "outputs": {}})["inputs"]["level-to-plc"]
        quality = request(url, "/snapshot")["connections"][0]["quality"]
        if frozen != changed or quality != "fault":
            raise AssertionError(f"Sensor fault was not held/marked: value={frozen}, quality={quality}")

        request(url, "/control", {"object_id": "level", "fault": False})
        time.sleep(1.1)
        restored = request(url, "/exchange", {"node_id": "plc-1", "outputs": {}})["inputs"]["level-to-plc"]
        if restored >= frozen:
            raise AssertionError(f"Sensor did not resume process tracking: {frozen} -> {restored}")
        print(f"PASS: process sensor tracked {original:.2f}->{changed:.2f}, froze at {frozen:.2f}, then recovered to {restored:.2f}")
        return 0
    finally:
        if container is not None:
            container.remove(force=True)
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
