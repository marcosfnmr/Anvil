"""Configuration for a polling SCADA collector."""

from __future__ import annotations

from sqlalchemy import UniqueConstraint
from pydantic import BaseModel, Field, StrictBool, StrictFloat, StrictInt, model_validator
from sqlmodel import Field as SqlField, SQLModel


class ScenarioScadaSample(SQLModel, table=True):
    """One durable SCADA point observation; grouped by sample timestamp on read."""

    __tablename__ = "scenario_scada_samples"
    __table_args__ = (UniqueConstraint(
        "scenario_id", "scada_node_id", "sampled_at", "target_node_id", "address",
        name="uq_scada_sample_point",
    ),)

    id: int | None = SqlField(default=None, primary_key=True)
    scenario_id: str = SqlField(index=True, max_length=36)
    scada_node_id: str = SqlField(index=True, max_length=128)
    sampled_at: str = SqlField(index=True, max_length=40)
    target_node_id: str = SqlField(max_length=128)
    address: str = SqlField(max_length=128)
    name: str = SqlField(default="", max_length=256)
    data_type: str = SqlField(default="", max_length=16)
    unit: str = SqlField(default="", max_length=32)
    value_json: str = SqlField(default="null", max_length=128)
    quality: str = SqlField(default="unknown", max_length=16)


class ScenarioScadaAlarmState(SQLModel, table=True):
    """Durable acknowledgement and activation time for currently active alarms."""

    __tablename__ = "scenario_scada_alarm_states"
    __table_args__ = (UniqueConstraint(
        "scenario_id", "scada_node_id", "target_node_id", "address",
        name="uq_scada_alarm_state",
    ),)

    id: int | None = SqlField(default=None, primary_key=True)
    scenario_id: str = SqlField(index=True, max_length=36)
    scada_node_id: str = SqlField(index=True, max_length=128)
    target_node_id: str = SqlField(max_length=128)
    address: str = SqlField(max_length=128)
    triggered_at: str = SqlField(max_length=40)
    acknowledged: bool = SqlField(default=False)
    acknowledged_at: str | None = SqlField(default=None, max_length=40)


class ScenarioScadaAlarmPending(SQLModel, table=True):
    """Condition start times needed to resume SCADA alarm delays after restart."""

    __tablename__ = "scenario_scada_alarm_pending"
    __table_args__ = (UniqueConstraint(
        "scenario_id", "scada_node_id", "target_node_id", "address",
        name="uq_scada_alarm_pending",
    ),)

    id: int | None = SqlField(default=None, primary_key=True)
    scenario_id: str = SqlField(index=True, max_length=36)
    scada_node_id: str = SqlField(index=True, max_length=128)
    target_node_id: str = SqlField(max_length=128)
    address: str = SqlField(max_length=128)
    condition_since: str = SqlField(max_length=40)


class ScadaAlarmRule(BaseModel):
    target_node_id: str = Field(min_length=1)
    address: str = Field(min_length=1)
    above: float | None = None
    below: float | None = None
    delay_ms: StrictInt = Field(default=0, ge=0, le=86_400_000)

    @model_validator(mode="after")
    def require_threshold(self) -> "ScadaAlarmRule":
        if self.above is None and self.below is None:
            raise ValueError("an alarm rule needs an above or below threshold")
        if self.above is not None and self.below is not None and self.below >= self.above:
            raise ValueError("alarm below threshold must be lower than above threshold")
        return self


class ScadaConfigInput(BaseModel):
    target_node_ids: str = Field(default="", max_length=500)
    poll_interval_ms: StrictInt = Field(default=1000, ge=250, le=60000)
    history_capacity: StrictInt = Field(default=600, ge=1, le=10000)
    durable_history_capacity: StrictInt = Field(default=100_000, ge=1000, le=100_000)
    durable_history_max_bytes: StrictInt = Field(default=134_217_728, ge=16_777_216, le=536_870_912)
    alarms: list[ScadaAlarmRule] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_targets_and_rules(self) -> "ScadaConfigInput":
        targets = [value.strip() for value in self.target_node_ids.split(",") if value.strip()]
        if not targets or len(targets) != len(set(targets)):
            raise ValueError("SCADA targets must be non-empty and unique")
        keys = [(rule.target_node_id, rule.address) for rule in self.alarms]
        if len(keys) != len(set(keys)):
            raise ValueError("SCADA alarm rules must be unique per target address")
        if any(rule.target_node_id not in targets for rule in self.alarms):
            raise ValueError("SCADA alarm rules must reference a selected target")
        return self


class ScadaWriteRequest(BaseModel):
    target_node_id: str = Field(min_length=1, max_length=128)
    address: str = Field(min_length=1, max_length=128)
    value: StrictBool | StrictInt | StrictFloat
