"""Persist meaningful discrete field-process transitions for scenario audit."""

from __future__ import annotations

import json
import math
from typing import Any

from sqlmodel import Session, select

from models.audit import ScenarioFieldAuditState
from services.audit import record_audit_event


_DISCRETE_OBJECTS = {"valve", "motor", "pump", "digital_input", "alarm"}


def _semantic_state(item: dict[str, Any], object_type: str) -> dict[str, Any] | None:
    """Return stable operational state; deliberately exclude analog samples."""
    state: dict[str, Any] = {}
    if object_type in _DISCRETE_OBJECTS:
        for key in ("command", "state", "fault"):
            value = item.get(key)
            if type(value) is bool:
                state[key] = value
        if object_type in {"digital_input", "alarm"}:
            value = item.get("override")
            if value is None or type(value) is bool:
                state["override"] = value
    elif object_type == "proportional_valve":
        opening = item.get("opening_pct")
        if type(opening) in (int, float) and math.isfinite(opening):
            state["open"] = opening > 0
        command = item.get("command")
        if type(command) in (int, float) and math.isfinite(command):
            state["command_open"] = command > 0
        if type(item.get("fault")) is bool:
            state["fault"] = item["fault"]
    elif object_type == "variable_speed_drive":
        if type(item.get("state")) is bool:
            state["running"] = item["state"]
        command = item.get("command")
        if type(command) in (int, float) and math.isfinite(command):
            state["command_run"] = command > 0
        if type(item.get("fault")) is bool:
            state["fault"] = item["fault"]
    elif object_type == "analog_sensor":
        if type(item.get("fault")) is bool:
            state["fault"] = item["fault"]
        override = item.get("override")
        if override is None or (type(override) in (int, float) and math.isfinite(override)):
            state["override"] = override
    return state or None


def ingest_field_snapshot(session: Session, scenario_id: str,
                          configured_objects: list[dict[str, Any]],
                          snapshot: dict[str, Any],
                          signal_connections: list[dict[str, Any]] | None = None) -> int:
    """Audit first observations as a baseline and later discrete transitions."""
    configured = {
        item.get("object_id"): item.get("type")
        for item in configured_objects
        if isinstance(item, dict) and isinstance(item.get("object_id"), str)
    }
    existing = session.exec(select(ScenarioFieldAuditState).where(
        ScenarioFieldAuditState.scenario_id == scenario_id,
    )).all()
    for state in existing:
        if state.object_id not in configured:
            session.delete(state)
    observed = {
        item.get("object_id"): item
        for item in snapshot.get("objects", [])
        if isinstance(item, dict) and isinstance(item.get("object_id"), str)
    }
    control_sources: dict[str, list[dict[str, str]]] = {}
    for connection in signal_connections or []:
        if not isinstance(connection, dict):
            continue
        source, target = connection.get("source"), connection.get("target")
        if (not isinstance(source, dict) or source.get("kind") != "device"
                or not isinstance(target, dict) or target.get("kind") != "field"
                or target.get("port") != "command"):
            continue
        object_id = target.get("object_id")
        node_id, address = source.get("node_id"), source.get("address")
        connection_id = connection.get("connection_id")
        if all(isinstance(value, str) and value for value in (object_id, node_id, address, connection_id)):
            control_sources.setdefault(object_id, []).append({
                "node_id": node_id, "address": address,
                "connection_id": connection_id,
            })
    inserted = 0
    for object_id, object_type in configured.items():
        current = _semantic_state(observed.get(object_id, {}), object_type)
        if current is None:
            continue
        state = session.get(ScenarioFieldAuditState, (scenario_id, object_id))
        current_json = json.dumps(current, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if state is None:
            session.add(ScenarioFieldAuditState(
                scenario_id=scenario_id, object_id=object_id, state_json=current_json,
            ))
            continue
        previous = json.loads(state.state_json or "{}")
        changed = {
            key: {"from": previous.get(key), "to": value}
            for key, value in current.items()
            if previous.get(key) != value
        }
        if changed:
            record_audit_event(session, scenario_id, "process.field_state_changed", {
                "object_id": object_id,
                "object_type": object_type,
                "changes": changed,
                **({"control_sources": control_sources[object_id]}
                   if control_sources.get(object_id) else {}),
            }, actor="field-runtime", commit=False)
            inserted += 1
        state.state_json = current_json
        session.add(state)
    session.commit()
    return inserted
