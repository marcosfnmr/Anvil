from __future__ import annotations

import sys
import unittest
from pathlib import Path

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from models.scenario import ScenarioRecord  # noqa: E402
from services.reconciliation import reconcile_runtime  # noqa: E402


class FakeRuntimeManager:
    def __init__(self) -> None:
        self.known_ids: set[str] | None = None

    def cleanup_orphans(self, known_ids: set[str]) -> dict[str, int]:
        self.known_ids = known_ids
        return {"containers": 1, "networks": 1}

    def status(self, scenario_id: str) -> dict:
        return {"scenario_id": scenario_id, "status": "stopped", "containers": []}


class ReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_runtime_state_and_orphans_are_reconciled(self) -> None:
        record = ScenarioRecord(
            id="scenario-1",
            name="reconcile",
            network_json='{"subnet": "172.30.50.0/24"}',
            devices_json="[]",
            runtime_devices_json="[]",
            status="running",
        )
        manager = FakeRuntimeManager()

        with Session(self.engine) as session:
            session.add(record)
            session.commit()
            result = reconcile_runtime(session, manager, cleanup_orphans=True)
            session.expire_all()
            reconciled = session.get(ScenarioRecord, "scenario-1")

        self.assertIsNotNone(reconciled)
        self.assertEqual(reconciled.status, "stopped")
        self.assertEqual(manager.known_ids, {"scenario-1"})
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["removed"], {"containers": 1, "networks": 1})
