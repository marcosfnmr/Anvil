"""Exercise Modbus audit persistence across container recreation with Docker."""

from __future__ import annotations

import json
import os
import secrets
import sys
import time

import docker
from docker.errors import DockerException
from pymodbus.client import ModbusTcpClient


IMAGE = os.environ.get("ANVIL_MODBUS_IMAGE", "anvil/modbus-tcp:rf35-audit-check")
ACK_TOKEN = secrets.token_urlsafe(32)


def wait_for_modbus(container, timeout: float = 15.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        container.reload()
        if container.status != "running":
            raise RuntimeError(f"Modbus runtime exited: {container.logs(tail=100).decode(errors='replace')}")
        mapping = container.attrs.get("NetworkSettings", {}).get("Ports", {}).get("502/tcp") or []
        if mapping:
            host_port = int(mapping[0]["HostPort"])
            probe = ModbusTcpClient("127.0.0.1", port=host_port, timeout=0.5)
            try:
                if probe.connect():
                    response = probe.read_coils(0, count=1, device_id=17)
                    if response is not None and not response.isError():
                        return host_port
            except OSError:
                pass
            finally:
                probe.close()
        time.sleep(0.1)
    raise TimeoutError("Modbus TCP did not become reachable on its loopback-only test port")


def queue_payload(container) -> dict:
    result = container.exec_run(
        ["python", "protocol_audit.py", "pending", "1000"],
        user="65534:65534", workdir="/app", stdout=True, stderr=True,
    )
    output = result.output.decode("utf-8", errors="replace") if isinstance(result.output, bytes) else str(result.output)
    if result.exit_code:
        raise RuntimeError(f"Could not read durable protocol queue: {output}")
    return json.loads(output.strip().splitlines()[-1])


def main() -> int:
    client = docker.from_env()
    suffix = secrets.token_hex(5)
    container_name = f"anvil-rf35-audit-{suffix}"
    volume_name = f"anvil-rf35-audit-{suffix}"
    volume = container = None
    config = {
        "unit_id": 17,
        "port": 502,
        "points": [
            {"type": "coil", "name": "pump-command", "address": 0, "initial_value": False},
            {"type": "holding_register", "name": "speed-command", "address": 4, "initial_value": 100},
        ],
    }

    def create_runtime():
        return client.containers.run(
            IMAGE,
            detach=True,
            name=container_name,
            environment={
                "MODBUS_CONFIG": json.dumps(config, separators=(",", ":")),
                "ANVIL_PROTOCOL_AUDIT_ACK_TOKEN": ACK_TOKEN,
            },
            ports={"502/tcp": ("127.0.0.1", None)},
            volumes={volume_name: {"bind": "/var/lib/anvil/protocol-audit", "mode": "rw"}},
            user="65534:65534",
            read_only=True,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            mem_limit="128m",
            nano_cpus=500_000_000,
            pids_limit=32,
        )

    try:
        volume = client.volumes.create(name=volume_name, driver="local")
        container = create_runtime()
        port = wait_for_modbus(container)
        protocol_client = ModbusTcpClient("127.0.0.1", port=port, timeout=2)
        try:
            if not protocol_client.connect():
                raise RuntimeError("Could not connect to the temporary Modbus runtime")
            response = protocol_client.write_coil(0, True, device_id=17)
            if response.isError():
                raise AssertionError(f"coil write failed: {response}")
            response = protocol_client.write_register(4, 4321, device_id=17)
            if response.isError():
                raise AssertionError(f"holding register write failed: {response}")
        finally:
            protocol_client.close()

        container.stop(timeout=5)
        container.remove()
        container = create_runtime()
        wait_for_modbus(container)
        pending = queue_payload(container)
        lines = [item["line"] for item in pending["events"]]
        assert pending["pending_count"] == 2, pending
        assert any("operation=write_coil" in line and "address=0" in line for line in lines), lines
        assert any("operation=write_holding_register" in line and "address=4" in line for line in lines), lines
        assert all("4321" not in line for line in lines), "protocol audit queue must not store written values"

        ack = container.exec_run(
            ["python", "protocol_audit.py", "ack", str(pending["cursor"])],
            user="65534:65534", workdir="/app", stdout=True, stderr=True,
            environment={"ANVIL_PROTOCOL_AUDIT_EXEC_TOKEN": ACK_TOKEN},
        )
        if ack.exit_code:
            raise RuntimeError(f"Queue acknowledgement failed: {ack.output!r}")
        assert queue_payload(container)["pending_count"] == 0
        print("RF-35 durable queue OK: real Modbus TCP writes survived container recreation; values absent; ack drained the batch")
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
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (DockerException, OSError, ValueError, AssertionError, RuntimeError, TimeoutError) as error:
        print(f"RF-35 durable queue smoke failed: {error}", file=sys.stderr)
        raise SystemExit(1)
