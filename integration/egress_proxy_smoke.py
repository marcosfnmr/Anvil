"""Check allowlisted and denied HTTP CONNECT requests in isolated Docker networks."""

from __future__ import annotations

import os
import uuid
from ipaddress import ip_network

import docker
from docker.types import IPAMConfig, IPAMPool

PROXY_IMAGE = os.environ.get("ANVIL_EGRESS_IMAGE", "anvil/egress-proxy:ci")
FIELD_IMAGE = os.environ.get("ANVIL_FIELD_IMAGE", "anvil/field:srs-v2")


def _unused_subnet(client: docker.DockerClient) -> str:
    used = [
        entry.get("Subnet")
        for network in client.networks.list()
        for entry in (network.attrs.get("IPAM") or {}).get("Config") or []
        if entry.get("Subnet")
    ]
    for third in range(190, 254):
        candidate = f"10.248.{third}.0/24"
        if all(not ip_network(candidate).overlaps(ip_network(value)) for value in used):
            return candidate
    raise RuntimeError("No free subnet in 10.248.190.0/24–10.248.253.0/24")


def main() -> None:
    client = docker.from_env()
    client.ping()
    suffix = uuid.uuid4().hex[:10]
    scenario_id = str(uuid.uuid4())
    subnet = _unused_subnet(client)
    addresses = list(ip_network(subnet).hosts())
    station_ip, proxy_ip, plc_ip = (str(value) for value in addresses[1:4])
    network = None
    proxy = station = plc = None
    try:
        network = client.networks.create(
            f"anvil-egress-{suffix}", driver="bridge", internal=True,
            options={"com.docker.network.bridge.gateway_mode_ipv4": "isolated"},
            ipam=IPAMConfig(pool_configs=[IPAMPool(subnet=subnet)]),
            labels={"anvil.managed": "true", "anvil.scenario_id": scenario_id, "anvil.zone_id": "default"},
        )
        proxy = client.containers.create(
            PROXY_IMAGE, name=f"anvil-egress-proxy-{suffix}",
            command=["python", "-u", "egress_proxy.py"], detach=True,
            environment={"ANVIL_EGRESS_CONFIG": (
                f'{{"listen_ip":"{proxy_ip}","source_ip":"{station_ip}",'
                '"rules":[{"hostname":"docs.python.org","tcp_port":443}]}'
            )}, network=network.name,
            networking_config={network.name: client.api.create_endpoint_config(ipv4_address=proxy_ip)},
            healthcheck={"test": ["NONE"]}, user="65534:65534", read_only=True,
            cap_drop=["ALL"], security_opt=["no-new-privileges:true"],
            labels={"anvil.integration": "egress-proxy-smoke"},
        )
        client.api.connect_container_to_network(proxy.id, "bridge")
        station = client.containers.create(
            FIELD_IMAGE, name=f"anvil-egress-station-{suffix}", detach=True,
            command=["python", "-c", "import time; time.sleep(60)"], network=network.name,
            networking_config={network.name: client.api.create_endpoint_config(ipv4_address=station_ip)},
            healthcheck={"test": ["NONE"]}, labels={"anvil.integration": "egress-proxy-smoke"},
        )
        plc = client.containers.create(
            FIELD_IMAGE, name=f"anvil-egress-plc-{suffix}", detach=True,
            command=["python", "-c", "import time; time.sleep(60)"], network=network.name,
            networking_config={network.name: client.api.create_endpoint_config(ipv4_address=plc_ip)},
            healthcheck={"test": ["NONE"]}, labels={"anvil.integration": "egress-proxy-smoke"},
        )
        for container in (proxy, station, plc):
            container.start()

        def connect_test(container, host: str, proxy_port: int, destination_port: int, expected: bytes) -> None:
            code = (
                "import socket; s=socket.create_connection((" + repr(host) + "," + str(proxy_port) + "),5); "
                "s.sendall(b'CONNECT docs.python.org:" + str(destination_port) + " HTTP/1.1\\r\\nHost: docs.python.org:" + str(destination_port)
                + "\\r\\n\\r\\n'); r=s.recv(4096); assert " + repr(expected) + " in r,r; s.close()"
            )
            result = container.exec_run(["python", "-c", code])
            if result.exit_code != 0:
                proxy.reload()
                container.reload()
                raise AssertionError({
                    "output": result.output.decode(errors="replace"),
                    "proxy_state": proxy.attrs.get("State"),
                    "proxy_logs": proxy.logs().decode(errors="replace"),
                    "proxy_networks": proxy.attrs.get("NetworkSettings", {}).get("Networks"),
                    "client_networks": container.attrs.get("NetworkSettings", {}).get("Networks"),
                })

        connect_test(station, proxy_ip, 3128, 443, b"200 Connection Established")
        denied_code = (
            "import socket; s=socket.create_connection((" + repr(proxy_ip) + ",3128),5); "
            "s.sendall(b'CONNECT docs.python.org:444 HTTP/1.1\\r\\nHost: docs.python.org:444\\r\\n\\r\\n'); "
            "r=s.recv(4096); assert b'403 Forbidden' in r,r; s.close()"
        )
        denied = station.exec_run(["python", "-c", denied_code])
        assert denied.exit_code == 0, denied.output
        direct = plc.exec_run(["python", "-c", (
            "import socket; s=socket.socket(); s.settimeout(3); "
            "\ntry: s.connect(('1.1.1.1',443))\n"
            "except OSError: raise SystemExit(0)\n"
            "else: raise SystemExit('unproxied egress unexpectedly worked')"
        )])
        assert direct.exit_code == 0, direct.output
        print("PASS: allowlisted public CONNECT succeeds; unlisted port and direct PLC egress are blocked")
    finally:
        for container in (plc, station, proxy):
            if container is not None:
                try:
                    container.remove(force=True)
                except docker.errors.NotFound:
                    pass
        if network is not None:
            try:
                network.remove()
            except docker.errors.NotFound:
                pass
        client.close()


if __name__ == "__main__":
    main()
