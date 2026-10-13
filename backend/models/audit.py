"""Durable, scenario-scoped operational audit records."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel
from sqlmodel import Field, SQLModel


class ScenarioAuditEvent(SQLModel, table=True):
    __tablename__ = "scenario_audit_events"

    id: int | None = Field(default=None, primary_key=True)
    scenario_id: str = Field(index=True)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)
    event_type: str = Field(index=True, max_length=80)
    actor: str = Field(default="system", max_length=80)
    details_json: str = Field(default="{}")
    previous_hash: str | None = Field(default=None, max_length=64)
    event_hash: str | None = Field(default=None, max_length=64)

    def to_read_model(self) -> "AuditEventRead":
        timestamp = self.timestamp
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return AuditEventRead(
            id=self.id or 0,
            scenario_id=self.scenario_id,
            timestamp=timestamp,
            event_type=self.event_type,
            actor=self.actor,
            details=json.loads(self.details_json or "{}"),
            previous_hash=self.previous_hash or "",
            event_hash=self.event_hash or "",
        )


class ScenarioNodeRuntimeState(SQLModel, table=True):
    """Last observed container state used to emit audit events only on transitions."""

    __tablename__ = "scenario_node_runtime_states"

    scenario_id: str = Field(primary_key=True)
    node_id: str = Field(primary_key=True)
    status: str = Field(max_length=32)
    health_status: str | None = Field(default=None, max_length=32)


class ScenarioProtocolReadState(SQLModel, table=True):
    """Last observed protocol read quality, used to audit only transitions."""

    __tablename__ = "scenario_protocol_read_states"

    scenario_id: str = Field(primary_key=True)
    source_node_id: str = Field(primary_key=True)
    target_node_id: str = Field(primary_key=True)
    protocol: str = Field(primary_key=True, max_length=24)
    quality: str = Field(max_length=24)
    point_count: int = Field(default=0)


class ScenarioProtocolTraceCursor(SQLModel, table=True):
    """Per-device cursor for idempotent protocol event ingestion."""

    __tablename__ = "scenario_protocol_trace_cursors"

    scenario_id: str = Field(primary_key=True, max_length=36)
    node_id: str = Field(primary_key=True, max_length=128)
    last_timestamp: str = Field(default="", max_length=40)
    same_timestamp_hashes_json: str = Field(default="[]")
    dropped_events_seen: int = Field(default=0)
    last_queue_id: int = Field(default=0)


class ScenarioFieldAuditState(SQLModel, table=True):
    """Last audited discrete process state for one independent field object."""

    __tablename__ = "scenario_field_audit_states"

    scenario_id: str = Field(primary_key=True, max_length=36)
    object_id: str = Field(primary_key=True, max_length=128)
    state_json: str = Field(default="{}")


class ScenarioAuditCheckpoint(SQLModel, table=True):
    """Retained audit-chain anchor after an explicitly configured prefix purge."""

    __tablename__ = "scenario_audit_checkpoints"

    scenario_id: str = Field(primary_key=True)
    pruned_through_event_id: int
    pruned_through_timestamp: datetime
    anchor_hash: str = Field(max_length=64)


class ScenarioAuditArchive(SQLModel, table=True):
    """Access snapshot for audit history retained after scenario deletion."""

    __tablename__ = "scenario_audit_archives"

    scenario_id: str = Field(primary_key=True)
    scenario_name: str = Field(max_length=120)
    owner_user_id: str | None = Field(default=None, max_length=36)
    memberships_json: str = Field(default="{}")
    archived_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)


class AuditEventRead(BaseModel):
    id: int
    scenario_id: str
    timestamp: datetime
    event_type: str
    actor: str
    details: dict[str, Any]
    previous_hash: str
    event_hash: str


class AuditEventPage(BaseModel):
    scenario_id: str
    events: list[AuditEventRead]
    next_before_id: int | None = None


class AuditIntegrityRead(BaseModel):
    scenario_id: str
    valid: bool
    checked_events: int
    first_invalid_event_id: int | None = None
    anchored_after_event_id: int | None = None
