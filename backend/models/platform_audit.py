"""Durable audit records for platform-wide account administration."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel
from sqlmodel import Field, SQLModel


class PlatformAuditEvent(SQLModel, table=True):
    __tablename__ = "platform_audit_events"

    id: int | None = Field(default=None, primary_key=True)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)
    actor_user_id: str | None = Field(default=None, index=True, max_length=36)
    target_user_id: str | None = Field(default=None, index=True, max_length=36)
    event_type: str = Field(index=True, max_length=80)
    details_json: str = Field(default="{}")
    previous_hash: str = Field(max_length=64)
    event_hash: str = Field(max_length=64, index=True)

    def to_read_model(self) -> "PlatformAuditEventRead":
        timestamp = self.timestamp
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return PlatformAuditEventRead(
            id=self.id or 0,
            timestamp=timestamp,
            actor_user_id=self.actor_user_id,
            target_user_id=self.target_user_id,
            event_type=self.event_type,
            details=json.loads(self.details_json or "{}"),
            previous_hash=self.previous_hash,
            event_hash=self.event_hash,
        )


class PlatformAuditEventRead(BaseModel):
    id: int
    timestamp: datetime
    actor_user_id: str | None
    target_user_id: str | None
    event_type: str
    details: dict[str, Any]
    previous_hash: str
    event_hash: str


class PlatformAuditEventPage(BaseModel):
    events: list[PlatformAuditEventRead]
    next_before_id: int | None = None


class PlatformAuditIntegrity(BaseModel):
    valid: bool
    checked_events: int
    first_invalid_event_id: int | None = None
