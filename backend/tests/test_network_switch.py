from __future__ import annotations

import io
import sys
import unittest
import zipfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from devices import DEVICE_REGISTRY  # noqa: E402
from models.scenario import ScenarioPayload  # noqa: E402
from services.compose_export import build_compose_bundle  # noqa: E402
from services.docker_manager import DockerManager  # noqa: E402


def scenario_payload(capacity: int = 2) -> dict:
    return {
        "name": "Switched zone",
        "network": {"subnet": "172.30.1.0/24"},
        "devices": [
            {"node_id": "switch-1", "device_type": "ethernet-switch", "name": "Switch",
             "ip": "172.30.1.10", "config": {"role": "switch", "summary": "L2 zone",
                                                "switch_port_capacity": capacity}},
            {"node_id": "hmi-1", "device_type": "hmi", "name": "HMI", "ip": "172.30.1.11",
             "config": {"role": "hmi", "summary": "operator"}},
        ],
        "network_connections": [{"connection_id": "link-1", "source_node_id": "switch-1",
                                  "target_node_id": "hmi-1", "label": "Port 1"}],
    }


class EthernetSwitchTests(unittest.TestCase):
    def test_switch_requires_each_network_attached_node_to_link_to_it(self) -> None:
        payload = scenario_payload()
        payload["network_connections"] = []
        with self.assertRaisesRegex(ValueError, "must connect to ethernet switch"):
            ScenarioPayload.model_validate(payload)

    def test_switch_port_capacity_is_enforced(self) -> None:
        payload = scenario_payload(capacity=2)
        payload["devices"].append({"node_id": "hmi-2", "device_type": "hmi", "name": "HMI 2",
                                   "ip": "172.30.1.12", "config": {"role": "hmi", "summary": "operator"}})
        payload["network_connections"].append({"connection_id": "link-2", "source_node_id": "switch-1",
                                                "target_node_id": "hmi-2", "label": "Port 2"})
        payload["devices"].append({"node_id": "hmi-3", "device_type": "hmi", "name": "HMI 3",
                                   "ip": "172.30.1.13", "config": {"role": "hmi", "summary": "operator"}})
        payload["network_connections"].append({"connection_id": "link-3", "source_node_id": "switch-1",
                                                "target_node_id": "hmi-3", "label": "Port 3"})
        with self.assertRaisesRegex(ValueError, "exceeds its configured port capacity"):
            ScenarioPayload.model_validate(payload)

    def test_compose_export_models_switch_as_zone_bridge_without_a_fake_service(self) -> None:
        scenario = ScenarioPayload.model_validate(scenario_payload())
        runtime = []
        for device in scenario.devices:
            adapter = DEVICE_REGISTRY[device.device_type]()
            item = adapter.build_runtime_device(device)
            item.update({"zone_id": device.zone_id, "zone_subnet": scenario.network.subnet,
                         "zoned": False, "hostname": device.hostname, "network_mode": None,
                         "external_access": False, "external_port": None})
            runtime.append(item)
        archive = zipfile.ZipFile(io.BytesIO(build_compose_bundle("switchscenario", scenario, runtime)))
        compose = yaml.safe_load(archive.read("docker-compose.yml"))
        self.assertEqual(set(compose["services"]), {"hmi-1"})
        network = compose["networks"]["scenario_net"]
        self.assertEqual(network["labels"]["anvil.switch_node_id"], "switch-1")
        self.assertTrue(network["internal"])

    def test_deploy_tracks_switch_as_network_resource_without_creating_container(self) -> None:
        from unittest.mock import Mock

        client = Mock()
        client.ping.return_value = True
        network = Mock()
        network.name = "anvil-switchscenario"
        network.attrs = {"Labels": {"anvil.managed": "true", "anvil.scenario_id": "switchscenario",
                                     "anvil.zone_id": "default", "anvil.switch_node_id": "switch-1"}}
        client.networks.list.side_effect = [[], [network]]
        client.networks.create.return_value = network
        client.containers.list.side_effect = [[], []]
        result = DockerManager(client=client).deploy("switchscenario", "172.30.1.0/24", [{
            "node_id": "switch-1", "device_type": "ethernet-switch", "name": "Switch",
            "ip": "172.30.1.10", "zone_id": "default", "zoned": False,
            "config": {"role": "switch", "summary": "L2", "switch_port_capacity": 24},
        }])

        self.assertEqual(result["status"], "running")
        self.assertEqual(client.containers.create.call_count, 0)
        self.assertEqual(client.networks.create.call_args.kwargs["labels"]["anvil.switch_node_id"], "switch-1")
        switch_state = next(item for item in result["containers"] if item["node_id"] == "switch-1")
        self.assertEqual(switch_state["runtime_kind"], "network")

    def test_switch_bridge_does_not_make_a_paused_scenario_look_partial(self) -> None:
        from unittest.mock import Mock

        client = Mock()
        container = Mock()
        container.reload.return_value = None
        container.attrs = {"State": {}}
        container.status = "paused"
        container.labels = {"anvil.node_id": "plc-1"}
        container.name = "plc-1"
        network = Mock()
        network.name = "zone-control"
        network.attrs = {"Labels": {"anvil.switch_node_id": "switch-1"}}
        client.containers.list.return_value = [container]
        client.networks.list.return_value = [network]

        status = DockerManager(client=client).status("switchscenario")
        self.assertEqual(status["status"], "paused")


if __name__ == "__main__":
    unittest.main()
