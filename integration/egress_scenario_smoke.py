"""Deploy an Anvil scenario through its API and Docker policy gateway with egress."""

from __future__ import annotations

import argparse
import os
import time
import uuid
from ipaddress import ip_network

import docker

from platform_smoke import request


def _free_subnet(client: docker.DockerClient) -> str:
    existing = [
        item.get("Subnet")
        for network in client.networks.list()
        for item in (network.attrs.get("IPAM") or {}).get("Config") or []
        if item.get("Subnet")
    ]
    for third in range(170, 254):
        subnet = f"10.247.{third}.0/24"
        if all(not ip_network(subnet).overlaps(ip_network(value)) for value in existing):
            return subnet
    raise RuntimeError("No free subnet in 10.247.170.0/24–10.247.253.0/24")


def _exec(container, command: list[str]) -> str:
    result = container.exec_run(command)
    output = result.output.decode(errors="replace") if isinstance(result.output, bytes) else str(result.output)
    if result.exit_code != 0:
        raise AssertionError(output)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8006")
    args = parser.parse_args()
    client = docker.DockerClient(base_url=os.environ.get("ANVIL_DOCKER_INSPECT_HOST", "unix:///var/run/docker.sock"))
    client.ping()
    scenario_id = None
    try:
        subnet = _free_subnet(client)
        hosts = list(ip_network(subnet).hosts())
        payload = {
            "name": f"RNF-10 egress smoke {uuid.uuid4().hex[:8]}",
            "network": {
                "subnet": subnet,
                "egress_rules": [{
                    "rule_id": "docs-https", "source_node_id": "engineering",
                    "hostname": "docs.python.org", "tcp_port": 443, "enabled": True,
                }],
            },
            "devices": [
                {"node_id": "plc", "device_type": "plc-s7", "name": "PLC",
                 "ip": str(hosts[1]), "config": {"components": []}},
                {"node_id": "engineering", "device_type": "engineering-station", "name": "Engineering",
                 "ip": str(hosts[2]), "config": {"role": "engineering", "summary": "RNF-10 smoke",
                                                    "engineering_access_enabled": True}},
            ],
        }
        created = request(args.base_url, "/api/scenarios", "POST", payload)
        scenario_id = created["id"]
        assert created["network"]["egress_rules"][0]["hostname"] == "docs.python.org"
        deployed = request(args.base_url, f"/api/scenarios/{scenario_id}/deploy", "POST")
        assert deployed["status"] == "running", deployed
        managed = client.containers.list(filters={"label": f"anvil.scenario_id={scenario_id}"})
        by_node = {item.labels["anvil.node_id"]: item for item in managed}
        assert set(by_node) == {"plc", "engineering", "__anvil_egress_engineering"}, by_node.keys()
        station = by_node["engineering"]
        gateway = by_node["__anvil_egress_engineering"]
        plc = by_node["plc"]
        gateway.reload()
        networks = gateway.attrs["NetworkSettings"]["Networks"]
        assert set(networks) == {f"anvil-{scenario_id[:12]}", "bridge"}, networks.keys()
        assert not gateway.attrs["HostConfig"].get("PortBindings"), gateway.attrs["HostConfig"]
        env = dict(item.split("=", 1) for item in station.attrs["Config"].get("Env", []) if "=" in item)
        proxy_url = env["HTTPS_PROXY"]
        proxy_host, proxy_port = proxy_url.removeprefix("http://").rsplit(":", 1)
        proxy_port = int(proxy_port)

        # The image startup is asynchronous; wait for the per-station listener.
        allowed_code = (
            "import socket; s=socket.create_connection((" + repr(proxy_host) + "," + str(proxy_port) + "),5); "
            "s.sendall(b'CONNECT docs.python.org:443 HTTP/1.1\\r\\nHost: docs.python.org:443\\r\\n\\r\\n'); "
            "r=s.recv(4096); assert b'200 Connection Established' in r,r; s.close()"
        )
        for attempt in range(20):
            try:
                _exec(station, ["python", "-c", allowed_code])
                break
            except AssertionError:
                if attempt == 19:
                    raise
                time.sleep(0.25)

        denied = station.exec_run(["python", "-c", (
            "import socket; s=socket.create_connection((" + repr(proxy_host) + "," + str(proxy_port) + "),5); "
            "s.sendall(b'CONNECT docs.python.org:444 HTTP/1.1\\r\\nHost: docs.python.org:444\\r\\n\\r\\n'); "
            "r=s.recv(4096); assert b'403 Forbidden' in r,r; s.close()"
        )])
        assert denied.exit_code == 0, denied.output
        _exec(plc, ["python", "-c", (
            "import socket; s=socket.socket(); s.settimeout(3); "
            "\ntry: s.connect(('1.1.1.1',443))\n"
            "except OSError: raise SystemExit(0)\n"
            "else: raise SystemExit('PLC reached public network without egress policy')"
        )])
        print("PASS: Anvil API deployed the egress gateway; authorized CONNECT works; other port and PLC direct egress are blocked")
        return 0
    finally:
        if scenario_id:
            try:
                request(args.base_url, f"/api/scenarios/{scenario_id}/stop", "POST")
            finally:
                request(args.base_url, f"/api/scenarios/{scenario_id}", "DELETE")
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
