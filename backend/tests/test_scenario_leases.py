from __future__ import annotations

import sys
import unittest
from pathlib import Path

from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from models.scenario import ScenarioOperationLease, ScenarioRecord  # noqa: E402,F401
from services.scenario_leases import (  # noqa: E402
    acquire_operation_lease,
    operation_lease_active,
    release_operation_lease,
    renew_operation_lease,
)


class ScenarioOperationLeaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            session.add(ScenarioRecord(
                id="scenario-lease", name="lease", network_json='{"subnet":"172.30.60.0/24"}',
                devices_json="[]", runtime_devices_json="[]", status="stopped",
            ))
            session.commit()

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_lease_is_exclusive_and_renewal_is_owner_scoped(self) -> None:
        self.assertTrue(acquire_operation_lease(
            self.engine, "scenario-lease", "deploy", "worker-a", now=100, lease_seconds=30,
        ))
        self.assertFalse(acquire_operation_lease(
            self.engine, "scenario-lease", "stop", "worker-b", now=101, lease_seconds=30,
        ))
        self.assertFalse(renew_operation_lease(
            self.engine, "scenario-lease", "deploy", "worker-b", now=102,
        ))
        self.assertTrue(renew_operation_lease(
            self.engine, "scenario-lease", "deploy", "worker-a", now=102, lease_seconds=30,
        ))
        self.assertTrue(operation_lease_active(self.engine, "scenario-lease", now=131))
        self.assertFalse(operation_lease_active(self.engine, "scenario-lease", now=133))

    def test_expired_lease_can_be_claimed_for_reconciliation(self) -> None:
        self.assertTrue(acquire_operation_lease(
            self.engine, "scenario-lease", "deploy", "worker-a", now=100, lease_seconds=30,
        ))
        self.assertTrue(acquire_operation_lease(
            self.engine, "scenario-lease", "reconcile", "worker-b", now=130, lease_seconds=30,
        ))
        with Session(self.engine) as session:
            lease = session.get(ScenarioOperationLease, "scenario-lease")
        self.assertEqual(lease.operation, "reconcile")
        self.assertEqual(lease.owner_id, "worker-b")

    def test_release_only_removes_the_matching_owner_operation(self) -> None:
        self.assertTrue(acquire_operation_lease(
            self.engine, "scenario-lease", "deploy", "worker-a", now=100, lease_seconds=30,
        ))
        release_operation_lease(self.engine, "scenario-lease", "deploy", "worker-b")
        self.assertTrue(operation_lease_active(self.engine, "scenario-lease", now=101))
        release_operation_lease(self.engine, "scenario-lease", "deploy", "worker-a")
        self.assertFalse(operation_lease_active(self.engine, "scenario-lease", now=101))

    def test_lease_cannot_be_created_for_a_deleted_scenario(self) -> None:
        with Session(self.engine) as session:
            scenario = session.get(ScenarioRecord, "scenario-lease")
            session.delete(scenario)
            session.commit()
        self.assertFalse(acquire_operation_lease(
            self.engine, "scenario-lease", "deploy", "late-worker",
        ))
        with Session(self.engine) as session:
            self.assertIsNone(session.get(ScenarioOperationLease, "scenario-lease"))


if __name__ == "__main__":
    unittest.main()
