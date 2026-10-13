"""Exercise the configured silent S7 data timeout in an isolated Docker PLC."""

from __future__ import annotations

import argparse
import json
import random
import socket
import time
import uuid

import docker
from snap7.client import Client


def unused_local_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def expect_timeout(host: str, port: int, operation) -> None:
    client = Client()
    try:
        client.connect(host, 0, 1, tcp_port=port)
        client.connection.timeout = 0.3
        client.connection.socket.settimeout(0.3)
        started = time.monotonic()
        try:
            operation(client)
        except Exception as error:
            elapsed = time.monotonic() - started
            if "timeout" not in type(error).__name__.lower() and "timeout" not in str(error).lower():
                raise AssertionError(f"S7 operation failed without a timeout: {error}") from error
            if not 0.2 <= elapsed < 2:
                raise AssertionError(f"Unexpected timeout duration: {elapsed:.3f}s")
            return
        raise AssertionError("S7 server unexpectedly answered an operation in timeout mode")
    finally:
        client.disconnect()


def wait_healthy(container) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        container.reload()
        if container.status == "exited":
            raise AssertionError(f"PLC container exited early: {container.logs().decode(errors='replace')[-2000:]}")
        health = container.attrs.get("State", {}).get("Health", {}).get("Status")
        if health == "healthy":
            return
        time.sleep(0.25)
    raise AssertionError(f"PLC failed to become healthy: {container.logs().decode(errors='replace')[-2000:]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="anvil/plc-s7:s7-fault-smoke")
    args = parser.parse_args()

    client = docker.from_env()
    containers = []
    try:
        image = client.images.get(args.image)
        host_port = unused_local_port()
        config = {
            "plc_id": "s7-fault-smoke", "rack": 0, "slot": 1, "db_size": 16,
            "fault_mode": "timeout",
            "components": [{"type": "valve", "name": "test-valve", "db": 1,
                            "byte_offset": 0, "bit_offset": 0}],
        }
        container = client.containers.run(
            image.id,
            detach=True,
            name=f"anvil-s7-fault-smoke-{uuid.uuid4().hex[:10]}",
            environment={"PLC_CONFIG": json.dumps(config)},
            ports={"102/tcp": ("127.0.0.1", host_port)},
            user="65534:65534",
            read_only=True,
            cap_drop=["ALL"],
            cap_add=["NET_BIND_SERVICE"],
            security_opt=["no-new-privileges:true"],
            pids_limit=64,
            mem_limit="128m",
        )

        containers.append(container)
        wait_healthy(container)
        expect_timeout("127.0.0.1", host_port, lambda plc: plc.db_read(1, 0, 1))
        expect_timeout("127.0.0.1", host_port, lambda plc: plc.db_write(1, 0, bytearray(b"\x01")))
        logs = container.logs().decode(errors="replace")
        if logs.count("S7 injected timeout: suppressing data response") < 2:
            raise AssertionError("PLC logs do not confirm both read and write responses were suppressed")

        probability, seed = 0.45, 2026
        probability_port = unused_local_port()
        probabilistic_config = {**config, "plc_id": "s7-fault-probability-smoke",
                                "fault_probability": probability, "fault_seed": seed}
        probabilistic_container = client.containers.run(
            image.id,
            detach=True,
            name=f"anvil-s7-fault-prob-{uuid.uuid4().hex[:10]}",
            environment={"PLC_CONFIG": json.dumps(probabilistic_config)},
            ports={"102/tcp": ("127.0.0.1", probability_port)},
            user="65534:65534",
            read_only=True,
            cap_drop=["ALL"],
            cap_add=["NET_BIND_SERVICE"],
            security_opt=["no-new-privileges:true"],
            pids_limit=64,
            mem_limit="128m",
        )
        containers.append(probabilistic_container)
        wait_healthy(probabilistic_container)
        expected_rng = random.Random(seed)
        expected = [expected_rng.random() < probability for _ in range(20)]
        observed = []
        for fault_expected in expected:
            probe = Client()
            try:
                probe.connect("127.0.0.1", 0, 1, tcp_port=probability_port)
                probe.connection.timeout = 0.15
                probe.connection.socket.settimeout(0.15)
                try:
                    value = probe.db_read(1, 0, 1)
                    observed.append(False)
                    if fault_expected or value[0] != 0:
                        raise AssertionError(f"Unexpected S7 read result {value!r}; fault_expected={fault_expected}")
                except Exception as error:
                    is_timeout = "timeout" in type(error).__name__.lower() or "timeout" in str(error).lower()
                    observed.append(is_timeout)
                    if not is_timeout or not fault_expected:
                        raise AssertionError(f"Unexpected S7 failure {error}; fault_expected={fault_expected}") from error
            finally:
                probe.disconnect()
        if observed != expected:
            raise AssertionError(f"Seeded S7 timeout pattern differs: {observed!r} != {expected!r}")
        print("PASS: Docker PLC kept session setup available and reproduced seeded S7 timeout probability")
        return 0
    finally:
        for container in containers:
            try:
                container.remove(force=True)
            except docker.errors.NotFound:
                pass
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
