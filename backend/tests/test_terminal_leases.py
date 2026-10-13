from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from sqlmodel import SQLModel, create_engine, select

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from models.db import TerminalSessionLease  # noqa: E402,F401
from services.terminal_leases import (  # noqa: E402
    TERMINAL_LEASE_SECONDS,
    TERMINAL_SLOTS_PER_WORKER,
    acquire_terminal_lease,
    release_terminal_lease,
    renew_terminal_lease,
)
from sqlmodel import Session  # noqa: E402


class TerminalLeaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.engine = create_engine(
            f"sqlite:///{Path(self.temp_dir.name) / 'leases.db'}",
            connect_args={"check_same_thread": False},
        )
        SQLModel.metadata.create_all(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temp_dir.cleanup()

    def test_station_is_unique_across_workers_and_release_allows_reopen(self) -> None:
        self.assertEqual(acquire_terminal_lease(self.engine, "scenario-a", "station-1", "worker-a", now=100), (True, None))
        self.assertEqual(acquire_terminal_lease(self.engine, "scenario-a", "station-1", "worker-b", now=101), (False, "station_busy"))
        release_terminal_lease(self.engine, "scenario-a", "station-1", "worker-a")
        self.assertEqual(acquire_terminal_lease(self.engine, "scenario-a", "station-1", "worker-b", now=102), (True, None))

    def test_worker_has_at_most_sixteen_simultaneous_slots(self) -> None:
        for number in range(TERMINAL_SLOTS_PER_WORKER):
            self.assertEqual(
                acquire_terminal_lease(self.engine, "scenario-a", f"station-{number}", "worker-a", now=100),
                (True, None),
            )
        self.assertEqual(acquire_terminal_lease(self.engine, "scenario-a", "station-overflow", "worker-a", now=100), (False, "worker_capacity"))
        self.assertEqual(acquire_terminal_lease(self.engine, "scenario-a", "station-overflow", "worker-b", now=100), (True, None))

    def test_lease_renews_and_expired_lease_can_be_reclaimed(self) -> None:
        self.assertEqual(acquire_terminal_lease(self.engine, "scenario-a", "station-1", "worker-a", now=100), (True, None))
        self.assertTrue(renew_terminal_lease(self.engine, "scenario-a", "station-1", "worker-a", now=110))
        with Session(self.engine) as session:
            lease = session.exec(select(TerminalSessionLease)).one()
            self.assertEqual(lease.expires_at, 110 + TERMINAL_LEASE_SECONDS)
        self.assertFalse(renew_terminal_lease(self.engine, "scenario-a", "station-1", "worker-a", now=131))
        self.assertEqual(acquire_terminal_lease(self.engine, "scenario-a", "station-1", "worker-b", now=131), (True, None))


if __name__ == "__main__":
    unittest.main()
