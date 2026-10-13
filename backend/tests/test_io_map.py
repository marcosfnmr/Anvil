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


class ScenarioIoMapTests(unittest.TestCase):
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
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.engine.dispose()

    def test_io_map_exposes_addresses_but_not_private_offsets(self) -> None:
        payload = {
            "name": "mapa s7",
            "network": {"subnet": "172.30.130.0/24"},
            "devices": [
                {
                    "node_id": "plc-map",
                    "device_type": "plc-s7",
                    "name": "PLC mapa",
                    "ip": "172.30.130.10",
                    "config": {
                        "components": [
                            {"type": "valve", "name": "Válvula 1", "db": 1},
                            {
                                "type": "analog_sensor",
                                "name": "Presión",
                                "db": 1,
                                "unit": "bar",
                            },
                        ]
                    },
                }
            ],
        }
        created = self.client.post("/api/scenarios", json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        scenario_id = created.json()["id"]

        response = self.client.get(f"/api/scenarios/{scenario_id}/io-map")

        self.assertEqual(response.status_code, 200, response.text)
        points = response.json()["points"]
        self.assertEqual([point["address"] for point in points], ["DB1.DBX0.0", "DB1.DBD2"])
        self.assertEqual(points[0]["access"], "read_write")
        self.assertEqual(points[1]["unit"], "bar")
        self.assertNotIn("byte_offset", response.text)
        self.assertNotIn("bit_offset", response.text)

    def test_io_map_exposes_non_db_area_and_s7_output_permissions(self):
        payload = {"name":"areas","network":{"subnet":"172.30.131.0/24"},"devices":[{
            "node_id":"plc-area","device_type":"plc-s7","name":"PLC","ip":"172.30.131.10",
            "config":{"components":[
                {"type":"io_channel","name":"marker","area":"M","direction":"output","data_type":"BOOL"},
                {"type":"io_channel","name":"input","area":"I","direction":"input","data_type":"BOOL"},
                {"type":"io_channel","name":"output","area":"Q","direction":"output","data_type":"REAL"},
            ]},
        }]}
        created=self.client.post("/api/scenarios",json=payload); self.assertEqual(created.status_code,201,created.text)
        points=self.client.get(f"/api/scenarios/{created.json()['id']}/io-map").json()["points"]
        self.assertEqual([(p["address"],p["area"],p["db"],p["access"]) for p in points],
                         [("M0.0","M",None,"read_write"),("I0.0","I",None,"read"),("QD0","Q",None,"read_write")])


if __name__ == "__main__":
    unittest.main()
