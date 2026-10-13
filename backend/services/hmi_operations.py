"""Run HMI protocol operations from the HMI container and its zone network."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import delete
from sqlmodel import Session, select

from models.hmi import ScenarioHmiAlarmState, ScenarioHmiAlarmTransition
from services.audit import record_audit_event

from docker.errors import DockerException

from services.docker_manager import DockerManagerError

_HMI_ALARM_HISTORY_CAPACITY = 10_000


def evaluate_hmi_alarms(
    session: Session,
    scenario_id: str,
    hmi_node_id: str,
    rules: list[dict[str, Any]],
    values: list[dict[str, Any]],
    *,
    clock: Callable[[], datetime] | None = None,
) -> list[dict[str, Any]]:
    """Evaluate configured rules only against point values returned by HMI protocol reads."""
    by_key = {(str(item.get("node_id")), str(item.get("address"))): item for item in values}
    states: dict[tuple[str, str], ScenarioHmiAlarmState] = {}
    for state in session.exec(select(ScenarioHmiAlarmState).where(
        ScenarioHmiAlarmState.scenario_id == scenario_id,
        ScenarioHmiAlarmState.hmi_node_id == hmi_node_id,
    )).all():
        states[(state.target_node_id, state.address)] = state

    current = (clock or (lambda: datetime.now(timezone.utc)))()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    else:
        current = current.astimezone(timezone.utc)
    now = current.isoformat()
    result: list[dict[str, Any]] = []
    transition_added = False
    for rule in rules:
        target, address = rule["target_node_id"], rule["address"]
        point = by_key.get((target, address))
        state = states.get((target, address))
        value = point.get("value") if point else None
        quality = point.get("quality", "good") if point else "unknown"
        expected_mode = rule.get("mode", "threshold")
        if expected_mode == "state":
            valid = type(value) is bool and quality == "good"
        else:
            valid = type(value) in (int, float) and math.isfinite(value) and quality == "good"
        if valid:
            condition_active = (value is rule["expected_state"] if expected_mode == "state" else
                                ((rule.get("above") is not None and value > rule["above"])
                                 or (rule.get("below") is not None and value < rule["below"])))
            serialized = json.dumps(value, allow_nan=False, separators=(",", ":"))
            if state is None:
                state = ScenarioHmiAlarmState(
                    scenario_id=scenario_id, hmi_node_id=hmi_node_id,
                    target_node_id=target, address=address,
                )
                session.add(state)
                states[(target, address)] = state
                previous_active = False
            else:
                previous_active = state.active
            state.value_json = serialized
            state.quality = "good"
            state.updated_at = now
            if condition_active and not previous_active:
                pending_since = state.condition_since
                if pending_since:
                    try:
                        pending_time = datetime.fromisoformat(pending_since)
                        if pending_time.tzinfo is None:
                            pending_time = pending_time.replace(tzinfo=timezone.utc)
                        else:
                            pending_time = pending_time.astimezone(timezone.utc)
                    except ValueError:
                        pending_time = current
                else:
                    pending_time = current
                state.condition_since = pending_time.isoformat()
                delay_ms = rule.get("delay_ms", 0)
                active = (current - pending_time).total_seconds() * 1000 >= delay_ms
                state.active = active
            elif condition_active:
                active = True
                state.active = True
                state.condition_since = None
            else:
                active = False
                state.active = False
                state.condition_since = None

            if active and not previous_active:
                state.triggered_at = now
                state.condition_since = None
                state.acknowledged = False
                state.acknowledged_at = None
                session.add(ScenarioHmiAlarmTransition(
                    scenario_id=scenario_id, hmi_node_id=hmi_node_id,
                    target_node_id=target, address=address, transition="activated",
                    value_json=serialized, occurred_at=now,
                ))
                transition_added = True
                session.flush()
                record_audit_event(session, scenario_id, "hmi.owned_alarm_activated", {
                    "hmi_node_id": hmi_node_id, "target_node_id": target,
                    "address": address, "value": value,
                })
            elif previous_active and not active:
                session.add(ScenarioHmiAlarmTransition(
                    scenario_id=scenario_id, hmi_node_id=hmi_node_id,
                    target_node_id=target, address=address, transition="cleared",
                    value_json=serialized, occurred_at=now,
                ))
                transition_added = True
                session.flush()
                record_audit_event(session, scenario_id, "hmi.owned_alarm_cleared", {
                    "hmi_node_id": hmi_node_id, "target_node_id": target,
                    "address": address, "value": value,
                })
                state.triggered_at = None
                state.condition_since = None
                state.acknowledged = False
                state.acknowledged_at = None
        elif state is not None:
            # Failed/missing protocol reads never clear an alarm; retain its last state as stale.
            state.quality = "unknown"
            state.updated_at = now
            if not state.active:
                state.condition_since = None
        session.flush()
        active = bool(state and state.active)
        pending = bool(state and state.condition_since and not active and valid)
        result.append({
            "target_node_id": target, "address": address, "value": value if valid else None,
            "active": active, "quality": "good" if valid else "unknown",
            "stale": not valid, "triggered_at": state.triggered_at if state else None,
            "acknowledged": bool(state and state.acknowledged),
            "acknowledged_at": state.acknowledged_at if state else None,
            "pending": pending,
            "condition_since": state.condition_since if pending else None,
            "delay_ms": rule.get("delay_ms", 0),
            "hmi_owned": True,
            "mode": expected_mode,
        })
    if transition_added:
        excess_ids = select(ScenarioHmiAlarmTransition.id).where(
            ScenarioHmiAlarmTransition.scenario_id == scenario_id,
            ScenarioHmiAlarmTransition.hmi_node_id == hmi_node_id,
        ).order_by(ScenarioHmiAlarmTransition.id.desc()).offset(_HMI_ALARM_HISTORY_CAPACITY)
        session.exec(delete(ScenarioHmiAlarmTransition).where(ScenarioHmiAlarmTransition.id.in_(excess_ids)))
    session.commit()
    return result


def acknowledge_hmi_alarm_state(
    session: Session, scenario_id: str, hmi_node_id: str, target_node_id: str, address: str,
) -> ScenarioHmiAlarmState | None:
    state = session.exec(select(ScenarioHmiAlarmState).where(
        ScenarioHmiAlarmState.scenario_id == scenario_id,
        ScenarioHmiAlarmState.hmi_node_id == hmi_node_id,
        ScenarioHmiAlarmState.target_node_id == target_node_id,
        ScenarioHmiAlarmState.address == address,
    )).first()
    if state is None or not state.active or state.quality != "good":
        return None
    state.acknowledged = True
    state.acknowledged_at = datetime.now(timezone.utc).isoformat()
    session.add(state)
    session.commit()
    session.refresh(state)
    return state


def hmi_operation(
    manager: Any,
    scenario_id: str,
    hmi_node_id: str,
    target: dict[str, Any],
    points: list[dict[str, Any]],
    route: dict[str, Any] | None = None,
    *,
    address: str | None = None,
    value: bool | int | float | None = None,
) -> list[dict[str, Any]]:
    try:
        containers = manager.client.containers.list(
            all=False, filters={"label": f"anvil.scenario_id={scenario_id}"},
        )
        container = next((item for item in containers if item.labels.get("anvil.node_id") == hmi_node_id), None)
    except DockerException as error:
        raise DockerManagerError(f"Unable to locate HMI container: {error}") from error
    if container is None:
        raise LookupError(f"Running HMI '{hmi_node_id}' was not found in this scenario")

    via_route = route or {}
    config = {
        "node_id": target["node_id"], "device_type": target["device_type"],
        "host": via_route.get("host", target["ip"]),
        "port": via_route.get("port", 102 if target["device_type"] == "plc-s7" else target["config"]["port"]),
        "rack": target["config"].get("rack", 0), "slot": target["config"].get("slot", 1),
        "unit_id": target["config"].get("unit_id", 1),
        "transport": target["config"].get("transport", "tcp"),
        "baudrate": target["config"].get("baudrate", 19200),
        "bytesize": target["config"].get("bytesize", 8),
        "parity": target["config"].get("parity", "N"),
        "stopbits": target["config"].get("stopbits", 1),
        "points": points,
    }
    action = "read" if address is None else "write"
    args = ["python", "/app/hmi_operations.py", json.dumps(config, separators=(",", ":")),
            action, address or "", json.dumps(value, allow_nan=False) if address is not None else ""]
    try:
        result = container.exec_run(args, stdout=True, stderr=True, demux=False)
    except DockerException as error:
        raise DockerManagerError(f"Unable to communicate from HMI: {error}") from error
    output = result.output.decode("utf-8", errors="replace") if isinstance(result.output, bytes) else str(result.output)
    if result.exit_code:
        raise DockerManagerError(f"HMI protocol operation failed: {output.strip()}")
    try:
        return json.loads(output.strip().splitlines()[-1])
    except (ValueError, IndexError) as error:
        raise DockerManagerError(f"HMI returned an invalid protocol response: {output.strip()}") from error
