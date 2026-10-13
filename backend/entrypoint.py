"""Prepare the dedicated SQLite data directory, then drop backend privileges."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from sqlalchemy.engine import make_url


def _numeric_setting(name: str, default: int) -> int:
    value = os.environ.get(name, str(default))
    if not value.isascii() or not value.isdecimal():
        raise SystemExit(f"{name} must be a non-negative numeric ID")
    return int(value)


def prepare_sqlite_data(database_url: str, data_dir: Path, uid: int, gid: int) -> None:
    """Make only the configured dedicated data directory writable by the app UID."""
    url = make_url(database_url)
    if not url.drivername.startswith("sqlite") or not url.database or url.database == ":memory:":
        return
    database = Path(url.database)
    if not database.is_absolute():
        database = Path.cwd() / database
    data_root = data_dir.resolve()
    parent = database.parent.resolve()
    if parent != data_root and data_root not in parent.parents:
        raise SystemExit(
            f"SQLite path must be inside the dedicated data directory {data_root}: {database}"
        )
    parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chown(parent, uid, gid)
    except OSError as error:
        raise OSError(error.errno, f"chown of dedicated data directory failed: {error.strerror}", str(parent)) from error
    try:
        os.chmod(parent, 0o711)
    except OSError as error:
        raise OSError(error.errno, f"chmod of dedicated data directory failed: {error.strerror}", str(parent)) from error
    # A SQLite file on an existing bind mount can start unreadable to the
    # bootstrap process. Its parent is a dedicated, trusted /data mount; make
    # the directory traversable only for bootstrap, then restrict it to the
    # application UID before privilege drop.
    for candidate in (database, Path(f"{database}-journal"),
                      Path(f"{database}-wal"), Path(f"{database}-shm")):
        try:
            os.chown(candidate, uid, gid)
            os.chmod(candidate, 0o600)
        except FileNotFoundError:
            pass
    os.chmod(parent, 0o700)


def main() -> None:
    uid = _numeric_setting("ANVIL_BACKEND_UID", 65534)
    gid = _numeric_setting("ANVIL_BACKEND_GID", 65534)
    data_dir = Path(os.environ.get("ANVIL_DATA_DIR", "/data"))
    database_url = os.environ.get("ANVIL_DATABASE_URL", "sqlite:////data/anvil.db")
    try:
        prepare_sqlite_data(database_url, data_dir, uid, gid)
    except OSError as error:
        capabilities = next((line.split(":", 1)[1].strip() for line in
                             Path("/proc/self/status").read_text().splitlines()
                             if line.startswith("CapEff:")), "unknown")
        raise SystemExit(
            f"Unable to prepare Anvil data directory: {error} "
            f"(effective uid={os.geteuid()}, capabilities={capabilities})"
        ) from error

    command = sys.argv[1:] or ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
    os.umask(0o077)
    os.setgroups([])
    os.setgid(gid)
    os.setuid(uid)
    os.execvp(command[0], command)


if __name__ == "__main__":
    main()
