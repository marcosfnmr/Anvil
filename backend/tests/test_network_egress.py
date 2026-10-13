"""Validation for explicit, workstation-only scenario egress allowlists."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from devices import DEVICE_REGISTRY  # noqa: E402
from models.scenario import ScenarioCreate, ScenarioPayload  # noqa: E402
from services.scenario_service import ScenarioService  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402


def _payload(*, rules=None, device_type="engineering-station", access=True):
    role = {
        "engineering-station": "engineering",
        "attacker-station": "attacker",
        "hmi": "hmi",
    }[device_type]
    config = {"role": role, "summary": "lab workstation"}
    if role == "engineering":
        config["engineering_access_enabled"] = access
    elif role == "attacker":
        config["participant_access_enabled"] = access
    return {
        "name": "Egress validation",
        "network": {"subnet": "172.30.1.0/24", "egress_rules": rules or []},
        "devices": [{
            "node_id": "station-1", "device_type": device_type, "name": "Station",
            "ip": "172.30.1.10", "config": config,
        }],
    }


def _rule(**overrides):
    return {"rule_id": "docs", "source_node_id": "station-1", "hostname": "docs.example.net",
            "tcp_port": 443, **overrides}


class NetworkEgressValidationTests(unittest.TestCase):
    def test_accepts_only_explicit_enabled_workstation_destination(self) -> None:
        scenario = ScenarioPayload.model_validate(_payload(rules=[_rule()]))
        self.assertEqual(scenario.network.egress_rules[0].hostname, "docs.example.net")
        self.assertEqual(scenario.network.egress_rules[0].tcp_port, 443)

    def test_normalizes_case_and_root_dot(self) -> None:
        scenario = ScenarioPayload.model_validate(_payload(rules=[_rule(hostname="Docs.Example.NET.")]))
        self.assertEqual(scenario.network.egress_rules[0].hostname, "docs.example.net")

    def test_rejects_ip_literal_wildcard_and_reserved_domain(self) -> None:
        for hostname in ("8.8.8.8", "*.example.net", "service.local", "host.internal", "localhost"):
            with self.subTest(hostname=hostname), self.assertRaises(ValueError):
                ScenarioPayload.model_validate(_payload(rules=[_rule(hostname=hostname)]))

    def test_rejects_unknown_source_or_device_without_egress_client_role(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown source"):
            ScenarioPayload.model_validate(_payload(rules=[_rule(source_node_id="missing")]))
        with self.assertRaisesRegex(ValueError, "workstation"):
            ScenarioPayload.model_validate(_payload(rules=[_rule()], device_type="hmi"))

    def test_requires_opt_in_workstation_network_access(self) -> None:
        with self.assertRaisesRegex(ValueError, "enable the workstation"):
            ScenarioPayload.model_validate(_payload(rules=[_rule()], access=False))

    def test_rejects_duplicate_rule_id_and_destination(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate egress rule id"):
            ScenarioPayload.model_validate(_payload(rules=[_rule(), _rule(hostname="mirror.example.org")]))
        with self.assertRaisesRegex(ValueError, "duplicate egress destination"):
            ScenarioPayload.model_validate(_payload(rules=[_rule(), _rule(rule_id="docs-copy")]))

    def test_rejects_boolean_and_out_of_range_tcp_ports(self) -> None:
        for port in (True, 0, 65536):
            with self.subTest(port=port), self.assertRaises(ValueError):
                ScenarioPayload.model_validate(_payload(rules=[_rule(tcp_port=port)]))

    def test_egress_rules_are_bounded(self) -> None:
        rules = [_rule(rule_id=f"r{i}", hostname=f"host{i}.example.net") for i in range(33)]
        with self.assertRaises(ValueError):
            ScenarioPayload.model_validate(_payload(rules=rules))

    def test_enabled_rules_materialize_a_scoped_gateway_and_station_proxy(self) -> None:
        scenario = ScenarioCreate.model_validate(_payload(rules=[_rule()]))
        runtime = ScenarioService._build_runtime_devices(scenario)
        station = next(item for item in runtime if item["node_id"] == "station-1")
        gateway = next(item for item in runtime if item["device_type"] == "egress-gateway")
        station_config = DEVICE_REGISTRY["engineering-station"]().build_container_config("scenario", station)
        gateway_config = DEVICE_REGISTRY["egress-gateway"]().build_container_config("scenario", gateway)

        self.assertTrue(station["egress_proxy_url"].startswith("http://egress-"))
        self.assertEqual(station_config["environment"]["HTTPS_PROXY"], station["egress_proxy_url"])
        self.assertEqual(gateway["config"]["source_ip"], station["ip"])
        self.assertEqual(gateway_config["labels"]["anvil.egress_allowlist"],
                         '[{"hostname":"docs.example.net","tcp_port":443}]')

    def test_older_update_without_egress_field_preserves_existing_rules(self) -> None:
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
        SQLModel.metadata.create_all(engine)
        with Session(engine) as session:
            service = ScenarioService(session)
            created = service.create(ScenarioCreate.model_validate(_payload(rules=[_rule()])))
            update = {
                "name": created.name,
                "network": {"subnet": "172.30.1.0/24"},
                "devices": [device.model_dump(mode="json") for device in created.devices],
                "canvas": created.canvas,
            }
            from models.scenario import ScenarioUpdate
            result = service.update(str(created.id), ScenarioUpdate.model_validate(update))
            self.assertEqual([rule.hostname for rule in result.network.egress_rules], ["docs.example.net"])


if __name__ == "__main__":
    unittest.main()
