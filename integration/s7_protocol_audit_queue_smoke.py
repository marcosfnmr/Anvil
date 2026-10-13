"""Verify S7 write metadata survives PLC container recreation in its volume."""

from __future__ import annotations

import json
import os
import secrets
import sys
import time

import docker
from docker.errors import DockerException
from snap7.client import Client


IMAGE = os.environ.get("ANVIL_PLC_IMAGE", "anvil/plc-s7:rf35-audit-check")
ACK_TOKEN = secrets.token_urlsafe(32)


def read_queue(container) -> dict:
    result = container.exec_run(
        ["python", "protocol_audit.py", "pending", "1000"],
        user="65534:65534", workdir="/app", stdout=True, stderr=True,
    )
    output = result.output.decode("utf-8", errors="replace") if isinstance(result.output, bytes) else str(result.output)
    if result.exit_code:
        raise RuntimeError(f"Could not read durable S7 audit queue: {output}")
    return json.loads(output.strip().splitlines()[-1])


def connect(host_port: int, timeout_seconds: float = 20) -> Client:
    deadline = time.monotonic() + timeout_seconds
    last_error = None
    while time.monotonic() < deadline:
        client = Client()
        try:
            client.connect("127.0.0.1", 0, 1, tcp_port=host_port)
            if client.get_connected():
                return client
        except Exception as error:
            last_error = error
        try:
            client.disconnect()
        except Exception:
            pass
        time.sleep(0.2)
    raise TimeoutError(f"S7 server did not start: {last_error}")


def main() -> int:
    engine = docker.from_env()
    suffix = secrets.token_hex(5)
    container_name = f"anvil-rf35-s7-audit-{suffix}"
    volume_name = f"anvil-rf35-s7-audit-{suffix}"
    token = ACK_TOKEN
    config = {
        "plc_id": "audit-plc", "rack": 0, "slot": 1, "db_size": 8,
        "components": [{"type": "valve", "name": "audit-valve", "db": 1,
                        "byte_offset": 0, "bit_offset": 0, "initial_state": False}],
    }
    volume = container = None

    def create_runtime():
        return engine.containers.run(
            IMAGE, detach=True, name=container_name,
            environment={"PLC_CONFIG": json.dumps(config, separators=(",", ":")),
                         "ANVIL_PROTOCOL_AUDIT_ACK_TOKEN": token},
            ports={"102/tcp": ("127.0.0.1", None)},
            volumes={volume_name: {"bind": "/var/lib/anvil/protocol-audit", "mode": "rw"}},
            user="65534:65534", read_only=True, cap_drop=["ALL"],
            cap_add=["NET_BIND_SERVICE"], security_opt=["no-new-privileges:true"],
            mem_limit="128m", nano_cpus=500_000_000, pids_limit=32,
        )

    try:
        volume = engine.volumes.create(name=volume_name, driver="local")
        container = create_runtime()
        container.reload()
        host_port = int(container.attrs["NetworkSettings"]["Ports"]["102/tcp"][0]["HostPort"])
        client = connect(host_port)
        try:
            initial = client.db_read(1, 0, 1)
            client.db_write(1, 0, bytes([initial[0] ^ 1]))
            client.db_read(1, 0, 1)
        finally:
            client.disconnect()

        container.stop(timeout=5)
        container.remove()
        container = create_runtime()
        container.reload()
        host_port = int(container.attrs["NetworkSettings"]["Ports"]["102/tcp"][0]["HostPort"])
        client = connect(host_port)
        try:
            client.db_read(1, 0, 1)
        finally:
            client.disconnect()
        pending = read_queue(container)
        lines = [item["line"] for item in pending["events"]]
        assert pending["pending_count"] >= 1, pending
        assert any("operation=write" in line and "area=DB db=1 byte_offset=0" in line for line in lines), lines
        assert all("data=" not in line and "raw_hex" not in line for line in lines), lines
        ack = container.exec_run(
            ["python", "protocol_audit.py", "ack", str(pending["cursor"])],
            user="65534:65534", workdir="/app", stdout=True, stderr=True,
            environment={"ANVIL_PROTOCOL_AUDIT_EXEC_TOKEN": token},
        )
        if ack.exit_code:
            raise RuntimeError(f"S7 queue acknowledgement failed: {ack.output!r}")
        assert read_queue(container)["pending_count"] == 0
        print("RF-35 durable queue OK: real S7 write survived PLC container recreation; process bytes absent; ack drained the batch")
        return 0
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except DockerException:
                pass
        if volume is not None:
            try:
                volume.remove(force=True)
            except DockerException:
                pass
        engine.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (DockerException, OSError, ValueError, AssertionError, RuntimeError, TimeoutError) as error:
        print(f"RF-35 S7 durable queue smoke failed: {error}", file=sys.stderr)
        raise SystemExit(1)
