from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel import select


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from models.audit import ScenarioAuditEvent  # noqa: F401,E402
from models.auth import PlatformUser  # noqa: F401,E402
from models.scenario import ScenarioNodeOperation, ScenarioRecord  # noqa: E402
from services.reconciliation import reconcile_runtime, recover_pending_node_operations  # noqa: E402
from services.scenario_leases import acquire_operation_lease  # noqa: E402


class FakeRuntimeManager:
    def __init__(self) -> None:
        self.known_ids: set[str] | None = None
        self.stopped: list[str] = []

    def stop(self, scenario_id: str) -> dict:
        self.stopped.append(scenario_id)
        return {"scenario_id": scenario_id, "status": "stopped", "containers": []}

    def has_managed_scenario_network(self, scenario_id: str) -> bool:
        del scenario_id
        return True

    def cleanup_orphans(self, known_ids: set[str]) -> dict[str, int]:
        self.known_ids = known_ids
        return {"containers": 1, "networks": 1}

    def status(self, scenario_id: str) -> dict:
        return {"scenario_id": scenario_id, "status": "stopped", "containers": []}


class FailingCleanupRuntimeManager(FakeRuntimeManager):
    def cleanup_orphans(self, known_ids: set[str]) -> dict:
        self.known_ids = known_ids
        return {
            "containers": 1,
            "networks": 0,
            "errors": ["network 'orphan-network': still in use"],
        }


class FailingStopRuntimeManager(FakeRuntimeManager):
    def stop(self, scenario_id: str) -> dict:
        self.stopped.append(scenario_id)
        from services.docker_manager import DockerManagerError
        raise DockerManagerError("daemon temporarily unavailable")


class MissingNodeRuntimeManager(FakeRuntimeManager):
    def status(self, scenario_id: str) -> dict:
        if scenario_id != "scenario-empty":
            return {"scenario_id": scenario_id, "status": "running", "containers": [
                {"node_id": "plc-2", "name": "PLC 2", "status": "running"},
            ]}
        return {"scenario_id": scenario_id, "status": "stopped", "containers": []}


class NetworkRecoveryRuntimeManager(FakeRuntimeManager):
    def __init__(self) -> None:
        super().__init__()
        self.restored: list[tuple[str, str, list[dict]]] = []

    def ensure_managed_scenario_networks(self, scenario_id: str, subnet: str, runtime_devices: list[dict]) -> list[str]:
        self.restored.append((scenario_id, subnet, runtime_devices))
        return ["control"]

    def status(self, scenario_id: str) -> dict:
        return {"scenario_id": scenario_id, "status": "running", "containers": [
            {"node_id": "plc-1", "name": "PLC 1", "status": "running"},
        ]}

    def has_managed_scenario_network(self, scenario_id: str) -> bool:
        del scenario_id
        return False


class RecoverableNodeRuntimeManager(FakeRuntimeManager):
    def __init__(self) -> None:
        super().__init__()
        self.fail = True
        self.recovered: list[tuple[str, str, str]] = []

    def recover_node_operation(self, scenario_id: str, runtime_device: dict, action: str,
                               precondition: dict | None = None) -> dict:
        del precondition
        self.recovered.append((scenario_id, runtime_device["node_id"], action))
        if self.fail:
            from services.docker_manager import DockerManagerError
            raise DockerManagerError("daemon temporarily unavailable")
        return {"scenario_id": scenario_id, "status": "running", "containers": [
            {"node_id": runtime_device["node_id"], "name": "PLC 1", "status": "running",
             "resource_metrics_sampled_at": datetime.now(timezone.utc)},
        ]}


class ReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_runtime_state_and_orphans_are_reconciled(self) -> None:
        record = ScenarioRecord(
            id="scenario-1",
            name="reconcile",
            network_json='{"subnet": "172.30.50.0/24"}',
            devices_json="[]",
            runtime_devices_json="[]",
            status="running",
        )
        manager = FakeRuntimeManager()

        with Session(self.engine) as session:
            session.add(record)
            session.commit()
            result = reconcile_runtime(session, manager, cleanup_orphans=True)
            session.expire_all()
            reconciled = session.get(ScenarioRecord, "scenario-1")

        self.assertIsNotNone(reconciled)
        self.assertEqual(reconciled.status, "stopped")
        self.assertEqual(manager.known_ids, {"scenario-1"})
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["removed"], {"containers": 1, "networks": 1})

    def test_active_scenario_rebuilds_missing_network_and_audits_it(self) -> None:
        record = ScenarioRecord(
            id="scenario-network-recovery", name="network recovery",
            network_json='{"subnet":"172.30.49.0/24"}', devices_json="[]",
            runtime_devices_json=json.dumps([{"node_id": "plc-1", "device_type": "plc-s7"}]),
            status="running",
        )
        manager = NetworkRecoveryRuntimeManager()
        scenario_id = record.id
        with Session(self.engine) as session:
            session.add(record)
            session.commit()
            result = reconcile_runtime(session, manager)
            audits = session.exec(select(ScenarioAuditEvent).where(
                ScenarioAuditEvent.scenario_id == scenario_id,
                ScenarioAuditEvent.event_type == "scenario.networks_recovered",
            )).all()

        self.assertEqual(manager.restored, [(scenario_id, "172.30.49.0/24", json.loads(record.runtime_devices_json))])
        self.assertEqual(result["networks_restored"], 1)
        self.assertEqual(len(audits), 1)
        self.assertEqual(json.loads(audits[0].details_json), {"zone_ids": ["control"]})

    def test_active_intent_survives_status_api_error_after_total_network_loss(self) -> None:
        from services.audit import record_audit_event

        record = ScenarioRecord(
            id="scenario-total-network-loss", name="total network loss",
            network_json='{"subnet":"172.30.48.0/24"}', devices_json="[]",
            runtime_devices_json=json.dumps([{"node_id": "plc-1", "device_type": "plc-s7"}]),
            status="error",
        )
        manager = NetworkRecoveryRuntimeManager()
        scenario_id = record.id
        with Session(self.engine) as session:
            session.add(record)
            session.commit()
            record_audit_event(session, scenario_id, "scenario.status_changed", {
                "previous": "running", "status": "error",
            }, commit=True)
            result = reconcile_runtime(session, manager)

        self.assertEqual(result["networks_restored"], 1)
        self.assertEqual(manager.restored[0][0], scenario_id)

    def test_failed_deployment_error_is_not_automatically_relaunched(self) -> None:
        from services.audit import record_audit_event

        record = ScenarioRecord(
            id="scenario-failed-deploy", name="failed deploy",
            network_json='{"subnet":"172.30.47.0/24"}', devices_json="[]",
            runtime_devices_json="[]", status="error",
        )
        manager = NetworkRecoveryRuntimeManager()
        scenario_id = record.id
        with Session(self.engine) as session:
            session.add(record)
            session.commit()
            record_audit_event(session, scenario_id, "scenario.status_changed", {
                "previous": "deploying", "status": "error",
            }, commit=True)
            reconcile_runtime(session, manager)

        self.assertEqual(manager.restored, [])

    def test_cleanup_errors_do_not_skip_persisted_scenario_reconciliation(self) -> None:
        record = ScenarioRecord(
            id="scenario-1",
            name="reconcile",
            network_json='{"subnet": "172.30.50.0/24"}',
            devices_json="[]",
            runtime_devices_json="[]",
            status="running",
        )
        manager = FailingCleanupRuntimeManager()

        with Session(self.engine) as session:
            session.add(record)
            session.commit()
            result = reconcile_runtime(session, manager, cleanup_orphans=True)
            session.expire_all()
            reconciled = session.get(ScenarioRecord, "scenario-1")

        self.assertIsNotNone(reconciled)
        self.assertEqual(reconciled.status, "stopped")
        self.assertEqual(result["removed"], {"containers": 1, "networks": 0})
        self.assertEqual(result["errors"], ["network 'orphan-network': still in use"])

    def test_startup_reconciliation_persists_partial_and_missing_runtime_nodes(self) -> None:
        planned = json.dumps([
            {"node_id": "plc-1", "name": "PLC 1", "device_type": "plc-s7"},
            {"node_id": "plc-2", "name": "PLC 2", "device_type": "plc-s7"},
        ])
        partial = ScenarioRecord(
            id="scenario-partial", name="partial", network_json='{"subnet":"172.30.51.0/24"}',
            devices_json="[]", runtime_devices_json=planned, status="running",
        )
        empty = ScenarioRecord(
            id="scenario-empty", name="empty", network_json='{"subnet":"172.30.52.0/24"}',
            devices_json="[]", runtime_devices_json=planned, status="running",
        )

        with Session(self.engine) as session:
            session.add_all([partial, empty])
            session.commit()
            manager = MissingNodeRuntimeManager()
            result = reconcile_runtime(session, manager)
            session.expire_all()
            partial_after = session.get(ScenarioRecord, "scenario-partial")
            empty_after = session.get(ScenarioRecord, "scenario-empty")
            transitions = session.exec(
                select(ScenarioAuditEvent).where(
                    ScenarioAuditEvent.scenario_id == "scenario-partial",
                    ScenarioAuditEvent.event_type == "scenario.node_state_changed",
                )
            ).all()
            scheduled = session.exec(select(ScenarioNodeOperation).where(
                ScenarioNodeOperation.scenario_id == "scenario-empty",
                ScenarioNodeOperation.source == "runtime-reconciler",
                ScenarioNodeOperation.action == "recreate",
            )).all()

        self.assertEqual(result["updated"], 2)
        self.assertEqual(partial_after.status, "partial")
        self.assertEqual(empty_after.status, "error")
        observed = {event.details_json for event in transitions}
        self.assertTrue(any('"node_id":"plc-1"' in detail and '"status":"missing"' in detail for detail in observed))
        self.assertTrue(any('"node_id":"plc-2"' in detail and '"status":"running"' in detail for detail in observed))
        self.assertEqual(len(transitions), 2)
        self.assertEqual({item.node_id for item in scheduled}, {"plc-1", "plc-2"})
        self.assertTrue(all(item.status == "pending" for item in scheduled))

    def test_missing_container_schedules_durable_recreate_without_touching_running_peer(self) -> None:
        record = ScenarioRecord(
            id="scenario-auto-recovery", name="auto recovery",
            network_json='{"subnet":"172.30.59.0/24"}', devices_json="[]",
            runtime_devices_json=json.dumps([
                {"node_id": "plc-1", "name": "PLC 1", "device_type": "plc-s7"},
                {"node_id": "plc-2", "name": "PLC 2", "device_type": "plc-s7"},
            ]), status="running",
        )
        with Session(self.engine) as session:
            session.add(record)
            session.commit()
            result = reconcile_runtime(session, MissingNodeRuntimeManager())
            operations = session.exec(select(ScenarioNodeOperation).where(
                ScenarioNodeOperation.scenario_id == record.id,
            )).all()
            scheduled = session.exec(select(ScenarioAuditEvent).where(
                ScenarioAuditEvent.scenario_id == record.id,
                ScenarioAuditEvent.event_type == "scenario.node_recovery_scheduled",
            )).all()
            session.refresh(record)

        self.assertEqual(result["updated"], 1)
        self.assertEqual(record.status, "partial")
        self.assertEqual([(item.node_id, item.action, item.status, item.source) for item in operations],
                         [("plc-1", "recreate", "pending", "runtime-reconciler")])
        self.assertEqual(len(scheduled), 1)

        recovery_manager = RecoverableNodeRuntimeManager()
        recovery_manager.fail = False
        recovered = recover_pending_node_operations(self.engine, recovery_manager)
        with Session(self.engine) as session:
            completed = session.exec(select(ScenarioNodeOperation).where(
                ScenarioNodeOperation.scenario_id == "scenario-auto-recovery",
            )).one()
        self.assertEqual(recovered["recovered"], 1)
        self.assertEqual(completed.status, "completed")
        self.assertEqual(recovery_manager.recovered, [
            ("scenario-auto-recovery", "plc-1", "recreate"),
        ])

    def test_missing_container_recovery_intent_is_not_scheduled_during_active_lifecycle(self) -> None:
        record = ScenarioRecord(
            id="scenario-auto-recovery-lease", name="auto recovery lease",
            network_json='{"subnet":"172.30.60.0/24"}', devices_json="[]",
            runtime_devices_json=json.dumps([
                {"node_id": "plc-1", "name": "PLC 1", "device_type": "plc-s7"},
                {"node_id": "plc-2", "name": "PLC 2", "device_type": "plc-s7"},
            ]), status="running",
        )
        with Session(self.engine) as session:
            session.add(record)
            session.commit()
        scenario_id = "scenario-auto-recovery-lease"
        self.assertTrue(acquire_operation_lease(self.engine, scenario_id, "deploy", "active-deploy"))

        with Session(self.engine) as session:
            result = reconcile_runtime(session, MissingNodeRuntimeManager())
            operations = session.exec(select(ScenarioNodeOperation).where(
                ScenarioNodeOperation.scenario_id == scenario_id,
            )).all()

        self.assertEqual(result["updated"], 0)
        self.assertEqual(operations, [])

    def test_error_status_with_fully_missing_runtime_schedules_every_planned_node(self) -> None:
        record = ScenarioRecord(
            id="scenario-empty", name="full loss",
            network_json='{"subnet":"172.30.61.0/24"}', devices_json="[]",
            runtime_devices_json=json.dumps([
                {"node_id": "plc-1", "name": "PLC 1", "device_type": "plc-s7"},
                {"node_id": "plc-2", "name": "PLC 2", "device_type": "plc-s7"},
            ]), status="error",
        )
        with Session(self.engine) as session:
            session.add(record)
            session.commit()
            result = reconcile_runtime(session, MissingNodeRuntimeManager())
            operations = session.exec(select(ScenarioNodeOperation).where(
                ScenarioNodeOperation.scenario_id == record.id,
                ScenarioNodeOperation.source == "runtime-reconciler",
                ScenarioNodeOperation.action == "recreate",
            )).all()

        self.assertEqual(result["updated"], 0)
        self.assertEqual({item.node_id for item in operations}, {"plc-1", "plc-2"})
        self.assertTrue(all(item.status == "pending" for item in operations))

    def test_active_deployment_lease_prevents_startup_from_marking_partial_runtime(self) -> None:
        record = ScenarioRecord(
            id="scenario-live-deploy", name="deploying", network_json='{"subnet":"172.30.53.0/24"}',
            devices_json="[]", runtime_devices_json="[]", status="deploying",
        )
        with Session(self.engine) as session:
            session.add(record)
            session.commit()
            self.assertTrue(acquire_operation_lease(
                self.engine, record.id, "deploy", "worker-a", lease_seconds=3600,
            ))
            manager = FakeRuntimeManager()
            result = reconcile_runtime(session, manager)
            session.refresh(record)
        self.assertEqual(record.status, "deploying")
        self.assertEqual(manager.stopped, [])
        self.assertEqual(result["updated"], 0)
        self.assertEqual(result["deployments_recovered"], 0)

    def test_expired_or_legacy_deploying_record_is_cleaned_and_reconciled(self) -> None:
        record = ScenarioRecord(
            id="scenario-stale-deploy", name="stale", network_json='{"subnet":"172.30.54.0/24"}',
            devices_json="[]", runtime_devices_json="[]", status="deploying",
        )
        with Session(self.engine) as session:
            session.add(record)
            session.commit()
            manager = FakeRuntimeManager()
            result = reconcile_runtime(session, manager)
            session.refresh(record)
        self.assertEqual(record.status, "stopped")
        self.assertEqual(manager.stopped, [record.id])
        self.assertEqual(result["deployments_recovered"], 1)
        self.assertEqual(result["deployment_recovery_errors"], 0)

    def test_failed_deployment_cleanup_flushes_protocol_logs_before_removal(self) -> None:
        record = ScenarioRecord(
            id="scenario-flush-deploy", name="flush", network_json='{"subnet":"172.30.54.0/24"}',
            devices_json="[]", runtime_devices_json=json.dumps([{
                "node_id": "plc-1", "device_type": "plc-s7", "config": {"components": []},
            }]), status="deploying",
        )
        with Session(self.engine) as session:
            session.add(record)
            session.commit()
            manager = FailingStopRuntimeManager()
            with patch("services.reconciliation.flush_protocol_traces_best_effort", return_value=0) as flush:
                result = reconcile_runtime(session, manager)
        self.assertEqual(result["deployment_recovery_errors"], 1)
        flush.assert_called_once_with(self.engine, manager=manager, scenario_id=record.id)

    def test_failed_deployment_cleanup_keeps_state_retryable(self) -> None:
        record = ScenarioRecord(
            id="scenario-retry-deploy", name="retry", network_json='{"subnet":"172.30.55.0/24"}',
            devices_json="[]", runtime_devices_json="[]", status="deploying",
        )
        with Session(self.engine) as session:
            session.add(record)
            session.commit()
            result = reconcile_runtime(session, FailingStopRuntimeManager())
            session.refresh(record)
        self.assertEqual(record.status, "deploying")
        self.assertEqual(result["deployment_recovery_errors"], 1)
        self.assertTrue(any("resources could not be fully reconciled" in error for error in result["errors"]))

    def test_pending_node_operation_is_durable_and_retried_to_completion(self) -> None:
        record = ScenarioRecord(
            id="scenario-node-retry", name="node retry", network_json='{"subnet":"172.30.56.0/24"}',
            devices_json="[]", runtime_devices_json=json.dumps([
                {"node_id": "plc-1", "device_type": "plc-s7", "name": "PLC 1"},
                {"node_id": "plc-2", "device_type": "plc-s7", "name": "PLC 2"},
            ]), status="partial",
        )
        operation = ScenarioNodeOperation(
            id="node-operation-1", scenario_id=record.id, node_id="plc-1",
            action="restart", source="api", status="pending",
        )
        operation_id = operation.id
        scenario_id = record.id
        with Session(self.engine) as session:
            session.add_all([record, operation])
            session.commit()

        manager = RecoverableNodeRuntimeManager()
        with patch("services.reconciliation.flush_protocol_traces_best_effort", return_value=0) as collect:
            first = recover_pending_node_operations(self.engine, manager)
        self.assertEqual(collect.call_count, 1)
        self.assertEqual(collect.call_args.kwargs["scenario_id"], scenario_id)
        self.assertEqual(collect.call_args.kwargs["target_node_id"], "plc-1")
        with Session(self.engine) as session:
            pending = session.get(ScenarioNodeOperation, operation_id)
        self.assertEqual(first["failed"], 1)
        self.assertEqual(pending.status, "pending")
        self.assertEqual(pending.attempts, 1)
        self.assertEqual(pending.last_error, "daemon temporarily unavailable")

        manager.fail = False
        with patch("services.reconciliation.flush_protocol_traces_best_effort", return_value=0) as collect:
            second = recover_pending_node_operations(self.engine, manager)
        self.assertEqual(collect.call_count, 2)
        self.assertTrue(all(call.kwargs["scenario_id"] == scenario_id
                            and call.kwargs["target_node_id"] == "plc-1" for call in collect.call_args_list))
        with Session(self.engine) as session:
            completed = session.get(ScenarioNodeOperation, operation_id)
            updated_scenario = session.get(ScenarioRecord, scenario_id)
            recovered_events = session.exec(select(ScenarioAuditEvent).where(
                ScenarioAuditEvent.scenario_id == scenario_id,
                ScenarioAuditEvent.event_type == "scenario.node_operation_recovered",
            )).all()
        self.assertEqual(second["recovered"], 1)
        self.assertEqual(completed.status, "completed")
        self.assertEqual(completed.attempts, 2)
        self.assertEqual(updated_scenario.status, "partial")
        self.assertEqual(len(recovered_events), 1)
        self.assertEqual(manager.recovered, [
            (scenario_id, "plc-1", "restart"), (scenario_id, "plc-1", "restart"),
        ])

    def test_active_lifecycle_lease_protects_pending_node_journal(self) -> None:
        scenario = ScenarioRecord(
            id="scenario-node-live", name="node live", network_json='{"subnet":"172.30.57.0/24"}',
            devices_json="[]", runtime_devices_json=json.dumps([{"node_id": "plc-1"}]), status="running",
        )
        operation = ScenarioNodeOperation(
            id="node-operation-live", scenario_id=scenario.id, node_id="plc-1",
            action="restart", source="api", status="pending",
        )
        scenario_id = scenario.id
        operation_id = operation.id
        with Session(self.engine) as session:
            session.add_all([scenario, operation])
            session.commit()
        self.assertTrue(acquire_operation_lease(self.engine, scenario_id, "node", "active-worker"))
        manager = RecoverableNodeRuntimeManager()
        result = recover_pending_node_operations(self.engine, manager)
        with Session(self.engine) as session:
            pending = session.get(ScenarioNodeOperation, operation_id)
        self.assertEqual(result["recovered"], 0)
        self.assertEqual(manager.recovered, [])
        self.assertEqual(pending.status, "pending")
