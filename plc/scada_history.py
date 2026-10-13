"""Durable, bounded store-and-forward queue for SCADA samples."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any


class DurableHistory:
    def __init__(self, path: str | None, capacity: int, max_bytes: int = 134_217_728) -> None:
        self.capacity = max(1, int(capacity))
        self.max_bytes = max(1, int(max_bytes))
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path or ":memory:", check_same_thread=False, timeout=5)
        self.connection.execute("PRAGMA busy_timeout=5000")
        if path and path != ":memory:":
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA synchronous=FULL")
            self.connection.execute("PRAGMA wal_autocheckpoint=1000")
            self.connection.execute("PRAGMA journal_size_limit=8388608")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS state (
                key TEXT PRIMARY KEY,
                value INTEGER NOT NULL
            );
            INSERT OR IGNORE INTO state(key, value) VALUES ('acked_through', 0);
            INSERT OR IGNORE INTO state(key, value) VALUES ('dropped_samples', 0);
        """)
        self.connection.commit()
        self.lock = threading.RLock()

    def append(self, sample: dict[str, Any]) -> dict[str, int]:
        payload = json.dumps(sample, allow_nan=False, separators=(",", ":"))
        with self.lock, self.connection:
            payload_bytes = len(payload.encode("utf-8"))
            if payload_bytes > self.max_bytes:
                self.connection.execute("UPDATE state SET value=value+1 WHERE key='dropped_samples'")
                return self.stats_locked()
            self.connection.execute("INSERT INTO samples(payload) VALUES (?)", (payload,))
            count = int(self.connection.execute("SELECT COUNT(*) FROM samples").fetchone()[0])
            total_bytes = int(self.connection.execute("SELECT COALESCE(SUM(LENGTH(payload)), 0) FROM samples").fetchone()[0])
            dropped = 0
            while count > self.capacity or total_bytes > self.max_bytes:
                oldest = self.connection.execute("SELECT id, LENGTH(payload) FROM samples ORDER BY id LIMIT 1").fetchone()
                if oldest is None:
                    break
                self.connection.execute("DELETE FROM samples WHERE id=?", (oldest[0],))
                total_bytes -= int(oldest[1])
                count -= 1
                dropped += 1
            if dropped:
                self.connection.execute("UPDATE state SET value=value+? WHERE key='dropped_samples'", (dropped,))
            return self.stats_locked()

    def pending(self, limit: int) -> dict[str, Any]:
        limit = max(1, min(10_000, int(limit)))
        with self.lock:
            acked = self._state("acked_through")
            rows = self.connection.execute(
                "SELECT id, payload FROM samples WHERE id>? ORDER BY id LIMIT ?", (acked, limit)
            ).fetchall()
            samples = [json.loads(row[1]) for row in rows]
            return {
                "samples": samples,
                "cursor": int(rows[-1][0]) if rows else acked,
                "dropped_samples": self._state("dropped_samples"),
                "pending_count": int(self.connection.execute("SELECT COUNT(*) FROM samples WHERE id>?", (acked,)).fetchone()[0]),
            }

    def acknowledge(self, cursor: int) -> dict[str, int]:
        if type(cursor) is not int or cursor < 0:
            raise ValueError("cursor must be a non-negative integer")
        with self.lock, self.connection:
            highest = int(self.connection.execute("SELECT COALESCE(MAX(id), 0) FROM samples").fetchone()[0])
            acked = self._state("acked_through")
            if cursor == acked:
                return self.stats_locked()
            if cursor > highest or cursor < acked:
                raise ValueError("cursor is outside the pending history range")
            self.connection.execute("UPDATE state SET value=? WHERE key='acked_through'", (cursor,))
            self.connection.execute("DELETE FROM samples WHERE id<=?", (cursor,))
            return self.stats_locked()

    def recent(self, limit: int) -> list[dict[str, Any]]:
        limit = max(1, min(10_000, int(limit)))
        with self.lock:
            rows = self.connection.execute("SELECT payload FROM samples ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return [json.loads(row[0]) for row in reversed(rows)]

    def stats(self) -> dict[str, int]:
        with self.lock:
            return self.stats_locked()

    def stats_locked(self) -> dict[str, int]:
        return {
            "pending_count": int(self.connection.execute("SELECT COUNT(*) FROM samples").fetchone()[0]),
            "dropped_samples": self._state("dropped_samples"),
        }

    def _state(self, key: str) -> int:
        row = self.connection.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return int(row[0]) if row else 0

    def close(self) -> None:
        with self.lock:
            self.connection.close()
