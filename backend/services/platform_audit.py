"""Append-only hash chain for platform-level account administration."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from sqlmodel import Session, select

from models.platform_audit import (
    PlatformAuditEvent,
    PlatformAuditEventRead,
    PlatformAuditIntegrity,
)


GENESIS_HASH = hashlib.sha256(b"anvil-platform-audit-v1").hexdigest()
MAX_DETAILS_BYTES = 16_384


def _canonical_hash(event: PlatformAuditEvent, previous_hash: str) -> str:
    timestamp = event.timestamp
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    else:
        timestamp = timestamp.astimezone(timezone.utc)
    payload = {
        "id": event.id,
        "timestamp": timestamp.isoformat(timespec="microseconds"),
        "actor_user_id": event.actor_user_id,
        "target_user_id": event.target_user_id,
        "event_type": event.event_type,
        "details_json": event.details_json or "{}",
        "previous_hash": previous_hash,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def append_platform_audit(
    session: Session,
    event_type: str,
    *,
    actor_user_id: str | None,
    target_user_id: str | None,
    details: dict,
) -> PlatformAuditEvent:
    details_json = json.dumps(details, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(details_json.encode("utf-8")) > MAX_DETAILS_BYTES:
        raise ValueError("Platform audit details exceed the 16 KiB limit")
    last = session.exec(select(PlatformAuditEvent).order_by(PlatformAuditEvent.id.desc()).limit(1)).first()
    previous_hash = last.event_hash if last is not None else GENESIS_HASH
    event = PlatformAuditEvent(
        timestamp=datetime.now(timezone.utc),
        actor_user_id=actor_user_id,
        target_user_id=target_user_id,
        event_type=event_type,
        details_json=details_json,
        previous_hash=previous_hash,
        event_hash="0" * 64,
    )
    session.add(event)
    session.flush()
    event.event_hash = _canonical_hash(event, previous_hash)
    session.add(event)
    return event


def list_platform_audit_events(
    session: Session, *, limit: int = 100, before_id: int | None = None,
) -> tuple[list[PlatformAuditEventRead], int | None]:
    query = select(PlatformAuditEvent)
    if before_id is not None:
        query = query.where(PlatformAuditEvent.id < before_id)
    rows = session.exec(query.order_by(PlatformAuditEvent.id.desc()).limit(limit)).all()
    events = [row.to_read_model() for row in rows]
    next_before_id = events[-1].id if len(events) == limit else None
    return events, next_before_id


def verify_platform_audit(session: Session) -> PlatformAuditIntegrity:
    expected_previous = GENESIS_HASH
    checked = 0
    rows = session.exec(select(PlatformAuditEvent).order_by(PlatformAuditEvent.id.asc())).yield_per(500)
    for event in rows:
        checked += 1
        if event.previous_hash != expected_previous or event.event_hash != _canonical_hash(event, expected_previous):
            return PlatformAuditIntegrity(valid=False, checked_events=checked, first_invalid_event_id=event.id)
        expected_previous = event.event_hash
    return PlatformAuditIntegrity(valid=True, checked_events=checked)
