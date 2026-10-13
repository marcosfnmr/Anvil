"""Durable state and transition history for HMI-owned alarms."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class ScenarioHmiAlarmState(SQLModel, table=True):
    __tablename__ = "scenario_hmi_alarm_states"
    __table_args__ = (UniqueConstraint(
        "scenario_id", "hmi_node_id", "target_node_id", "address",
        name="uq_hmi_alarm_state",
    ),)

    id: int | None = Field(default=None, primary_key=True)
    scenario_id: str = Field(index=True, max_length=36)
    hmi_node_id: str = Field(index=True, max_length=128)
    target_node_id: str = Field(max_length=128)
    address: str = Field(max_length=128)
    active: bool = Field(default=False)
    value_json: str = Field(default="null", max_length=128)
    quality: str = Field(default="unknown", max_length=16)
    triggered_at: str | None = Field(default=None, max_length=40)
    condition_since: str | None = Field(default=None, max_length=40)
    acknowledged: bool = Field(default=False)
    acknowledged_at: str | None = Field(default=None, max_length=40)
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat(), max_length=40)


class ScenarioHmiAlarmTransition(SQLModel, table=True):
    __tablename__ = "scenario_hmi_alarm_transitions"

    id: int | None = Field(default=None, primary_key=True)
    scenario_id: str = Field(index=True, max_length=36)
    hmi_node_id: str = Field(index=True, max_length=128)
    target_node_id: str = Field(max_length=128)
    address: str = Field(max_length=128)
    transition: str = Field(max_length=16)
    value_json: str = Field(default="null", max_length=128)
    occurred_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat(), index=True, max_length=40)
