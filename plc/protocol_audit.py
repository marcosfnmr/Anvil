"""Bounded durable queue for value-free S7 and Modbus audit metadata."""

from __future__ import annotations

import hmac
import json
import os
import sqlite3
import sys
import threading
from pathlib import Path
from typing import Any


DEFAULT_PATH = "/var/lib/anvil/protocol-audit/events.sqlite3"
DEFAULT_CAPACITY = 100_000
DEFAULT_MAX_BYTES = 32 * 1024 * 1024
MAX_LINE_BYTES = 4096


class ProtocolAuditQueue:
    """Store protocol write/failure metadata until the backend commits it."""

    def __init__(self, path: str | None = None, *, capacity: int = DEFAULT_CAPACITY,
                 max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        self.path = path or os.environ.get("ANVIL_PROTOCOL_AUDIT_PATH", DEFAULT_PATH)
        self.capacity = max(1, int(capacity))
        self.max_bytes = max(1024, int(max_bytes))
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(
            self.path, check_same_thread=False, timeout=5,
        )
        self.connection.execute("PRAGMA busy_timeout=5000")
        if self.path != ":memory:":
            self.connection.execute("PRAGMA journal_mode=DELETE")
            self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                line TEXT NOT NULL,
                payload_bytes INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS state (
                key TEXT PRIMARY KEY,
                value INTEGER NOT NULL
            );
            INSERT OR IGNORE INTO state(key, value) VALUES ('acked_through', 0);
            INSERT OR IGNORE INTO state(key, value) VALUES ('dropped_events', 0);
            INSERT OR IGNORE INTO state(key, value)
                SELECT 'queued_count', COUNT(*) FROM events;
            INSERT OR IGNORE INTO state(key, value)
                SELECT 'queued_bytes', COALESCE(SUM(payload_bytes), 0) FROM events;
        """)
        self.connection.commit()
        self.lock = threading.RLock()
        if self.path != ":memory:":
            try:
                Path(self.path).chmod(0o600)
            except OSError:
                pass

    def append(self, line: str) -> dict[str, int]:
        """Append a bounded single-line event; reject payload-bearing/bad lines upstream."""
        if not isinstance(line, str) or not line or "\n" in line or "\r" in line:
            raise ValueError("protocol audit line must be one non-empty line")
        size = len(line.encode("utf-8"))
        with self.lock, self.connection:
            if size > min(MAX_LINE_BYTES, self.max_bytes):
                self.connection.execute("UPDATE state SET value=value+1 WHERE key='dropped_events'")
                return self.stats_locked()
            self.connection.execute(
                "INSERT INTO events(line, payload_bytes) VALUES (?, ?)", (line, size),
            )
            self.connection.execute("UPDATE state SET value=value+1 WHERE key='queued_count'")
            self.connection.execute("UPDATE state SET value=value+? WHERE key='queued_bytes'", (size,))
            count = self._state("queued_count")
            total_bytes = self._state("queued_bytes")
            dropped = 0
            while count > self.capacity or total_bytes > self.max_bytes:
                oldest = self.connection.execute(
                    "SELECT id, payload_bytes FROM events ORDER BY id LIMIT 1"
                ).fetchone()
                if oldest is None:
                    break
                self.connection.execute("DELETE FROM events WHERE id=?", (oldest[0],))
                count -= 1
                total_bytes -= int(oldest[1])
                dropped += 1
            if dropped:
                self.connection.execute("UPDATE state SET value=? WHERE key='queued_count'", (count,))
                self.connection.execute("UPDATE state SET value=? WHERE key='queued_bytes'", (total_bytes,))
                self.connection.execute(
                    "UPDATE state SET value=value+? WHERE key='dropped_events'", (dropped,),
                )
            return self.stats_locked()

    def pending(self, limit: int = 1000) -> dict[str, Any]:
        limit = max(1, min(10_000, int(limit)))
        with self.lock:
            acked = self._state("acked_through")
            rows = self.connection.execute(
                "SELECT id, line FROM events WHERE id>? ORDER BY id LIMIT ?", (acked, limit),
            ).fetchall()
            return {
                "events": [{"id": int(row[0]), "line": str(row[1])} for row in rows],
                "cursor": int(rows[-1][0]) if rows else acked,
                "dropped_events": self._state("dropped_events"),
                "pending_count": self._state("queued_count"),
            }

    def acknowledge(self, cursor: int) -> dict[str, int]:
        if type(cursor) is not int or cursor < 0:
            raise ValueError("cursor must be a non-negative integer")
        with self.lock, self.connection:
            highest = int(self.connection.execute(
                "SELECT COALESCE(MAX(id), 0) FROM events"
            ).fetchone()[0])
            acked = self._state("acked_through")
            if cursor == acked:
                return self.stats_locked()
            if cursor > highest or cursor < acked:
                raise ValueError("cursor is outside the pending protocol audit range")
            acknowledged = self.connection.execute(
                "SELECT COUNT(*), COALESCE(SUM(payload_bytes), 0) FROM events WHERE id<=?", (cursor,),
            ).fetchone()
            self.connection.execute("UPDATE state SET value=? WHERE key='acked_through'", (cursor,))
            self.connection.execute("DELETE FROM events WHERE id<=?", (cursor,))
            self.connection.execute("UPDATE state SET value=value-? WHERE key='queued_count'", (int(acknowledged[0]),))
            self.connection.execute("UPDATE state SET value=value-? WHERE key='queued_bytes'", (int(acknowledged[1]),))
            return self.stats_locked()

    def stats(self) -> dict[str, int]:
        with self.lock:
            return self.stats_locked()

    def stats_locked(self) -> dict[str, int]:
        return {
            "pending_count": self._state("queued_count"),
            "dropped_events": self._state("dropped_events"),
        }

    def _state(self, key: str) -> int:
        row = self.connection.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return int(row[0]) if row else 0

    def close(self) -> None:
        with self.lock:
            self.connection.close()


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    queue = ProtocolAuditQueue()
    try:
        if len(args) == 2 and args[0] == "pending":
            try:
                limit = int(args[1])
            except ValueError:
                return 2
            print(json.dumps(queue.pending(limit), separators=(",", ":"), allow_nan=False))
            return 0
        if len(args) == 2 and args[0] == "ack":
            expected = os.environ.get("ANVIL_PROTOCOL_AUDIT_ACK_TOKEN", "")
            supplied = os.environ.get("ANVIL_PROTOCOL_AUDIT_EXEC_TOKEN", "")
            if not expected or not supplied or not hmac.compare_digest(expected, supplied):
                return 3
            try:
                cursor = int(args[1])
            except ValueError:
                return 2
            print(json.dumps(queue.acknowledge(cursor), separators=(",", ":"), allow_nan=False))
            return 0
        return 2
    finally:
        queue.close()


if __name__ == "__main__":
    raise SystemExit(main())
