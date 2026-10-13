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


class LogManager:
    def logs(self, scenario_id: str) -> dict:
        return {
            "scenario_id": scenario_id,
            "containers": [
                {"node_id": "plc-log", "name": "anvil-plc", "lines": ["PLC ready"]}
            ],
        }


class ScenarioLogApiTests(unittest.TestCase):
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
        app.dependency_overrides[get_docker_manager] = LogManager
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.engine.dispose()

    @staticmethod
    def payload() -> dict:
        return {
            "name": "logs",
            "network": {"subnet": "172.30.140.0/24"},
            "devices": [
                {
                    "node_id": "plc-log",
                    "device_type": "plc-s7",
                    "name": "PLC logs",
                    "ip": "172.30.140.10",
                    "config": {"components": [{"type": "valve", "name": "v1"}]},
                }
            ],
        }

    def test_http_and_websocket_log_snapshots(self) -> None:
        scenario_id = self.client.post("/api/scenarios", json=self.payload()).json()["id"]

        response = self.client.get(f"/api/scenarios/{scenario_id}/logs")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["containers"][0]["lines"], ["PLC ready"])

        with self.client.websocket_connect(
            f"/api/scenarios/{scenario_id}/logs"
        ) as websocket:
            event = websocket.receive_json()
        self.assertEqual(event["type"], "snapshot")
        self.assertEqual(event["containers"][0]["node_id"], "plc-log")


if __name__ == "__main__":
    unittest.main()
