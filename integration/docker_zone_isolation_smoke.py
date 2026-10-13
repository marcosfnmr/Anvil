"""Verify same-zone reachability and isolation across zones and scenarios."""
from __future__ import annotations

import os
import time
import uuid

import docker
from docker.types import IPAMConfig, IPAMPool

IMAGE = os.environ.get("ANVIL_ZONE_TEST_IMAGE", "anvil/field:srs-v2")
PORT = 25000


def run_client(client, network_name: str, source_ip: str, target_ip: str, expect_reachable: bool):
    check = (
        "import socket,sys; s=socket.socket(); s.settimeout(1.5); "
        "\ntry: s.connect((sys.argv[1],int(sys.argv[2]))); ok=True "
        "\nexcept OSError: ok=False "
        "\nfinally: s.close() "
        "\nprint('reachable' if ok else 'blocked'); sys.exit(0 if ok is "+str(expect_reachable)+" else 2)"
    )
    endpoint = client.api.create_endpoint_config(ipv4_address=source_ip)
    container = client.containers.create(
        IMAGE,
        command=["python", "-c", check, target_ip, str(PORT)],
        name=f"anvil-zone-check-{uuid.uuid4().hex[:10]}",
        network=network_name,
        networking_config={network_name: endpoint},
        labels={"anvil.integration": "zone-isolation"},
    )
    try:
        container.start()
        result = container.wait(timeout=8)
        logs = container.logs(stdout=True, stderr=True).decode(errors="replace")
        if result.get("StatusCode") != 0:
            raise RuntimeError(f"network reachability assertion failed: {logs}")
        print(logs.strip())
    finally:
        container.remove(force=True)


def main() -> None:
    client = docker.from_env()
    client.ping()
    suffix = uuid.uuid4().hex[:8]
    existing = []
    for network in client.networks.list():
        for config in (network.attrs.get("IPAM") or {}).get("Config") or []:
            if config.get("Subnet"):
                existing.append(config["Subnet"])
    from ipaddress import ip_network

    selected = None
    for third in range(220, 253, 3):
        candidates = tuple(f"10.249.{value}.0/24" for value in (third, third + 1, third + 2))
        if all(not ip_network(candidate).overlaps(ip_network(used)) for candidate in candidates for used in existing):
            selected = candidates
            break
    if selected is None:
        raise RuntimeError("no free test subnets found in 10.249.220.0/24–10.249.252.0/24")

    first_name, second_name, other_scenario_name = (
        f"anvil-zone-a-{suffix}", f"anvil-zone-b-{suffix}", f"anvil-scenario-other-{suffix}"
    )
    networks, server = [], None
    try:
        for name, subnet in zip((first_name, second_name, other_scenario_name), selected):
            networks.append(client.networks.create(
                name, driver="bridge", internal=True,
                ipam=IPAMConfig(pool_configs=[IPAMPool(subnet=subnet)]),
                labels={"anvil.integration": "zone-isolation"},
            ))
        server_endpoint = client.api.create_endpoint_config(ipv4_address="10.249." + selected[0].split(".")[2] + ".10")
        server = client.containers.create(
            IMAGE,
            command=["python", "-c", "import socket,time; s=socket.socket(); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1); s.bind(('0.0.0.0',25000)); s.listen(); time.sleep(20)"],
            name=f"anvil-zone-server-{suffix}",
            network=first_name,
            networking_config={first_name: server_endpoint},
            labels={"anvil.integration": "zone-isolation"},
        )
        server.start()
        time.sleep(1)
        first_ip = "10.249."+selected[0].split(".")[2]+".10"
        second_ip = "10.249."+selected[1].split(".")[2]+".11"
        other_scenario_ip = "10.249."+selected[2].split(".")[2]+".11"
        run_client(client, first_name, "10.249."+selected[0].split(".")[2]+".11", first_ip, True)
        run_client(client, second_name, second_ip, first_ip, False)
        run_client(client, other_scenario_name, other_scenario_ip, first_ip, False)
        print("PASS: same-zone TCP reachable; cross-zone and cross-scenario TCP blocked")
    finally:
        if server is not None:
            server.remove(force=True)
        for network in reversed(networks):
            network.remove()


if __name__ == "__main__":
    main()
