"""Run an exported Compose bundle beside Anvil, then verify Anvil cleanup isolation."""

from __future__ import annotations

import argparse
from ipaddress import ip_network
import subprocess
import tempfile
import zipfile
from pathlib import Path

import docker

from platform_smoke import request, request_bytes


def free_subnet(client) -> str:
    used = [ip_network(entry["Subnet"]) for network in client.networks.list()
            for entry in ((network.attrs.get("IPAM") or {}).get("Config") or []) if entry.get("Subnet")]
    for third in range(20, 240):
        candidate = ip_network(f"10.247.{third}.0/24")
        if all(not candidate.overlaps(item) for item in used):
            return str(candidate)
    raise RuntimeError("No free subnet in the deployment-bundle smoke range")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8004")
    args = parser.parse_args()
    client = docker.from_env()
    scenario_id = None
    compose_file: Path | None = None
    temporary_directory: tempfile.TemporaryDirectory | None = None
    project = ""
    payload: dict = {}
    try:
        subnet = free_subnet(client)
        subnet_network = ip_network(subnet)
        payload = {
            "name": "Standalone bundle smoke",
            "network": {"subnet": subnet},
            "devices": [{
                "node_id": "bundle-plc", "hostname": "bundle-control-plc",
                "device_type": "plc-s7", "name": "Bundle PLC",
                "ip": str(subnet_network.network_address + 10),
                "config": {"components": [{"type": "valve", "name": "Válvula bundle"}]},
            }],
        }
        scenario_id = request(args.base_url, "/api/scenarios", "POST", payload)["id"]
        path = f"/api/scenarios/{scenario_id}"
        deployed = request(args.base_url, f"{path}/deploy", "POST")
        if deployed["status"] != "running":
            raise AssertionError(f"Anvil deployment failed: {deployed}")

        project = f"bundle-smoke-{scenario_id[:8]}"
        temporary_directory = tempfile.TemporaryDirectory(prefix="anvil-compose-bundle-")
        directory = Path(temporary_directory.name)
        bundle_path = directory / "bundle.zip"
        bundle_path.write_bytes(request_bytes(args.base_url, f"{path}/deployment-bundle"))
        with zipfile.ZipFile(bundle_path) as archive:
            archive.extractall(directory)
        compose_file = directory / "docker-compose.yml"
        subprocess.run(
            ["sh", str(directory / "export-images.sh")],
            cwd=directory, check=True, capture_output=True, text=True, timeout=180,
        )
        subprocess.run(
            ["sh", str(directory / "check-images.sh")],
            cwd=directory, check=True, capture_output=True, text=True, timeout=30,
        )
        if not (directory / "anvil-images.tar").is_file() or not (directory / "anvil-images.lock").is_file():
            raise AssertionError("Image export did not produce the TAR and immutable ID lock")
        # Readdress the standalone bundle so it can run beside its source
        # scenario, which remains deployed in its original subnet.
        bundle_subnet = free_subnet(client)
        bundle_network = ip_network(bundle_subnet)
        subprocess.run(
            ["python3", str(directory / "readdress.py"), "--zone", f"default={bundle_subnet}"],
            cwd=directory, check=True, capture_output=True, text=True, timeout=30,
        )
        try:
            subprocess.run(
                ["docker", "compose", "-p", project, "-f", str(compose_file), "up", "-d"],
                cwd=directory, check=True, capture_output=True, text=True, timeout=90,
            )
        except subprocess.CalledProcessError as error:
            details = (error.stderr or error.stdout or "no output").strip()
            raise RuntimeError(f"docker compose up failed: {details}") from error
        bundle_containers = client.containers.list(
            filters={"label": f"anvil.bundle_scenario_id={scenario_id}"}
        )
        if len(bundle_containers) != 1:
            raise AssertionError(f"Expected one exported PLC container, found {len(bundle_containers)}")
        standalone = bundle_containers[0]
        standalone.reload()
        labels = standalone.labels
        if standalone.status != "running":
            raise AssertionError(f"Exported PLC is not running: {standalone.status}")
        if labels.get("anvil.managed") or labels.get("anvil.scenario_id"):
            raise AssertionError(f"Standalone container claims Anvil ownership: {labels}")
        if standalone.attrs["HostConfig"].get("PortBindings"):
            raise AssertionError("Exported PLC published host ports by default")
        endpoint = standalone.attrs["NetworkSettings"]["Networks"]
        if len(endpoint) != 1:
            raise AssertionError(f"Unexpected exported network endpoints: {endpoint}")
        network_name = next(iter(endpoint))
        network = client.networks.get(network_name)
        if network.attrs.get("Internal") is not True:
            raise AssertionError(f"Exported scenario network is not internal: {network.attrs}")
        if network.attrs["IPAM"]["Config"][0]["Subnet"] != bundle_subnet:
            raise AssertionError(f"Bundle ignored its .env subnet override: {network.attrs['IPAM']}")
        expected_ip = str(bundle_network.network_address + 10)
        if endpoint[network_name]["IPAddress"] != expected_ip:
            raise AssertionError(f"Exported PLC IP differs from scenario: {endpoint[network_name]}")

        still_running = request(args.base_url, f"{path}/status")
        if still_running["status"] != "running":
            raise AssertionError(f"Original Anvil scenario stopped while the bundle started: {still_running}")

        stopped = request(args.base_url, f"{path}/stop", "POST")
        if stopped["status"] != "stopped":
            raise AssertionError(f"Anvil scenario failed to stop after the parallel-run check: {stopped}")
        request(args.base_url, path, "DELETE")
        scenario_id = None
        standalone.reload()
        if standalone.status != "running":
            raise AssertionError("Deleting the Anvil scenario stopped its independent Compose bundle")
        print("PASS: readdressed exported Compose PLC ran beside Anvil on a separate internal network, with no host ports, and survived Anvil scenario deletion")
        return 0
    finally:
        if compose_file is not None and compose_file.exists() and project:
            subprocess.run(
                ["docker", "compose", "-p", project, "-f", str(compose_file), "down", "--remove-orphans"],
                cwd=temporary_directory.name, check=False, capture_output=True, text=True, timeout=60,
            )
        if temporary_directory is not None:
            temporary_directory.cleanup()
        if scenario_id:
            try:
                request(args.base_url, f"/api/scenarios/{scenario_id}/stop", "POST")
            finally:
                request(args.base_url, f"/api/scenarios/{scenario_id}", "DELETE")
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
