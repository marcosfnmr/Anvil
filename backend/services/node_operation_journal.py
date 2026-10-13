"""Durable intent journal for per-node Docker lifecycle operations."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi.encoders import jsonable_encoder
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from models.scenario import ScenarioNodeOperation


def begin_node_operation(engine, scenario_id: str, node_id: str, action: str,
                         *, source: str = "api", idempotency_key: str | None = None,
                         precondition: dict | None = None
                         ) -> tuple[ScenarioNodeOperation, bool]:
    """Persist intent before Docker is called; bool means already completed."""
    with Session(engine) as session:
        if idempotency_key is not None:
            existing = session.exec(select(ScenarioNodeOperation).where(
                ScenarioNodeOperation.idempotency_key == idempotency_key,
            )).first()
            if existing is not None:
                return existing, existing.status == "completed"
        operation = ScenarioNodeOperation(
            scenario_id=scenario_id, node_id=node_id, action=action,
            source=source, idempotency_key=idempotency_key,
            precondition_json=json.dumps(precondition or {}, ensure_ascii=False, separators=(",", ":")),
        )
        session.add(operation)
        try:
            session.commit()
            session.refresh(operation)
            return operation, False
        except IntegrityError:
            session.rollback()
            if idempotency_key is None:
                raise
            existing = session.exec(select(ScenarioNodeOperation).where(
                ScenarioNodeOperation.idempotency_key == idempotency_key,
            )).first()
            if existing is None:
                raise
            return existing, existing.status == "completed"


def mark_node_operation_complete(engine, operation_id: str, result: dict) -> None:
    with Session(engine) as session:
        operation = session.get(ScenarioNodeOperation, operation_id)
        if operation is None:
            raise LookupError("Node operation journal entry disappeared")
        operation.status = "completed"
        operation.updated_at = datetime.now(timezone.utc)
        operation.attempts += 1
        operation.result_json = json.dumps(
            jsonable_encoder(result), ensure_ascii=False, separators=(",", ":"),
        )
        operation.last_error = None
        session.add(operation)
        session.commit()


def mark_node_operation_retryable(engine, operation_id: str, error: str) -> None:
    with Session(engine) as session:
        operation = session.get(ScenarioNodeOperation, operation_id)
        if operation is None:
            return
        operation.updated_at = datetime.now(timezone.utc)
        operation.attempts += 1
        operation.last_error = error[:1000]
        operation.status = "pending"
        session.add(operation)
        session.commit()


def mark_node_operation_terminal(engine, operation_id: str, status: str, error: str) -> None:
    if status not in {"failed", "cancelled"}:
        raise ValueError("Terminal node-operation status must be failed or cancelled")
    with Session(engine) as session:
        operation = session.get(ScenarioNodeOperation, operation_id)
        if operation is None:
            return
        operation.status = status
        operation.updated_at = datetime.now(timezone.utc)
        operation.last_error = error[:1000]
        session.add(operation)
        session.commit()


def pending_node_operations(engine, *, limit: int = 100) -> list[ScenarioNodeOperation]:
    with Session(engine) as session:
        return session.exec(
            select(ScenarioNodeOperation)
            .where(ScenarioNodeOperation.status == "pending")
            .order_by(ScenarioNodeOperation.requested_at)
            .limit(limit)
        ).all()
