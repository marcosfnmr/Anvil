from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import create_engine


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from models.db import protect_sqlite_database_files  # noqa: E402


class SqliteFileSecurityTests(unittest.TestCase):
    def test_database_and_existing_sidecars_are_restricted_and_umask_protects_new_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="anvil-db-permissions-") as directory:
            path = Path(directory) / "anvil.db"
            database_engine = create_engine(f"sqlite:///{path}")
            try:
                with database_engine.connect():
                    pass
                sidecars = [Path(f"{path}-journal"), Path(f"{path}-wal"), Path(f"{path}-shm")]
                for candidate in (path, *sidecars):
                    candidate.touch(exist_ok=True)
                    candidate.chmod(0o644)

                original_umask = os.umask(0o022)
                try:
                    protect_sqlite_database_files(database_engine)
                    current_umask = os.umask(0o022)
                    os.umask(current_umask)
                    self.assertEqual(current_umask, 0o077)

                    created_after_start = Path(directory) / "later-journal"
                    created_after_start.touch()
                    self.assertEqual(stat.S_IMODE(created_after_start.stat().st_mode), 0o600)
                finally:
                    os.umask(original_umask)

                for candidate in (path, *sidecars):
                    self.assertEqual(stat.S_IMODE(candidate.stat().st_mode), 0o600, candidate.name)
            finally:
                database_engine.dispose()

    def test_in_memory_sqlite_does_not_change_umask(self) -> None:
        database_engine = create_engine("sqlite://", poolclass=StaticPool)
        original_umask = os.umask(0o022)
        try:
            protect_sqlite_database_files(database_engine)
            current_umask = os.umask(0o022)
            os.umask(current_umask)
            self.assertEqual(current_umask, 0o022)
        finally:
            os.umask(original_umask)
            database_engine.dispose()

    def test_application_startup_applies_file_protection_before_database_creation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="anvil-db-startup-security-") as directory:
            path = Path(directory) / "startup.db"
            database_engine = create_engine(f"sqlite:///{path}")
            events: list[str] = []

            def protect_before_creation(engine) -> None:
                self.assertFalse(path.exists())
                events.append("protect")
                protect_sqlite_database_files(engine)

            def create_database() -> None:
                events.append("create")
                path.touch()

            async def wait_for_shutdown(stop, *args) -> None:
                await stop.wait()

            import main  # noqa: PLC0415

            try:
                with (
                    patch.object(main, "engine", database_engine),
                    patch.object(main, "protect_sqlite_database_files", side_effect=protect_before_creation),
                    patch.object(main, "create_db_and_tables", side_effect=create_database),
                    patch.object(main, "reconcile_runtime", return_value={"errors": []}),
                    patch.object(main, "DockerManager", return_value=object()),
                    patch.object(main, "interrupt_active_exercises", return_value=0),
                    patch.object(main, "exercise_scheduler", side_effect=wait_for_shutdown),
                    patch.object(main, "scada_historian_scheduler", side_effect=wait_for_shutdown),
                    patch.object(main, "protocol_trace_scheduler", side_effect=wait_for_shutdown),
                    patch.object(main, "deployment_recovery_scheduler", side_effect=wait_for_shutdown),
                    patch.object(main, "configured_audit_retention_days", return_value=0),
                    patch.object(main.scenario_event_hub, "shutdown", new=AsyncMock()),
                    patch.object(main.scenario_log_hub, "shutdown", new=AsyncMock()),
                    TestClient(main.create_app(initialize_database=True, auth_enabled=False)) as client,
                ):
                    self.assertEqual(client.get("/api/health").status_code, 200)
                self.assertEqual(events, ["protect", "create"])
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            finally:
                database_engine.dispose()


if __name__ == "__main__":
    unittest.main()
