from __future__ import annotations

import sys
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from main import create_app  # noqa: E402
from models.db import get_session  # noqa: E402
from routes.scenarios import get_docker_manager  # noqa: E402


class FakeDockerManager:
    def __init__(self) -> None:
        self.running: dict[str, list[dict[str, str]]] = {}

    def deploy(self, scenario_id: str, subnet: str, devices: list[dict]) -> dict:
        del subnet
        containers = [
            {
                "node_id": device["node_id"],
                "name": f"anvil-{device['node_id']}",
                "status": "running",
            }
            for device in devices
        ]
        self.running[scenario_id] = containers
        return {"scenario_id": scenario_id, "status": "running", "containers": containers}

    def stop(self, scenario_id: str) -> dict:
        self.running.pop(scenario_id, None)
        return {"scenario_id": scenario_id, "status": "stopped", "containers": []}

    def status(self, scenario_id: str) -> dict:
        containers = self.running.get(scenario_id, [])
        return {
            "scenario_id": scenario_id,
            "status": "running" if containers else "stopped",
            "containers": containers,
        }


class ScenarioApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(self.engine)
        self.manager = FakeDockerManager()
        app = create_app(initialize_database=False)

        def session_override():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_session] = session_override
        app.dependency_overrides[get_docker_manager] = lambda: self.manager
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.engine.dispose()

    @staticmethod
    def payload() -> dict:
        return {
            "name": "linea de prueba",
            "network": {"subnet": "172.30.10.0/24"},
            "devices": [
                {
                    "node_id": "plc-1",
                    "device_type": "plc-s7",
                    "name": "PLC principal",
                    "ip": "172.30.10.10",
                    "config": {
                        "components": [
                            {"type": "valve", "name": "entrada", "db": 1},
                            {
                                "type": "analog_sensor",
                                "name": "presion",
                                "db": 1,
                                "max_value": 10,
                                "simulation": {
                                    "mode": "sine_wave",
                                    "period_seconds": 30,
                                },
                            },
                        ]
                    },
                }
            ],
        }

    def test_crud_does_not_expose_offsets(self) -> None:
        created = self.client.post("/api/scenarios", json=self.payload())
        self.assertEqual(created.status_code, 201, created.text)
        scenario = created.json()
        scenario_id = scenario["id"]
        self.assertNotIn("byte_offset", str(scenario))

        listed = self.client.get("/api/scenarios")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()), 1)

        updated_payload = self.payload()
        updated_payload["name"] = "linea actualizada"
        updated = self.client.put(f"/api/scenarios/{scenario_id}", json=updated_payload)
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["name"], "linea actualizada")

        deleted = self.client.delete(f"/api/scenarios/{scenario_id}")
        self.assertEqual(deleted.status_code, 204)
        self.assertEqual(self.client.get(f"/api/scenarios/{scenario_id}").status_code, 404)

    def test_deploy_status_and_stop_lifecycle(self) -> None:
        scenario_id = self.client.post("/api/scenarios", json=self.payload()).json()["id"]

        deployed = self.client.post(f"/api/scenarios/{scenario_id}/deploy")
        self.assertEqual(deployed.status_code, 200, deployed.text)
        self.assertEqual(deployed.json()["status"], "running")
        self.assertEqual(len(deployed.json()["containers"]), 1)

        duplicate = self.client.post(f"/api/scenarios/{scenario_id}/deploy")
        self.assertEqual(duplicate.status_code, 409)

        current = self.client.get(f"/api/scenarios/{scenario_id}/status")
        self.assertEqual(current.json()["status"], "running")

        stopped = self.client.post(f"/api/scenarios/{scenario_id}/stop")
        self.assertEqual(stopped.status_code, 200)
        self.assertEqual(stopped.json()["status"], "stopped")

    def test_rejects_device_outside_scenario_network(self) -> None:
        payload = self.payload()
        payload["devices"][0]["ip"] = "172.31.10.10"

        response = self.client.post("/api/scenarios", json=payload)

        self.assertEqual(response.status_code, 422)
        self.assertIn("outside", response.text)


if __name__ == "__main__":
    unittest.main()
