"""Immutable, scenario-scoped topology revisions."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel
from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class ScenarioRevision(SQLModel, table=True):
    __tablename__ = "scenario_revisions"
    __table_args__ = (UniqueConstraint("scenario_id", "version", name="uq_scenario_revision_version"),)

    id: int | None = Field(default=None, primary_key=True)
    scenario_id: str = Field(index=True)
    version: int = Field(ge=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)
    content_hash: str = Field(max_length=64)
    snapshot_json: str

    def to_read_model(self) -> "ScenarioRevisionRead":
        timestamp = self.created_at
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        snapshot = json.loads(self.snapshot_json)
        return ScenarioRevisionRead(
            id=self.id or 0,
            scenario_id=self.scenario_id,
            version=self.version,
            created_at=timestamp,
            name=snapshot["name"],
            device_count=len(snapshot["devices"]),
            field_object_count=len(snapshot.get("field_objects", [])),
            content_hash=self.content_hash,
        )


class ScenarioRevisionRead(BaseModel):
    id: int
    scenario_id: str
    version: int
    created_at: datetime
    name: str
    device_count: int
    field_object_count: int
    content_hash: str


class ScenarioRevisionDetail(ScenarioRevisionRead):
    snapshot: dict[str, Any]
