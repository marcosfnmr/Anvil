"""Verify the SCADA store-and-forward buffer survives container recreation."""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import time
import uuid

import docker
from docker.types import Mount


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="anvil/scada:dev")
    args = parser.parse_args()
    scenario_id = str(uuid.uuid4())
    node_id = "scada-buffer-smoke"
    volume_name = f"anvil-scada-history-{scenario_id}-{hashlib.sha256(node_id.encode()).hexdigest()[:16]}"
    labels = {"anvil.managed": "true", "anvil.purpose": "scada-history",
              "anvil.scenario_id": scenario_id, "anvil.node_id": node_id,
              "anvil.volume_name": volume_name}
    client = docker.from_env()
    containers = []
    volume = None
    ack_token = secrets.token_urlsafe(32)
    config = {"poll_interval_ms": 60_000, "history_capacity": 10,
              "durable_history_capacity": 1000, "durable_history_max_bytes": 16_777_216,
              "durable_history_path": "/var/lib/anvil/scada-history/history.sqlite3", "targets": [], "alarms": []}

    def start_runtime() -> docker.models.containers.Container:
        container = client.containers.run(
            args.image, detach=True, name=f"anvil-scada-buffer-{uuid.uuid4().hex[:10]}",
            network_mode="none", user="65534:65534", read_only=True,
            cap_drop=["ALL"], security_opt=["no-new-privileges:true"], pids_limit=32,
            mem_limit="96m", nano_cpus=500_000_000,
            tmpfs={"/tmp": "rw,nosuid,nodev,noexec,size=16m"},
            mounts=[Mount(target="/var/lib/anvil/scada-history", source=volume_name, type="volume")],
            labels={"anvil.managed": "true", "anvil.scenario_id": scenario_id,
                    "anvil.node_id": node_id, "anvil.device_type": "scada"},
            environment={"SCADA_CONFIG": json.dumps(config), "SCADA_HISTORY_ACK_TOKEN": ack_token},
        )
        containers.append(container)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            container.reload()
            if container.status != "running":
                raise RuntimeError(f"SCADA runtime exited: {container.logs(tail=80).decode(errors='replace')}")
            result = container.exec_run(["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8766/health',timeout=1)"])
            if result.exit_code == 0:
                return container
            time.sleep(0.1)
        raise TimeoutError("SCADA runtime did not become ready")

    def request(container: docker.models.containers.Container, path: str, payload: dict | None = None) -> dict:
        script = r'''import json,os,sys,urllib.request
path,payload=sys.argv[1:]
data=payload.encode() if payload else None
headers={'Content-Type':'application/json'}
if path == 'history/acknowledge': headers['Authorization']='Bearer '+os.environ['SCADA_HISTORY_ACK_TOKEN']
request=urllib.request.Request('http://127.0.0.1:8766/'+path,data=data,headers=headers)
with urllib.request.urlopen(request,timeout=3) as response: print(response.read().decode())
'''
        result = container.exec_run(["python", "-c", script, path, json.dumps(payload) if payload else ""])
        if result.exit_code:
            raise RuntimeError(result.output.decode(errors="replace"))
        return json.loads(result.output.decode().splitlines()[-1])

    try:
        volume = client.volumes.create(name=volume_name, driver="local", labels=labels)
        first = start_runtime()
        deadline = time.monotonic() + 5
        initial = None
        while time.monotonic() < deadline:
            initial = request(first, "history/pending?limit=10")
            if initial["samples"]:
                break
            time.sleep(0.05)
        if not initial or not initial["samples"]:
            raise AssertionError("SCADA did not enqueue its first sample")
        timestamp = initial["samples"][0]["timestamp"]
        first.remove(force=True)
        containers.remove(first)

        second = start_runtime()
        recovered = request(second, "history/pending?limit=10")
        if timestamp not in [sample["timestamp"] for sample in recovered["samples"]]:
            raise AssertionError("unacknowledged sample did not survive SCADA container recreation")
        unauthorized = second.exec_run(["python", "-c", r'''import json,urllib.error,urllib.request
request=urllib.request.Request('http://127.0.0.1:8766/history/acknowledge',data=json.dumps({'cursor':1}).encode(),headers={'Content-Type':'application/json'})
try:
    urllib.request.urlopen(request,timeout=3)
except urllib.error.HTTPError as error:
    print(error.code)
'''])
        if unauthorized.exit_code or unauthorized.output.strip() != b"403":
            raise AssertionError(f"unauthenticated queue acknowledgement was not rejected: {unauthorized.output!r}")
        request(second, "history/acknowledge", {"cursor": recovered["cursor"]})
        after_ack = request(second, "history/pending?limit=10")
        if after_ack["samples"]:
            raise AssertionError(f"acknowledged samples remain pending: {after_ack}")
        print("PASS: SCADA durable history survived container recreation and cleared after acknowledgement")
        return 0
    finally:
        for container in containers:
            try:
                container.remove(force=True)
            except docker.errors.DockerException:
                pass
        if volume is not None:
            try:
                volume.remove(force=False)
            except docker.errors.DockerException:
                pass
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
