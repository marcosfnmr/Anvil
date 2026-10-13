from __future__ import annotations

import io
import ipaddress
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from models.scenario import ScenarioCreate  # noqa: E402
from services.bundle_readdress import readdress_bundle  # noqa: E402
from services.compose_export import build_compose_bundle  # noqa: E402
from services.scenario_service import ScenarioService  # noqa: E402


class ComposeBundleExportTests(unittest.TestCase):
    @staticmethod
    def scenario_payload() -> dict:
        return {
            "name": "Portable multi-zone bundle",
            "network": {
                "subnet": "172.30.12.0/24",
                "zones": [
                    {"zone_id": "control", "name": "Control", "purdue_level": "L1", "subnet": "172.30.10.0/24"},
                    {"zone_id": "operations", "name": "Operations", "purdue_level": "L2", "subnet": "172.30.11.0/24"},
                ],
            },
            "devices": [
                {"node_id": "plc-control", "device_type": "plc-s7", "name": "PLC control",
                 "ip": "172.30.10.10", "zone_id": "control", "config": {"components": []}},
                {"node_id": "plc-operations", "device_type": "plc-s7", "name": "PLC operations",
                 "ip": "172.30.11.20", "zone_id": "operations", "config": {"components": []}},
            ],
        }

    @classmethod
    def bundle(cls) -> bytes:
        scenario = ScenarioCreate.model_validate(cls.scenario_payload())
        runtime = ScenarioService._build_runtime_devices(scenario)
        return build_compose_bundle("portable-bundle-id", scenario, runtime)

    @staticmethod
    def env_values(path: Path) -> dict[str, str]:
        return dict(line.split("=", 1) for line in path.read_text().splitlines()
                    if line and not line.startswith("#") and "=" in line)

    def test_compose_bundle_exports_zone_and_device_ip_overrides(self) -> None:
        with zipfile.ZipFile(io.BytesIO(self.bundle())) as archive:
            names = set(archive.namelist())
            self.assertTrue({".env", "network-addresses.json", "readdress.py"} <= names)
            scenario_export = json.loads(archive.read("scenario.json"))
            self.assertEqual(scenario_export["format"], "anvil-scenario")
            self.assertEqual(scenario_export["schema_version"], 2)
            compose = yaml.safe_load(archive.read("docker-compose.yml"))
            metadata = json.loads(archive.read("network-addresses.json"))
            self.assertIn("${ANVIL_BUNDLE_SUBNET_CONTROL_", compose["networks"]["zone_control"]["ipam"]["config"][0]["subnet"])
            self.assertIn("${ANVIL_BUNDLE_IP_CONTROL_PLC_CONTROL_", compose["services"]["plc-control"]["networks"]["zone_control"]["ipv4_address"])
            self.assertEqual({item["zone_id"] for item in metadata["zones"]}, {"control", "operations"})
            self.assertIn("python3 readdress.py --zone", archive.read("README.txt").decode())

    def test_compose_bundle_exports_opt_in_ingress_only_on_loopback_by_default(self) -> None:
        payload = self.scenario_payload()
        payload["devices"][0].update(external_access=True, external_port=1102)
        payload["devices"].append({
            "node_id": "modbus-line", "device_type": "modbus-tcp", "name": "Modbus line",
            "ip": "172.30.10.30", "zone_id": "control", "external_access": True,
            "external_port": 1502, "config": {"unit_id": 1, "port": 502,
                                                   "points": [{"type": "coil", "name": "pump"}]},
        })
        scenario = ScenarioCreate.model_validate(payload)
        runtime = ScenarioService._build_runtime_devices(scenario)
        with zipfile.ZipFile(io.BytesIO(build_compose_bundle("bundle-ingress-id", scenario, runtime))) as archive:
            compose = yaml.safe_load(archive.read("docker-compose.yml"))
            services = compose["services"]
            ingresses = [service for service in services.values()
                         if service["labels"].get("anvil.role") == "protocol-ingress"]
            self.assertEqual(len(ingresses), 2)
            self.assertEqual(compose["networks"]["host_bridge"], {"driver": "bridge"})
            expected_logging = {
                "driver": "json-file", "options": {"max-size": "10m", "max-file": "3"},
            }
            self.assertEqual(ingresses[0]["logging"], expected_logging)
            self.assertEqual(services["plc-control"]["logging"], expected_logging)
            self.assertEqual(services["modbus-line"]["logging"], expected_logging)
            for service in ingresses:
                config = json.loads(service["environment"]["ANVIL_INGRESS_CONFIG"])
                self.assertEqual(config["allowed_peer_ip"], "__docker_default_gateway__")
                self.assertEqual(service["networks"]["host_bridge"]["gw_priority"], 1)
                self.assertEqual(len(service["networks"]), 2)
                self.assertEqual(len(service["ports"]), 1)
                self.assertIn("127.0.0.1:${ANVIL_BUNDLE_HOST_PORT_", service["ports"][0])
                self.assertIn(":25000/tcp", service["ports"][0])
            for name in ("plc-control", "modbus-line"):
                self.assertNotIn("ports", services[name])
            self.assertIn("anvil_ingress_plc-control", " ".join(services))
            self.assertIn("anvil_ingress_modbus-line", " ".join(services))

    def test_readdress_helper_preserves_host_offsets_for_all_selected_zones(self) -> None:
        with tempfile.TemporaryDirectory(prefix="anvil-bundle-readdress-") as temporary:
            directory = Path(temporary)
            with zipfile.ZipFile(io.BytesIO(self.bundle())) as archive:
                archive.extract(".env", directory)
                archive.extract("network-addresses.json", directory)
                archive.extract("readdress.py", directory)
            subprocess.run([
                sys.executable, str(directory / "readdress.py"),
                "--zone", "control=10.250.0.0/24",
                "--zone", "operations=10.251.0.0/24",
            ], cwd=directory, check=True, capture_output=True, text=True)
            values = self.env_values(directory / ".env")
            metadata = json.loads((directory / "network-addresses.json").read_text())
            for zone in metadata["zones"]:
                new_subnet = ipaddress.ip_network(values[zone["subnet_env"]])
                self.assertEqual(str(new_subnet), "10.250.0.0/24" if zone["zone_id"] == "control" else "10.251.0.0/24")
                for address in zone["addresses"]:
                    old_subnet = ipaddress.ip_network(zone["subnet"])
                    offset = int(ipaddress.ip_address(address["ip"])) - int(old_subnet.network_address)
                    expected = ipaddress.ip_address(int(new_subnet.network_address) + offset)
                    self.assertEqual(values[address["env"]], str(expected))

    def test_readdress_rejects_overlapping_zones_without_modifying_env(self) -> None:
        with tempfile.TemporaryDirectory(prefix="anvil-bundle-readdress-") as temporary:
            directory = Path(temporary)
            with zipfile.ZipFile(io.BytesIO(self.bundle())) as archive:
                archive.extract(".env", directory)
                archive.extract("network-addresses.json", directory)
            env_path = directory / ".env"
            before = env_path.read_bytes()
            with self.assertRaisesRegex(ValueError, "overlap"):
                readdress_bundle(
                    zones=[
                        ("control", ipaddress.ip_network("10.250.0.0/24")),
                        ("operations", ipaddress.ip_network("10.250.0.0/24")),
                    ],
                    metadata_path=directory / "network-addresses.json",
                    env_path=env_path,
                )
            self.assertEqual(env_path.read_bytes(), before)

    def test_readdress_rejects_subnet_that_cannot_preserve_device_offset(self) -> None:
        with tempfile.TemporaryDirectory(prefix="anvil-bundle-readdress-") as temporary:
            directory = Path(temporary)
            with zipfile.ZipFile(io.BytesIO(self.bundle())) as archive:
                archive.extract(".env", directory)
                archive.extract("network-addresses.json", directory)
            with self.assertRaisesRegex(ValueError, "no usable matching address"):
                readdress_bundle(
                    zones=[("control", ipaddress.ip_network("10.250.0.0/29"))],
                    metadata_path=directory / "network-addresses.json",
                    env_path=directory / ".env",
                )


if __name__ == "__main__":
    unittest.main()
