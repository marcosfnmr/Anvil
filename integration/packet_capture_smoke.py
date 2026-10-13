"""Capture filtered inter-PLC S7 traffic and validate the exported evidence PCAP."""
from __future__ import annotations

import io
import json
import os
import struct
import time
import zipfile
from ipaddress import ip_network

import docker
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from main import create_app
from models.db import get_session
from routes.scenarios import get_docker_manager
from services.docker_manager import DockerManager


def pcap_tcp_observations(payload: bytes) -> tuple[int, list[tuple[int, int]], set[str]]:
    magic = payload[:4]
    endian = "<" if magic in {b"\xd4\xc3\xb2\xa1", b"\x4d\x3c\xb2\xa1"} else ">"
    link_type = struct.unpack_from(endian + "I", payload, 20)[0]
    offset = 24
    ports: list[tuple[int, int]] = []
    hosts: set[str] = set()
    while offset + 16 <= len(payload):
        _seconds, _fraction, length, _original = struct.unpack_from(endian + "IIII", payload, offset)
        offset += 16
        if length > len(payload) - offset:
            raise AssertionError("PCAP packet record is truncated")
        packet = payload[offset:offset + length]
        offset += length
        if link_type == 276:  # Linux cooked v2, emitted by tcpdump -i any.
            network_offset = 20
        elif link_type == 113:  # Linux cooked v1.
            network_offset = 16
        elif link_type == 1:  # Ethernet.
            network_offset = 14
        elif link_type in {101, 228, 229}:  # Raw IP, IPv4, IPv6.
            network_offset = 0
        else:
            continue
        if len(packet) <= network_offset:
            continue
        version = packet[network_offset] >> 4
        if version != 4:
            continue
        hosts.add(".".join(str(part) for part in packet[network_offset + 12:network_offset + 16]))
        hosts.add(".".join(str(part) for part in packet[network_offset + 16:network_offset + 20]))
        header_length = (packet[network_offset] & 0x0F) * 4
        if packet[network_offset + 9] != 6 or len(packet) < network_offset + header_length + 4:
            continue
        ports.append(struct.unpack_from("!HH", packet, network_offset + header_length))
    return link_type, ports, hosts


def main() -> None:
    if not os.environ.get("ANVIL_PLC_IMAGE") or not os.environ.get("ANVIL_CAPTURE_IMAGE"):
        raise RuntimeError("Set ANVIL_PLC_IMAGE and ANVIL_CAPTURE_IMAGE to local images")
    client = docker.from_env()
    manager = DockerManager(client=client)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    app = create_app(initialize_database=False)

    def session_override():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_docker_manager] = lambda: manager
    scenario_id = None
    used = [entry["Subnet"] for network in client.networks.list(filters={"label": "anvil.managed=true"})
            for entry in (network.attrs.get("IPAM") or {}).get("Config") or [] if entry.get("Subnet")]
    subnet = next((ip_network(f"10.247.{third}.0/24") for third in range(20, 220)
                   if all(not ip_network(existing).overlaps(ip_network(f"10.247.{third}.0/24")) for existing in used)), None)
    if subnet is None:
        raise RuntimeError("No free subnet in the packet capture smoke range")
    try:
        with TestClient(app) as api:
            created = api.post("/api/scenarios", json={
                "name": "S7 packet capture smoke",
                "network": {"subnet": str(subnet)},
                "devices": [{
                    "node_id": "plc", "device_type": "plc-s7", "name": "PLC capture test",
                    "ip": str(subnet.network_address + 10),
                    "config": {"db_size": 32, "components": [{"type": "valve", "name": "Valve"}]},
                }, {
                    "node_id": "client", "device_type": "plc-s7", "name": "PLC traffic generator",
                    "ip": str(subnet.network_address + 11),
                    "config": {"db_size": 32, "components": [{
                        "type": "remote_input", "name": "captured_source",
                        "source_node_id": "plc", "source_address": "DB1.DBX0.0",
                        "data_type": "BOOL", "period_ms": 100,
                        "timeout_ms": 500, "stale_after_ms": 1000,
                    }]},
                }],
            })
            if created.status_code != 201:
                raise AssertionError(f"scenario creation failed: {created.status_code} {created.text}")
            scenario_id = created.json()["id"]
            deployed = api.post(f"/api/scenarios/{scenario_id}/deploy")
            if deployed.status_code != 200:
                raise AssertionError(f"scenario deployment failed: {deployed.status_code} {deployed.text}")
            started = api.post(f"/api/scenarios/{scenario_id}/captures", json={
                "link_source_node_id": "plc", "link_target_node_id": "client",
            })
            if started.status_code != 202:
                raise AssertionError(f"capture start failed: {started.status_code} {started.text}")
            if started.json().get("scope") != "link" or started.json().get("target_node_id") != "client":
                raise AssertionError(f"API did not start a pair-specific link capture: {started.json()}")

            time.sleep(1.5)  # The receiver PLC polls the source over S7 every 100 ms.
            capture_id = started.json()["capture_id"]
            exported = api.get(f"/api/scenarios/{scenario_id}/evidence-bundle.zip?capture_id={capture_id}")
            if exported.status_code != 200:
                raise AssertionError(f"Evidence bundle export failed: {exported.status_code} {exported.text}")
            with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
                names = set(archive.namelist())
                expected_capture = f"captures/{capture_id}.pcap"
                if not {"manifest.json", "audit/events.jsonl", "logs/containers.json", expected_capture}.issubset(names):
                    raise AssertionError(f"Evidence archive is missing files: {names}")
                manifest = json.loads(archive.read("manifest.json"))
                if manifest["scenario_id"] != scenario_id:
                    raise AssertionError(f"Evidence manifest belongs to another scenario: {manifest}")
                event_types = {json.loads(line)["event_type"] for line in archive.read("audit/events.jsonl").decode().splitlines()}
                if "network.packet_capture_exported" not in event_types:
                    raise AssertionError("Evidence audit does not record the included PCAP export")
                payload = archive.read(expected_capture)
            if len(payload) < 40 or payload[:4] not in {b"\xd4\xc3\xb2\xa1", b"\xa1\xb2\xc3\xd4", b"\x4d\x3c\xb2\xa1", b"\xa1\xb2\x3c\x4d"}:
                raise AssertionError(f"Export is not a valid packet capture: {len(payload)} bytes, magic={payload[:4].hex()}")
            link_type, ports, hosts = pcap_tcp_observations(payload)
            if not any(102 in pair for pair in ports):
                raise AssertionError(f"PCAP contains no captured S7 TCP/102 packets; link_type={link_type}, ports={ports}")
            peer_ip = next(container for container in manager._scenario_containers(scenario_id)
                           if container.labels.get("anvil.node_id") == "client").attrs["NetworkSettings"]["Networks"]
            peer_addresses = {network.get("IPAddress") for network in peer_ip.values() if network.get("IPAddress")}
            if not hosts.intersection(peer_addresses):
                raise AssertionError(f"PCAP contains no packets for the selected peer; peer_addresses={peer_addresses}, hosts={hosts}")
            capture_containers = [container for container in manager._scenario_containers(scenario_id)
                                  if container.labels.get("anvil.role") == "packet-capture"]
            if capture_containers:
                raise AssertionError("Capture sidecar was not removed after export")
            print(f"PASS: pair capture includes peer IP and TCP/102 in {len(payload)} PCAP bytes; evidence audit exported and sidecar removed")
    finally:
        if scenario_id:
            try:
                manager.stop(scenario_id)
            except Exception:
                pass
        engine.dispose()
        client.close()


if __name__ == "__main__":
    main()
