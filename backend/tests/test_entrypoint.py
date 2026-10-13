from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from entrypoint import prepare_sqlite_data  # noqa: E402


class BackendEntrypointTests(unittest.TestCase):
    def test_sqlite_directory_and_existing_database_are_owned_by_runtime_uid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary) / "data"
            data_dir.mkdir(mode=0o755)
            database = data_dir / "anvil.db"
            database.write_bytes(b"existing data")
            database.chmod(0o644)
            uid, gid = os.getuid(), os.getgid()

            prepare_sqlite_data(f"sqlite:///{database}", data_dir, uid, gid)

            directory_stat = data_dir.stat()
            database_stat = database.stat()
            self.assertEqual((directory_stat.st_uid, directory_stat.st_gid), (uid, gid))
            self.assertEqual(directory_stat.st_mode & 0o777, 0o700)
            self.assertEqual((database_stat.st_uid, database_stat.st_gid), (uid, gid))
            self.assertEqual(database_stat.st_mode & 0o777, 0o600)
            self.assertEqual(database.read_bytes(), b"existing data")

    def test_sqlite_path_outside_dedicated_data_directory_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data_dir = root / "data"
            data_dir.mkdir()
            outside = root / "other.db"

            with self.assertRaisesRegex(SystemExit, "inside the dedicated data directory"):
                prepare_sqlite_data(f"sqlite:///{outside}", data_dir, os.getuid(), os.getgid())


if __name__ == "__main__":
    unittest.main()
