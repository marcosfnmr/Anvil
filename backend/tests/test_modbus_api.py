from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from main import create_app  # noqa: E402
from models.db import get_session  # noqa: E402
from dependencies import get_docker_manager  # noqa: E402
from services.docker_manager import DockerManagerError  # noqa: E402


class FakeManager:
    pass


class ModbusApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)
        app = create_app(initialize_database=False)
        def session_override():
            with Session(self.engine) as session:
                yield session
        app.dependency_overrides[get_session] = session_override
        app.dependency_overrides[get_docker_manager] = lambda: FakeManager()
        self.context = TestClient(app)
        self.client = self.context.__enter__()
        payload = {"name": "modbus scenario", "network": {"subnet": "172.30.10.0/24"}, "devices": [{
            "node_id": "mb-1", "device_type": "modbus-tcp", "name": "meter", "ip": "172.30.10.11",
            "config": {"points": [
                {"type": "coil", "name": "pump"},
                {"type": "discrete_input", "name": "switch", "initial_value": True},
                {"type": "holding_register", "name": "speed", "initial_value": 10, "unit": "rpm", "min_value": 10, "max_value": 100},
                {"type": "input_register", "name": "pressure"},
            ]},
        }]}
        self.scenario_id = self.client.post("/api/scenarios", json=payload).json()["id"]
        with Session(self.engine) as session:
            from models.scenario import ScenarioRecord
            record = session.get(ScenarioRecord, self.scenario_id)
            record.status = "running"
            session.add(record)
            session.commit()

    def tearDown(self) -> None:
        self.context.__exit__(None, None, None)
        self.engine.dispose()

    def test_io_map_and_live_read(self) -> None:
        io_map = self.client.get(f"/api/scenarios/{self.scenario_id}/io-map").json()
        self.assertEqual([point["access"] for point in io_map["points"]], ["read_write", "read", "read_write", "read"])
        with patch("routes.plc_operations.read_modbus_values", return_value={"scenario_id": self.scenario_id, "node_id": "mb-1", "values": []}):
            response = self.client.get(f"/api/scenarios/{self.scenario_id}/modbus/mb-1/values")
        self.assertEqual(response.status_code, 200, response.text)

    def test_live_read_marks_register_outside_declared_range(self) -> None:
        value = {"node_id": "mb-1", "address": "holding_register:0", "component_name": "speed",
                 "data_type": "UINT16", "value": 120, "raw_hex": "0078", "unit": "rpm"}
        with patch("routes.plc_operations.read_modbus_values", return_value={
            "scenario_id": self.scenario_id, "node_id": "mb-1", "values": [value],
        }):
            response = self.client.get(f"/api/scenarios/{self.scenario_id}/modbus/mb-1/values")
        self.assertEqual(response.status_code, 200, response.text)
        point = response.json()["values"][0]
        self.assertEqual(point["quality"], "out_of_range")
        self.assertEqual(point["protocol_quality"], "good")
        self.assertEqual((point["min_value"], point["max_value"], point["unit"]), (10, 100, "rpm"))

    def test_reads_audit_only_quality_transitions_and_never_store_values(self) -> None:
        value = {"node_id": "mb-1", "address": "coil:0", "component_name": "pump",
                 "data_type": "BOOL", "value": True, "raw_hex": "01"}
        with patch("routes.plc_operations.read_modbus_values", return_value={
            "scenario_id": self.scenario_id, "node_id": "mb-1", "values": [value],
        }) as read:
            self.assertEqual(self.client.get(f"/api/scenarios/{self.scenario_id}/modbus/mb-1/values").status_code, 200)
            self.assertEqual(self.client.get(f"/api/scenarios/{self.scenario_id}/modbus/mb-1/values").status_code, 200)
            read.side_effect = DockerManagerError("offline")
            self.assertEqual(self.client.get(f"/api/scenarios/{self.scenario_id}/modbus/mb-1/values").status_code, 502)
            read.side_effect = None
            read.return_value = {"scenario_id": self.scenario_id, "node_id": "mb-1", "values": [value]}
            self.assertEqual(self.client.get(f"/api/scenarios/{self.scenario_id}/modbus/mb-1/values").status_code, 200)
        events = self.client.get(f"/api/scenarios/{self.scenario_id}/audit-events").json()["events"]
        reads = [item for item in events if item["event_type"].startswith("protocol.read_")]
        self.assertEqual([item["event_type"] for item in reversed(reads)], [
            "protocol.read_good", "protocol.read_failed", "protocol.read_good",
        ])
        self.assertTrue(all("value" not in item["details"] for item in reads))
        self.assertEqual([item["details"]["point_count"] for item in reversed(reads)], [1, 0, 1])

    def test_writes_read_write_points_and_rejects_read_only_inputs(self) -> None:
        with patch("routes.plc_operations.write_modbus_point", side_effect=lambda manager, scenario, runtime, point, value: {"scenario_id": scenario, "node_id": "mb-1", "address": f"{point['type']}:{point['address']}", "component_name": point["name"], "data_type": "BOOL" if point["type"] == "coil" else "UINT16", "value": value, "raw_hex": "01", "unit": None}):
            coil = self.client.post(f"/api/scenarios/{self.scenario_id}/modbus/mb-1/write", json={"address": "coil:0", "value": True})
            register = self.client.post(f"/api/scenarios/{self.scenario_id}/modbus/mb-1/write", json={"address": "holding_register:0", "value": 99})
        denied = self.client.post(f"/api/scenarios/{self.scenario_id}/modbus/mb-1/write", json={"address": "input_register:0", "value": 99})
        denied_di = self.client.post(f"/api/scenarios/{self.scenario_id}/modbus/mb-1/write", json={"address": "discrete_input:0", "value": True})
        outside_range = self.client.post(f"/api/scenarios/{self.scenario_id}/modbus/mb-1/write", json={"address": "holding_register:0", "value": 5})
        self.assertEqual(coil.status_code, 200, coil.text)
        self.assertEqual(register.status_code, 200, register.text)
        self.assertEqual(denied.status_code, 403, denied.text)
        self.assertEqual(denied_di.status_code, 403, denied_di.text)
        self.assertEqual(outside_range.status_code, 409, outside_range.text)


if __name__ == "__main__":
    unittest.main()
