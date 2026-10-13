"""Bounded exercise timeline execution and objective evaluation."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import math
from datetime import datetime, timedelta, timezone
from functools import wraps
from threading import RLock
from typing import Any

from sqlmodel import Session, select

from models.audit import ScenarioAuditEvent
from models.exercise import AuditEventObjective, ExerciseDefinition, ExerciseRun, ProtocolWritePolicyObjective
from models.field import FieldControl
from models.scenario import ScenarioRecord
from services.audit import record_audit_event
from services.docker_manager import DockerManager
from services.io_map import build_scenario_io_map
from services.io_policy import enforce_firewall_policy as _enforce_firewall_policy
from services.io_policy import validate_component_range as _validate_component_range
from services.io_policy import validate_s7_write_value as _validate_s7_write_value
from services.modbus_operations import read_device_values as read_modbus_values, write_device_point as write_modbus_point
from services.plc_operations import _component_address, read_device_values as read_plc_values, write_device_point as write_plc_point
from services.protocol_attack import execute_protocol_write
from services.node_operation_journal import (
    begin_node_operation,
    mark_node_operation_complete,
    mark_node_operation_retryable,
)
from services.scenario_leases import (
    OperationLeaseHeartbeat,
    acquire_operation_lease,
    make_operation_owner_id,
    release_operation_lease,
)

_EXERCISE_LOCK = RLock()


def _serialized(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with _EXERCISE_LOCK:
            return function(*args, **kwargs)
    return wrapped


def create_run(session: Session, scenario_id: str, definition: ExerciseDefinition) -> ExerciseRun:
    scenario = session.get(ScenarioRecord, scenario_id)
    if scenario is None:
        raise LookupError("Scenario was not found")
    _validate_definition(scenario_id, scenario, definition)
    run = ExerciseRun(scenario_id=scenario_id, definition_json=definition.model_dump_json(exclude_unset=True))
    session.add(run)
    session.commit()
    session.refresh(run)
    record_audit_event(session, scenario_id, "exercise.created", {
        "exercise_id": run.id, "name": definition.name,
        "event_count": len(definition.events), "objective_count": len(definition.objectives),
    }, actor="api-client")
    return run


@_serialized
def start_run(session: Session, scenario_id: str, run_id: str) -> ExerciseRun:
    scenario = session.get(ScenarioRecord, scenario_id)
    run = session.get(ExerciseRun, run_id)
    if scenario is None or run is None or run.scenario_id != scenario_id:
        raise LookupError("Exercise was not found for this scenario")
    if scenario.status not in {"running", "partial"}:
        raise ValueError("Deploy the scenario before starting an exercise")
    _validate_definition(scenario_id, scenario, ExerciseDefinition.model_validate_json(run.definition_json))
    if run.status != "ready":
        raise ValueError(f"Exercise cannot start from status '{run.status}'")
    active = session.exec(select(ExerciseRun).where(
        ExerciseRun.scenario_id == scenario_id, ExerciseRun.status.in_(["running", "paused"])
    )).first()
    if active:
        raise ValueError("Only one exercise may run in a scenario at a time")
    run.started_at = datetime.now(timezone.utc)
    run.status = "running"
    run.paused_at = None
    run.paused_duration_seconds = 0.0
    session.add(run)
    session.commit()
    record_audit_event(session, scenario_id, "exercise.started", {"exercise_id": run.id}, actor="api-client")
    return run


@_serialized
def cancel_run(session: Session, scenario_id: str, run_id: str, *, manager: Any | None = None) -> ExerciseRun:
    run = session.get(ExerciseRun, run_id)
    if run is None or run.scenario_id != scenario_id:
        raise LookupError("Exercise was not found for this scenario")
    if run.status not in {"running", "paused"}:
        raise ValueError("Only an active exercise can be cancelled")
    _clear_active_modbus_interceptions(session, scenario_id, run, manager=manager, strict=True)
    run.status = "cancelled"
    run.finished_at = datetime.now(timezone.utc)
    run.paused_at = None
    session.add(run)
    session.commit()
    record_audit_event(session, scenario_id, "exercise.cancelled", {"exercise_id": run.id}, actor="api-client")
    return run


@_serialized
def pause_active_exercises(session: Session, scenario_id: str, *, now: datetime | None = None) -> int:
    current = _aware(now or datetime.now(timezone.utc))
    runs = session.exec(select(ExerciseRun).where(
        ExerciseRun.scenario_id == scenario_id, ExerciseRun.status == "running"
    )).all()
    for run in runs:
        run.status = "paused"
        run.paused_at = current
        session.add(run)
    if runs:
        session.commit()
        for run in runs:
            record_audit_event(session, scenario_id, "exercise.paused", {"exercise_id": run.id}, actor="exercise-engine")
    return len(runs)


def _shift_objective_timestamps(tracking: dict[str, Any], seconds: float) -> None:
    """Exclude scenario pauses from duration, trend and field-sequence clocks."""
    delta = timedelta(seconds=seconds)
    for objective_id, progress in tracking.items():
        if not progress:
            continue
        if isinstance(progress, str):
            try:
                tracking[objective_id] = (_aware(datetime.fromisoformat(progress)) + delta).isoformat()
            except (TypeError, ValueError):
                tracking[objective_id] = None
            continue
        if not isinstance(progress, dict):
            continue
        timestamps: list[dict[str, Any]] = []
        for key in ("samples", "matches"):
            items = progress.get(key)
            if isinstance(items, list):
                timestamps.extend(item for item in items if isinstance(item, dict))
        for item in timestamps:
            try:
                item["at"] = (_aware(datetime.fromisoformat(item["at"])) + delta).isoformat()
            except (KeyError, TypeError, ValueError):
                continue
        if progress.get("last_match_at"):
            try:
                progress["last_match_at"] = (
                    _aware(datetime.fromisoformat(progress["last_match_at"])) + delta
                ).isoformat()
            except (TypeError, ValueError):
                progress["last_match_at"] = None


@_serialized
def resume_paused_exercises(session: Session, scenario_id: str, *, now: datetime | None = None) -> int:
    current = _aware(now or datetime.now(timezone.utc))
    runs = session.exec(select(ExerciseRun).where(
        ExerciseRun.scenario_id == scenario_id, ExerciseRun.status == "paused"
    )).all()
    for run in runs:
        paused_at = _aware(run.paused_at) if run.paused_at else current
        pause_seconds = max(0.0, (current - paused_at).total_seconds())
        run.paused_duration_seconds = max(0.0, run.paused_duration_seconds or 0.0) + pause_seconds
        run.paused_at = None
        run.status = "running"
        tracking = json.loads(run.objective_tracking_json or "{}")
        _shift_objective_timestamps(tracking, pause_seconds)
        run.objective_tracking_json = json.dumps(tracking, ensure_ascii=False, separators=(",", ":"))
        session.add(run)
    if runs:
        session.commit()
        for run in runs:
            record_audit_event(session, scenario_id, "exercise.resumed", {"exercise_id": run.id}, actor="exercise-engine")
    return len(runs)


@_serialized
def run_due_exercises(session: Session, manager: Any, *, now: datetime | None = None) -> int:
    """Apply due timeline entries, then evaluate objectives when the timeline ends."""
    current = now or datetime.now(timezone.utc)
    runs = session.exec(select(ExerciseRun).where(ExerciseRun.status.in_(["running", "paused"]))).all()
    processed = 0
    for run in runs:
        scenario = session.get(ScenarioRecord, run.scenario_id)
        if scenario is None or scenario.status not in {"running", "partial", "paused"}:
            _finish(run, "failed", current)
            run.event_results_json = _append_json(run.event_results_json, {
                "kind": "runtime", "status": "failed", "error": "Scenario is no longer deployed",
            })
            _clear_active_modbus_interceptions(session, run.scenario_id, run, manager=manager, strict=False)
            session.add(run)
            session.commit()
            record_audit_event(session, run.scenario_id, "exercise.failed", {
                "exercise_id": run.id, "reason": "scenario_not_deployed",
            })
            processed += 1
            continue
        if scenario.status == "paused":
            if run.status == "running":
                run.status = "paused"
                run.paused_at = _aware(current)
                session.add(run)
                session.commit()
                record_audit_event(session, run.scenario_id, "exercise.paused", {"exercise_id": run.id}, actor="exercise-engine")
                processed += 1
            continue
        if run.status == "paused":
            pause_seconds = max(0.0, (_aware(current) - _aware(run.paused_at)).total_seconds()) if run.paused_at else 0.0
            run.paused_duration_seconds = max(0.0, run.paused_duration_seconds or 0.0) + pause_seconds
            run.paused_at = None
            run.status = "running"
            tracking = json.loads(run.objective_tracking_json or "{}")
            _shift_objective_timestamps(tracking, pause_seconds)
            run.objective_tracking_json = json.dumps(tracking, ensure_ascii=False, separators=(",", ":"))
            session.add(run)
            session.commit()
            record_audit_event(session, run.scenario_id, "exercise.resumed", {"exercise_id": run.id}, actor="exercise-engine")
        definition = ExerciseDefinition.model_validate_json(run.definition_json)
        elapsed = max(0.0, (current - _aware(run.started_at)).total_seconds()
                      - max(0.0, run.paused_duration_seconds or 0.0))
        event_deferred = False
        while run.next_event_index < len(definition.events):
            violation = _first_fail_fast_audit_violation(
                session, scenario, definition, run.started_at,
            )
            if violation is not None:
                _finish_forbidden_audit_violation(run, definition, violation, current)
                _clear_active_modbus_interceptions(session, run.scenario_id, run, manager=manager, strict=False)
                session.add(run)
                session.commit()
                record_audit_event(session, run.scenario_id, "exercise.failed", {
                    "exercise_id": run.id, "reason": "forbidden_audit_event",
                    "event_type": violation.event_type,
                    "objective_id": violation.objective_id,
                })
                processed += 1
                break
            event = definition.events[run.next_event_index]
            if event.at_seconds > elapsed:
                break
            lease_engine = session.get_bind()
            lease_owner = make_operation_owner_id()
            if not acquire_operation_lease(lease_engine, run.scenario_id, "exercise", lease_owner):
                event_deferred = True
                break
            try:
                with OperationLeaseHeartbeat(lease_engine, run.scenario_id, "exercise", lease_owner) as heartbeat:
                    event_payload = {**event.model_dump(mode="json", exclude_unset=True), "exercise_id": run.id}
                    result = _apply_event(session, manager, scenario, event_payload)
                    if heartbeat.lost.is_set():
                        raise RuntimeError("Exercise event lease was lost during execution")
                    entry = {"event_id": event.event_id, "kind": event.kind, "status": "applied", "result": result}
                    record_audit_event(session, run.scenario_id, "exercise.event_applied", {
                        "exercise_id": run.id, "event_id": event.event_id,
                        "event_kind": event.kind, **({"node_id": event.node_id} if hasattr(event, "node_id") else {}),
                    })
                    run.event_results_json = _append_json(run.event_results_json, entry)
                    run.next_event_index += 1
                    session.add(run)
                    session.commit()
            except Exception as error:
                entry = {"event_id": event.event_id, "kind": event.kind, "status": "failed", "error": str(error)[:500]}
                run.event_results_json = _append_json(run.event_results_json, entry)
                run.next_event_index += 1
                _finish(run, "failed", current)
                _clear_active_modbus_interceptions(session, run.scenario_id, run, manager=manager, strict=False)
                session.add(run)
                session.commit()
                record_audit_event(session, run.scenario_id, "exercise.failed", {
                    "exercise_id": run.id, "event_id": event.event_id,
                })
                processed += 1
                break
            finally:
                release_operation_lease(lease_engine, run.scenario_id, "exercise", lease_owner)
            processed += 1
        if event_deferred:
            continue
        if run.status == "running":
            violation = _first_fail_fast_audit_violation(
                session, scenario, definition, run.started_at,
            )
            if violation is not None:
                _finish_forbidden_audit_violation(run, definition, violation, current)
                _clear_active_modbus_interceptions(session, run.scenario_id, run, manager=manager, strict=False)
                session.add(run)
                session.commit()
                record_audit_event(session, run.scenario_id, "exercise.failed", {
                    "exercise_id": run.id, "reason": "forbidden_audit_event",
                    "event_type": violation.event_type,
                    "objective_id": violation.objective_id,
                })
                processed += 1
        if run.status == "running":
            try:
                _sample_timed_objectives(session, manager, scenario, definition, run, current)
                _detect_protocol_write_policy_violations(
                    session, scenario, definition, run,
                )
            except Exception as error:
                run.event_results_json = _append_json(run.event_results_json, {
                    "kind": "runtime", "status": "failed", "error": str(error)[:500],
                })
                _finish(run, "failed", current)
                _clear_active_modbus_interceptions(session, run.scenario_id, run, manager=manager, strict=False)
                session.add(run)
                session.commit()
                record_audit_event(session, run.scenario_id, "exercise.failed", {
                    "exercise_id": run.id, "reason": "objective_sampling_failed",
                })
                processed += 1
                continue
        if run.status != "running" or run.next_event_index < len(definition.events):
            continue
        try:
            if any(isinstance(item, AuditEventObjective)
                   and item.event_type in {
                       "security.modbus_interception_match",
                       "protocol.s7_operation",
                       "protocol.modbus_operation",
                   }
                   for item in definition.objectives) or any(
                       isinstance(item, ProtocolWritePolicyObjective) for item in definition.objectives
                   ):
                # Protocol traces use durable per-device queues. The periodic
                # collector may not have run yet when a short exercise ends.
                from services.protocol_trace_collector import flush_protocol_traces_best_effort
                session.commit()
                flush_protocol_traces_best_effort(
                    session.get_bind(), manager=manager, scenario_id=run.scenario_id,
                )
                _detect_protocol_write_policy_violations(
                    session, scenario, definition, run,
                )
            tracking = json.loads(run.objective_tracking_json or "{}")
            outcomes = [_evaluate_objective(session, manager, scenario, item.model_dump(mode="json"),
                                            run.started_at, now=current, tracking=tracking)
                        for item in definition.objectives]
        except Exception as error:
            run.objective_results_json = json.dumps([{"passed": False, "error": str(error)[:500]}])
            _finish(run, "failed", current)
            _clear_active_modbus_interceptions(session, run.scenario_id, run, manager=manager, strict=False)
            session.add(run)
            session.commit()
            record_audit_event(session, run.scenario_id, "exercise.failed", {
                "exercise_id": run.id, "reason": "objective_evaluation_failed",
            })
            processed += 1
            continue
        if any(item.get("waiting_duration") for item in outcomes):
            run.objective_results_json = json.dumps(outcomes, ensure_ascii=False, separators=(",", ":"))
            session.add(run)
            session.commit()
            processed += 1
            continue
        run.objective_results_json = json.dumps(outcomes, ensure_ascii=False, separators=(",", ":"))
        ok, score = _score_outcomes(definition, outcomes)
        run.score = score
        run.passed = ok
        _finish(run, "completed" if ok else "failed", current)
        _clear_active_modbus_interceptions(session, run.scenario_id, run, manager=manager, strict=False)
        session.add(run)
        session.commit()
        record_audit_event(session, run.scenario_id, "exercise.completed" if ok else "exercise.failed", {
            "exercise_id": run.id, "objectives_passed": sum(item["passed"] for item in outcomes),
            "objectives_total": len(outcomes), "score": score,
        })
        processed += 1
    return processed


def _first_fail_fast_audit_violation(session: Session, scenario: ScenarioRecord,
                                     definition: ExerciseDefinition,
                                     started_at: datetime | None) -> AuditEventObjective | None:
    for objective in definition.objectives:
        if not isinstance(objective, AuditEventObjective) or not objective.fail_fast:
            continue
        query = select(ScenarioAuditEvent).where(
            ScenarioAuditEvent.scenario_id == scenario.id,
            ScenarioAuditEvent.event_type == objective.event_type,
        )
        if started_at is not None:
            query = query.where(ScenarioAuditEvent.timestamp >= _aware(started_at))
        if any(_audit_details_match(event.details_json, objective.details_match)
               for event in session.exec(query).all()):
            return objective
    return None


def _audit_details_match(details_json: str, expected: dict[str, Any]) -> bool:
    if not expected:
        return True
    try:
        details = json.loads(details_json or "{}")
    except (TypeError, ValueError):
        return False
    if not isinstance(details, dict):
        return False
    for key, wanted in expected.items():
        actual = details.get(key)
        if type(wanted) is bool:
            if type(actual) is not bool or actual is not wanted:
                return False
        elif isinstance(wanted, (int, float)) and not isinstance(wanted, bool):
            if type(actual) not in (int, float) or isinstance(actual, bool) or actual != wanted:
                return False
        elif type(actual) is not type(wanted) or actual != wanted:
            return False
    return True


def _detect_protocol_write_policy_violations(
    session: Session, scenario: ScenarioRecord, definition: ExerciseDefinition, run: ExerciseRun,
) -> int:
    """Compare durable protocol write traces with exercise peer-IP allowlists."""
    policies = [item for item in definition.objectives if isinstance(item, ProtocolWritePolicyObjective)]
    if not policies or run.started_at is None:
        return 0
    tracking = json.loads(run.objective_tracking_json or "{}")
    detected = 0
    changed = False
    modbus_writes = {
        "write_coil", "write_holding_register", "write_coils",
        "write_holding_registers", "mask_write_register", "read_write_registers",
    }
    for objective in policies:
        progress = tracking.get(objective.objective_id)
        if not isinstance(progress, dict) or progress.get("kind") != "protocol_write_policy":
            progress = {"kind": "protocol_write_policy", "last_event_id": 0, "detection_count": 0}
        try:
            last_event_id = max(0, int(progress.get("last_event_id", 0)))
        except (TypeError, ValueError):
            last_event_id = 0
        expected_type = ("protocol.s7_operation" if objective.protocol == "s7"
                         else "protocol.modbus_operation")
        traces = session.exec(
            select(ScenarioAuditEvent).where(
                ScenarioAuditEvent.scenario_id == scenario.id,
                ScenarioAuditEvent.event_type == expected_type,
                ScenarioAuditEvent.id > last_event_id,
                ScenarioAuditEvent.timestamp >= _aware(run.started_at),
            ).order_by(ScenarioAuditEvent.id.asc())
        ).all()
        for trace in traces:
            progress["last_event_id"] = trace.id or last_event_id
            try:
                details = json.loads(trace.details_json or "{}")
            except (TypeError, ValueError):
                continue
            if details.get("node_id") != objective.target_node_id:
                continue
            operation = details.get("operation")
            is_write = operation == "write" if objective.protocol == "s7" else operation in modbus_writes
            if not is_write:
                continue
            source = details.get("source")
            source_ip = source.strip() if isinstance(source, str) else "unknown"
            if source_ip in objective.allowed_source_ips:
                continue
            violation_details: dict[str, Any] = {
                "exercise_id": run.id,
                "objective_id": objective.objective_id,
                "protocol_event_id": trace.id,
                "protocol": objective.protocol,
                "target_node_id": objective.target_node_id,
                "source": source_ip or "unknown",
                "source_attribution": "observed_peer_ip",
                "operation": operation,
                "result": details.get("result"),
                "reason": "source_ip_not_allowlisted",
            }
            for key in ("address", "area", "db", "byte_offset", "byte_count", "space",
                        "unit_id", "function_code", "count"):
                if key in details:
                    violation_details[key] = details[key]
            record_audit_event(
                session, scenario.id, "security.protocol_write_policy_violation",
                violation_details, actor="protocol-policy-detector", commit=False,
            )
            progress["detection_count"] = int(progress.get("detection_count", 0)) + 1
            detected += 1
        tracking[objective.objective_id] = progress
        changed = changed or bool(traces)
    if changed:
        run.objective_tracking_json = json.dumps(tracking, ensure_ascii=False, separators=(",", ":"))
        session.add(run)
        session.commit()
    return detected


def _finish_forbidden_audit_violation(run: ExerciseRun, definition: ExerciseDefinition,
                                      objective: AuditEventObjective, now: datetime) -> None:
    results = []
    for item in definition.objectives:
        if item.objective_id == objective.objective_id:
            results.append({
                "objective_id": item.objective_id,
                "description": item.description,
                "weight": item.weight,
                "kind": "audit_event",
                "expected_event": item.event_type,
                "details_match": item.details_match,
                "expectation": "absent",
                "actual": True,
                "passed": False,
                "terminated_run": True,
            })
        else:
            results.append({
                "objective_id": item.objective_id,
                "description": item.description,
                "weight": item.weight,
                "kind": item.kind,
                "passed": False,
                "status": "not_evaluated",
                "reason": "Exercise terminated after a forbidden audit event",
            })
    run.objective_results_json = json.dumps(results, ensure_ascii=False, separators=(",", ":"))
    run.score = 0
    run.passed = False
    run.event_results_json = _append_json(run.event_results_json, {
        "kind": "objective", "status": "failed", "reason": "forbidden_audit_event",
        "objective_id": objective.objective_id, "event_type": objective.event_type,
    })
    _finish(run, "failed", now)


def interrupt_active_exercises(session: Session) -> int:
    runs = session.exec(select(ExerciseRun).where(ExerciseRun.status.in_(["running", "paused"]))).all()
    for run in runs:
        _finish(run, "interrupted", datetime.now(timezone.utc))
        session.add(run)
        record_audit_event(session, run.scenario_id, "exercise.interrupted", {"exercise_id": run.id})
    if runs:
        session.commit()
    return len(runs)


async def exercise_scheduler(stop: asyncio.Event, engine: Any, manager: Any | None = None) -> None:
    from sqlmodel import Session as SqlSession

    owns_manager = manager is None
    manager = manager or DockerManager()
    while not stop.is_set():
        try:
            def tick() -> None:
                with SqlSession(engine) as session:
                    run_due_exercises(session, manager)
            await asyncio.to_thread(tick)
        except Exception:
            import logging
            logging.getLogger("anvil.exercises").exception("Exercise scheduler tick failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=0.25)
        except TimeoutError:
            pass
    if owns_manager:
        manager.client.close()


def _apply_event(session: Session, manager: Any, scenario: ScenarioRecord, event: dict[str, Any]) -> dict[str, Any]:
    scenario_id = scenario.id
    if event["kind"] == "modbus_interception":
        route_node_id = f"__anvil_route_{event['route_id']}"
        rules = ([{**item, "exercise_id": event["exercise_id"], "event_id": event["event_id"]}
                  for item in event.get("rules", [])] if event["action"] == "set" else None)
        result = manager.set_modbus_route_interception(scenario_id, route_node_id, rules)
        record_audit_event(session, scenario_id, "security.modbus_interception", {
            "source": "exercise", "exercise_id": event.get("exercise_id"),
            "event_id": event.get("event_id"), "route_id": event["route_id"],
            "action": event["action"], "rule_ids": [item["rule_id"] for item in rules or []],
            "active_rules": result.get("active_rules", 0),
        }, actor="exercise-engine")
        return {"route_id": event["route_id"], "action": event["action"],
                "active_rules": result.get("active_rules", 0)}
    if event["kind"] == "field_control":
        from services.field_operations import field_operation

        controls = {key: event[key] for key in ("fault", "override", "demand_flow_lpm", "leak_flow_lpm") if key in event}
        payload = {"object_id": event["object_id"], **controls}
        snapshot = field_operation(manager, scenario_id, "control", payload)
        record_audit_event(session, scenario_id, "operator.field_control", {
            **payload, "source": "exercise", "exercise_id": event.get("exercise_id"),
        }, actor="exercise-engine")
        current = next((item for item in snapshot.get("objects", [])
                        if item.get("object_id") == event["object_id"]), {})
        return {"object_id": event["object_id"], **controls,
                "state": current.get("state"), "fault": current.get("fault")}
    if event["kind"] == "node_lifecycle":
        result = _journaled_node_lifecycle(
            session, manager, scenario_id, event["node_id"], event["action"], event,
        )
        record_audit_event(session, scenario_id, "scenario.node_" + event["action"], {
            "node_id": event["node_id"], "source": "exercise", "exercise_id": event.get("exercise_id"),
        }, actor="exercise-engine")
        return {"node_id": event["node_id"], "action": event["action"], "status": result.get("status")}
    if event["kind"] == "network_route":
        route_node_id = f"__anvil_route_{event['route_id']}"
        action = "stop" if event["action"] == "interrupt" else "start"
        result = _journaled_node_lifecycle(session, manager, scenario_id, route_node_id, action, event)
        audit_type = "scenario.network_route_interrupted" if action == "stop" else "scenario.network_route_restored"
        record_audit_event(session, scenario_id, audit_type, {
            "route_id": event["route_id"], "source": "exercise", "exercise_id": event.get("exercise_id"),
        }, actor="exercise-engine")
        return {"route_id": event["route_id"], "action": event["action"], "status": result.get("status")}
    if event["kind"] == "protocol_write":
        runtimes = scenario.runtime_devices()
        by_node = {item["node_id"]: item for item in runtimes}
        source = by_node[event["source_node_id"]]
        target = by_node[event["target_node_id"]]
        point = next(item for item in build_scenario_io_map(scenario_id, [target]).points
                     if item.address == event["address"])
        component = next((item for item in target["config"].get("components", [])
                          if _component_address(item) == event["address"]), None)
        route = None
        if event.get("route_id"):
            route = source["config"].get("tcp_routes", {}).get(event["route_id"])
            if route is None:
                raise ValueError("The selected protocol attack route is unavailable in the runtime plan")
        result = execute_protocol_write(manager, scenario_id, source, target, point, component,
                                        point.data_type, event["value"], route)
        record_audit_event(session, scenario_id, "attack.protocol_write", {
            "source": "exercise", "source_node_id": source["node_id"],
            "target_node_id": target["node_id"], "address": point.address,
            "value": result["value"], "protocol": result["protocol"],
            "route_id": event.get("route_id"), "exercise_id": event.get("exercise_id"),
            "event_id": event.get("event_id"),
        }, actor="exercise-engine")
        return result
    runtimes = scenario.runtime_devices()
    target = next(item for item in runtimes if item["node_id"] == event["node_id"])
    point = next(item for item in build_scenario_io_map(scenario_id, [target]).points if item.address == event["address"])
    if point.access != "read_write":
        raise PermissionError("Exercise event tried to write a read-only I/O point")
    _enforce_firewall_policy(scenario, target)
    _validate_write(target, point.data_type, event["value"], event["address"])
    if target["device_type"] == "plc-s7":
        component = next(item for item in target["config"]["components"]
                         if _component_address(item) == event["address"]
                         and item["type"] in {"valve", "motor", "pump_command", "io_channel"})
        _validate_component_range(component, event["value"])
        result = write_plc_point(manager, scenario_id, target, component, event["value"])
    elif target["device_type"] == "modbus-tcp":
        point_config = next(item for item in target["config"]["points"]
                            if f"{item['type']}:{item['address']}" == event["address"])
        result = write_modbus_point(manager, scenario_id, target, point_config, event["value"])
    else:
        raise ValueError("Exercise point writes support S7 and Modbus TCP only")
    record_audit_event(session, scenario_id, "operator.point_written", {
        "source": "exercise", "exercise_id": event.get("exercise_id"),
        "node_id": event["node_id"], "address": event["address"], "value": event["value"],
    }, actor="exercise-engine")
    return {"node_id": event["node_id"], "address": event["address"], "value": result.get("value")}


def _journaled_node_lifecycle(session: Session, manager: Any, scenario_id: str,
                              node_id: str, action: str, event: dict[str, Any]) -> dict[str, Any]:
    exercise_id = str(event.get("exercise_id") or "unknown")
    event_id = str(event.get("event_id") or "unknown")
    precondition = (
        manager.node_operation_checkpoint(scenario_id, node_id)
        if action == "restart" and hasattr(manager, "node_operation_checkpoint") else {}
    )
    entry, completed = begin_node_operation(
        session.get_bind(), scenario_id, node_id, action, source="exercise",
        idempotency_key=f"exercise:{exercise_id}:{event_id}",
        precondition=precondition,
    )
    if completed:
        return json.loads(entry.result_json or "{}")
    try:
        result = manager.node_lifecycle(scenario_id, node_id, action)
    except Exception as error:
        mark_node_operation_retryable(session.get_bind(), entry.id, str(error))
        raise
    mark_node_operation_complete(session.get_bind(), entry.id, result)
    return result


def _evaluate_objective(session: Session, manager: Any, scenario: ScenarioRecord,
                        objective: dict[str, Any], started_at: datetime | None, *,
                        now: datetime | None = None, tracking: dict[str, Any] | None = None) -> dict[str, Any]:
    base = {"objective_id": objective["objective_id"], "description": objective["description"],
            "weight": objective.get("weight", 1)}
    if objective["kind"] == "audit_event":
        query = select(ScenarioAuditEvent).where(
            ScenarioAuditEvent.scenario_id == scenario.id,
            ScenarioAuditEvent.event_type == objective["event_type"],
        )
        if started_at is not None:
            query = query.where(ScenarioAuditEvent.timestamp >= _aware(started_at))
        details_match = objective.get("details_match", {})
        found = any(_audit_details_match(event.details_json, details_match)
                    for event in session.exec(query).all())
        expectation = objective.get("expectation", "occurred")
        passed = found if expectation == "occurred" else not found
        return {**base, "kind": "audit_event", "expected_event": objective["event_type"],
                "details_match": details_match, "expectation": expectation,
                "actual": found, "passed": passed}
    if objective["kind"] == "protocol_write_policy":
        query = select(ScenarioAuditEvent).where(
            ScenarioAuditEvent.scenario_id == scenario.id,
            ScenarioAuditEvent.event_type == "security.protocol_write_policy_violation",
        )
        if started_at is not None:
            query = query.where(ScenarioAuditEvent.timestamp >= _aware(started_at))
        detections = []
        for event in session.exec(query.order_by(ScenarioAuditEvent.id.asc())).all():
            try:
                details = json.loads(event.details_json or "{}")
            except (TypeError, ValueError):
                continue
            if details.get("objective_id") == objective["objective_id"]:
                detections.append({
                    "protocol_event_id": details.get("protocol_event_id"),
                    "source": details.get("source"),
                    "operation": details.get("operation"),
                    "address": details.get("address"),
                    "area": details.get("area"),
                    "db": details.get("db"),
                })
        found = bool(detections)
        expectation = objective.get("expectation", "detected")
        passed = found if expectation == "detected" else not found
        return {**base, "kind": "protocol_write_policy", "protocol": objective["protocol"],
                "target_node_id": objective["target_node_id"],
                "allowed_source_ips": objective.get("allowed_source_ips", []),
                "expectation": expectation, "actual": found,
                "detection_count": len(detections), "detections": detections,
                "passed": passed}
    if objective["kind"] == "audit_sequence":
        expected = objective["event_types"]
        query = select(ScenarioAuditEvent.timestamp, ScenarioAuditEvent.id,
                       ScenarioAuditEvent.event_type).where(
            ScenarioAuditEvent.scenario_id == scenario.id,
            ScenarioAuditEvent.event_type.in_(set(expected)),
        )
        if started_at is not None:
            query = query.where(ScenarioAuditEvent.timestamp >= _aware(started_at))
        query = query.order_by(ScenarioAuditEvent.timestamp.asc(), ScenarioAuditEvent.id.asc())
        matched: list[str] = []
        last_match: datetime | None = None
        max_gap = objective.get("max_gap_seconds")
        for timestamp, _event_id, event_type in session.exec(query).all():
            timestamp = _aware(timestamp)
            if (last_match is not None and max_gap is not None
                    and (timestamp - last_match).total_seconds() > max_gap):
                matched, last_match = [], None
            next_index = len(matched)
            if event_type == expected[next_index]:
                matched.append(event_type)
                last_match = timestamp
                if len(matched) == len(expected):
                    break
            elif event_type == expected[0]:
                matched, last_match = [event_type], timestamp
        return {**base, "kind": "audit_sequence", "expected_sequence": expected,
                "matched_sequence": matched, "max_gap_seconds": max_gap,
                "passed": len(matched) == len(expected)}
    if objective["kind"] == "field_sequence":
        progress = (tracking or {}).get(objective["objective_id"], {})
        matches = progress.get("matches", []) if isinstance(progress, dict) else []
        next_step = progress.get("next_step_index", 0) if isinstance(progress, dict) else 0
        expected = [step if isinstance(step, dict) else step.model_dump(mode="json")
                    for step in objective["steps"]]
        return {**base, "kind": "field_sequence", "expected_sequence": expected,
                "matched_sequence": matches, "next_step_index": next_step,
                "max_gap_seconds": objective.get("max_gap_seconds"),
                "passed": next_step >= len(expected)}
    if objective["kind"] == "field_trend":
        progress = (tracking or {}).get(objective["objective_id"], {})
        samples = progress.get("samples", []) if isinstance(progress, dict) else []
        window = objective["window_seconds"]
        current = _aware(now or datetime.now(timezone.utc))
        valid = []
        for sample in samples if isinstance(samples, list) else []:
            try:
                timestamp = _aware(datetime.fromisoformat(sample["at"]))
                value = sample["value"]
                if type(value) in {int, float} and math.isfinite(value) and current - timedelta(seconds=window + 1) <= timestamp <= current:
                    valid.append((timestamp, float(value)))
            except (KeyError, TypeError, ValueError):
                continue
        valid.sort(key=lambda item: item[0])
        gaps = [(right[0] - left[0]).total_seconds() for left, right in zip(valid, valid[1:])]
        covered = bool(
            len(valid) >= 2
            and (valid[-1][0] - valid[0][0]).total_seconds() >= window - 1.25
            and (current - valid[-1][0]).total_seconds() <= 2.5
            and all(gap <= 2.5 for gap in gaps)
        )
        delta = valid[-1][1] - valid[0][1] if covered else None
        threshold = float(objective.get("minimum_delta", 0))
        direction = objective["direction"]
        passed = bool(covered and (
            delta >= threshold if direction == "rising" else
            delta <= -threshold if direction == "falling" else abs(delta) <= threshold
        ))
        from services.field_operations import field_operation
        snapshot = field_operation(manager, scenario.id)
        field_object = next((item for item in snapshot.get("objects", []) if item.get("object_id") == objective["object_id"]), None)
        if field_object is None or objective["property"] not in field_object:
            raise LookupError(f"Field trend '{objective['object_id']}.{objective['property']}' is unavailable")
        return {**base, "kind": "field_trend", "object_id": objective["object_id"],
                "property": objective["property"], "direction": direction,
                "window_seconds": window, "minimum_delta": threshold,
                "actual_delta": round(delta, 6) if delta is not None else None,
                "sample_count": len(valid), "covered_seconds": round((valid[-1][0] - valid[0][0]).total_seconds(), 3) if len(valid) >= 2 else 0,
                "waiting_window": not covered, "passed": passed}
    if objective["kind"] == "field_value":
        from services.field_operations import field_operation

        snapshot = field_operation(manager, scenario.id)
        field_object = next((item for item in snapshot.get("objects", [])
                             if item.get("object_id") == objective["object_id"]), None)
        if field_object is None or objective["property"] not in field_object:
            raise LookupError(
                f"Field objective '{objective['object_id']}.{objective['property']}' is unavailable"
            )
        actual, expected, operator = (
            field_object[objective["property"]], objective["value"], objective["operator"]
        )
        condition_met = _compare(actual, operator, expected)
        duration = objective.get("duration_seconds", 0)
        condition_since = (tracking or {}).get(objective["objective_id"])
        sustained = 0.0
        if condition_since:
            since = datetime.fromisoformat(condition_since)
            sustained = max(0.0, (_aware(now) - _aware(since)).total_seconds())
        waiting = condition_met and duration > 0 and sustained < duration
        passed = condition_met and not waiting
        return {**base, "kind": "field_value", "object_id": objective["object_id"],
                "property": objective["property"], "operator": operator, "expected": expected,
                "actual": actual, "duration_seconds": duration,
                "sustained_seconds": round(sustained, 3), "waiting_duration": waiting,
                "passed": passed}
    runtimes = scenario.runtime_devices()
    target = next(item for item in runtimes if item["node_id"] == objective["node_id"])
    _enforce_firewall_policy(scenario, target)
    if target["device_type"] == "plc-s7":
        snapshot = read_plc_values(manager, scenario.id, target)
    elif target["device_type"] == "modbus-tcp":
        snapshot = read_modbus_values(manager, scenario.id, target)
    else:
        raise ValueError("Point-value objectives support S7 and Modbus TCP only")
    found = next((item for item in snapshot["values"] if item["address"] == objective["address"]), None)
    if found is None:
        raise LookupError(f"Objective point '{objective['address']}' is unavailable")
    actual, expected, operator = found["value"], objective["value"], objective["operator"]
    quality = _point_quality(found)
    condition_met = quality == "good" and _compare(actual, operator, expected)
    duration = objective.get("duration_seconds", 0)
    condition_since = (tracking or {}).get(objective["objective_id"])
    sustained = max(0.0, (_aware(now) - _aware(datetime.fromisoformat(condition_since))).total_seconds()) if condition_since else 0.0
    waiting = condition_met and duration > 0 and sustained < duration
    passed = condition_met and not waiting
    return {**base, "kind": "point_value", "node_id": objective["node_id"],
            "address": objective["address"], "operator": operator, "expected": expected,
            "actual": actual, "quality": quality, "duration_seconds": duration,
            "sustained_seconds": round(sustained, 3), "waiting_duration": waiting,
            "passed": passed}


def _sample_timed_objectives(session: Session, manager: Any, scenario: ScenarioRecord,
                             definition: ExerciseDefinition, run: ExerciseRun,
                             current: datetime) -> None:
    trends = [item for item in definition.objectives if item.kind == "field_trend"]
    sequences = [item for item in definition.objectives if item.kind == "field_sequence"]
    timed = [item for item in definition.objectives
             if getattr(item, "duration_seconds", 0) > 0
             and item.kind in {"field_value", "point_value"}]
    if not timed and not trends and not sequences:
        return
    tracking = json.loads(run.objective_tracking_json or "{}")
    updated = deepcopy(tracking)
    field_objects: dict[str, dict[str, Any]] | None = None
    point_values: dict[str, dict[str, Any]] = {}
    if sequences:
        from services.field_operations import field_operation
        snapshot = field_operation(manager, scenario.id)
        field_objects = {item.get("object_id"): item for item in snapshot.get("objects", [])}
    for objective in sequences:
        progress = updated.get(objective.objective_id)
        if not isinstance(progress, dict) or progress.get("kind") != "field_sequence":
            progress = {"kind": "field_sequence", "next_step_index": 0,
                        "matches": [], "last_match_at": None}
        if progress.get("next_step_index", 0) >= len(objective.steps):
            updated[objective.objective_id] = progress
            continue
        now = _aware(current)
        max_gap = objective.max_gap_seconds
        last_match_at = progress.get("last_match_at")
        if last_match_at and max_gap is not None:
            try:
                elapsed = (now - _aware(datetime.fromisoformat(last_match_at))).total_seconds()
            except (TypeError, ValueError):
                elapsed = max_gap + 1
            if elapsed > max_gap:
                progress = {"kind": "field_sequence", "next_step_index": 0,
                            "matches": [], "last_match_at": None}
        step_index = progress.get("next_step_index", 0)
        step = objective.steps[step_index]
        field_object = (field_objects or {}).get(step.object_id)
        if field_object is None or step.property not in field_object:
            raise LookupError(
                f"Field sequence '{objective.objective_id}' step {step_index + 1} is unavailable"
            )
        actual = field_object[step.property]
        if _compare(actual, step.operator, step.value):
            matched_at = now.isoformat()
            progress["matches"].append({
                "step_index": step_index, "object_id": step.object_id,
                "property": step.property, "operator": step.operator,
                "expected": step.value, "actual": actual, "at": matched_at,
            })
            progress["next_step_index"] = step_index + 1
            progress["last_match_at"] = matched_at
        updated[objective.objective_id] = progress
    for objective in trends:
        progress = updated.get(objective.objective_id)
        if not isinstance(progress, dict) or not isinstance(progress.get("samples"), list):
            progress = {"kind": "field_trend", "samples": []}
        samples = progress["samples"]
        now = _aware(current)
        last_at = None
        if samples:
            try:
                last_at = _aware(datetime.fromisoformat(samples[-1]["at"]))
            except (KeyError, TypeError, ValueError):
                samples.clear()
        # The scheduler ticks at 250 ms; a 900 ms threshold avoids losing
        # nominal 1 s samples to clock rounding and ordinary loop jitter.
        if last_at is None or (now - last_at).total_seconds() >= 0.9:
            if field_objects is None:
                from services.field_operations import field_operation
                snapshot = field_operation(manager, scenario.id)
                field_objects = {item.get("object_id"): item for item in snapshot.get("objects", [])}
            field_object = field_objects.get(objective.object_id)
            if field_object is None or objective.property not in field_object:
                raise LookupError(f"Field trend '{objective.object_id}.{objective.property}' is unavailable")
            value = field_object[objective.property]
            if type(value) in {int, float} and math.isfinite(value):
                samples.append({"at": now.isoformat(), "value": float(value)})
                cutoff = now - timedelta(seconds=objective.window_seconds + 1)
                progress["samples"] = [sample for sample in samples
                                        if _sample_timestamp(sample) is not None
                                        and _sample_timestamp(sample) >= cutoff][-302:]
                updated[objective.objective_id] = progress
    for objective in timed:
        if objective.kind == "field_value":
            if field_objects is None:
                from services.field_operations import field_operation

                snapshot = field_operation(manager, scenario.id)
                field_objects = {item.get("object_id"): item for item in snapshot.get("objects", [])}
            field_object = field_objects.get(objective.object_id)
            if field_object is None or objective.property not in field_object:
                raise LookupError(f"Timed field objective '{objective.object_id}.{objective.property}' is unavailable")
            actual = field_object[objective.property]
            condition_met = _compare(actual, objective.operator, objective.value)
        else:
            target = point_values.get(objective.node_id)
            if target is None:
                target = next((item for item in scenario.runtime_devices()
                               if item["node_id"] == objective.node_id), None)
                if target is None:
                    raise LookupError(f"Timed point target '{objective.node_id}' is unavailable")
                _enforce_firewall_policy(scenario, target)
                if target["device_type"] == "plc-s7":
                    snapshot = read_plc_values(manager, scenario.id, target)
                elif target["device_type"] == "modbus-tcp":
                    snapshot = read_modbus_values(manager, scenario.id, target)
                else:
                    raise ValueError("Point-value objectives support S7 and Modbus TCP only")
                target = {point["address"]: point for point in snapshot["values"]}
                point_values[objective.node_id] = target
            if objective.address not in target:
                raise LookupError(f"Timed objective point '{objective.address}' is unavailable")
            point = target[objective.address]
            actual = point["value"]
            condition_met = _point_quality(point) == "good" and _compare(actual, objective.operator, objective.value)
        if condition_met and not updated.get(objective.objective_id):
            updated[objective.objective_id] = _aware(current).isoformat()
        elif not condition_met and updated.get(objective.objective_id):
            updated[objective.objective_id] = None
    if updated != tracking:
        run.objective_tracking_json = json.dumps(updated, ensure_ascii=False, separators=(",", ":"))
        session.add(run)
        session.commit()


def _sample_timestamp(sample: dict[str, Any]) -> datetime | None:
    try:
        return _aware(datetime.fromisoformat(sample["at"]))
    except (KeyError, TypeError, ValueError):
        return None


def _validate_write(target: dict[str, Any], data_type: str, value: Any, address: str) -> None:
    if target["device_type"] == "plc-s7":
        _validate_s7_write_value(data_type, value)
        component = next((item for item in target["config"]["components"]
                          if _component_address(item) == address and item["type"] in {"valve", "motor", "pump_command", "io_channel"}), None)
        if component is None or component["type"] == "io_channel" and (component["direction"] != "output" or component["data_type"] != data_type or component.get("area", "DB") == "I"):
            raise PermissionError("Exercise writes must target an S7 output")
        _validate_component_range(component, value)
        return
    if target["device_type"] == "modbus-tcp":
        if data_type == "BOOL" and type(value) is not bool:
            raise ValueError("Modbus coils require boolean values")
        if data_type == "UINT16" and (type(value) is not int or not 0 <= value <= 65535):
            raise ValueError("Modbus holding registers require integers from 0 to 65535")
        if not address.startswith(("coil:", "holding_register:")):
            raise PermissionError("Exercise writes must target a coil or holding register")
        return
    raise ValueError("Exercise writes support S7 and Modbus TCP only")


def _compare(actual: Any, operator: str, expected: Any) -> bool:
    try:
        if operator == "eq": return actual == expected
        if operator == "ne": return actual != expected
        if operator == "gt": return actual > expected
        if operator == "gte": return actual >= expected
        if operator == "lt": return actual < expected
        if operator == "lte": return actual <= expected
    except TypeError:
        return False
    return False


def _point_quality(point: dict[str, Any]) -> str:
    """Treat unavailable remote-input communication as unusable objective data."""
    communication = point.get("communication")
    if communication is None:
        return "good"
    if not isinstance(communication, dict):
        return "bad"
    quality = communication.get("quality", "unknown")
    return quality if quality == "good" else str(quality)


def _score_outcomes(definition: ExerciseDefinition, outcomes: list[dict[str, Any]]) -> tuple[bool, int]:
    """Return pass/fail and a weighted 0–100 score for the configured success rule."""
    if not outcomes:
        return True, 100
    weights = {item.objective_id: item.weight for item in definition.objectives}
    total_weight = sum(weights[item["objective_id"]] for item in outcomes)
    earned_weight = sum(weights[item["objective_id"]] for item in outcomes if item["passed"])
    score = round(100 * earned_weight / total_weight) if total_weight else 100
    if definition.success_rule == "any":
        return any(item["passed"] for item in outcomes), score
    if definition.success_rule == "score":
        return score >= definition.passing_score, score
    return all(item["passed"] for item in outcomes), score


def _append_json(payload: str, item: dict[str, Any]) -> str:
    return json.dumps([*json.loads(payload or "[]"), item], ensure_ascii=False, separators=(",", ":"))


def _clear_active_modbus_interceptions(
    session: Session, scenario_id: str, run: ExerciseRun, *, manager: Any | None, strict: bool,
) -> None:
    active: dict[str, bool] = {}
    try:
        entries = json.loads(run.event_results_json or "[]")
    except (TypeError, ValueError):
        entries = []
    for entry in entries:
        if (not isinstance(entry, dict) or entry.get("kind") != "modbus_interception"
                or entry.get("status") != "applied"):
            continue
        result = entry.get("result") or {}
        route_id = result.get("route_id")
        action = result.get("action")
        if isinstance(route_id, str) and action in {"set", "clear"}:
            active[route_id] = action == "set"
    for route_id, is_active in active.items():
        if not is_active:
            continue
        if manager is None:
            if strict:
                raise ValueError("Docker manager is required to clear active Modbus interception rules")
            continue
        try:
            manager.set_modbus_route_interception(scenario_id, f"__anvil_route_{route_id}", None)
        except Exception as error:
            if strict:
                raise
            record_audit_event(session, scenario_id, "security.modbus_interception_cleanup_failed", {
                "exercise_id": run.id, "route_id": route_id, "error": str(error)[:240],
            }, actor="exercise-engine")
            continue
        run.event_results_json = _append_json(run.event_results_json, {
            "event_id": f"cleanup-{route_id}", "kind": "modbus_interception", "status": "applied",
            "result": {"route_id": route_id, "action": "clear", "active_rules": 0},
        })
        session.add(run)
        record_audit_event(session, scenario_id, "security.modbus_interception", {
            "source": "exercise-cleanup", "exercise_id": run.id,
            "route_id": route_id, "action": "clear", "active_rules": 0,
        }, actor="exercise-engine")


def _finish(run: ExerciseRun, status: str, at: datetime) -> None:
    run.status = status
    run.finished_at = at
    run.paused_at = None


def _aware(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _validate_definition(scenario_id: str, scenario: ScenarioRecord, definition: ExerciseDefinition) -> None:
    runtimes = scenario.runtime_devices()
    by_node = {device["node_id"]: device for device in runtimes}
    points = {(point.node_id, point.address): point for point in build_scenario_io_map(scenario_id, runtimes).points}
    field_objects = {item.object_id: item for item in scenario.to_read_model().field_objects}
    interception_state: dict[str, int | None] = {}
    for item in definition.events:
        if item.kind == "modbus_interception":
            configured_routes = json.loads(scenario.network_routes_json or "[]")
            route = next((route for route in configured_routes
                          if route.get("route_id") == item.route_id and route.get("enabled", True)), None)
            if route is None or route.get("protocol") != "tcp":
                raise ValueError(f"Event '{item.event_id}' requires an enabled generic TCP route")
            source = by_node.get(route.get("source_node_id"))
            target = by_node.get(route.get("target_node_id"))
            if (source is None or source.get("device_type") != "attacker-station"
                    or not source["config"].get("participant_access_enabled")):
                raise ValueError(f"Event '{item.event_id}' route source must be an enabled participant station")
            if target is None or target.get("device_type") != "modbus-tcp":
                raise ValueError(f"Event '{item.event_id}' route must target a Modbus TCP device")
            target_config = target["config"]
            target_port = int(target_config.get("port", 502))
            if (target_config.get("transport", "tcp") != "tcp"
                    or int(route.get("tcp_port", 0)) != target_port):
                raise ValueError(f"Event '{item.event_id}' route must target the Modbus TCP service port")
            if target.get("zone_id", "default") == source.get("zone_id", "default"):
                raise ValueError(f"Event '{item.event_id}' interception route must cross network zones")
            gateway_id = f"__anvil_route_{item.route_id}"
            gateway = by_node.get(gateway_id)
            if gateway is None or gateway["config"].get("inspect_protocol") != "modbus-tcp":
                raise ValueError(f"Event '{item.event_id}' route does not inspect Modbus TCP responses")
            configured_points = {(point["type"], point["address"]): point
                                 for point in target_config.get("points", [])}
            for rule in item.rules:
                expected_type = ({1: "coil", 2: "discrete_input", 3: "holding_register", 4: "input_register", 5: "coil",
                                  6: "holding_register", 15: "coil", 16: "holding_register",
                                  22: "holding_register", 23: "holding_register"}[rule.function_code])
                if rule.unit_id != int(target_config.get("unit_id", 1)):
                    raise ValueError(f"Event '{item.event_id}' rule Unit ID does not match the Modbus device")
                if (expected_type, rule.address) not in configured_points:
                    raise ValueError(f"Event '{item.event_id}' rule targets an unknown Modbus point")
            active_at = interception_state.get(item.route_id)
            if item.action == "set" and active_at is not None:
                raise ValueError(f"Event '{item.event_id}' replaces an active interception without clearing it")
            if item.action == "clear" and active_at is None:
                raise ValueError(f"Event '{item.event_id}' clears an inactive interception")
            if item.action == "clear" and item.at_seconds <= active_at:
                raise ValueError(f"Event '{item.event_id}' clear must occur after its set event")
            interception_state[item.route_id] = item.at_seconds if item.action == "set" else None
            continue
        if item.kind == "protocol_write":
            source = by_node.get(item.source_node_id)
            target = by_node.get(item.target_node_id)
            if source is None or source["device_type"] != "attacker-station":
                raise ValueError(f"Event '{item.event_id}' source must be a participant station")
            if not source["config"].get("participant_access_enabled"):
                raise ValueError(f"Event '{item.event_id}' participant station network access is disabled")
            if target is None or target["device_type"] not in {"plc-s7", "modbus-tcp"}:
                raise ValueError(f"Event '{item.event_id}' target must be an S7 PLC or Modbus TCP device")
            point = points.get((item.target_node_id, item.address))
            if point is None:
                raise ValueError(f"Event '{item.event_id}' targets an unknown I/O point")
            _validate_write(target, point.data_type, item.value, item.address)
            configured_routes = json.loads(scenario.network_routes_json or "[]")
            routes = [route for route in configured_routes
                      if route.get("route_id") == item.route_id and route.get("enabled", True)]
            source_zone, target_zone = source.get("zone_id", "default"), target.get("zone_id", "default")
            if source_zone != target_zone:
                if len(routes) != 1:
                    raise ValueError(f"Event '{item.event_id}' needs an enabled route from its station to the target")
                route = routes[0]
                expected_port = 102 if target["device_type"] == "plc-s7" else int(target["config"].get("port", 502))
                if (route.get("protocol") != "tcp" or route.get("source_node_id") != source["node_id"]
                        or route.get("target_node_id") != target["node_id"]
                        or int(route.get("tcp_port", 0)) != expected_port):
                    raise ValueError(f"Event '{item.event_id}' route must be the station's TCP route to the target protocol port")
                endpoint = source["config"].get("tcp_routes", {}).get(item.route_id)
                if not endpoint or endpoint.get("target_node_id") != target["node_id"]:
                    raise ValueError(f"Event '{item.event_id}' route is not present in the deployed runtime plan")
            elif item.route_id:
                raise ValueError(f"Event '{item.event_id}' must omit route_id for devices in the same zone")
            continue
        if item.kind == "network_route":
            configured_routes = {route.get("route_id") for route in json.loads(scenario.network_routes_json or "[]")
                                 if route.get("enabled", True)}
            if item.route_id not in configured_routes:
                raise ValueError(f"Event '{item.event_id}' targets an unknown or disabled network route")
            route_node_id = f"__anvil_route_{item.route_id}"
            if route_node_id not in by_node or by_node[route_node_id].get("device_type") != "tcp-gateway":
                raise ValueError(f"Event '{item.event_id}' targets a route without a deployed gateway")
            continue
        if item.kind == "node_lifecycle":
            if item.node_id not in by_node:
                raise ValueError(f"Event '{item.event_id}' targets an unknown device")
            continue
        if item.kind == "field_control":
            target = field_objects.get(item.object_id)
            if target is None:
                raise ValueError(f"Event '{item.event_id}' targets an unknown field object")
            controls = FieldControl.model_validate(item.model_dump(
                exclude={"event_id", "at_seconds", "kind", "object_id"}, exclude_unset=True
            ))
            if "fault" in controls.model_fields_set and type(controls.fault) is not bool:
                raise ValueError(f"Event '{item.event_id}' requires a boolean fault state")
            if controls.fault is not None and target.type not in {
                    "valve", "proportional_valve", "motor", "pump", "analog_sensor", "digital_input"}:
                raise ValueError(f"Event '{item.event_id}' can only fault an actuator or process sensor")
            if "override" in controls.model_fields_set:
                value = controls.override
                if target.type == "analog_sensor":
                    if value is not None and (type(value) not in (int, float) or isinstance(value, bool)
                                              or not target.min_value <= value <= target.max_value):
                        raise ValueError(f"Event '{item.event_id}' has an override outside the sensor range")
                elif target.type in {"digital_input", "alarm"}:
                    if value is not None and type(value) is not bool:
                        raise ValueError(f"Event '{item.event_id}' requires a boolean override")
                else:
                    raise ValueError(f"Event '{item.event_id}' can only override an input sensor")
            if "demand_flow_lpm" in controls.model_fields_set:
                if controls.demand_flow_lpm is None:
                    raise ValueError(f"Event '{item.event_id}' requires a numeric tank demand")
                if target.type != "tank":
                    raise ValueError(f"Event '{item.event_id}' can only change demand on a tank")
            if "leak_flow_lpm" in controls.model_fields_set:
                if controls.leak_flow_lpm is None:
                    raise ValueError(f"Event '{item.event_id}' requires a numeric tank leak")
                if target.type != "tank":
                    raise ValueError(f"Event '{item.event_id}' can only change leakage on a tank")
            continue
        target = by_node.get(item.node_id)
        point = points.get((item.node_id, item.address))
        if target is None or point is None:
            raise ValueError(f"Event '{item.event_id}' targets an unknown I/O point")
        if point.access != "read_write":
            raise ValueError(f"Event '{item.event_id}' targets a read-only point")
        _validate_write(target, point.data_type, item.value, item.address)
    if any(at_seconds is not None for at_seconds in interception_state.values()):
        raise ValueError("Every Modbus interception set event must have a later clear event")
    for item in definition.objectives:
        if item.kind == "protocol_write_policy":
            target = by_node.get(item.target_node_id)
            expected_type = "plc-s7" if item.protocol == "s7" else "modbus-tcp"
            if target is None or target.get("device_type") != expected_type:
                raise ValueError(
                    f"Objective '{item.objective_id}' target must be a {expected_type} device"
                )
            if item.protocol == "modbus-tcp" and target.get("config", {}).get("transport", "tcp") != "tcp":
                raise ValueError(
                    f"Objective '{item.objective_id}' requires a Modbus TCP transport"
                )
            continue
        if item.kind == "field_sequence":
            field_objects = {obj.object_id: obj for obj in scenario.to_read_model().field_objects}
            properties = {
                "tank": {"value", "pressure_kpa", "volume_liters", "inflow_lpm", "outflow_lpm"},
                "analog_sensor": {"value", "fault"},
                "digital_input": {"state", "fault"}, "alarm": {"state"},
                "valve": {"state", "flow", "fault"}, "motor": {"state", "flow", "fault"},
                "pump": {"state", "flow", "fault"},
                "proportional_valve": {"opening_pct", "flow", "fault"},
                "variable_speed_drive": {"speed_pct", "flow", "fault"},
            }
            for index, step in enumerate(item.steps, 1):
                target = field_objects.get(step.object_id)
                if target is None:
                    raise ValueError(
                        f"Objective '{item.objective_id}' step {index} targets an unknown field object"
                    )
                if step.property not in properties.get(target.type, set()):
                    raise ValueError(
                        f"Objective '{item.objective_id}' step {index} cannot inspect "
                        f"'{step.property}' on {target.type}"
                    )
            continue
        if item.kind in {"field_value", "field_trend"}:
            field_objects = {obj.object_id: obj for obj in scenario.to_read_model().field_objects}
            target = field_objects.get(item.object_id)
            if target is None:
                raise ValueError(f"Objective '{item.objective_id}' targets an unknown field object")
            properties = {
                "tank": {"value", "pressure_kpa", "volume_liters", "inflow_lpm", "outflow_lpm"},
                "analog_sensor": {"value", "fault"},
                "digital_input": {"state", "fault"}, "alarm": {"state"},
                "valve": {"state", "flow", "fault"}, "motor": {"state", "flow", "fault"},
                "pump": {"state", "flow", "fault"},
                "proportional_valve": {"opening_pct", "flow", "fault"},
                "variable_speed_drive": {"speed_pct", "flow", "fault"},
            }
            if item.property not in properties.get(target.type, set()):
                raise ValueError(
                    f"Objective '{item.objective_id}' cannot inspect '{item.property}' on {target.type}"
                )
            if item.kind == "field_trend":
                if item.property == "state":
                    raise ValueError(f"Objective '{item.objective_id}' requires a numeric field property")
                continue
            if item.property in {"state", "fault"}:
                if type(item.value) is not bool or item.operator not in {"eq", "ne"}:
                    raise ValueError(f"Objective '{item.objective_id}' requires a boolean eq/ne state or fault condition")
            elif type(item.value) not in (int, float) or isinstance(item.value, bool):
                raise ValueError(f"Objective '{item.objective_id}' requires a numeric condition")
            continue
        if item.kind != "point_value":
            continue
        point = points.get((item.node_id, item.address))
        if point is None:
            raise ValueError(f"Objective '{item.objective_id}' targets an unknown I/O point")
        if point.data_type == "BOOL" and type(item.value) is not bool:
            raise ValueError(f"Objective '{item.objective_id}' must compare a BOOL point with a boolean")
        if point.data_type != "BOOL" and (type(item.value) not in (int, float) or isinstance(item.value, bool)):
            raise ValueError(f"Objective '{item.objective_id}' must compare a numeric point with a number")
        if point.data_type == "BOOL" and item.operator not in {"eq", "ne"}:
            raise ValueError(f"Objective '{item.objective_id}' supports only eq/ne for BOOL points")
