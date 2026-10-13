"""Shared-database leases for scenario lifecycle operations."""

from __future__ import annotations

import threading
import time
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, delete, select, update

from models.scenario import ScenarioOperationLease, ScenarioRecord


LEASE_SECONDS = 45
RENEW_SECONDS = 10


def acquire_operation_lease(engine, scenario_id: str, operation: str, owner_id: str,
                           *, now: float | None = None, lease_seconds: int = LEASE_SECONDS) -> bool:
    """Acquire an absent/expired scenario lease atomically across workers."""
    current = time.time() if now is None else now
    with Session(engine) as session:
        reclaimed = session.exec(update(ScenarioOperationLease).where(
            ScenarioOperationLease.scenario_id == scenario_id,
            ScenarioOperationLease.expires_at <= current,
        ).values(owner_id=owner_id, operation=operation, expires_at=current + lease_seconds))
        if reclaimed.rowcount == 1:
            if session.get(ScenarioRecord, scenario_id) is None:
                session.rollback()
                return False
            session.commit()
            return True
        session.add(ScenarioOperationLease(
            scenario_id=scenario_id, owner_id=owner_id, operation=operation,
            expires_at=current + lease_seconds,
        ))
        try:
            session.flush()
            if session.get(ScenarioRecord, scenario_id) is None:
                session.rollback()
                return False
            session.commit()
            return True
        except IntegrityError:
            session.rollback()
            return False


def renew_operation_lease(engine, scenario_id: str, operation: str, owner_id: str,
                          *, now: float | None = None, lease_seconds: int = LEASE_SECONDS) -> bool:
    current = time.time() if now is None else now
    with Session(engine) as session:
        result = session.exec(update(ScenarioOperationLease).where(
            ScenarioOperationLease.scenario_id == scenario_id,
            ScenarioOperationLease.operation == operation,
            ScenarioOperationLease.owner_id == owner_id,
            ScenarioOperationLease.expires_at > current,
        ).values(expires_at=current + lease_seconds))
        session.commit()
        return result.rowcount == 1


def release_operation_lease(engine, scenario_id: str, operation: str, owner_id: str) -> None:
    with Session(engine) as session:
        session.exec(delete(ScenarioOperationLease).where(
            ScenarioOperationLease.scenario_id == scenario_id,
            ScenarioOperationLease.operation == operation,
            ScenarioOperationLease.owner_id == owner_id,
        ))
        session.commit()


def operation_lease_active(engine, scenario_id: str, operation: str | None = None,
                           *, now: float | None = None) -> bool:
    current = time.time() if now is None else now
    query = select(ScenarioOperationLease.scenario_id).where(
        ScenarioOperationLease.scenario_id == scenario_id,
        ScenarioOperationLease.expires_at > current,
    )
    if operation is not None:
        query = query.where(ScenarioOperationLease.operation == operation)
    with Session(engine) as session:
        return session.exec(query).first() is not None


class OperationLeaseHeartbeat:
    """Renew a lease while a synchronous Docker lifecycle call is in progress."""

    def __init__(self, engine, scenario_id: str, operation: str, owner_id: str,
                 *, renew_seconds: int = RENEW_SECONDS) -> None:
        self.engine = engine
        self.scenario_id = scenario_id
        self.operation = operation
        self.owner_id = owner_id
        self.renew_seconds = renew_seconds
        self.lost = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "OperationLeaseHeartbeat":
        self._thread = threading.Thread(target=self._run, name="scenario-lease-heartbeat", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1, self.renew_seconds))

    def _run(self) -> None:
        while not self._stop.wait(self.renew_seconds):
            try:
                if not renew_operation_lease(self.engine, self.scenario_id,
                                             self.operation, self.owner_id):
                    self.lost.set()
                    return
            except Exception:
                self.lost.set()
                return


def make_operation_owner_id() -> str:
    import os
    import socket

    return f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex}"[:128]
