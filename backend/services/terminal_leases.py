"""Shared-database leases for bounded participant and engineering terminals."""

from __future__ import annotations

import time
from uuid import uuid4

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import Session, delete, select, update

from models.auth import TerminalSessionLease


TERMINAL_SLOTS_PER_WORKER = 16
TERMINAL_LEASE_SECONDS = 20
TERMINAL_RENEW_SECONDS = 5


def make_worker_id() -> str:
    """Return a process-unique identifier; separate workers never share a quota."""
    import os
    import socket

    return f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex}"[:128]


def acquire_terminal_lease(engine, scenario_id: str, node_id: str, worker_id: str,
                           *, now: float | None = None) -> tuple[bool, str | None]:
    """Atomically reserve one station and one of the worker's 16 slots."""
    current = time.time() if now is None else now
    # Retry a concurrent slot collision: another request may have committed the
    # selected slot between our read and insert. The DB uniqueness constraints
    # remain the authority across threads and worker processes.
    for _attempt in range(3):
        with Session(engine) as session:
            try:
                session.exec(delete(TerminalSessionLease).where(TerminalSessionLease.expires_at <= current))
                active = session.exec(select(TerminalSessionLease).where(
                    TerminalSessionLease.scenario_id == scenario_id,
                    TerminalSessionLease.node_id == node_id,
                )).first()
                if active is not None:
                    session.commit()
                    return False, "station_busy"
                used = set(session.exec(select(TerminalSessionLease.slot).where(
                    TerminalSessionLease.worker_id == worker_id,
                    TerminalSessionLease.expires_at > current,
                )).all())
                slot = next((candidate for candidate in range(TERMINAL_SLOTS_PER_WORKER)
                             if candidate not in used), None)
                if slot is None:
                    session.commit()
                    return False, "worker_capacity"
                session.add(TerminalSessionLease(
                    scenario_id=scenario_id, node_id=node_id, worker_id=worker_id,
                    slot=slot, expires_at=current + TERMINAL_LEASE_SECONDS,
                ))
                session.commit()
                return True, None
            except IntegrityError:
                session.rollback()
                # The collision may be a station lease or a slot reserved by a
                # concurrent request. Re-read and retry before reporting busy.
                continue
            except OperationalError:
                session.rollback()
                raise
    with Session(engine) as session:
        session.exec(delete(TerminalSessionLease).where(TerminalSessionLease.expires_at <= current))
        active = session.exec(select(TerminalSessionLease).where(
            TerminalSessionLease.scenario_id == scenario_id,
            TerminalSessionLease.node_id == node_id,
        )).first()
        if active is not None:
            session.commit()
            return False, "station_busy"
        session.commit()
        return False, "worker_capacity"


def renew_terminal_lease(engine, scenario_id: str, node_id: str, worker_id: str,
                         *, now: float | None = None) -> bool:
    current = time.time() if now is None else now
    with Session(engine) as session:
        result = session.exec(update(TerminalSessionLease).where(
            TerminalSessionLease.scenario_id == scenario_id,
            TerminalSessionLease.node_id == node_id,
            TerminalSessionLease.worker_id == worker_id,
            TerminalSessionLease.expires_at > current,
        ).values(expires_at=current + TERMINAL_LEASE_SECONDS))
        session.commit()
        return result.rowcount == 1


def release_terminal_lease(engine, scenario_id: str, node_id: str, worker_id: str) -> None:
    with Session(engine) as session:
        session.exec(delete(TerminalSessionLease).where(
            TerminalSessionLease.scenario_id == scenario_id,
            TerminalSessionLease.node_id == node_id,
            TerminalSessionLease.worker_id == worker_id,
        ))
        session.commit()
