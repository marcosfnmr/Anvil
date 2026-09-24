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


class EventDockerManager:
    def status(self, scenario_id: str) -> dict:
        return {"scenario_id": scenario_id, "status": "stopped", "containers": []}


class ScenarioEventTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(self.engine)
        app = create_app(initialize_database=False)

        def session_override():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_session] = session_override
        app.dependency_overrides[get_docker_manager] = EventDockerManager
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.engine.dispose()

    @staticmethod
    def payload() -> dict:
        return {
            "name": "eventos",
            "network": {"subnet": "172.30.120.0/24"},
            "devices": [
                {
                    "node_id": "plc-events",
                    "device_type": "plc-s7",
                    "name": "PLC eventos",
                    "ip": "172.30.120.10",
                    "config": {
                        "components": [{"type": "valve", "name": "v1", "db": 1}]
                    },
                }
            ],
        }

    def test_websocket_sends_initial_runtime_snapshot(self) -> None:
        scenario_id = self.client.post("/api/scenarios", json=self.payload()).json()["id"]

        with self.client.websocket_connect(
            f"/api/scenarios/{scenario_id}/events"
        ) as websocket:
            event = websocket.receive_json()

        self.assertEqual(event["type"], "snapshot")
        self.assertEqual(event["scenario_id"], scenario_id)
        self.assertEqual(event["status"], "stopped")
        self.assertEqual(event["containers"], [])
        self.assertIn("timestamp", event)

    def test_websocket_reports_missing_scenario_and_closes(self) -> None:
        missing_id = "00000000-0000-0000-0000-000000000001"

        with self.client.websocket_connect(
            f"/api/scenarios/{missing_id}/events"
        ) as websocket:
            event = websocket.receive_json()

        self.assertEqual(event["type"], "error")
        self.assertIn("was not found", event["detail"])


if __name__ == "__main__":
    unittest.main()
