"""Verify seeded sensor samples repeat across isolated field runtime containers."""
from __future__ import annotations

import argparse
import json
import time
import uuid

import docker


def collect_samples(client: docker.DockerClient, image: str) -> list[float]:
    config = {
        "objects": [{
            "object_id": "pressure", "type": "analog_sensor", "name": "Pressure",
            "min_value": 0, "max_value": 100,
            "simulation": {"mode": "constant", "noise": 0.2, "seed": 1234},
        }],
        "connections": [{
            "connection_id": "pressure-reading",
            "source": {"kind": "field", "object_id": "pressure", "port": "value"},
            "target": {"kind": "device", "node_id": "probe", "address": "DB1.DBD0"},
        }],
        "process_connections": [],
    }
    container = client.containers.run(
        image, detach=True, network_mode="none",
        name=f"anvil-field-noise-smoke-{uuid.uuid4().hex[:10]}",
        environment={"FIELD_CONFIG": json.dumps(config)},
        healthcheck={"test": ["NONE"]},
    )
    try:
        script = r'''import json, time, urllib.request
base = "http://127.0.0.1:8765"
def ready():
    with urllib.request.urlopen(base + "/snapshot", timeout=2) as response:
        return response.status == 200
def sample():
    payload = json.dumps({"node_id": "probe", "outputs": {}}).encode()
    request = urllib.request.Request(base + "/exchange", data=payload,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=2) as response:
        return json.load(response)["inputs"]["pressure-reading"]
deadline = time.monotonic() + 15
while True:
    try:
        if not ready():
            raise OSError("field runtime is not ready")
        values = [sample() for _ in range(6)]
        print(json.dumps(values))
        break
    except OSError:
        if time.monotonic() >= deadline:
            raise
        time.sleep(.1)
'''
        result = container.exec_run(["python", "-c", script], demux=True)
        stdout, stderr = result.output
        if result.exit_code != 0:
            raise AssertionError((stderr or stdout or b"").decode(errors="replace")[-3000:])
        return json.loads(stdout.decode().strip().splitlines()[-1])
    finally:
        container.remove(force=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="anvil/field:ci")
    args = parser.parse_args()
    client = docker.from_env()
    try:
        first = collect_samples(client, args.image)
        second = collect_samples(client, args.image)
        if len(first) != 6 or len(set(first)) < 2:
            raise AssertionError(f"configured noise did not produce varied samples: {first}")
        if any(not 0 <= value <= 100 for value in first):
            raise AssertionError(f"seeded samples escaped the configured sensor range: {first}")
        if first != second:
            raise AssertionError(f"seeded sensor sequences differ across containers: {first} != {second}")
        print(f"PASS: seeded sensor sequence repeats across isolated containers: {first}")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
