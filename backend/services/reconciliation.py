"""Reconcile persisted scenario state with Docker runtime state."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlmodel import Session, select

from devices import DEVICE_REGISTRY
from models.audit import ScenarioAuditEvent
from models.scenario import ScenarioNodeOperation, ScenarioRecord
from services.audit import record_audit_event, record_node_runtime_transitions
from services.docker_manager import DockerManager, DockerManagerError
from services.runtime_status import include_missing_runtime_nodes
from services.scenario_leases import (
    acquire_operation_lease,
    make_operation_owner_id,
    operation_lease_active,
    release_operation_lease,
)
from services.node_operation_journal import (
    mark_node_operation_retryable,
    mark_node_operation_terminal,
    pending_node_operations,
)
from services.protocol_trace_collector import flush_protocol_traces_best_effort


def _recover_deploying_record(session: Session, manager: DockerManager, record: ScenarioRecord) -> str:
    """Return active, recovered, or failed; never clean a live lifecycle operation."""
    engine = session.get_bind()
    if operation_lease_active(engine, record.id):
        return "active"
    owner_id = make_operation_owner_id()
    if not acquire_operation_lease(engine, record.id, "reconcile", owner_id):
        return "active"
    try:
        flush_protocol_traces_best_effort(engine, manager=manager, scenario_id=record.id)
        manager.stop(record.id)
    except DockerManagerError:
        # Keep the deployment recoverable; a later scheduler tick retries after
        # Docker becomes available or the remaining resource is released.
        return "failed"
    finally:
        release_operation_lease(engine, record.id, "reconcile", owner_id)
    record.status = "stopped"
    session.add(record)
    session.commit()
    return "recovered"


def _has_active_runtime_intent(session: Session, manager: DockerManager, record: ScenarioRecord) -> bool:
    if record.status in {"running", "partial"}:
        return True
    if record.status != "error":
        return False
    if manager.has_managed_scenario_network(record.id):
        return True
    # The status API may have persisted `error` after observing total runtime
    # loss. Preserve that prior active intent, while a failed deploy (deploying
    # -> error) remains stopped and is never automatically relaunched.
    transition = session.exec(
        select(ScenarioAuditEvent)
        .where(ScenarioAuditEvent.scenario_id == record.id,
               ScenarioAuditEvent.event_type == "scenario.status_changed")
        .order_by(ScenarioAuditEvent.id.desc())
        .limit(1)
    ).first()
    if transition is None:
        return False
    try:
        details = json.loads(transition.details_json or "{}")
    except (TypeError, json.JSONDecodeError):
        return False
    return details.get("previous") in {"running", "partial"}


def recover_pending_node_operations(engine, manager: DockerManager, *, limit: int = 100) -> dict[str, Any]:
    """Replay durable node-operation intents after the originating worker exits."""
    recovered = 0
    failed = 0
    errors: list[str] = []
    for pending in pending_node_operations(engine, limit=limit):
        owner_id = make_operation_owner_id()
        if not acquire_operation_lease(engine, pending.scenario_id, "node-recovery", owner_id):
            continue
        try:
            with Session(engine) as session:
                operation = session.get(ScenarioNodeOperation, pending.id)
                scenario = session.get(ScenarioRecord, pending.scenario_id)
                if operation is None or operation.status != "pending":
                    continue
                if scenario is None:
                    operation.status = "failed"
                    operation.updated_at = datetime.now(timezone.utc)
                    operation.last_error = "Scenario no longer exists"
                    session.add(operation)
                    session.commit()
                    failed += 1
                    continue
                if scenario.status == "stopped":
                    operation.status = "cancelled"
                    operation.updated_at = datetime.now(timezone.utc)
                    operation.last_error = "Scenario was stopped before the pending node operation could be recovered"
                    session.add(operation)
                    session.commit()
                    continue
                runtime_device = next(
                    (item for item in scenario.runtime_devices() if item.get("node_id") == operation.node_id), None
                )
                if runtime_device is None:
                    operation.status = "failed"
                    operation.updated_at = datetime.now(timezone.utc)
                    operation.last_error = "Node is absent from the persisted runtime plan"
                    session.add(operation)
                    session.commit()
                    failed += 1
                    errors.append(f"{operation.id}: node is absent from the persisted runtime plan")
                    continue
                operation_id = operation.id
                scenario_id = scenario.id
                persisted_scenario_status = scenario.status
                planned_devices = scenario.runtime_devices()
                node_id = operation.node_id
                action = operation.action
                source = operation.source
                precondition = json.loads(operation.precondition_json or "{}")
            try:
                if action in {"start", "stop", "restart", "recreate"}:
                    flush_protocol_traces_best_effort(
                        engine, manager=manager, scenario_id=scenario_id, target_node_id=node_id,
                    )
                result = manager.recover_node_operation(
                    scenario_id, runtime_device, action, precondition=precondition,
                )
            except DockerManagerError as error:
                message = str(error)
                if "Expected one managed container" in message or "Stored node operation is invalid" in message:
                    mark_node_operation_terminal(engine, operation_id, "failed", message)
                else:
                    mark_node_operation_retryable(engine, operation_id, message)
                failed += 1
                errors.append(f"{operation_id}: {error}")
                continue
            if action in {"start", "stop", "restart", "recreate"}:
                flush_protocol_traces_best_effort(
                    engine, manager=manager, scenario_id=scenario_id, target_node_id=node_id,
                )
            result = include_missing_runtime_nodes(
                result, planned_devices, persisted_scenario_status,
            )
            with Session(engine) as session:
                operation = session.get(ScenarioNodeOperation, operation_id)
                scenario = session.get(ScenarioRecord, scenario_id)
                if operation is None or scenario is None or operation.status != "pending":
                    continue
                operation.status = "completed"
                operation.updated_at = datetime.now(timezone.utc)
                operation.attempts += 1
                operation.result_json = json.dumps(
                    jsonable_encoder(result), ensure_ascii=False, separators=(",", ":"),
                )
                operation.last_error = None
                scenario.status = result["status"]
                session.add(operation)
                session.add(scenario)
                record_audit_event(session, scenario_id, "scenario.node_operation_recovered", {
                    "operation_id": operation_id, "node_id": node_id,
                    "action": action, "source": source,
                }, actor="runtime-reconciler", commit=False)
                session.commit()
                record_node_runtime_transitions(session, scenario_id, result.get("containers", []), result["status"])
            recovered += 1
        finally:
            release_operation_lease(engine, pending.scenario_id, "node-recovery", owner_id)
    return {"recovered": recovered, "failed": failed, "errors": errors}


def reconcile_runtime(
    session: Session,
    manager: DockerManager,
    *,
    cleanup_orphans: bool = False,
) -> dict[str, Any]:
    node_recovery = recover_pending_node_operations(session.get_bind(), manager)
    node_recovery_errors = list(node_recovery["errors"])
    records = session.exec(select(ScenarioRecord)).all()
    known_ids = {record.id for record in records}
    removed = {
        "containers": 0,
        "networks": 0,
    }
    errors: list[str] = []
    errors.extend(node_recovery_errors)
    if cleanup_orphans:
        try:
            cleanup_result = manager.cleanup_orphans(known_ids)
            removed = {
                "containers": cleanup_result.get("containers", 0),
                "networks": cleanup_result.get("networks", 0),
            }
            errors.extend(cleanup_result.get("errors", []))
        except DockerManagerError as error:
            errors.append(str(error))
    updated = 0
    recovered = 0
    recovery_errors = 0
    networks_restored = 0

    for record in records:
        if record.status == "deploying":
            recovery = _recover_deploying_record(session, manager, record)
            if recovery == "active":
                continue
            if recovery == "recovered":
                recovered += 1
            else:
                recovery_errors += 1
                errors.append(f"{record.id}: deployment resources could not be fully reconciled")
                continue
        owner_id = make_operation_owner_id()
        if not acquire_operation_lease(session.get_bind(), record.id, "runtime-reconcile", owner_id):
            continue
        try:
            try:
                planned_devices = record.runtime_devices()
                active_intent = _has_active_runtime_intent(session, manager, record)
                restore_networks = getattr(manager, "ensure_managed_scenario_networks", None)
                if active_intent and callable(restore_networks):
                    network_config = json.loads(record.network_json or "{}")
                    restored_zones = restore_networks(
                        record.id, network_config.get("subnet", "172.30.0.0/24"), planned_devices,
                    )
                    if restored_zones:
                        networks_restored += len(restored_zones)
                        record_audit_event(session, record.id, "scenario.networks_recovered", {
                            "zone_ids": sorted(restored_zones),
                        }, actor="runtime-reconciler", commit=False)
                runtime = manager.status(record.id)
                runtime = include_missing_runtime_nodes(
                    runtime, planned_devices, record.status, reconcile_deploying=True
                )
            except DockerManagerError as error:
                errors.append(f"{record.id}: {error}")
                continue

            missing_containers = {
                item.get("node_id") for item in runtime.get("containers", [])
                if item.get("status") == "missing" and item.get("runtime_kind") != "network"
            }
            # The status endpoint can persist `error` after total runtime loss.
            # Preserve the previous active transition, but never relaunch a
            # deployment whose last transition was `deploying` -> `error`.
            active_intent = _has_active_runtime_intent(session, manager, record)
            if active_intent and runtime["status"] in {"partial", "error"}:
                for device in planned_devices:
                    node_id = device.get("node_id")
                    if node_id not in missing_containers:
                        continue
                    adapter = DEVICE_REGISTRY.get(device.get("device_type"))
                    if adapter is not None and getattr(adapter, "network_resource", False):
                        continue
                    pending = session.exec(select(ScenarioNodeOperation).where(
                        ScenarioNodeOperation.scenario_id == record.id,
                        ScenarioNodeOperation.node_id == node_id,
                        ScenarioNodeOperation.action == "recreate",
                        ScenarioNodeOperation.source == "runtime-reconciler",
                        ScenarioNodeOperation.status == "pending",
                    )).first()
                    if pending is not None:
                        continue
                    operation = ScenarioNodeOperation(
                        scenario_id=record.id, node_id=node_id, action="recreate",
                        source="runtime-reconciler",
                    )
                    session.add(operation)
                    record_audit_event(session, record.id, "scenario.node_recovery_scheduled", {
                        "operation_id": operation.id, "node_id": node_id,
                    }, actor="runtime-reconciler", commit=False)

            if record.status != runtime["status"]:
                record.status = runtime["status"]
                session.add(record)
                updated += 1
            record_node_runtime_transitions(session, record.id, runtime.get("containers", []), runtime["status"])
            session.commit()
        finally:
            release_operation_lease(session.get_bind(), record.id, "runtime-reconcile", owner_id)

    if updated:
        session.commit()

    return {
        "scenarios": len(records),
        "updated": updated,
        "removed": removed,
        "deployments_recovered": recovered,
        "deployment_recovery_errors": recovery_errors,
        "node_operations_recovered": node_recovery["recovered"],
        "node_operation_recovery_errors": node_recovery["failed"],
        "networks_restored": networks_restored,
        "errors": errors,
    }


async def deployment_recovery_scheduler(stop_event: asyncio.Event, engine,
                                        *, interval_seconds: float = 10.0) -> None:
    """Clean only deployments whose shared operation lease has expired."""
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            continue
        except TimeoutError:
            pass
        try:
            await asyncio.to_thread(_recover_expired_deployments, engine)
        except Exception:
            # A later interval retries when SQLite or Docker becomes available.
            continue


def _recover_expired_deployments(engine) -> None:
    try:
        manager = DockerManager()
    except DockerManagerError:
        return
    with Session(engine) as session:
        result = reconcile_runtime(session, manager)
    if result["errors"]:
        import logging
        logging.getLogger("anvil.reconciliation").warning(
            "Runtime resources remain recoverable: %s", result
        )
