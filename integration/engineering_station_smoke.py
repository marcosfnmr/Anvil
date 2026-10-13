"""Verify an isolated-by-default engineering host and its opt-in zone access."""
from __future__ import annotations

import os
import socket
import time
import uuid
from ipaddress import ip_network

import docker
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from main import create_app
from models.db import get_session
from routes.scenarios import get_docker_manager
from services.docker_manager import DockerManager


def free_subnet(client):
    used = [entry["Subnet"] for network in client.networks.list(filters={"label": "anvil.managed=true"})
            for entry in (network.attrs.get("IPAM") or {}).get("Config") or [] if entry.get("Subnet")]
    for third in range(170, 240):
        candidate = ip_network(f"10.248.{third}.0/24")
        if all(not candidate.overlaps(ip_network(item)) for item in used):
            return candidate
    raise RuntimeError("No free integration subnet in 10.248.170.0/24–10.248.239.0/24")


def main() -> None:
    required = ("ANVIL_PLC_IMAGE", "ANVIL_ENGINEERING_IMAGE")
    if not all(os.environ.get(name) for name in required):
        raise RuntimeError("Set ANVIL_PLC_IMAGE and ANVIL_ENGINEERING_IMAGE to local images")

    docker_client = docker.from_env()
    manager = DockerManager(client=docker_client)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    app = create_app(initialize_database=False)

    def session_override():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_docker_manager] = lambda: manager
    scenario_id = None
    try:
        subnet = free_subnet(docker_client)
        with TestClient(app) as api:
            created = api.post("/api/scenarios", json={
                "name": "Engineering station access smoke",
                "network": {"subnet": str(subnet)},
                "devices": [
                    {"node_id": "plc", "device_type": "plc-s7", "name": "S7 PLC",
                     "ip": str(subnet.network_address + 10), "config": {"components": [
                         {"type": "valve", "name": "S7 reachability probe"},
                     ]}},
                    {"node_id": "engineer-off", "device_type": "engineering-station", "name": "Isolated workstation",
                     "ip": str(subnet.network_address + 11), "config": {"role": "engineering", "summary": "Disabled by default"}},
                    {"node_id": "engineer-on", "device_type": "engineering-station", "name": "Engineering workstation",
                     "ip": str(subnet.network_address + 12), "config": {"role": "engineering", "summary": "Opt-in zone host",
                                                                          "engineering_access_enabled": True}},
                ],
            })
            if created.status_code != 201:
                raise AssertionError(f"scenario creation failed: {created.status_code} {created.text}")
            scenario_id = created.json()["id"]
            deployed = api.post(f"/api/scenarios/{scenario_id}/deploy")
            if deployed.status_code != 200 or deployed.json().get("status") != "running":
                raise AssertionError(f"scenario deployment failed: {deployed.status_code} {deployed.text}")

            containers = {item.labels["anvil.node_id"]: item for item in manager._scenario_containers(scenario_id)}
            isolated = containers["engineer-off"]
            isolated.reload()
            isolated_networks = isolated.attrs["NetworkSettings"].get("Networks") or {}
            if isolated.attrs["HostConfig"].get("NetworkMode") != "none" or any(
                name != "none" and value.get("IPAddress") for name, value in isolated_networks.items()
            ):
                raise AssertionError("engineering station without opt-in received network access")
            connected = containers["engineer-on"]
            connected.reload()
            networks = connected.attrs["NetworkSettings"].get("Networks") or {}
            if len(networks) != 1 or next(iter(networks.values())).get("IPAddress") != str(subnet.network_address + 12):
                raise AssertionError(f"opted-in station was not attached only to its scenario zone: {networks}")
            for node_id, container in containers.items():
                container.reload()
                if container.attrs["HostConfig"].get("PortBindings"):
                    raise AssertionError(f"scenario published host ports for {node_id}")

            probe = """from snap7.client import Client
c=Client(); c.connect('172.30.0.10',0,1,tcp_port=102)
try:
 raw=c.db_read(1,0,1)
 assert len(raw)==1
 print('S7 read OK')
finally: c.disconnect()
""".replace("172.30.0.10", str(subnet.network_address + 10))
            result = connected.exec_run(["python", "-c", probe])
            if result.exit_code != 0 or b"S7 read OK" not in result.output:
                raise AssertionError(f"engineering station could not reach its zone PLC over S7: {result.output!r}")

            exec_id, terminal = manager.open_engineering_terminal(scenario_id, "engineer-on")
            try:
                terminal.sendall(b"id -u\n")
                output = bytearray()
                deadline = time.monotonic() + 5
                while b"65534" not in output and time.monotonic() < deadline:
                    try:
                        output.extend(terminal.recv(4096))
                    except socket.timeout:
                        continue
                if b"65534" not in output:
                    raise AssertionError(f"engineering terminal did not run as the unprivileged user: {output!r}")
            finally:
                terminal.close()
                manager.close_participant_terminal(exec_id)
            print("PASS: engineering host is isolated by default; opt-in attaches only its zone, reaches S7, opens a UID 65534 terminal, and publishes no host ports")
    finally:
        if scenario_id:
            try:
                manager.stop(scenario_id)
            except Exception:
                pass
        engine.dispose()
        docker_client.close()


if __name__ == "__main__":
    main()
