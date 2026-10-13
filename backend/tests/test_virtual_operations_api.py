from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from main import create_app  # noqa: E402
from models.db import get_session  # noqa: E402
from routes.scenarios import get_docker_manager  # noqa: E402
from dependencies import get_optional_docker_manager  # noqa: E402


class FakeDockerManager:
    def deploy(self, scenario_id: str, subnet: str, devices: list[dict]) -> dict:
        del subnet
        containers = [{"node_id": item["node_id"], "name": item["name"], "status": "running"} for item in devices]
        return {"scenario_id": scenario_id, "status": "running", "containers": containers}

    def stop(self, scenario_id: str) -> dict:
        return {"scenario_id": scenario_id, "status": "stopped", "containers": []}

    def status(self, scenario_id: str) -> dict:
        return {"scenario_id": scenario_id, "status": "running", "containers": []}

    def logs(self, scenario_id: str, *, tail: int = 100) -> dict:
        del tail
        return {"scenario_id": scenario_id, "containers": []}


class VirtualOperationsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)
        self.manager = FakeDockerManager()
        app = create_app(initialize_database=False)

        def session_override():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_session] = session_override
        app.dependency_overrides[get_docker_manager] = lambda: self.manager
        app.dependency_overrides[get_optional_docker_manager] = lambda: self.manager
        self.context = TestClient(app)
        self.client = self.context.__enter__()

    def tearDown(self) -> None:
        self.context.__exit__(None, None, None)
        self.engine.dispose()

    def create_running_scenario(self, *, allowed_ports: list[int] | None = None, modbus_target: bool = False, plc_point_type: str = "valve", real_output: bool = False) -> str:
        target = {
            "node_id": "modbus-1", "device_type": "modbus-tcp", "name": "Modbus", "ip": "172.30.10.11",
            "config": {"unit_id": 1, "port": 1502, "points": [
                {"type": "coil", "name": "C1"},
                {"type": "discrete_input", "name": "DI1", "initial_value": True},
                {"type": "holding_register", "name": "HR1"},
                {"type": "input_register", "name": "IR1"},
            ]},
        } if modbus_target else {
            "node_id": "plc-1", "device_type": "plc-s7", "name": "PLC", "ip": "172.30.10.11",
            "config": {"components": [
                {"type": plc_point_type, "name": "V1"},
                *([{"type": "io_channel", "name": "Setpoint", "direction": "output", "data_type": "REAL", "unit": "%"}] if real_output else []),
            ]},
        }
        target["node_id"] = "modbus-1" if modbus_target else "plc-1"
        devices = [
            {
                "node_id": "hmi-1", "device_type": "hmi", "name": "HMI", "ip": "172.30.10.10",
                "config": {"role": "hmi", "summary": "Consulta", "target_node_ids": target["node_id"]},
            },
            target,
        ]
        if allowed_ports is not None:
            devices.append({
                "node_id": "fw-1", "device_type": "firewall", "name": "Firewall", "ip": "172.30.10.12",
                "config": {"role": "firewall", "summary": "Política consola", "firewall_allow_tcp_ports": allowed_ports},
            })
        created = self.client.post("/api/scenarios", json={
            "name": "HMI", "network": {"subnet": "172.30.10.0/24"}, "devices": devices,
        })
        self.assertEqual(created.status_code, 201, created.text)
        scenario_id = created.json()["id"]
        deployed = self.client.post(f"/api/scenarios/{scenario_id}/deploy")
        self.assertEqual(deployed.status_code, 200, deployed.text)
        return scenario_id

    @patch("routes.plc_operations.hmi_operation")
    def test_hmi_reads_configured_s7_values(self, read_values: object) -> None:
        read_values.return_value = [{
            "node_id": "plc-1", "address": "DB1.DBX0.0", "component_name": "V1",
            "value": True, "data_type": "BOOL", "raw_hex": "01", "unit": None,
        }]
        scenario_id = self.create_running_scenario()

        response = self.client.get(f"/api/scenarios/{scenario_id}/hmi/hmi-1/values")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["values"][0]["component_name"], "V1")
        self.assertEqual(response.json()["values"][0]["access"], "read_write")
        read_values.assert_called_once()

    @patch("routes.plc_operations.hmi_operation")
    def test_hmi_marks_explicitly_out_of_range_analog_reading(self, read_values: object) -> None:
        read_values.return_value = [{
            "node_id": "plc-1", "address": "DB1.DBD0", "component_name": "Nivel",
            "value": 125.0, "data_type": "REAL", "raw_hex": "42 fa 00 00", "unit": "%",
            "communication": {"quality": "good"},
        }]
        scenario_id = self.create_running_scenario()
        scenario = self.client.get(f"/api/scenarios/{scenario_id}").json()
        self.client.post(f"/api/scenarios/{scenario_id}/stop")
        plc = next(device for device in scenario["devices"] if device["node_id"] == "plc-1")
        plc["config"]["components"] = [{
            "type": "io_channel", "name": "Nivel", "direction": "input", "data_type": "REAL",
            "unit": "%", "min_value": 0, "max_value": 100,
        }]
        updated = self.client.put(f"/api/scenarios/{scenario_id}", json=scenario)
        self.assertEqual(updated.status_code, 200, updated.text)
        deployed = self.client.post(f"/api/scenarios/{scenario_id}/deploy")
        self.assertEqual(deployed.status_code, 200, deployed.text)

        response = self.client.get(f"/api/scenarios/{scenario_id}/hmi/hmi-1/values")

        self.assertEqual(response.status_code, 200, response.text)
        point = response.json()["values"][0]
        self.assertEqual(point["quality"], "out_of_range")
        self.assertEqual(point["protocol_quality"], "good")
        self.assertIn("125", point["range_diagnostic"]["message"])
        self.assertEqual(point["access"], "read")

    @patch("routes.plc_operations.hmi_operation")
    def test_hmi_owned_alarm_uses_protocol_reads_persists_ack_and_records_transitions(self, read_values: object) -> None:
        created = self.client.post("/api/scenarios", json={
            "name": "HMI-owned alarm", "network": {"subnet": "172.30.10.0/24"},
            "devices": [
                {"node_id": "hmi", "device_type": "hmi", "name": "HMI", "ip": "172.30.10.10",
                 "config": {"role": "hmi", "summary": "Panel", "target_node_ids": "plc",
                            "alarms": [{"target_node_id": "plc", "address": "DB1.DBD0", "above": 10}]}},
                {"node_id": "plc", "device_type": "plc-s7", "name": "PLC", "ip": "172.30.10.11",
                 "config": {"components": [{"type": "analog_sensor", "name": "Level"}]}},
            ],
        })
        self.assertEqual(created.status_code, 201, created.text)
        scenario_id = created.json()["id"]
        self.assertEqual(self.client.post(f"/api/scenarios/{scenario_id}/deploy").status_code, 200)
        read_values.return_value = [{"node_id": "plc", "address": "DB1.DBD0", "value": 12.0,
                                     "data_type": "REAL", "component_name": "Level"}]

        active = self.client.get(f"/api/scenarios/{scenario_id}/hmi/hmi/values")
        ack = self.client.post(f"/api/scenarios/{scenario_id}/hmi/hmi/alarms/acknowledge", json={
            "target_node_id": "plc", "address": "DB1.DBD0",
        })
        read_values.return_value = [{"node_id": "plc", "address": "DB1.DBD0", "value": 12.0,
                                     "data_type": "REAL", "component_name": "Level", "quality": "bad"}]
        stale = self.client.get(f"/api/scenarios/{scenario_id}/hmi/hmi/values")
        read_values.return_value = [{"node_id": "plc", "address": "DB1.DBD0", "value": 8.0,
                                     "data_type": "REAL", "component_name": "Level"}]
        cleared = self.client.get(f"/api/scenarios/{scenario_id}/hmi/hmi/values")

        self.assertEqual(active.status_code, 200, active.text)
        alarm = next(item for item in active.json()["alarms"] if item.get("hmi_owned"))
        self.assertTrue(alarm["active"])
        self.assertEqual(ack.status_code, 200, ack.text)
        self.assertTrue(ack.json()["acknowledged"])
        stale_alarm = next(item for item in stale.json()["alarms"] if item.get("hmi_owned"))
        self.assertTrue(stale_alarm["active"])
        self.assertTrue(stale_alarm["stale"])
        self.assertTrue(stale_alarm["acknowledged"])
        clear_alarm = next(item for item in cleared.json()["alarms"] if item.get("hmi_owned"))
        self.assertFalse(clear_alarm["active"])
        self.assertEqual([item["transition"] for item in cleared.json()["alarm_history"]], ["activated", "cleared"])
        with Session(self.engine) as session:
            from models.audit import ScenarioAuditEvent
            from models.hmi import ScenarioHmiAlarmTransition
            transitions = session.exec(select(ScenarioHmiAlarmTransition).where(
                ScenarioHmiAlarmTransition.scenario_id == scenario_id,
            )).all()
            audit = session.exec(select(ScenarioAuditEvent).where(
                ScenarioAuditEvent.scenario_id == scenario_id,
            )).all()
            self.assertEqual([item.transition for item in transitions], ["activated", "cleared"])
            event_types = [item.event_type for item in audit]
            self.assertIn("hmi.alarm_acknowledged", event_types)
            self.assertIn("hmi.owned_alarm_activated", event_types)
            self.assertIn("hmi.owned_alarm_cleared", event_types)

    def test_hmi_alarm_rules_must_be_unique_and_target_selected_device(self) -> None:
        base = {"role": "hmi", "summary": "Panel", "target_node_ids": "plc",
                "alarms": [{"target_node_id": "plc", "address": "DB1.DBD0", "above": 10}]}
        for config in (
            {**base, "alarms": [*base["alarms"], *base["alarms"]]},
            {**base, "alarms": [{"target_node_id": "elsewhere", "address": "DB1.DBD0", "above": 10}]},
            {**base, "alarms": [{"target_node_id": "plc", "address": "DB1.DBD0"}]},
            {**base, "alarms": [{"target_node_id": "plc", "address": "DB1.DBX0.0", "above": 1}]},
            {**base, "alarms": [{"target_node_id": "plc", "address": "DB1.DBD0", "above": 10, "delay_ms": True}]},
            {**base, "alarms": [{"target_node_id": "plc", "address": "DB1.DBD0", "above": 10, "delay_ms": 86_400_001}]},
        ):
            response = self.client.post("/api/scenarios", json={
                "name": "Invalid HMI rule", "network": {"subnet": "172.30.10.0/24"},
                "devices": [
                    {"node_id": "hmi", "device_type": "hmi", "name": "HMI", "ip": "172.30.10.10", "config": config},
                    {"node_id": "plc", "device_type": "plc-s7", "name": "PLC", "ip": "172.30.10.11",
                     "config": {"components": [{"type": "analog_sensor", "name": "Level"}]}},
                ],
            })
            self.assertEqual(response.status_code, 422, response.text)

    @patch("routes.plc_operations.hmi_operation")
    def test_hmi_owned_state_alarm_handles_false_stale_ack_and_normalization(self, read_values: object) -> None:
        created = self.client.post("/api/scenarios", json={
            "name": "HMI state alarm", "network": {"subnet": "172.30.10.0/24"},
            "devices": [
                {"node_id": "hmi", "device_type": "hmi", "name": "HMI", "ip": "172.30.10.10",
                 "config": {"role": "hmi", "summary": "Panel", "target_node_ids": "plc",
                            "alarms": [{"target_node_id": "plc", "address": "DB1.DBX0.0",
                                        "mode": "state", "expected_state": False}]}},
                {"node_id": "plc", "device_type": "plc-s7", "name": "PLC", "ip": "172.30.10.11",
                 "config": {"components": [{"type": "digital_input", "name": "Pressure switch"}]}},
            ],
        })
        self.assertEqual(created.status_code, 201, created.text)
        scenario_id = created.json()["id"]
        self.assertEqual(self.client.post(f"/api/scenarios/{scenario_id}/deploy").status_code, 200)
        read_values.return_value = [{"node_id": "plc", "address": "DB1.DBX0.0", "value": False,
                                     "data_type": "BOOL", "component_name": "Pressure switch"}]

        active = self.client.get(f"/api/scenarios/{scenario_id}/hmi/hmi/values")
        ack = self.client.post(f"/api/scenarios/{scenario_id}/hmi/hmi/alarms/acknowledge", json={
            "target_node_id": "plc", "address": "DB1.DBX0.0",
        })
        read_values.return_value = [{"node_id": "plc", "address": "DB1.DBX0.0", "value": True,
                                     "data_type": "BOOL", "component_name": "Pressure switch", "quality": "bad"}]
        stale = self.client.get(f"/api/scenarios/{scenario_id}/hmi/hmi/values")
        read_values.return_value = [{"node_id": "plc", "address": "DB1.DBX0.0", "value": True,
                                     "data_type": "BOOL", "component_name": "Pressure switch"}]
        clear = self.client.get(f"/api/scenarios/{scenario_id}/hmi/hmi/values")

        self.assertEqual(active.status_code, 200, active.text)
        alarm = next(item for item in active.json()["alarms"] if item.get("hmi_owned"))
        self.assertTrue(alarm["active"])
        self.assertEqual(alarm["mode"], "state")
        self.assertEqual(ack.status_code, 200, ack.text)
        stale_alarm = next(item for item in stale.json()["alarms"] if item.get("hmi_owned"))
        self.assertTrue(stale_alarm["active"])
        self.assertTrue(stale_alarm["stale"])
        self.assertTrue(stale_alarm["acknowledged"])
        clear_alarm = next(item for item in clear.json()["alarms"] if item.get("hmi_owned"))
        self.assertFalse(clear_alarm["active"])

    @patch("routes.plc_operations.hmi_operation")
    def test_hmi_reads_configured_modbus_values(self, read_values: object) -> None:
        read_values.return_value = [{
            "node_id": "modbus-1", "address": "coil:0", "component_name": "C1",
            "value": True, "data_type": "BOOL", "raw_hex": "01", "unit": None,
        }]
        scenario_id = self.create_running_scenario(modbus_target=True)

        response = self.client.get(f"/api/scenarios/{scenario_id}/hmi/hmi-1/values")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["values"][0]["address"], "coil:0")
        self.assertEqual(response.json()["values"][0]["access"], "read_write")
        read_values.assert_called_once()

    @patch("routes.plc_operations.scada_operation")
    def test_hmi_reads_and_acknowledges_alarms_from_associated_scada(self, operation: object) -> None:
        created = self.client.post("/api/scenarios", json={
            "name": "HMI alarms", "network": {"subnet": "172.30.10.0/24"},
            "devices": [
                {"node_id": "hmi", "device_type": "hmi", "name": "HMI", "ip": "172.30.10.10",
                 "config": {"role": "hmi", "summary": "Panel", "target_node_ids": "",
                            "alarm_source_node_ids": "scada"}},
                {"node_id": "scada", "device_type": "scada", "name": "SCADA", "ip": "172.30.10.11",
                 "config": {"target_node_ids": "plc", "alarms": [
                     {"target_node_id": "plc", "address": "DB1.DBD0", "above": 10}]}},
                {"node_id": "plc", "device_type": "plc-s7", "name": "PLC", "ip": "172.30.10.12",
                 "config": {"components": [{"type": "analog_sensor", "name": "Level"}]}},
            ],
        })
        self.assertEqual(created.status_code, 201, created.text)
        scenario_id = created.json()["id"]
        self.assertEqual(self.client.post(f"/api/scenarios/{scenario_id}/deploy").status_code, 200)
        operation.side_effect = [
            {"alarms": [{"target_node_id": "plc", "address": "DB1.DBD0", "value": 12.0,
                          "active": True, "triggered_at": "2026-10-07T12:00:00+00:00",
                          "acknowledged": False}]},
            {"acknowledged": True},
            {"alarms": [{"target_node_id": "plc", "address": "DB1.DBD0", "value": 12.0,
                          "active": True, "triggered_at": "2026-10-07T12:05:00+00:00",
                          "acknowledged": False}]},
        ]

        read = self.client.get(f"/api/scenarios/{scenario_id}/hmi/hmi/values")
        ack = self.client.post(f"/api/scenarios/{scenario_id}/hmi/hmi/alarms/acknowledge", json={
            "scada_node_id": "scada", "target_node_id": "plc", "address": "DB1.DBD0",
        })
        after_runtime_reset = self.client.get(f"/api/scenarios/{scenario_id}/hmi/hmi/values")
        forbidden = self.client.post(f"/api/scenarios/{scenario_id}/hmi/hmi/alarms/acknowledge", json={
            "scada_node_id": "other", "target_node_id": "plc", "address": "DB1.DBD0",
        })

        self.assertEqual(read.status_code, 200, read.text)
        self.assertEqual(read.json()["alarms"][0]["scada_node_id"], "scada")
        self.assertEqual(ack.status_code, 200, ack.text)
        self.assertTrue(after_runtime_reset.json()["alarms"][0]["acknowledged"])
        self.assertEqual(after_runtime_reset.json()["alarms"][0]["triggered_at"], "2026-10-07T12:00:00+00:00")
        self.assertEqual(forbidden.status_code, 403, forbidden.text)
        self.assertEqual(operation.call_args_list[1].args[2:4], ("scada", "alarms/acknowledge"))
        with Session(self.engine) as session:
            from models.audit import ScenarioAuditEvent
            events = session.exec(select(ScenarioAuditEvent).where(ScenarioAuditEvent.scenario_id == scenario_id)).all()
            self.assertIn("hmi.alarm_acknowledged", [item.event_type for item in events])

    def test_hmi_alarm_sources_must_be_same_zone_scada_devices(self) -> None:
        invalid = self.client.post("/api/scenarios", json={
            "name": "Invalid HMI alarm source", "network": {"subnet": "172.30.10.0/24"},
            "devices": [
                {"node_id": "hmi", "device_type": "hmi", "name": "HMI", "ip": "172.30.10.10",
                 "config": {"role": "hmi", "summary": "Panel", "alarm_source_node_ids": "plc"}},
                {"node_id": "plc", "device_type": "plc-s7", "name": "PLC", "ip": "172.30.10.11",
                 "config": {"components": [{"type": "valve", "name": "V1"}]}},
            ],
        })
        self.assertEqual(invalid.status_code, 422, invalid.text)

    @patch("routes.plc_operations.hmi_operation")
    def test_hmi_api_passes_explicit_cross_zone_route_to_hmi_runtime(self, operation: object) -> None:
        from test_api import ScenarioApiTests

        payload = ScenarioApiTests.payload()
        payload["network"]["zones"] = [
            {"zone_id": "control", "name": "Control", "purdue_level": "L1", "subnet": "172.30.10.0/24"},
            {"zone_id": "operations", "name": "Operations", "purdue_level": "L2", "subnet": "172.30.11.0/24"},
        ]
        payload["devices"][0].update(zone_id="control", ip="172.30.10.10")
        payload["devices"].append({
            "node_id": "hmi-1", "device_type": "hmi", "name": "HMI", "ip": "172.30.11.10",
            "zone_id": "operations", "config": {"role": "hmi", "summary": "Operator", "target_node_ids": "plc-1"},
        })
        payload["network_routes"] = [{
            "route_id": "hmi-to-plc", "source_node_id": "hmi-1", "target_node_id": "plc-1",
            "protocol": "s7", "enabled": True,
        }]
        created = self.client.post("/api/scenarios", json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        scenario_id = created.json()["id"]
        self.client.post(f"/api/scenarios/{scenario_id}/deploy")
        operation.return_value = []

        response = self.client.get(f"/api/scenarios/{scenario_id}/hmi/hmi-1/values")

        self.assertEqual(response.status_code, 200, response.text)
        route = operation.call_args.args[5]
        self.assertEqual(route["protocol"], "s7")
        self.assertNotEqual(route["host"], "172.30.10.10")
        self.assertGreaterEqual(route["port"], 20000)

    @patch("routes.plc_operations.hmi_operation")
    def test_hmi_writes_selected_writable_s7_outputs(self, write_point: object) -> None:
        scenario_id = self.create_running_scenario()
        write_point.return_value = [{"node_id": "plc-1", "address": "DB1.DBX0.0", "value": True}]

        response = self.client.post(f"/api/scenarios/{scenario_id}/hmi/hmi-1/write", json={
            "target_node_id": "plc-1", "address": "DB1.DBX0.0", "value": True,
        })

        self.assertEqual(response.status_code, 200, response.text)
        write_point.assert_called_once()
        self.assertIs(write_point.call_args.kwargs["value"], True)

    @patch("routes.plc_operations.hmi_operation")
    def test_hmi_writes_finite_real_s7_output_channel(self, write_point: object) -> None:
        scenario_id = self.create_running_scenario(real_output=True)
        write_point.return_value = [{"node_id": "plc-1", "address": "DB1.DBD2", "value": 37.5}]

        response = self.client.post(f"/api/scenarios/{scenario_id}/hmi/hmi-1/write", json={
            "target_node_id": "plc-1", "address": "DB1.DBD2", "value": 37.5,
        })
        too_large = self.client.post(f"/api/scenarios/{scenario_id}/hmi/hmi-1/write", json={
            "target_node_id": "plc-1", "address": "DB1.DBD2", "value": 1e40,
        })

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["value"], 37.5)
        self.assertEqual(too_large.status_code, 409, too_large.text)
        write_point.assert_called_once()
        self.assertEqual(write_point.call_args.kwargs["value"], 37.5)

    @patch("routes.plc_operations.write_plc_point")
    def test_hmi_cannot_write_output_owned_by_plc_control_logic(self, write_point: object) -> None:
        created = self.client.post("/api/scenarios", json={
            "name": "PLC logic permissions", "network": {"subnet": "172.30.10.0/24"},
            "devices": [
                {"node_id": "hmi-1", "device_type": "hmi", "name": "HMI", "ip": "172.30.10.10",
                 "config": {"role": "hmi", "summary": "Operator", "target_node_ids": "plc-1"}},
                {"node_id": "plc-1", "device_type": "plc-s7", "name": "PLC", "ip": "172.30.10.11",
                 "config": {"components": [
                     {"type": "analog_sensor", "name": "Level", "min_value": 0, "max_value": 100},
                     {"type": "valve", "name": "V1"}],
                     "control_rules": [{"name": "level-control", "input_component": "Level", "output_component": "V1",
                                        "mode": "below", "setpoint": 20, "hysteresis": 5}]}},
            ],
        })
        self.assertEqual(created.status_code, 201, created.text)
        scenario_id = created.json()["id"]
        self.assertEqual(self.client.post(f"/api/scenarios/{scenario_id}/deploy").status_code, 200)
        response = self.client.post(f"/api/scenarios/{scenario_id}/hmi/hmi-1/write", json={
            "target_node_id": "plc-1", "address": "DB1.DBX4.0", "value": True,
        })
        self.assertEqual(response.status_code, 403, response.text)
        write_point.assert_not_called()

    @patch("routes.plc_operations.hmi_operation")
    def test_hmi_writes_selected_modbus_coils(self, write_point: object) -> None:
        scenario_id = self.create_running_scenario(modbus_target=True)
        write_point.return_value = [{"node_id": "modbus-1", "address": "coil:0", "value": True}]

        response = self.client.post(f"/api/scenarios/{scenario_id}/hmi/hmi-1/write", json={
            "target_node_id": "modbus-1", "address": "coil:0", "value": True,
        })

        self.assertEqual(response.status_code, 200, response.text)
        write_point.assert_called_once()

    @patch("routes.plc_operations.hmi_operation")
    def test_hmi_writes_holding_register_and_rejects_read_only_registers(self, write_point: object) -> None:
        scenario_id = self.create_running_scenario(modbus_target=True)
        write_point.return_value = [{"node_id": "modbus-1", "address": "holding_register:0", "value": 900}]

        holding = self.client.post(f"/api/scenarios/{scenario_id}/hmi/hmi-1/write", json={
            "target_node_id": "modbus-1", "address": "holding_register:0", "value": 900,
        })
        input_register = self.client.post(f"/api/scenarios/{scenario_id}/hmi/hmi-1/write", json={
            "target_node_id": "modbus-1", "address": "input_register:0", "value": 900,
        })
        discrete_input = self.client.post(f"/api/scenarios/{scenario_id}/hmi/hmi-1/write", json={
            "target_node_id": "modbus-1", "address": "discrete_input:0", "value": True,
        })

        self.assertEqual(holding.status_code, 200, holding.text)
        self.assertEqual(input_register.status_code, 403, input_register.text)
        self.assertEqual(discrete_input.status_code, 403, discrete_input.text)
        self.assertEqual(write_point.call_count, 1)

    @patch("routes.plc_operations.write_plc_point")
    def test_hmi_cannot_write_unselected_or_read_only_points(self, write_point: object) -> None:
        scenario_id = self.create_running_scenario(plc_point_type="digital_input")

        unselected = self.client.post(f"/api/scenarios/{scenario_id}/hmi/hmi-1/write", json={
            "target_node_id": "other", "address": "DB1.DBX0.0", "value": True,
        })
        read_only = self.client.post(f"/api/scenarios/{scenario_id}/hmi/hmi-1/write", json={
            "target_node_id": "plc-1", "address": "DB1.DBX0.0", "value": True,
        })

        self.assertEqual(unselected.status_code, 403, unselected.text)
        self.assertEqual(read_only.status_code, 403, read_only.text)
        write_point.assert_not_called()

    @patch("routes.plc_operations.hmi_operation")
    @patch("routes.plc_operations.write_plc_point")
    def test_firewall_blocks_hmi_and_direct_console_reads_and_writes_when_port_is_not_allowed(self, write_point: object, read_values: object) -> None:
        scenario_id = self.create_running_scenario(allowed_ports=[])

        hmi = self.client.get(f"/api/scenarios/{scenario_id}/hmi/hmi-1/values")
        hmi_write = self.client.post(f"/api/scenarios/{scenario_id}/hmi/hmi-1/write", json={
            "target_node_id": "plc-1", "address": "DB1.DBX0.0", "value": True,
        })
        direct = self.client.get(f"/api/scenarios/{scenario_id}/plcs/plc-1/values")
        write = self.client.post(f"/api/scenarios/{scenario_id}/plcs/plc-1/write", json={"address": "DB1.DBX0.0", "value": True})

        self.assertEqual(hmi.status_code, 403, hmi.text)
        self.assertEqual(hmi_write.status_code, 403, hmi_write.text)
        self.assertEqual(direct.status_code, 403, direct.text)
        self.assertEqual(write.status_code, 403, write.text)
        read_values.assert_not_called()
        write_point.assert_not_called()
        audit = self.client.get(f"/api/scenarios/{scenario_id}/audit-events").json()["events"]
        rejected = [event for event in audit if event["event_type"] == "api.request_rejected"]
        self.assertEqual(len(rejected), 4)
        self.assertEqual(rejected[0]["details"]["status"], 403)
        self.assertIn("{scenario_id}", rejected[0]["details"]["route"])
        hmi_write_rejection = next(event for event in rejected if event["details"].get("source") == "hmi")
        self.assertEqual(hmi_write_rejection["details"]["source_node_id"], "hmi-1")
        self.assertEqual(hmi_write_rejection["details"]["target_node_id"], "plc-1")
        self.assertEqual(hmi_write_rejection["details"]["address"], "DB1.DBX0.0")
        self.assertNotIn("value", hmi_write_rejection["details"])
        console_write_rejection = next(event for event in rejected if event["details"].get("source") == "s7-console")
        self.assertEqual(console_write_rejection["details"]["node_id"], "plc-1")
        self.assertEqual(console_write_rejection["details"]["target_node_id"], "plc-1")
        self.assertEqual(console_write_rejection["details"]["address"], "DB1.DBX0.0")

    def test_firewall_rejection_audits_modbus_console_target_and_address(self) -> None:
        scenario_id = self.create_running_scenario(allowed_ports=[], modbus_target=True)

        response = self.client.post(
            f"/api/scenarios/{scenario_id}/modbus/modbus-1/write",
            json={"address": "coil:0", "value": True},
        )

        self.assertEqual(response.status_code, 403, response.text)
        audit = self.client.get(f"/api/scenarios/{scenario_id}/audit-events").json()["events"]
        rejected = [event for event in audit if event["event_type"] == "api.request_rejected"]
        self.assertEqual(len(rejected), 1)
        details = rejected[0]["details"]
        self.assertEqual(details["source"], "modbus-console")
        self.assertEqual(details["node_id"], "modbus-1")
        self.assertEqual(details["target_node_id"], "modbus-1")
        self.assertEqual(details["address"], "coil:0")
        self.assertNotIn("value", details)

    @patch("routes.plc_operations.read_plc_values")
    def test_firewall_allows_console_reads_when_s7_port_is_allowed(self, read_values: object) -> None:
        scenario_id = self.create_running_scenario(allowed_ports=[102])
        read_values.return_value = {"scenario_id": scenario_id, "node_id": "plc-1", "values": []}

        response = self.client.get(f"/api/scenarios/{scenario_id}/plcs/plc-1/values")

        self.assertEqual(response.status_code, 200, response.text)
        read_values.assert_called_once()

    def test_firewall_port_configuration_rejects_duplicates_and_invalid_values(self) -> None:
        for ports in ([102, 102], [0], [65536], [True]):
            with self.subTest(ports=ports):
                created = self.client.post("/api/scenarios", json={
                    "name": "Invalid", "network": {"subnet": "172.30.20.0/24"},
                    "devices": [{
                        "node_id": "fw", "device_type": "firewall", "name": "FW", "ip": "172.30.20.2",
                        "config": {"role": "firewall", "summary": "Policy", "firewall_allow_tcp_ports": ports},
                    }],
                })
                self.assertEqual(created.status_code, 422)


if __name__ == "__main__":
    unittest.main()
