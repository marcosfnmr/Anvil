"""Deploy an exported Compose bundle and probe S7/Modbus through its ingress relays."""

from __future__ import annotations

import argparse
import io
import json
import os
import socket
import subprocess
import tempfile
import zipfile
from ipaddress import ip_network
from pathlib import Path
from uuid import uuid4

import docker


def free_subnet(client: docker.DockerClient) -> str:
    used = [ip_network(entry["Subnet"]) for network in client.networks.list()
            for entry in ((network.attrs.get("IPAM") or {}).get("Config") or []) if entry.get("Subnet")]
    for second in range(180, 251):
        candidate = ip_network(f"10.247.{second}.0/24")
        if all(not candidate.overlaps(item) for item in used):
            return str(candidate)
    raise RuntimeError("No free subnet in the 10.247.180.0/24–10.247.250.0/24 range")


def free_port(excluded: set[int]) -> int:
    for _ in range(50):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        if port not in excluded:
            return port
    raise RuntimeError("Could not select two distinct loopback ports")


def probe_protocols(s7_port: int, modbus_port: int) -> None:
    from pymodbus.client import ModbusTcpClient
    from snap7.client import Client as Snap7Client

    s7 = Snap7Client()
    try:
        s7.connect("127.0.0.1", 0, 1, tcp_port=s7_port)
        if not s7.get_connected():
            raise AssertionError("Snap7 client did not connect through the bundle relay")
        before = s7.db_read(1, 0, 1)[0]
        s7.db_write(1, 0, bytearray([before ^ 1]))
        if s7.db_read(1, 0, 1)[0] != before ^ 1:
            raise AssertionError("S7 DB write/readback failed through Compose ingress")
    finally:
        if s7.get_connected():
            s7.disconnect()

    modbus = ModbusTcpClient("127.0.0.1", port=modbus_port, timeout=3)
    try:
        if not modbus.connect():
            raise AssertionError("PyModbus client did not connect through the bundle relay")
        initial = modbus.read_coils(0, count=1, device_id=1)
        if initial.isError() or initial.bits[0]:
            raise AssertionError(f"Unexpected initial coil response: {initial!r}")
        written = modbus.write_coil(0, True, device_id=1)
        readback = modbus.read_coils(0, count=1, device_id=1)
        if written.isError() or readback.isError() or not readback.bits[0]:
            raise AssertionError(f"Modbus coil write/readback failed: {written!r}; {readback!r}")
    finally:
        modbus.close()


WINDOWS_STDLIB_PROBE = r'''import json, socket, struct, sys
s7_port, modbus_port = map(int, sys.argv[1:3])
def recv_exact(sock, count):
    data = b""
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise AssertionError("protocol response closed before completion")
        data += chunk
    return data
cr = bytes([17,0xe0,0,0,0,1,0,0xc1,2,1,0,0xc2,2,1,1,0xc0,1,0x0a])
with socket.create_connection(("127.0.0.1", s7_port), timeout=4) as s:
    s.settimeout(4)
    s.sendall(b"\x03\x00" + struct.pack(">H", len(cr) + 4) + cr)
    header = recv_exact(s, 4)
    if len(header) != 4 or header[0] != 3:
        raise AssertionError(f"invalid S7 TPKT response: {header!r}")
    size = struct.unpack(">H", header[2:])[0]
    body = recv_exact(s, size - 4)
    if len(body) < 2 or body[1] != 0xd0:
        raise AssertionError(f"S7 COTP connection was not confirmed: {body.hex()}")
def modbus(sock, transaction, function, data):
    pdu = bytes([function]) + data
    request = struct.pack(">HHHB", transaction, 0, len(pdu) + 1, 1) + pdu
    sock.sendall(request)
    header = recv_exact(sock, 6)
    size = 6 + struct.unpack(">H", header[4:6])[0]
    response = header + recv_exact(sock, size - 6)
    if response[7] == function | 0x80:
        raise AssertionError(f"Modbus exception response: {response.hex()}")
    if response[7] != function:
        raise AssertionError(f"unexpected Modbus response: {response.hex()}")
    return response
with socket.create_connection(("127.0.0.1", modbus_port), timeout=4) as s:
    s.settimeout(4)
    initial = modbus(s, 1, 1, b"\x00\x00\x00\x01")
    initial_value = bool(initial[9] & 1)
    written = modbus(s, 2, 5, b"\x00\x00\x00\x00")
    if written[7:] != b"\x05\x00\x00\x00\x00":
        raise AssertionError(f"coil false-write acknowledgement mismatch: {written.hex()}")
    readback = modbus(s, 3, 1, b"\x00\x00\x00\x01")
    if readback[9] & 1:
        raise AssertionError(f"coil false write/readback failed: {readback.hex()}")
    written = modbus(s, 3, 5, b"\x00\x00\xff\x00")
    if written[7:] != b"\x05\x00\x00\xff\x00":
        raise AssertionError(f"coil write acknowledgement mismatch: {written.hex()}")
    readback = modbus(s, 4, 1, b"\x00\x00\x00\x01")
    if not readback[9] & 1:
        raise AssertionError(f"coil true write/readback failed: {readback.hex()}")
print(json.dumps({"windows_s7": "COTP handshake passed", "windows_modbus": "coil false/true writes and readbacks passed", "initial_coil": initial_value}))
'''


def probe_windows(python_executable: str, s7_port: int, modbus_port: int) -> dict[str, str]:
    try:
        result = subprocess.run(
            [python_executable, "-c", WINDOWS_STDLIB_PROBE, str(s7_port), str(modbus_port)],
            check=True, capture_output=True, text=True, timeout=20,
        )
    except subprocess.CalledProcessError as error:
        details = (error.stderr or error.stdout or "Windows probe exited without output").strip()
        raise RuntimeError(f"Windows protocol probe failed: {details}") from error
    return json.loads(result.stdout.strip().splitlines()[-1])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plc-image", default=os.environ.get("ANVIL_PLC_IMAGE", ""))
    parser.add_argument("--modbus-image", default=os.environ.get("ANVIL_MODBUS_IMAGE", ""))
    parser.add_argument("--ingress-image", default=os.environ.get("ANVIL_INGRESS_IMAGE", ""))
    args = parser.parse_args()
    images = {"ANVIL_PLC_IMAGE": args.plc_image,
              "ANVIL_MODBUS_IMAGE": args.modbus_image,
              "ANVIL_INGRESS_IMAGE": args.ingress_image}
    missing = [key for key, value in images.items() if not value]
    if missing:
        raise SystemExit("Set local runtime image references: " + ", ".join(missing))
    os.environ.update(images)

    from models.scenario import ScenarioCreate
    from services.compose_export import build_compose_bundle
    from services.scenario_service import ScenarioService

    client = docker.from_env()
    scenario_id = f"bundle-{uuid4()}"
    project = f"anvil-ingress-{uuid4().hex[:10]}"
    directory: tempfile.TemporaryDirectory | None = None
    compose_file: Path | None = None
    try:
        for image in images.values():
            client.images.get(image)
        subnet = ip_network(free_subnet(client))
        plc_port = free_port(set())
        modbus_port = free_port({plc_port})
        scenario = ScenarioCreate.model_validate({
            "name": "External bundle protocol smoke",
            "network": {"subnet": str(subnet)},
            "devices": [
                {"node_id": "bundle-s7", "device_type": "plc-s7", "name": "Bundle S7",
                 "ip": str(subnet.network_address + 10), "external_access": True,
                 "external_host_ip": "127.0.0.1", "external_port": plc_port,
                 "config": {"components": [{"type": "valve", "name": "smoke", "initial_state": False}]}},
                {"node_id": "bundle-modbus", "device_type": "modbus-tcp", "name": "Bundle Modbus",
                 "ip": str(subnet.network_address + 11), "external_access": True,
                 "external_host_ip": "127.0.0.1", "external_port": modbus_port,
                 "config": {"unit_id": 1, "port": 1502, "points": [
                     {"type": "coil", "name": "smoke", "address": 0, "initial_value": False}]}},
            ],
        })
        runtime = ScenarioService._build_runtime_devices(scenario)
        bundle = build_compose_bundle(scenario_id, scenario, runtime)
        directory = tempfile.TemporaryDirectory(prefix="anvil-external-bundle-smoke-")
        path = Path(directory.name)
        with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
            archive.extractall(path)
        compose_file = path / "docker-compose.yml"
        subprocess.run(["docker", "compose", "-p", project, "-f", str(compose_file), "config", "--quiet"],
                       cwd=path, check=True, capture_output=True, text=True, timeout=30)
        try:
            subprocess.run(["docker", "compose", "-p", project, "-f", str(compose_file), "up", "-d", "--wait", "--wait-timeout", "60"],
                           cwd=path, check=True, capture_output=True, text=True, timeout=90)
        except subprocess.CalledProcessError as error:
            detail = (error.stderr or error.stdout or "no Compose output").strip()
            raise RuntimeError(f"Compose bundle deployment failed: {detail}") from error

        relays = client.containers.list(filters={"label": [f"anvil.bundle_scenario_id={scenario_id}",
                                                            "anvil.role=protocol-ingress"]})
        if len(relays) != 2:
            raise AssertionError(f"Expected two ingress relays; found {len(relays)}")
        targets = client.containers.list(filters={"label": [f"anvil.bundle_scenario_id={scenario_id}",
                                                             "anvil.bundle=true"]})
        by_node = {item.labels.get("anvil.bundle_node_id"): item for item in targets}
        if set(by_node) != {"bundle-s7", "bundle-modbus", "__anvil_ingress_bundle-s7", "__anvil_ingress_bundle-modbus"}:
            raise AssertionError(f"Unexpected bundle services: {sorted(by_node)}")
        for node_id in ("bundle-s7", "bundle-modbus"):
            by_node[node_id].reload()
            if by_node[node_id].attrs["HostConfig"].get("PortBindings"):
                raise AssertionError(f"Protocol target {node_id} published a host port directly")
        for relay in relays:
            relay.reload()
            bindings = relay.attrs["HostConfig"].get("PortBindings") or {}
            if len(bindings) != 1 or any(item.get("HostIp") != "127.0.0.1" for values in bindings.values() for item in values):
                raise AssertionError(f"Ingress relay does not have a loopback-only binding: {bindings}")
            networks = relay.attrs["NetworkSettings"]["Networks"]
            if len(networks) != 2 or not any(name.endswith("_host_bridge") for name in networks):
                raise AssertionError(f"Ingress relay is not attached to project uplink plus one OT zone: {networks}")

        relay = relays[0]
        relay_network_name = next(name for name in relay.attrs["NetworkSettings"]["Networks"]
                                  if name.endswith("_host_bridge"))
        relay_ip = relay.attrs["NetworkSettings"]["Networks"][relay_network_name]["IPAddress"]
        denied_peer = subprocess.run(
            ["docker", "run", "--rm", "--network", relay_network_name,
             "--entrypoint", "python", args.ingress_image, "-c",
             "import socket,sys; s=socket.create_connection((sys.argv[1],25000),timeout=2); s.settimeout(2); data=s.recv(1); s.close(); sys.exit(0 if data == b'' else 1)",
             relay_ip], check=False, capture_output=True, text=True, timeout=20)
        if denied_peer.returncode:
            raise AssertionError(f"A peer container on the uplink was not rejected by the relay: {denied_peer.stderr or denied_peer.stdout}")

        probe_protocols(plc_port, modbus_port)
        windows_result = None
        if os.environ.get("ANVIL_WINDOWS_PYTHON"):
            windows_result = probe_windows(os.environ["ANVIL_WINDOWS_PYTHON"], plc_port, modbus_port)
        print(json.dumps({
            "status": "passed",
            "linux_host": {"s7": "DB read/write passed", "modbus": "coil read/write passed"},
            "windows_host": windows_result,
            "security": {"target_ports_private": True, "unauthorized_uplink_peer_rejected": True,
                         "host_binding": "127.0.0.1"},
        }))
        return 0
    finally:
        if compose_file and compose_file.exists():
            subprocess.run(["docker", "compose", "-p", project, "-f", str(compose_file), "down", "--remove-orphans"],
                           cwd=directory.name if directory else None, check=False,
                           capture_output=True, text=True, timeout=60)
        if directory:
            directory.cleanup()
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
