"""Scenario-scoped exercise definitions and durable runs."""

from __future__ import annotations

import json
from ipaddress import IPv4Address
from datetime import datetime, timezone
from math import isfinite
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr, StringConstraints, model_validator
from sqlmodel import Field as SqlField, SQLModel


ExerciseId = Annotated[str, StringConstraints(min_length=1, max_length=48, pattern=r"^[a-zA-Z0-9_.-]+$")]


class PointWriteEvent(BaseModel):
    event_id: ExerciseId
    at_seconds: StrictInt = Field(ge=0, le=86_400)
    kind: Literal["write_point"]
    node_id: ExerciseId
    address: str = Field(min_length=1, max_length=64)
    value: StrictBool | StrictInt | StrictFloat


class ProtocolWriteEvent(BaseModel):
    """Issue a native S7/Modbus write from an enabled participant station."""
    model_config = ConfigDict(extra="forbid")

    event_id: ExerciseId
    at_seconds: StrictInt = Field(ge=0, le=86_400)
    kind: Literal["protocol_write"]
    source_node_id: ExerciseId
    target_node_id: ExerciseId
    address: str = Field(min_length=1, max_length=64)
    value: StrictBool | StrictInt | StrictFloat
    route_id: str | None = Field(default=None, min_length=1, max_length=64,
                                 pattern=r"^[a-zA-Z0-9_.-]+$")


class NodeLifecycleEvent(BaseModel):
    event_id: ExerciseId
    at_seconds: StrictInt = Field(ge=0, le=86_400)
    kind: Literal["node_lifecycle"]
    node_id: ExerciseId
    action: Literal["start", "stop", "restart"]


class NetworkRouteEvent(BaseModel):
    """Interrupt or restore one explicitly declared scenario route."""
    event_id: ExerciseId
    at_seconds: StrictInt = Field(ge=0, le=86_400)
    kind: Literal["network_route"]
    route_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")
    action: Literal["interrupt", "restore"]


class ModbusRewriteRule(BaseModel):
    """Exact value replacement in supported Modbus TCP reads or writes."""
    model_config = ConfigDict(extra="forbid")

    rule_id: ExerciseId
    unit_id: StrictInt = Field(ge=0, le=255)
    function_code: Literal[1, 2, 3, 4, 5, 6, 15, 16, 22, 23]
    address: StrictInt = Field(ge=0, le=65535)
    replacement_value: StrictInt = Field(ge=0, le=65535)
    phase: Literal["read", "write"] = "read"
    exercise_id: ExerciseId | None = None
    event_id: ExerciseId | None = None

    @model_validator(mode="after")
    def validate_metadata_pair(self) -> "ModbusRewriteRule":
        if (self.exercise_id is None) != (self.event_id is None):
            raise ValueError("rewrite exercise metadata requires both exercise_id and event_id")
        if self.function_code in {1, 2, 5, 15} and self.replacement_value not in {0, 1}:
            raise ValueError(f"FC{self.function_code} bit replacement must be 0 or 1")
        if self.function_code != 23 and self.phase != "read":
            raise ValueError("rewrite phase is only configurable for FC23")
        return self


class ModbusInterceptionEvent(BaseModel):
    """Set or clear bounded Modbus value rewrites on one declared TCP route."""
    model_config = ConfigDict(extra="forbid")

    event_id: ExerciseId
    at_seconds: StrictInt = Field(ge=0, le=86_400)
    kind: Literal["modbus_interception"]
    route_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")
    action: Literal["set", "clear"]
    rules: list[ModbusRewriteRule] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def validate_action_rules(self) -> "ModbusInterceptionEvent":
        if self.action == "set" and not self.rules:
            raise ValueError("set interception events require at least one rewrite rule")
        if self.action == "clear" and self.rules:
            raise ValueError("clear interception events cannot contain rewrite rules")
        ids = [rule.rule_id for rule in self.rules]
        points = [(rule.unit_id, rule.function_code, rule.phase, rule.address) for rule in self.rules]
        if len(ids) != len(set(ids)) or len(points) != len(set(points)):
            raise ValueError("interception rule IDs and Modbus points must be unique")
        return self


class FieldControlEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: ExerciseId
    at_seconds: StrictInt = Field(ge=0, le=86_400)
    kind: Literal["field_control"]
    object_id: ExerciseId
    fault: StrictBool | None = None
    override: StrictBool | StrictInt | StrictFloat | None = None
    demand_flow_lpm: StrictInt | StrictFloat | None = Field(default=None, ge=0, le=3.4e38, allow_inf_nan=False)
    leak_flow_lpm: StrictInt | StrictFloat | None = Field(default=None, ge=0, le=3.4e38, allow_inf_nan=False)

    @model_validator(mode="after")
    def require_control(self) -> "FieldControlEvent":
        if not {"fault", "override", "demand_flow_lpm", "leak_flow_lpm"}.intersection(self.model_fields_set):
            raise ValueError("field_control events must set fault, override, demand_flow_lpm or leak_flow_lpm")
        return self


ExerciseEvent = Annotated[
    PointWriteEvent | ProtocolWriteEvent | NodeLifecycleEvent | NetworkRouteEvent
    | ModbusInterceptionEvent | FieldControlEvent,
    Field(discriminator="kind"),
]


class AuditEventObjective(BaseModel):
    objective_id: ExerciseId
    description: str = Field(min_length=1, max_length=240)
    weight: StrictInt = Field(default=1, ge=1, le=100)
    kind: Literal["audit_event"]
    event_type: str = Field(min_length=1, max_length=80)
    details_match: dict[str, StrictBool | StrictInt | StrictFloat | StrictStr] = Field(default_factory=dict, max_length=8)
    expectation: Literal["occurred", "absent"] = "occurred"
    fail_fast: StrictBool = False

    @model_validator(mode="after")
    def validate_fail_fast(self) -> "AuditEventObjective":
        allowed = {"source", "node_id", "address", "route_id", "object_id", "action", "value",
                   "exercise_id", "event_id", "fault", "override", "demand_flow_lpm", "status",
                   "reason", "protocol", "target_node_id", "source_node_id", "rule_id", "unit_id", "direction",
                   "function_code", "count", "operation", "area", "db", "byte_offset",
                   "byte_count", "result", "result_code", "space"}
        if any(not key or len(key) > 64 or key not in allowed for key in self.details_match):
            raise ValueError("audit details_match contains an unsupported detail key")
        for value in self.details_match.values():
            if type(value) is str and len(value) > 512:
                raise ValueError("audit details_match strings are limited to 512 characters")
            if type(value) in (int, float) and (not isfinite(value) or abs(value) > 3.4e38):
                raise ValueError("audit details_match numeric values must be finite and in range")
        if self.fail_fast and self.expectation != "absent":
            raise ValueError("fail_fast is only valid when an audit event is expected to be absent")
        return self


class ProtocolWritePolicyObjective(BaseModel):
    """Detect protocol writes whose observed IPv4 peer is not allowlisted."""
    model_config = ConfigDict(extra="forbid")

    objective_id: ExerciseId
    description: str = Field(min_length=1, max_length=240)
    weight: StrictInt = Field(default=1, ge=1, le=100)
    kind: Literal["protocol_write_policy"]
    target_node_id: ExerciseId
    protocol: Literal["s7", "modbus-tcp"]
    allowed_source_ips: list[str] = Field(default_factory=list, max_length=64)
    expectation: Literal["detected", "none"] = "detected"

    @model_validator(mode="after")
    def validate_sources(self) -> "ProtocolWritePolicyObjective":
        normalized: list[str] = []
        for value in self.allowed_source_ips:
            try:
                address = str(IPv4Address(value.strip()))
            except (AttributeError, ValueError) as error:
                raise ValueError("allowed_source_ips must contain IPv4 addresses") from error
            if address in normalized:
                raise ValueError("allowed_source_ips must not contain duplicates")
            normalized.append(address)
        self.allowed_source_ips = normalized
        return self


class AuditSequenceObjective(BaseModel):
    objective_id: ExerciseId
    description: str = Field(min_length=1, max_length=240)
    weight: StrictInt = Field(default=1, ge=1, le=100)
    kind: Literal["audit_sequence"]
    event_types: list[str] = Field(min_length=2, max_length=16)
    max_gap_seconds: float | None = Field(default=None, ge=0.001, le=86_400, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_event_types(self) -> "AuditSequenceObjective":
        if any(not value.strip() or value != value.strip() or len(value) > 80 for value in self.event_types):
            raise ValueError("audit sequence event_types must contain 1–80 character event names")
        return self


class PointValueObjective(BaseModel):
    objective_id: ExerciseId
    description: str = Field(min_length=1, max_length=240)
    weight: StrictInt = Field(default=1, ge=1, le=100)
    kind: Literal["point_value"]
    node_id: ExerciseId
    address: str = Field(min_length=1, max_length=64)
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte"]
    value: StrictBool | StrictInt | StrictFloat
    duration_seconds: float = Field(default=0, ge=0, le=86_400, allow_inf_nan=False)


class FieldValueObjective(BaseModel):
    objective_id: ExerciseId
    description: str = Field(min_length=1, max_length=240)
    weight: StrictInt = Field(default=1, ge=1, le=100)
    kind: Literal["field_value"]
    object_id: ExerciseId
    property: Literal["state", "fault", "value", "opening_pct", "flow", "pressure_kpa", "volume_liters", "inflow_lpm", "outflow_lpm"]
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte"]
    value: StrictBool | StrictInt | StrictFloat
    duration_seconds: float = Field(default=0, ge=0, le=86_400, allow_inf_nan=False)


class FieldTrendObjective(BaseModel):
    """Compare numeric field values across a bounded, sampled time window."""
    objective_id: ExerciseId
    description: str = Field(min_length=1, max_length=240)
    weight: StrictInt = Field(default=1, ge=1, le=100)
    kind: Literal["field_trend"]
    object_id: ExerciseId
    property: Literal["value", "opening_pct", "flow", "pressure_kpa", "volume_liters", "inflow_lpm", "outflow_lpm"]
    direction: Literal["rising", "falling", "stable"]
    window_seconds: StrictInt = Field(ge=2, le=300)
    minimum_delta: StrictFloat | StrictInt = Field(default=0, ge=0, le=3.4e38, allow_inf_nan=False)


class FieldSequenceStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_id: ExerciseId
    property: Literal["state", "fault", "value", "opening_pct", "flow", "pressure_kpa", "volume_liters", "inflow_lpm", "outflow_lpm"]
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte"]
    value: StrictBool | StrictInt | StrictFloat

    @model_validator(mode="after")
    def validate_condition_type(self) -> "FieldSequenceStep":
        if self.property in {"state", "fault"}:
            if type(self.value) is not bool or self.operator not in {"eq", "ne"}:
                raise ValueError("state and fault conditions require a boolean eq/ne comparison")
        elif type(self.value) not in (int, float) or isinstance(self.value, bool):
            raise ValueError("numeric field conditions require a numeric value")
        else:
            try:
                numeric = float(self.value)
            except OverflowError as error:
                raise ValueError("numeric field conditions must be finite and within the supported range") from error
            if not isfinite(numeric) or abs(numeric) > 3.4e38:
                raise ValueError("numeric field conditions must be finite and within the supported range")
        return self


class FieldSequenceObjective(BaseModel):
    objective_id: ExerciseId
    description: str = Field(min_length=1, max_length=240)
    weight: StrictInt = Field(default=1, ge=1, le=100)
    kind: Literal["field_sequence"]
    steps: list[FieldSequenceStep] = Field(min_length=2, max_length=16)
    max_gap_seconds: float | None = Field(default=None, ge=0.001, le=86_400, allow_inf_nan=False)


ExerciseObjective = Annotated[
    AuditEventObjective | ProtocolWritePolicyObjective | AuditSequenceObjective | PointValueObjective
    | FieldValueObjective | FieldTrendObjective | FieldSequenceObjective,
    Field(discriminator="kind"),
]


class ExerciseDefinition(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    events: list[ExerciseEvent] = Field(default_factory=list, max_length=64)
    objectives: list[ExerciseObjective] = Field(default_factory=list, max_length=32)
    success_rule: Literal["all", "any", "score"] = "all"
    passing_score: StrictInt = Field(default=100, ge=0, le=100)

    @model_validator(mode="after")
    def validate_unique_ids(self) -> "ExerciseDefinition":
        event_ids = [event.event_id for event in self.events]
        objective_ids = [objective.objective_id for objective in self.objectives]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("exercise event_id values must be unique")
        if len(objective_ids) != len(set(objective_ids)):
            raise ValueError("exercise objective_id values must be unique")
        if [event.at_seconds for event in self.events] != sorted(event.at_seconds for event in self.events):
            raise ValueError("exercise events must be ordered by at_seconds")
        return self


class ExerciseRunStatus(BaseModel):
    id: UUID
    scenario_id: UUID
    definition: ExerciseDefinition
    status: Literal["ready", "running", "paused", "completed", "failed", "cancelled", "interrupted"]
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    next_event_index: int = 0
    event_results: list[dict[str, Any]] = Field(default_factory=list)
    objective_results: list[dict[str, Any]] = Field(default_factory=list)
    objective_progress: dict[str, Any] = Field(default_factory=dict)
    score: int | None = None
    passed: bool | None = None


class ExerciseRun(SQLModel, table=True):
    __tablename__ = "scenario_exercise_runs"

    id: str = SqlField(default_factory=lambda: str(uuid4()), primary_key=True)
    scenario_id: str = SqlField(index=True)
    definition_json: str
    status: str = SqlField(default="ready", index=True)
    created_at: datetime = SqlField(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    finished_at: datetime | None = None
    next_event_index: int = 0
    event_results_json: str = SqlField(default="[]")
    objective_results_json: str = SqlField(default="[]")
    objective_tracking_json: str = SqlField(default="{}")
    paused_at: datetime | None = None
    paused_duration_seconds: float = SqlField(default=0)
    score: int | None = None
    passed: bool | None = None

    def to_read_model(self) -> ExerciseRunStatus:
        return ExerciseRunStatus(
            id=UUID(self.id), scenario_id=UUID(self.scenario_id),
            definition=ExerciseDefinition.model_validate_json(self.definition_json),
            status=self.status, created_at=self.created_at, started_at=self.started_at,
            finished_at=self.finished_at, next_event_index=self.next_event_index,
            event_results=json.loads(self.event_results_json or "[]"),
            objective_results=json.loads(self.objective_results_json or "[]"),
            objective_progress=json.loads(self.objective_tracking_json or "{}"),
            score=self.score, passed=self.passed,
        )
