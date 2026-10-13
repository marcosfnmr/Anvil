import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from models.scenario import ScenarioCreate
from services.scenario_service import ScenarioService
from devices.plc_s7 import PlcS7Device


def payload():
    return {"name": "Peers", "network": {"subnet": "172.30.243.0/24"}, "devices": [
        {"node_id": "sender", "device_type": "plc-s7", "name": "Sender", "ip": "172.30.243.10", "config": {"components": [
            {"type": "valve", "name": "run"}, {"type": "analog_sensor", "name": "pressure", "min_value": 10, "max_value": 20}]}},
        {"node_id": "receiver", "device_type": "plc-s7", "name": "Receiver", "ip": "172.30.243.11", "config": {"components": [
            {"type": "remote_input", "name": "run_remote", "source_node_id": "sender", "source_address": "DB1.DBX0.0"},
            {"type": "remote_input", "name": "pressure_remote", "data_type": "REAL", "source_node_id": "sender", "source_address": "DB1.DBD2"}]}}]}


class S7LinksTests(unittest.TestCase):
    def test_stale_behavior_is_validated_and_passed_to_plc_runtime(self):
        data = payload()
        data["devices"][1]["config"]["components"][1]["stale_behavior"] = "force_off"
        scenario = ScenarioCreate.model_validate(data)
        receiver = next(item for item in ScenarioService._build_runtime_devices(scenario) if item["node_id"] == "receiver")
        self.assertEqual(receiver["config"]["components"][1]["stale_behavior"], "force_off")
        data["devices"][1]["config"]["components"][1]["stale_behavior"] = "keep_running"
        with self.assertRaises(ValueError):
            ScenarioCreate.model_validate(data)

    def test_resolves_peer_settings_and_allocates_read_only_inputs(self):
        runtime = ScenarioService._build_runtime_devices(ScenarioCreate.model_validate(payload()))
        boolean, real = runtime[1]["config"]["components"]
        self.assertEqual((boolean["source_ip"], boolean["source_db"], boolean["source_byte_offset"]), ("172.30.243.10", 1, 0))
        self.assertEqual(real["source_byte_offset"], 2)
        points = PlcS7Device().build_io_map(runtime[1])
        self.assertEqual([(p["address"], p["access"]) for p in points], [("DB1.DBX0.0", "read"), ("DB1.DBD2", "read")])

    def test_rejects_invalid_peers_types_addresses_and_stale_intervals(self):
        for changes in ({"source_node_id": "missing"}, {"source_node_id": "receiver"},
                        {"source_address": "DB1.DBX9.0"}, {"data_type": "REAL"},
                        {"source_name": "wrong name"}, {"period_ms": 1001},
                        {"timeout_ms": True}):
            invalid = copy.deepcopy(payload())
            invalid["devices"][1]["config"]["components"][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                ScenarioCreate.model_validate(invalid)

    def test_rejects_remote_to_remote_feedback_loops(self):
        invalid = payload()
        invalid["devices"][0]["config"]["components"][0] = {
            "type": "remote_input", "name": "feedback", "source_node_id": "receiver", "source_address": "DB1.DBX0.0"}
        with self.assertRaises(ValueError):
            ScenarioCreate.model_validate(invalid)

    def test_cross_zone_peer_requires_explicit_route_and_uses_gateway_address(self):
        data = payload()
        data["network"]["zones"] = [
            {"zone_id": "control", "name": "Control", "purdue_level": "L1", "subnet": "172.30.10.0/24"},
            {"zone_id": "operations", "name": "Operations", "purdue_level": "L2", "subnet": "172.30.11.0/24"},
        ]
        data["devices"][0].update(zone_id="control", ip="172.30.10.10")
        data["devices"][1].update(zone_id="operations", ip="172.30.11.10")
        data["network_routes"] = [{"route_id": "receiver-to-sender", "source_node_id": "receiver",
                                   "target_node_id": "sender", "protocol": "s7", "enabled": True}]
        scenario = ScenarioCreate.model_validate(data)
        runtime = ScenarioService._build_runtime_devices(scenario)
        receiver = next(item for item in runtime if item["node_id"] == "receiver")
        sender = next(item for item in runtime if item["node_id"] == "sender")
        gateway = next(item for item in runtime if item["device_type"] == "tcp-gateway")
        remote = receiver["config"]["components"][0]
        self.assertEqual(remote["source_ip"], gateway["ip"])
        self.assertNotEqual(remote["source_ip"], sender["ip"])
        self.assertEqual(gateway["config"]["source_node_id"], "receiver")
        self.assertEqual(gateway["config"]["source_ip"], receiver["ip"])
        self.assertEqual(gateway["config"]["target_ip"], sender["ip"])
        self.assertEqual(gateway["config"]["target_port"], 102)

        without_route = copy.deepcopy(data)
        without_route["network_routes"] = []
        with self.assertRaisesRegex(ValueError, "add an explicit S7 network route"):
            ScenarioCreate.model_validate(without_route)
