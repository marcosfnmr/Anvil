"""Append and query durable per-scenario audit events."""

from __future__ import annotations

import asyncio
import json
import hashlib
import logging
import os
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete
from sqlmodel import Session, select
from starlette.concurrency import run_in_threadpool

from models.audit import AuditEventRead, AuditIntegrityRead, ScenarioAuditArchive, ScenarioAuditCheckpoint, ScenarioAuditEvent, ScenarioNodeRuntimeState, ScenarioProtocolReadState
from models.exercise import ExerciseRun
from models.scenario import ScenarioRecord
from services.request_context import audit_actor


LOGGER = logging.getLogger("anvil.audit")


def _genesis_hash(scenario_id: str) -> str:
    return hashlib.sha256(f"anvil-audit-v1:{scenario_id}".encode("utf-8")).hexdigest()


def _event_hash(event: ScenarioAuditEvent, previous_hash: str) -> str:
    timestamp = event.timestamp
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    else:
        timestamp = timestamp.astimezone(timezone.utc)
    payload = {
        "scenario_id": event.scenario_id,
        "timestamp": timestamp.isoformat(timespec="microseconds"),
        "event_type": event.event_type,
        "actor": event.actor,
        "details_json": event.details_json or "{}",
        "previous_hash": previous_hash,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _new_audit_event(
    session: Session,
    scenario_id: str,
    event_type: str,
    details: dict[str, Any] | None,
    *,
    actor: str,
    timestamp: datetime | None = None,
) -> ScenarioAuditEvent:
    serialized = json.dumps(details or {}, ensure_ascii=False, separators=(",", ":"))
    if len(serialized) > 16_384:
        raise ValueError("Audit event details exceed the 16 KiB limit")
    event = ScenarioAuditEvent(
        scenario_id=scenario_id,
        event_type=event_type,
        actor=audit_actor.get() if audit_actor.get() and actor in {"system", "api-client"} else actor,
        details_json=serialized,
        timestamp=timestamp or datetime.now(timezone.utc),
    )
    previous = session.exec(
        select(ScenarioAuditEvent)
        .where(ScenarioAuditEvent.scenario_id == scenario_id)
        .order_by(ScenarioAuditEvent.id.desc())
        .limit(1)
    ).first()
    if previous is not None and not previous.event_hash:
        raise RuntimeError("Audit chain is incomplete; verify or migrate the scenario audit log before appending")
    checkpoint = session.get(ScenarioAuditCheckpoint, scenario_id) if previous is None else None
    event.previous_hash = (
        previous.event_hash if previous is not None
        else checkpoint.anchor_hash if checkpoint is not None
        else _genesis_hash(scenario_id)
    )
    event.event_hash = _event_hash(event, event.previous_hash)
    return event


def record_audit_event(
    session: Session,
    scenario_id: str,
    event_type: str,
    details: dict[str, Any] | None = None,
    *,
    actor: str = "system",
    timestamp: datetime | None = None,
    commit: bool = True,
) -> AuditEventRead:
    event = _new_audit_event(session, scenario_id, event_type, details, actor=actor, timestamp=timestamp)
    session.add(event)
    if commit:
        session.commit()
        session.refresh(event)
    return event.to_read_model()


def record_node_runtime_transitions(
    session: Session,
    scenario_id: str,
    containers: list[dict[str, Any]],
    scenario_status: str,
) -> None:
    """Persist per-node state changes without emitting an event on every poll."""
    known = {
        item.node_id: item
        for item in session.exec(
            select(ScenarioNodeRuntimeState).where(ScenarioNodeRuntimeState.scenario_id == scenario_id)
        ).all()
    }
    observed = {str(item.get("node_id", "unknown")): item for item in containers}
    for node_id in known.keys() - observed.keys():
        observed[node_id] = {"node_id": node_id, "status": "stopped" if scenario_status == "stopped" else "missing"}

    for node_id, current in observed.items():
        status = str(current.get("status") or "unknown")[:32]
        health_status = str(current["health_status"])[:32] if current.get("health_status") else None
        previous = known.get(node_id)
        if previous is not None and previous.status == status and previous.health_status == health_status:
            continue
        previous_status = previous.status if previous else None
        previous_health_status = previous.health_status if previous else None
        if previous is None:
            previous = ScenarioNodeRuntimeState(scenario_id=scenario_id, node_id=node_id, status=status)
        previous.status = status
        previous.health_status = health_status
        session.add(previous)
        details: dict[str, Any] = {
            "node_id": node_id[:256],
            "previous_status": previous_status,
            "status": status,
            "previous_health_status": previous_health_status,
            "health_status": health_status,
        }
        for field in ("health_error", "runtime_error"):
            value = str(current.get(field) or "").strip()[:500]
            if value:
                details[field] = value
        record_audit_event(session, scenario_id, "scenario.node_state_changed", details)


def record_protocol_read_transition(
    session: Session,
    scenario_id: str,
    source_node_id: str,
    target_node_id: str,
    protocol: str,
    quality: str,
    point_count: int,
) -> None:
    """Audit first observation and quality changes, never each polling cycle."""
    if quality not in {"good", "degraded", "failed"}:
        raise ValueError("Protocol read quality must be good, degraded, or failed")
    key = (scenario_id, source_node_id, target_node_id, protocol)
    state = session.get(ScenarioProtocolReadState, key)
    if state is not None and state.quality == quality:
        return
    previous_quality = state.quality if state else None
    if state is None:
        state = ScenarioProtocolReadState(
            scenario_id=scenario_id,
            source_node_id=source_node_id,
            target_node_id=target_node_id,
            protocol=protocol[:24],
            quality=quality,
            point_count=max(0, point_count),
        )
    else:
        state.quality = quality
        state.point_count = max(0, point_count)
    session.add(state)
    record_audit_event(session, scenario_id, f"protocol.read_{quality}", {
        "source_node_id": source_node_id[:128],
        "target_node_id": target_node_id[:128],
        "protocol": protocol[:24],
        "previous_quality": previous_quality,
        "quality": quality,
        "point_count": max(0, point_count),
    })


def list_audit_events(
    session: Session,
    scenario_id: str,
    *,
    limit: int = 100,
    before_id: int | None = None,
    chronological: bool = False,
) -> list[AuditEventRead]:
    statement = select(ScenarioAuditEvent).where(ScenarioAuditEvent.scenario_id == scenario_id)
    if before_id is not None:
        statement = statement.where(ScenarioAuditEvent.id < before_id)
    ordering = ScenarioAuditEvent.id.asc() if chronological else ScenarioAuditEvent.id.desc()
    records = session.exec(statement.order_by(ordering).limit(limit)).all()
    return [record.to_read_model() for record in records]


def iter_audit_events(session: Session, scenario_id: str) -> Iterator[AuditEventRead]:
    statement = (
        select(ScenarioAuditEvent)
        .where(ScenarioAuditEvent.scenario_id == scenario_id)
        .order_by(ScenarioAuditEvent.id.asc())
    )
    for record in session.exec(statement).yield_per(500):
        yield record.to_read_model()


def verify_audit_integrity(session: Session, scenario_id: str) -> AuditIntegrityRead:
    """Check event content and links without modifying or repairing the log."""
    statement = (
        select(ScenarioAuditEvent)
        .where(ScenarioAuditEvent.scenario_id == scenario_id)
        .order_by(ScenarioAuditEvent.id.asc())
    )
    checkpoint = session.get(ScenarioAuditCheckpoint, scenario_id)
    expected_previous = checkpoint.anchor_hash if checkpoint else _genesis_hash(scenario_id)
    checked = 0
    for event in session.exec(statement).yield_per(500):
        checked += 1
        if event.previous_hash != expected_previous or event.event_hash != _event_hash(event, expected_previous):
            return AuditIntegrityRead(
                scenario_id=scenario_id,
                valid=False,
                checked_events=checked,
                first_invalid_event_id=event.id,
                anchored_after_event_id=checkpoint.pruned_through_event_id if checkpoint else None,
            )
        expected_previous = event.event_hash or expected_previous
    return AuditIntegrityRead(
        scenario_id=scenario_id,
        valid=True,
        checked_events=checked,
        anchored_after_event_id=checkpoint.pruned_through_event_id if checkpoint else None,
    )


def prune_audit_events_once(
    database_engine: Any,
    retention_days: int,
    *,
    now: datetime | None = None,
) -> int:
    """Delete only an expired, contiguous audit prefix; preserve its chain anchor."""
    if type(retention_days) is not int or not 1 <= retention_days <= 3650:
        raise ValueError("Audit retention must be between 1 and 3650 days")
    current = now or datetime.now(timezone.utc)
    current = current.replace(tzinfo=timezone.utc) if current.tzinfo is None else current.astimezone(timezone.utc)
    retention_cutoff = current - timedelta(days=retention_days)
    pruned_total = 0
    with Session(database_engine) as session:
        running_runs = session.exec(select(ExerciseRun).where(ExerciseRun.status == "running")).all()
        protected_from: dict[str, datetime] = {}
        for run in running_runs:
            if run.started_at is None:
                continue
            started = run.started_at.replace(tzinfo=timezone.utc) if run.started_at.tzinfo is None else run.started_at.astimezone(timezone.utc)
            previous = protected_from.get(run.scenario_id)
            if previous is None or started < previous:
                protected_from[run.scenario_id] = started

        scenario_ids = set(session.exec(select(ScenarioRecord.id)).all())
        scenario_ids.update(session.exec(select(ScenarioAuditArchive.scenario_id)).all())
        for scenario_id in scenario_ids:
            cutoff = min(retention_cutoff, protected_from.get(scenario_id, retention_cutoff))
            integrity = verify_audit_integrity(session, scenario_id)
            if not integrity.valid:
                LOGGER.error(
                    "Skipping audit retention for %s: invalid chain at event %s",
                    scenario_id,
                    integrity.first_invalid_event_id,
                )
                continue
            events = session.exec(
                select(ScenarioAuditEvent)
                .where(ScenarioAuditEvent.scenario_id == scenario_id)
                .order_by(ScenarioAuditEvent.id.asc())
            ).all()
            prefix: list[ScenarioAuditEvent] = []
            for event in events:
                timestamp = event.timestamp.replace(tzinfo=timezone.utc) if event.timestamp.tzinfo is None else event.timestamp.astimezone(timezone.utc)
                if timestamp >= cutoff:
                    break
                prefix.append(event)
            if not prefix:
                continue
            last = prefix[-1]
            if not last.event_hash:
                LOGGER.error("Skipping audit retention for %s: chain has no hash at event %s", scenario_id, last.id)
                continue
            checkpoint = ScenarioAuditCheckpoint(
                scenario_id=scenario_id,
                pruned_through_event_id=last.id or 0,
                pruned_through_timestamp=last.timestamp,
                anchor_hash=last.event_hash,
            )
            session.merge(checkpoint)
            retention_event = _new_audit_event(session, scenario_id, "audit.retention_applied", {
                "retention_days": retention_days,
                "pruned_count": len(prefix),
                "pruned_through_event_id": last.id,
                "cutoff": cutoff.isoformat(),
            }, actor="system", timestamp=current)
            session.add(retention_event)
            session.exec(delete(ScenarioAuditEvent).where(
                ScenarioAuditEvent.scenario_id == scenario_id,
                ScenarioAuditEvent.id <= (last.id or 0),
            ))
            session.commit()
            pruned_total += len(prefix)
    return pruned_total


def configured_audit_retention_days() -> int:
    raw = os.environ.get("ANVIL_AUDIT_RETENTION_DAYS", "0").strip()
    try:
        days = int(raw)
    except ValueError:
        LOGGER.warning("Ignoring invalid ANVIL_AUDIT_RETENTION_DAYS=%r", raw)
        return 0
    if days == 0:
        return 0
    if not 1 <= days <= 3650:
        LOGGER.warning("ANVIL_AUDIT_RETENTION_DAYS must be 0 or between 1 and 3650; retention is disabled")
        return 0
    return days


async def audit_retention_scheduler(
    stop_event: asyncio.Event,
    database_engine: Any,
    retention_days: int,
    interval_seconds: float = 86_400,
) -> None:
    while not stop_event.is_set():
        try:
            deleted = await run_in_threadpool(prune_audit_events_once, database_engine, retention_days)
            if deleted:
                LOGGER.info("Audit retention removed %s expired events", deleted)
        except Exception:
            LOGGER.exception("Audit retention cycle failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=max(60.0, interval_seconds))
        except TimeoutError:
            pass
