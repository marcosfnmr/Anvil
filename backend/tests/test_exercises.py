from __future__ import annotations

import sys
import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from pydantic import ValidationError

from fastapi.testclient import TestClient
from sqlalchemy import inspect
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from main import create_app  # noqa: E402
from models.db import create_db_and_tables, get_session  # noqa: E402
from models.exercise import ExerciseDefinition, ExerciseRun  # noqa: E402
from models.scenario import ScenarioCreate, ScenarioRecord  # noqa: E402
from models.audit import ScenarioAuditEvent  # noqa: E402
from routes.scenarios import get_docker_manager  # noqa: E402
from services.exercise_runtime import (_evaluate_objective, _sample_timed_objectives, _score_outcomes,
                                      _journaled_node_lifecycle, create_run, exercise_scheduler,
                                      run_due_exercises, _detect_protocol_write_policy_violations)  # noqa: E402
from services.audit import record_audit_event  # noqa: E402
from services.scenario_service import ScenarioService  # noqa: E402
from services.scenario_leases import acquire_operation_lease, release_operation_lease  # noqa: E402


class ExerciseManager:
    def __init__(self) -> None:
        self.running: dict[str, list[dict[str, str]]] = {}
        self.actions: list[tuple[str, str, str]] = []
        self.interceptions: dict[tuple[str, str], list[dict]] = {}

    def set_modbus_route_interception(self, scenario_id: str, route_node_id: str, rules: list[dict] | None) -> dict:
        key = (scenario_id, route_node_id)
        if rules is None:
            self.interceptions.pop(key, None)
            return {"ok": True, "active_rules": 0}
        self.interceptions[key] = rules
        return {"ok": True, "active_rules": len(rules)}

    def deploy(self, scenario_id: str, subnet: str, devices: list[dict]) -> dict:
        del subnet
        self.running[scenario_id] = [{"node_id": item["node_id"], "name": item["name"], "status": "running"} for item in devices]
        return self.status(scenario_id)

    def stop(self, scenario_id: str) -> dict:
        self.running.pop(scenario_id, None)
        return self.status(scenario_id)

    def pause_scenario(self, scenario_id: str) -> dict:
        for container in self.running.get(scenario_id, []):
            container["status"] = "paused"
        return self.status(scenario_id)

    def resume_scenario(self, scenario_id: str) -> dict:
        for container in self.running.get(scenario_id, []):
            container["status"] = "running"
        return self.status(scenario_id)

    def status(self, scenario_id: str) -> dict:
        containers = self.running.get(scenario_id, [])
        states = {item.get("status", "unknown") for item in containers}
        status = ("stopped" if not containers else "paused" if states == {"paused"}
                  else "running" if states == {"running"} else "partial")
        return {"scenario_id": scenario_id, "status": status, "containers": containers}

    def node_lifecycle(self, scenario_id: str, node_id: str, action: str) -> dict:
        self.actions.append((scenario_id, node_id, action))
        for container in self.running[scenario_id]:
            if container["node_id"] == node_id:
                container["status"] = "exited" if action == "stop" else "running"
        return self.status(scenario_id)


class ExerciseApiTests(unittest.TestCase):
    def setUp(self) -> None:
        if self._testMethodName == "test_async_scheduler_applies_due_timeline_without_request_polling":
            # The scheduler runs in a worker thread. Give its transaction a
            # separate connection instead of sharing StaticPool's one handle.
            directory = tempfile.TemporaryDirectory(prefix="anvil-exercise-scheduler-")
            self.addCleanup(directory.cleanup)
            self.engine = create_engine(
                f"sqlite:///{Path(directory.name) / 'scheduler.db'}",
                connect_args={"check_same_thread": False},
            )
        else:
            self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)
        self.manager = ExerciseManager()
        app = create_app(initialize_database=False)

        def session_override():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_session] = session_override
        app.dependency_overrides[get_docker_manager] = lambda: self.manager
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()
        created = self.client.post("/api/scenarios", json={
            "name": "exercise test", "network": {"subnet": "172.30.20.0/24"},
            "devices": [{"node_id": "plc-1", "device_type": "plc-s7", "name": "PLC",
                         "ip": "172.30.20.10", "config": {"components": [
                             {"type": "valve", "name": "Válvula"},
                             {"type": "analog_sensor", "name": "Nivel"},
                         ]}}],
        })
        self.assertEqual(created.status_code, 201, created.text)
        self.scenario_id = created.json()["id"]

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.engine.dispose()

    def test_timeline_incident_and_audit_objective_complete(self) -> None:
        self.assertEqual(self.client.post(f"/api/scenarios/{self.scenario_id}/deploy").status_code, 200)
        definition = {
            "name": "Parada de emergencia", "description": "Incidente de indisponibilidad controlado",
            "events": [{"event_id": "stop-plc", "at_seconds": 0, "kind": "node_lifecycle", "node_id": "plc-1", "action": "stop"}],
            "objectives": [{"objective_id": "detect-stop", "description": "Se registró la parada", "kind": "audit_event", "event_type": "scenario.node_stop"}],
        }
        created = self.client.post(f"/api/scenarios/{self.scenario_id}/exercises", json=definition)
        self.assertEqual(created.status_code, 201, created.text)
        exercise_id = created.json()["id"]
        self.assertEqual(self.client.post(f"/api/scenarios/{self.scenario_id}/exercises/{exercise_id}/start").json()["status"], "running")
        with Session(self.engine) as session:
            run = session.get(ExerciseRun, exercise_id)
            self.assertIsNotNone(run)
            run_due_exercises(session, self.manager, now=run.started_at + timedelta(seconds=1))
            session.refresh(run)
            self.assertEqual(run.status, "completed")
            self.assertEqual(run.to_read_model().objective_results[0]["passed"], True)
            self.assertEqual(run.score, 100)
            self.assertTrue(run.passed)
        self.assertEqual(self.manager.actions, [(self.scenario_id, "plc-1", "stop")])

    def test_protocol_write_event_requires_enabled_station_and_declared_route(self) -> None:
        from models.scenario import ScenarioCreate

        payload = ScenarioCreate.model_validate({
            "name": "protocol exercise validation",
            "network": {"subnet": "172.30.26.0/24", "zones": [
                {"zone_id": "field", "name": "Campo", "purdue_level": "L1", "subnet": "172.30.26.0/25"},
                {"zone_id": "participant", "name": "Participante", "purdue_level": "L2", "subnet": "172.30.26.128/25"},
            ]},
            "devices": [
                {"node_id": "plc", "device_type": "plc-s7", "name": "PLC", "ip": "172.30.26.10",
                 "zone_id": "field", "config": {"components": [{"type": "valve", "name": "Válvula"}]}},
                {"node_id": "participant", "device_type": "attacker-station", "name": "Estación",
                 "ip": "172.30.26.138", "zone_id": "participant",
                 "config": {"role": "attacker", "summary": "Ejercicio", "participant_access_enabled": True}},
            ],
            "network_routes": [{"route_id": "participant-plc", "source_node_id": "participant",
                                "target_node_id": "plc", "protocol": "tcp", "tcp_port": 102}],
        })
        with Session(self.engine) as session:
            scenario = ScenarioService(session).create(payload)
            record = session.get(ScenarioRecord, str(scenario.id))
            self.assertIn("participant-plc", record.runtime_devices()[1]["config"].get("tcp_routes", {}))

        valid = {"name": "Native protocol write", "events": [{
            "event_id": "write", "at_seconds": 0, "kind": "protocol_write",
            "source_node_id": "participant", "target_node_id": "plc",
            "address": "DB1.DBX0.0", "value": True, "route_id": "participant-plc",
        }], "objectives": [{
            "objective_id": "detect-write", "description": "Detect an unapproved peer",
            "kind": "protocol_write_policy", "target_node_id": "plc", "protocol": "s7",
            "allowed_source_ips": ["172.30.26.11"], "expectation": "detected",
        }]}
        response = self.client.post(f"/api/scenarios/{scenario.id}/exercises", json=valid)
        self.assertEqual(response.status_code, 201, response.text)

        invalid = {**valid, "events": [{**valid["events"][0], "route_id": "missing-route"}]}
        rejected = self.client.post(f"/api/scenarios/{scenario.id}/exercises", json=invalid)
        self.assertEqual(rejected.status_code, 422, rejected.text)

        invalid_source = {**valid, "events": [{**valid["events"][0], "source_node_id": "plc"}]}
        rejected_source = self.client.post(f"/api/scenarios/{scenario.id}/exercises", json=invalid_source)
        self.assertEqual(rejected_source.status_code, 422, rejected_source.text)

        invalid_policy = {**valid, "objectives": [{**valid["objectives"][0],
            "target_node_id": "participant"}]}
        rejected_policy = self.client.post(f"/api/scenarios/{scenario.id}/exercises", json=invalid_policy)
        self.assertEqual(rejected_policy.status_code, 422, rejected_policy.text)

    def test_modbus_interception_exercise_applies_audits_and_cleans_rules_on_cancel(self) -> None:
        payload = ScenarioCreate.model_validate({
            "name": "Modbus interception exercise",
            "network": {"subnet": "172.30.27.0/24", "zones": [
                {"zone_id": "control", "name": "Control", "purdue_level": "L1", "subnet": "172.30.27.0/25"},
                {"zone_id": "participant", "name": "Participant", "purdue_level": "L2", "subnet": "172.30.27.128/25"},
            ]},
            "devices": [
                {"node_id": "modbus", "device_type": "modbus-tcp", "name": "Modbus",
                 "ip": "172.30.27.10", "zone_id": "control", "config": {"unit_id": 7, "port": 502,
                     "points": [{"type": "holding_register", "name": "pressure", "address": 4,
                                 "initial_value": 123},
                                {"type": "coil", "name": "valve", "address": 2},
                                {"type": "discrete_input", "name": "limit switch", "address": 3}]}},
                {"node_id": "participant", "device_type": "attacker-station", "name": "Participant",
                 "ip": "172.30.27.138", "zone_id": "participant",
                 "config": {"role": "attacker", "summary": "Authorized exercise",
                            "participant_access_enabled": True}},
            ],
            "network_routes": [{"route_id": "participant-modbus", "source_node_id": "participant",
                                "target_node_id": "modbus", "protocol": "tcp", "tcp_port": 502}],
        })
        with Session(self.engine) as session:
            created_scenario = ScenarioService(session).create(payload)
            scenario_id = str(created_scenario.id)
        self.assertEqual(self.client.post(f"/api/scenarios/{scenario_id}/deploy").status_code, 200)
        definition = {
            "name": "Rewrite one pressure read",
            "events": [
                {"event_id": "tamper", "at_seconds": 0, "kind": "modbus_interception",
                 "route_id": "participant-modbus", "action": "set", "rules": [{
                     "rule_id": "pressure-swap", "unit_id": 7, "function_code": 3,
                     "address": 4, "replacement_value": 65000,
                }, {
                     "rule_id": "pressure-write-swap", "unit_id": 7, "function_code": 6,
                     "address": 4, "replacement_value": 65000,
                }, {
                     "rule_id": "pressure-multiple-write-swap", "unit_id": 7, "function_code": 16,
                     "address": 4, "replacement_value": 64000,
                }, {
                     "rule_id": "open-valve-coil", "unit_id": 7, "function_code": 15,
                     "address": 2, "replacement_value": 1,
                }, {
                     "rule_id": "close-single-coil", "unit_id": 7, "function_code": 5,
                     "address": 2, "replacement_value": 0,
                }, {
                     "rule_id": "mask-write-force", "unit_id": 7, "function_code": 22,
                     "address": 4, "replacement_value": 4660,
                }, {
                     "rule_id": "read-write-response-force", "unit_id": 7, "function_code": 23,
                     "address": 4, "replacement_value": 4660,
                }, {
                     "rule_id": "read-write-request-force", "unit_id": 7, "function_code": 23,
                     "address": 4, "replacement_value": 4567, "phase": "write",
                }, {
                     "rule_id": "hide-valve-coil", "unit_id": 7, "function_code": 1,
                     "address": 2, "replacement_value": 0,
                }, {
                     "rule_id": "hide-limit-input", "unit_id": 7, "function_code": 2,
                     "address": 3, "replacement_value": 0,
                }]},
                {"event_id": "restore", "at_seconds": 10, "kind": "modbus_interception",
                 "route_id": "participant-modbus", "action": "clear"},
            ],
        }
        created = self.client.post(f"/api/scenarios/{scenario_id}/exercises", json=definition)
        self.assertEqual(created.status_code, 201, created.text)
        exercise_id = created.json()["id"]
        self.assertEqual(self.client.post(
            f"/api/scenarios/{scenario_id}/exercises/{exercise_id}/start").status_code, 200)
        with Session(self.engine) as session:
            run = session.get(ExerciseRun, exercise_id)
            run_due_exercises(session, self.manager, now=run.started_at + timedelta(seconds=1))
            session.refresh(run)
            self.assertEqual(run.status, "running")
            self.assertIn((scenario_id, "__anvil_route_participant-modbus"), self.manager.interceptions)
            self.assertEqual([item["function_code"] for item in self.manager.interceptions[
                (scenario_id, "__anvil_route_participant-modbus")]], [3, 6, 16, 15, 5, 22, 23, 23, 1, 2])
        cancelled = self.client.post(f"/api/scenarios/{scenario_id}/exercises/{exercise_id}/cancel")
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertEqual(cancelled.json()["status"], "cancelled")
        self.assertNotIn((scenario_id, "__anvil_route_participant-modbus"), self.manager.interceptions)
        events = self.client.get(f"/api/scenarios/{scenario_id}/audit-events").json()["events"]
        self.assertGreaterEqual(sum(item["event_type"] == "security.modbus_interception" for item in events), 2)

    def test_modbus_interception_rejects_unmatched_route_register_and_missing_clear(self) -> None:
        payload = ScenarioCreate.model_validate({
            "name": "Reject invalid interception", "network": {"subnet": "172.30.28.0/24", "zones": [
                {"zone_id": "control", "name": "Control", "purdue_level": "L1", "subnet": "172.30.28.0/25"},
                {"zone_id": "participant", "name": "Participant", "purdue_level": "L2", "subnet": "172.30.28.128/25"},
            ]},
            "devices": [
                {"node_id": "modbus", "device_type": "modbus-tcp", "name": "Modbus",
                 "ip": "172.30.28.10", "zone_id": "control", "config": {"unit_id": 7, "port": 502,
                     "points": [{"type": "holding_register", "name": "pressure", "address": 4},
                                {"type": "coil", "name": "valve", "address": 2}]}},
                {"node_id": "participant", "device_type": "attacker-station", "name": "Participant",
                 "ip": "172.30.28.138", "zone_id": "participant",
                 "config": {"role": "attacker", "summary": "Exercise", "participant_access_enabled": True}},
            ],
            "network_routes": [{"route_id": "participant-modbus", "source_node_id": "participant",
                                "target_node_id": "modbus", "protocol": "tcp", "tcp_port": 502}],
        })
        with Session(self.engine) as session:
            scenario = ScenarioService(session).create(payload)
            scenario_id = str(scenario.id)
        base_event = {"event_id": "tamper", "at_seconds": 0, "kind": "modbus_interception",
                      "route_id": "participant-modbus", "action": "set", "rules": [{
                          "rule_id": "pressure-swap", "unit_id": 7, "function_code": 3,
                          "address": 4, "replacement_value": 65000,
                      }]}
        missing_clear = self.client.post(f"/api/scenarios/{scenario_id}/exercises", json={
            "name": "Uncleared", "events": [base_event],
        })
        self.assertEqual(missing_clear.status_code, 422, missing_clear.text)
        same_second_clear = self.client.post(f"/api/scenarios/{scenario_id}/exercises", json={
            "name": "Zero duration", "events": [base_event,
                {"event_id": "clear-now", "at_seconds": 0, "kind": "modbus_interception",
                 "route_id": "participant-modbus", "action": "clear"}],
        })
        self.assertEqual(same_second_clear.status_code, 422, same_second_clear.text)
        self.assertIn("clear must occur after its set", same_second_clear.text)
        unknown_register = {**base_event, "event_id": "unknown", "rules": [{
            **base_event["rules"][0], "address": 5,
        }]}
        rejected_register = self.client.post(f"/api/scenarios/{scenario_id}/exercises", json={
            "name": "Unknown address", "events": [unknown_register,
                {"event_id": "clear", "at_seconds": 1, "kind": "modbus_interception",
                 "route_id": "participant-modbus", "action": "clear"}],
        })
        self.assertEqual(rejected_register.status_code, 422, rejected_register.text)

        coil_event = {"event_id": "coil-set", "at_seconds": 0, "kind": "modbus_interception",
                      "route_id": "participant-modbus", "action": "set", "rules": [{
                          "rule_id": "open-valve", "unit_id": 7, "function_code": 15,
                          "address": 2, "replacement_value": 1,
                      }]}
        coil_clear = {"event_id": "coil-clear", "at_seconds": 1,
                      "kind": "modbus_interception", "route_id": "participant-modbus",
                      "action": "clear"}
        accepted_coil = self.client.post(f"/api/scenarios/{scenario_id}/exercises", json={
            "name": "Alter coil", "events": [coil_event, coil_clear],
        })
        self.assertEqual(accepted_coil.status_code, 201, accepted_coil.text)
        rejected_coil_value = self.client.post(f"/api/scenarios/{scenario_id}/exercises", json={
            "name": "Invalid coil value", "events": [{**coil_event, "event_id": "bad-value",
                "rules": [{**coil_event["rules"][0], "rule_id": "bad-value", "replacement_value": 2}]},
                coil_clear],
        })
        self.assertEqual(rejected_coil_value.status_code, 422, rejected_coil_value.text)
        rejected_coil_space = self.client.post(f"/api/scenarios/{scenario_id}/exercises", json={
            "name": "Wrong coil space", "events": [{**coil_event, "event_id": "bad-space",
                "rules": [{**coil_event["rules"][0], "rule_id": "bad-space", "address": 4}]},
                coil_clear],
        })
        self.assertEqual(rejected_coil_space.status_code, 422, rejected_coil_space.text)
        single_coil_event = {**coil_event, "event_id": "single-coil-set", "rules": [{
            **coil_event["rules"][0], "rule_id": "single-coil", "function_code": 5,
        }]}
        single_coil_clear = {**coil_clear, "event_id": "single-coil-clear"}
        accepted_single_coil = self.client.post(f"/api/scenarios/{scenario_id}/exercises", json={
            "name": "Alter single coil", "events": [single_coil_event, single_coil_clear],
        })
        self.assertEqual(accepted_single_coil.status_code, 201, accepted_single_coil.text)
        rejected_single_coil_value = self.client.post(f"/api/scenarios/{scenario_id}/exercises", json={
            "name": "Invalid single coil value", "events":[
                {**single_coil_event, "rules": [{**single_coil_event["rules"][0], "replacement_value": 2}]},
                single_coil_clear,
            ],
        })
        self.assertEqual(rejected_single_coil_value.status_code, 422, rejected_single_coil_value.text)
        mask_event = {"event_id": "mask-set", "at_seconds": 0, "kind": "modbus_interception",
                      "route_id": "participant-modbus", "action": "set", "rules": [{
                          "rule_id": "mask-force", "unit_id": 7, "function_code": 22,
                          "address": 4, "replacement_value": 0xA55A,
                      }]}
        mask_clear = {"event_id": "mask-clear", "at_seconds": 1,
                      "kind": "modbus_interception", "route_id": "participant-modbus",
                      "action": "clear"}
        accepted_mask_write = self.client.post(f"/api/scenarios/{scenario_id}/exercises", json={
            "name": "Alter mask write", "events": [mask_event, mask_clear],
        })
        self.assertEqual(accepted_mask_write.status_code, 201, accepted_mask_write.text)
        rejected_mask_space = self.client.post(f"/api/scenarios/{scenario_id}/exercises", json={
            "name": "Wrong mask write space", "events":[
                {**mask_event, "rules": [{**mask_event["rules"][0], "address": 2}]}, mask_clear,
            ],
        })
        self.assertEqual(rejected_mask_space.status_code, 422, rejected_mask_space.text)
        read_write_event = {"event_id": "read-write-set", "at_seconds": 0,
                            "kind": "modbus_interception", "route_id": "participant-modbus",
                            "action": "set", "rules": [{"rule_id": "read-write-force",
                            "unit_id": 7, "function_code": 23, "address": 4,
                            "replacement_value": 0xA55A}]}
        read_write_clear = {"event_id": "read-write-clear", "at_seconds": 1,
                            "kind": "modbus_interception", "route_id": "participant-modbus",
                            "action": "clear"}
        accepted_read_write = self.client.post(f"/api/scenarios/{scenario_id}/exercises", json={
            "name": "Alter read-write response", "events": [read_write_event, read_write_clear],
        })
        self.assertEqual(accepted_read_write.status_code, 201, accepted_read_write.text)
        accepted_write_phase = self.client.post(f"/api/scenarios/{scenario_id}/exercises", json={
            "name": "Alter combined write", "events": [
                {**read_write_event, "rules": [
                    read_write_event["rules"][0],
                    {**read_write_event["rules"][0], "rule_id": "write-force", "phase": "write"},
                ]}, read_write_clear],
        })
        self.assertEqual(accepted_write_phase.status_code, 201, accepted_write_phase.text)
        rejected_other_phase = self.client.post(f"/api/scenarios/{scenario_id}/exercises", json={
            "name": "Wrong phase", "events": [
                {**read_write_event, "rules": [{**read_write_event["rules"][0],
                    "function_code": 6, "phase": "write"}]}, read_write_clear],
        })
        self.assertEqual(rejected_other_phase.status_code, 422, rejected_other_phase.text)
        rejected_read_write_space = self.client.post(f"/api/scenarios/{scenario_id}/exercises", json={
            "name": "Wrong read-write space", "events":[
                {**read_write_event, "rules": [{**read_write_event["rules"][0], "address": 2}]},
                read_write_clear,
            ],
        })
        self.assertEqual(rejected_read_write_space.status_code, 422, rejected_read_write_space.text)

    def test_timeline_event_waits_for_another_lifecycle_operation(self) -> None:
        self.assertEqual(self.client.post(f"/api/scenarios/{self.scenario_id}/deploy").status_code, 200)
        created = self.client.post(f"/api/scenarios/{self.scenario_id}/exercises", json={
            "name": "Evento coordinado",
            "events": [{"event_id": "stop-plc", "at_seconds": 0, "kind": "node_lifecycle",
                        "node_id": "plc-1", "action": "stop"}],
            "objectives": [],
        })
        self.assertEqual(created.status_code, 201, created.text)
        exercise_id = created.json()["id"]
        started = self.client.post(f"/api/scenarios/{self.scenario_id}/exercises/{exercise_id}/start")
        self.assertEqual(started.status_code, 200, started.text)
        owner = "manual-stop-worker"
        self.assertTrue(acquire_operation_lease(self.engine, self.scenario_id, "stop", owner))
        try:
            with Session(self.engine) as session:
                run = session.get(ExerciseRun, exercise_id)
                run_due_exercises(session, self.manager, now=run.started_at + timedelta(seconds=1))
                session.refresh(run)
                self.assertEqual(run.status, "running")
                self.assertEqual(run.next_event_index, 0)
            self.assertEqual(self.manager.actions, [])
        finally:
            release_operation_lease(self.engine, self.scenario_id, "stop", owner)
        with Session(self.engine) as session:
            run = session.get(ExerciseRun, exercise_id)
            run_due_exercises(session, self.manager, now=run.started_at + timedelta(seconds=1))
            session.refresh(run)
            self.assertEqual(run.status, "completed")
            self.assertEqual(run.next_event_index, 1)
        self.assertEqual(self.manager.actions, [(self.scenario_id, "plc-1", "stop")])

    def test_exercise_node_operation_retry_does_not_repeat_completed_docker_action(self) -> None:
        self.assertEqual(self.client.post(f"/api/scenarios/{self.scenario_id}/deploy").status_code, 200)
        event = {"exercise_id": "run-1", "event_id": "stop-plc"}
        with Session(self.engine) as session:
            first = _journaled_node_lifecycle(session, self.manager, self.scenario_id, "plc-1", "stop", event)
            repeated = _journaled_node_lifecycle(session, self.manager, self.scenario_id, "plc-1", "stop", event)
        self.assertEqual(first, repeated)
        self.assertEqual(self.manager.actions, [(self.scenario_id, "plc-1", "stop")])

    def test_pausing_scenario_suspends_exercise_timeline_and_resumes_remaining_events(self) -> None:
        self.assertEqual(self.client.post(f"/api/scenarios/{self.scenario_id}/deploy").status_code, 200)
        definition = {
            "name": "Pause-safe exercise",
            "events": [{"event_id": "initial-stop", "at_seconds": 0, "kind": "node_lifecycle",
                        "node_id": "plc-1", "action": "stop"},
                       {"event_id": "later-restart", "at_seconds": 10, "kind": "node_lifecycle",
                        "node_id": "plc-1", "action": "restart"}],
            "objectives": [{"objective_id": "restart-recorded", "description": "Restart was audited",
                            "kind": "audit_event", "event_type": "scenario.node_restart"}],
        }
        created = self.client.post(f"/api/scenarios/{self.scenario_id}/exercises", json=definition)
        self.assertEqual(created.status_code, 201, created.text)
        exercise_id = created.json()["id"]
        self.assertEqual(self.client.post(
            f"/api/scenarios/{self.scenario_id}/exercises/{exercise_id}/start").status_code, 200)
        with Session(self.engine) as session:
            run = session.get(ExerciseRun, exercise_id)
            started_at = run.started_at
            run_due_exercises(session, self.manager, now=started_at + timedelta(seconds=1))
            session.refresh(run)
            self.assertEqual(run.status, "running")
            self.assertEqual(run.next_event_index, 1)
            condition_since = (started_at.replace(tzinfo=timezone.utc) if started_at.tzinfo is None else started_at)
            condition_since += timedelta(milliseconds=500)
            trend_sample_at = condition_since - timedelta(seconds=1)
            run.objective_tracking_json = json.dumps({
                "continuous-condition": condition_since.isoformat(),
                "tank-trend": {"kind": "field_trend", "samples": [
                    {"at": trend_sample_at.isoformat(), "value": 123.0},
                ]},
                "tank-sequence": {"kind": "field_sequence", "next_step_index": 1,
                                  "last_match_at": trend_sample_at.isoformat(), "matches": [
                                      {"at": trend_sample_at.isoformat(), "actual": 123.0},
                                  ]},
            })
            session.add(run)
            session.commit()

        paused = self.client.post(f"/api/scenarios/{self.scenario_id}/pause")
        self.assertEqual(paused.status_code, 200, paused.text)
        with Session(self.engine) as session:
            run = session.get(ExerciseRun, exercise_id)
            self.assertEqual(run.status, "paused")
            paused_at = run.paused_at
            run_due_exercises(session, self.manager, now=paused_at + timedelta(seconds=30))
            session.refresh(run)
            self.assertEqual(run.status, "paused")
            self.assertEqual(run.next_event_index, 1)
            self.assertEqual(run.paused_duration_seconds, 0)

        resumed = self.client.post(f"/api/scenarios/{self.scenario_id}/resume")
        self.assertEqual(resumed.status_code, 200, resumed.text)
        with Session(self.engine) as session:
            run = session.get(ExerciseRun, exercise_id)
            self.assertEqual(run.status, "running")
            self.assertIsNone(run.paused_at)
            shifted_condition_since = datetime.fromisoformat(
                json.loads(run.objective_tracking_json)["continuous-condition"])
            self.assertAlmostEqual((shifted_condition_since - condition_since).total_seconds(),
                                   run.paused_duration_seconds, delta=0.02)
            shifted_sample_at = datetime.fromisoformat(
                json.loads(run.objective_tracking_json)["tank-trend"]["samples"][0]["at"])
            self.assertAlmostEqual((shifted_sample_at - trend_sample_at).total_seconds(),
                                   run.paused_duration_seconds, delta=0.02)
            sequence_progress = json.loads(run.objective_tracking_json)["tank-sequence"]
            self.assertAlmostEqual(
                (datetime.fromisoformat(sequence_progress["last_match_at"]) - trend_sample_at).total_seconds(),
                run.paused_duration_seconds, delta=0.02,
            )
            self.assertEqual(run.started_at, started_at)
            resumed_elapsed_base = run.started_at + timedelta(seconds=run.paused_duration_seconds)
            run_due_exercises(session, self.manager, now=resumed_elapsed_base + timedelta(seconds=9))
            session.refresh(run)
            self.assertEqual(run.status, "running")
            self.assertEqual(run.next_event_index, 1)
            self.assertEqual(self.manager.actions, [(self.scenario_id, "plc-1", "stop")])
            run_due_exercises(session, self.manager, now=resumed_elapsed_base + timedelta(seconds=11))
            session.refresh(run)
            self.assertEqual(run.status, "completed")
            self.assertTrue(run.passed)
            self.assertEqual(run.to_read_model().objective_results[0]["passed"], True)
        self.assertEqual(self.manager.actions, [(self.scenario_id, "plc-1", "stop"),
                                                (self.scenario_id, "plc-1", "restart")])

    def test_audit_sequence_objective_respects_order_start_time_and_optional_gap(self) -> None:
        start = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
        with Session(self.engine) as session:
            session.add_all([
                ScenarioAuditEvent(scenario_id=self.scenario_id, timestamp=start - timedelta(seconds=1),
                                   event_type="step.a", actor="test"),
                ScenarioAuditEvent(scenario_id=self.scenario_id, timestamp=start + timedelta(milliseconds=200),
                                   event_type="step.b", actor="test"),
                ScenarioAuditEvent(scenario_id=self.scenario_id, timestamp=start + timedelta(seconds=1),
                                   event_type="step.a", actor="test"),
                ScenarioAuditEvent(scenario_id=self.scenario_id, timestamp=start + timedelta(seconds=1, milliseconds=200),
                                   event_type="noise", actor="test"),
                ScenarioAuditEvent(scenario_id=self.scenario_id, timestamp=start + timedelta(seconds=3),
                                   event_type="step.b", actor="test"),
                ScenarioAuditEvent(scenario_id=self.scenario_id, timestamp=start + timedelta(seconds=4),
                                   event_type="step.c", actor="test"),
            ])
            session.commit()
            scenario = session.get(ScenarioRecord, self.scenario_id)
            objective = {"objective_id": "ordered", "description": "Steps happened in order",
                         "weight": 1, "kind": "audit_sequence",
                         "event_types": ["step.a", "step.b", "step.c"], "max_gap_seconds": 2}
            result = _evaluate_objective(session, self.manager, scenario, objective, start)
            self.assertTrue(result["passed"])
            self.assertEqual(result["matched_sequence"], objective["event_types"])

            objective["max_gap_seconds"] = 1
            result = _evaluate_objective(session, self.manager, scenario, objective, start)
            self.assertFalse(result["passed"])
            self.assertEqual(result["matched_sequence"], [])

    def test_audit_event_objective_can_require_an_event_to_be_absent(self) -> None:
        start = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
        with Session(self.engine) as session:
            scenario = session.get(ScenarioRecord, self.scenario_id)
            absent = {"objective_id": "no-denial", "description": "No hubo denegaciones",
                      "weight": 1, "kind": "audit_event", "event_type": "api.request_rejected",
                      "expectation": "absent"}
            result = _evaluate_objective(session, self.manager, scenario, absent, start)
            self.assertFalse(result["actual"])
            self.assertTrue(result["passed"])

            session.add(ScenarioAuditEvent(scenario_id=self.scenario_id,
                                           timestamp=start + timedelta(seconds=1),
                                           event_type="api.request_rejected", actor="test"))
            session.commit()
            result = _evaluate_objective(session, self.manager, scenario, absent, start)
            self.assertTrue(result["actual"])
            self.assertFalse(result["passed"])

            legacy = {**absent, "expectation": "occurred"}
            self.assertTrue(_evaluate_objective(session, self.manager, scenario, legacy, start)["passed"])

    def test_protocol_write_policy_detects_only_non_allowlisted_writes_and_is_idempotent(self) -> None:
        start = datetime.now(timezone.utc) - timedelta(seconds=5)
        with Session(self.engine) as session:
            scenario = session.get(ScenarioRecord, self.scenario_id)
            definition = ExerciseDefinition.model_validate({
                "name": "policy test",
                "objectives": [{
                    "objective_id": "policy", "description": "Detect an unapproved peer",
                    "kind": "protocol_write_policy", "target_node_id": "plc-1",
                    "protocol": "s7", "allowed_source_ips": ["172.30.20.15"],
                }],
            })
            run = ExerciseRun(
                scenario_id=self.scenario_id, definition_json=definition.model_dump_json(),
                status="running", started_at=start,
            )
            session.add(run)
            session.commit()
            record_audit_event(session, self.scenario_id, "protocol.s7_operation", {
                "node_id": "plc-1", "protocol": "s7", "source": "172.30.20.15",
                "operation": "write", "area": "DB", "db": 1,
                "byte_offset": 4, "byte_count": 4, "result": "ok",
            }, actor="s7-client", timestamp=start + timedelta(seconds=1))
            record_audit_event(session, self.scenario_id, "protocol.s7_operation", {
                "node_id": "plc-1", "protocol": "s7", "source": "10.20.0.8",
                "operation": "read", "area": "DB", "db": 1,
                "byte_offset": 4, "byte_count": 4, "result": "ok",
            }, actor="s7-client", timestamp=start + timedelta(seconds=2))
            record_audit_event(session, self.scenario_id, "protocol.s7_operation", {
                "node_id": "plc-1", "protocol": "s7", "source": "10.20.0.8",
                "operation": "write", "area": "DB", "db": 1,
                "byte_offset": 4, "byte_count": 4, "result": "ok",
            }, actor="s7-client", timestamp=start + timedelta(seconds=3))
            session.commit()

            self.assertEqual(_detect_protocol_write_policy_violations(
                session, scenario, definition, run,
            ), 1)
            self.assertEqual(_detect_protocol_write_policy_violations(
                session, scenario, definition, run,
            ), 0)
            result = _evaluate_objective(
                session, self.manager, scenario,
                definition.objectives[0].model_dump(mode="json"), start,
            )
            self.assertTrue(result["passed"])
            self.assertEqual(result["detection_count"], 1)
            detection = session.exec(select(ScenarioAuditEvent).where(
                ScenarioAuditEvent.scenario_id == self.scenario_id,
                ScenarioAuditEvent.event_type == "security.protocol_write_policy_violation",
            )).one()
            details = json.loads(detection.details_json)
            self.assertEqual(details["source"], "10.20.0.8")
            self.assertEqual(details["source_attribution"], "observed_peer_ip")
            self.assertNotIn("value", details)

    def test_protocol_write_policy_rejects_invalid_or_duplicate_source_addresses(self) -> None:
        base = {
            "objective_id": "policy", "description": "Watch writes",
            "kind": "protocol_write_policy", "target_node_id": "plc-1", "protocol": "s7",
        }
        with self.assertRaises(ValidationError):
            ExerciseDefinition.model_validate({"name": "bad ip", "objectives": [{
                **base, "allowed_source_ips": ["not-an-ip"],
            }]})
        with self.assertRaises(ValidationError):
            ExerciseDefinition.model_validate({"name": "duplicate ip", "objectives": [{
                **base, "allowed_source_ips": ["10.0.0.1", "10.0.0.1"],
            }]})

    def test_audit_event_details_filter_scopes_occurrence_and_absence(self) -> None:
        start = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
        with Session(self.engine) as session:
            scenario = session.get(ScenarioRecord, self.scenario_id)
            record_audit_event(session, self.scenario_id, "operator.point_written", {
                "node_id": "plc-1", "address": "holding_register:1", "source": "api",
            }, actor="test", timestamp=start + timedelta(seconds=1))
            objective = {"objective_id": "specific-write", "description": "Se manipuló el punto concreto",
                         "kind": "audit_event", "event_type": "operator.point_written",
                         "details_match": {"node_id": "plc-1", "address": "holding_register:7",
                                           "source": "exercise"}}
            result = _evaluate_objective(session, self.manager, scenario, objective, start)
            self.assertFalse(result["actual"])
            self.assertFalse(result["passed"])

            record_audit_event(session, self.scenario_id, "operator.point_written", {
                "node_id": "plc-1", "address": "holding_register:7", "source": "exercise",
            }, actor="test", timestamp=start + timedelta(seconds=2))
            result = _evaluate_objective(session, self.manager, scenario, objective, start)
            self.assertTrue(result["actual"])
            self.assertTrue(result["passed"])

            absent = {**objective, "expectation": "absent", "details_match": {"source": "attacker"}}
            self.assertTrue(_evaluate_objective(session, self.manager, scenario, absent, start)["passed"])

    def test_audit_event_can_detect_a_specific_native_s7_or_modbus_write(self) -> None:
        start = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
        with Session(self.engine) as session:
            scenario = session.get(ScenarioRecord, self.scenario_id)
            record_audit_event(session, self.scenario_id, "protocol.s7_operation", {
                "node_id": "plc-1", "protocol": "s7", "source": "172.30.20.40",
                "operation": "write", "area": "DB", "db": 1,
                "byte_offset": 4, "byte_count": 4, "result": "ok", "result_code": 0,
            }, actor="s7-client", timestamp=start + timedelta(seconds=1))
            record_audit_event(session, self.scenario_id, "protocol.modbus_operation", {
                "node_id": "modbus-1", "source": "172.30.20.40", "unit_id": 1,
                "function_code": 6, "operation": "write_holding_register",
                "space": "holding_register", "address": 4, "count": 1, "result": "ok",
            }, actor="modbus-client", timestamp=start + timedelta(seconds=2))

            s7_objective = {
                "objective_id": "detect-s7-db-write", "description": "Detectar escritura DB",
                "kind": "audit_event", "event_type": "protocol.s7_operation",
                "details_match": {"node_id": "plc-1", "operation": "write", "area": "DB",
                                  "db": 1, "byte_offset": 4, "result": "ok"},
            }
            modbus_objective = {
                "objective_id": "detect-modbus-write", "description": "Detectar FC06",
                "kind": "audit_event", "event_type": "protocol.modbus_operation",
                "details_match": {"node_id": "modbus-1", "function_code": 6,
                                  "operation": "write_holding_register",
                                  "space": "holding_register", "address": 4, "result": "ok"},
            }
            definition = ExerciseDefinition.model_validate({
                "name": "Detectar escrituras protocolarias",
                "objectives": [s7_objective, modbus_objective],
            })
            self.assertEqual(len(definition.objectives), 2)
            self.assertTrue(_evaluate_objective(session, self.manager, scenario,
                                                s7_objective, start)["passed"])
            self.assertTrue(_evaluate_objective(session, self.manager, scenario,
                                                modbus_objective, start)["passed"])

            wrong_offset = {**s7_objective, "details_match": {
                **s7_objective["details_match"], "byte_offset": 8,
            }}
            wrong_address = {**modbus_objective, "details_match": {
                **modbus_objective["details_match"], "address": 8,
            }}
            self.assertFalse(_evaluate_objective(session, self.manager, scenario,
                                                 wrong_offset, start)["passed"])
            self.assertFalse(_evaluate_objective(session, self.manager, scenario,
                                                 wrong_address, start)["passed"])

    def test_audit_event_objective_rejects_unknown_expectation(self) -> None:
        definition = {"name": "Invalid audit objective", "objectives": [{
            "objective_id": "invalid", "description": "Invalid", "kind": "audit_event",
            "event_type": "operator.field_control", "expectation": "maybe",
        }]}
        with self.assertRaises(ValidationError):
            ExerciseDefinition.model_validate(definition)

    def test_audit_event_filter_rejects_unknown_or_unbounded_details(self) -> None:
        base = {"name": "Invalid detail selector", "objectives": [{
            "objective_id": "invalid", "description": "Invalid", "kind": "audit_event",
            "event_type": "operator.point_written",
        }]}
        for details_match in ({"not_a_detail": "value"}, {"source": "x" * 513}, {"value": 10**130}):
            with self.subTest(details_match=next(iter(details_match))):
                with self.assertRaises(ValidationError):
                    ExerciseDefinition.model_validate({
                        **base, "objectives": [{**base["objectives"][0], "details_match": details_match}],
                    })

    def test_fail_fast_is_only_valid_for_absent_audit_expectations(self) -> None:
        with self.assertRaises(ValidationError):
            ExerciseDefinition.model_validate({"name": "Invalid fail fast", "objectives": [{
                "objective_id": "invalid", "description": "Invalid", "kind": "audit_event",
                "event_type": "operator.field_control", "expectation": "occurred", "fail_fast": True,
            }]})

    def test_fail_fast_audit_violation_stops_before_remaining_timeline(self) -> None:
        self.assertEqual(self.client.post(f"/api/scenarios/{self.scenario_id}/deploy").status_code, 200)
        definition = {
            "name": "Regla de parada inmediata",
            "events": [{"event_id": "future-stop", "at_seconds": 20, "kind": "node_lifecycle",
                        "node_id": "plc-1", "action": "stop"}],
            "objectives": [{"objective_id": "no-rejection", "description": "No hay rechazos",
                            "kind": "audit_event", "event_type": "api.request_rejected",
                            "details_match": {"source": "exercise"},
                            "expectation": "absent", "fail_fast": True}],
        }
        created = self.client.post(f"/api/scenarios/{self.scenario_id}/exercises", json=definition)
        self.assertEqual(created.status_code, 201, created.text)
        exercise_id = created.json()["id"]
        started = self.client.post(f"/api/scenarios/{self.scenario_id}/exercises/{exercise_id}/start")
        self.assertEqual(started.status_code, 200)
        with Session(self.engine) as session:
            run = session.get(ExerciseRun, exercise_id)
            started_at = run.started_at
            record_audit_event(session, self.scenario_id, "api.request_rejected", {}, actor="test",
                               timestamp=started_at + timedelta(milliseconds=500))
            run_due_exercises(session, self.manager, now=started_at + timedelta(seconds=1))
            session.refresh(run)
            self.assertEqual(run.status, "running")
            self.assertEqual(run.next_event_index, 0)
            record_audit_event(session, self.scenario_id, "api.request_rejected", {"source": "exercise"},
                               actor="test", timestamp=started_at + timedelta(milliseconds=1500))
            run_due_exercises(session, self.manager, now=started_at + timedelta(seconds=2))
            session.refresh(run)
            result = run.to_read_model()
            self.assertEqual(run.status, "failed")
            self.assertEqual(run.score, 0)
            self.assertFalse(run.passed)
            self.assertEqual(run.next_event_index, 0)
            self.assertEqual(self.manager.actions, [])
            self.assertTrue(result.objective_results[0]["terminated_run"])
            self.assertEqual(result.event_results[-1]["reason"], "forbidden_audit_event")

    def test_audit_sequence_requires_two_nonempty_event_types(self) -> None:
        with self.assertRaises(ValueError):
            ExerciseDefinition.model_validate({"name": "short sequence", "objectives": [{
                "objective_id": "seq", "description": "too short", "kind": "audit_sequence",
                "event_types": ["only.one"],
            }]})
        with self.assertRaises(ValueError):
            ExerciseDefinition.model_validate({"name": "blank sequence", "objectives": [{
                "objective_id": "seq", "description": "blank", "kind": "audit_sequence",
                "event_types": ["first", "  "],
            }]})
        for max_gap in (0, 86_401, float("inf")):
            with self.subTest(max_gap=max_gap), self.assertRaises(ValueError):
                ExerciseDefinition.model_validate({"name": "invalid gap", "objectives": [{
                    "objective_id": "seq", "description": "gap", "kind": "audit_sequence",
                    "event_types": ["first", "second"], "max_gap_seconds": max_gap,
                }]})
    def test_timeline_can_interrupt_and_restore_only_declared_network_routes(self) -> None:
        created = self.client.post("/api/scenarios", json={
            "name": "Route exercise", "network": {"subnet": "172.30.20.0/24", "zones": [
                {"zone_id": "control", "name": "Control", "purdue_level": "L1", "subnet": "172.30.20.0/25"},
                {"zone_id": "operations", "name": "Operations", "purdue_level": "L2", "subnet": "172.30.20.128/25"},
            ]},
            "devices": [
                {"node_id": "source", "device_type": "scada", "name": "SCADA", "ip": "172.30.20.130", "zone_id": "operations", "config": {"target_node_ids": "target", "poll_interval_ms": 500}},
                {"node_id": "target", "device_type": "plc-s7", "name": "Target", "ip": "172.30.20.10", "zone_id": "control", "config": {"components": []}},
            ],
            "network_routes": [{"route_id": "scada-plc", "source_node_id": "source", "target_node_id": "target", "protocol": "s7", "enabled": True}],
        })
        self.assertEqual(created.status_code, 201, created.text)
        scenario_id = created.json()["id"]
        self.assertEqual(self.client.post(f"/api/scenarios/{scenario_id}/deploy").status_code, 200)
        with Session(self.engine) as session:
            definition = ExerciseDefinition.model_validate({
                "name": "Interrupción controlada de red",
                "events": [
                    {"event_id": "cut", "at_seconds": 0, "kind": "network_route", "route_id": "scada-plc", "action": "interrupt"},
                    {"event_id": "restore", "at_seconds": 2, "kind": "network_route", "route_id": "scada-plc", "action": "restore"},
                ],
                "objectives": [{"objective_id": "restored", "description": "Se restauró la ruta", "kind": "audit_event",
                                "event_type": "scenario.network_route_restored"}],
            })
            run = create_run(session, scenario_id, definition)
            start = datetime.now(timezone.utc)
            run.status = "running"
            run.started_at = start
            session.add(run)
            session.commit()
            run_due_exercises(session, self.manager, now=start + timedelta(seconds=1))
            session.refresh(run)
            self.assertEqual(run.next_event_index, 1)
            run_due_exercises(session, self.manager, now=start + timedelta(seconds=3))
            session.refresh(run)
            self.assertEqual(run.status, "completed")
            self.assertEqual(self.manager.actions[-2:], [
                (scenario_id, "__anvil_route_scada-plc", "stop"),
                (scenario_id, "__anvil_route_scada-plc", "start"),
            ])
            event_types = {event.event_type for event in session.exec(select(ScenarioAuditEvent).where(
                ScenarioAuditEvent.scenario_id == scenario_id,
            )).all()}
            self.assertTrue({"scenario.network_route_interrupted", "scenario.network_route_restored"} <= event_types)

    def test_timeline_rejects_undeclared_network_route(self) -> None:
        definition = ExerciseDefinition.model_validate({"name": "Ruta no declarada", "events": [{
            "event_id": "cut", "at_seconds": 0, "kind": "network_route", "route_id": "outside", "action": "interrupt",
        }]})
        with Session(self.engine) as session:
            with self.assertRaisesRegex(ValueError, "unknown or disabled network route"):
                create_run(session, self.scenario_id, definition)

    def test_read_only_point_cannot_be_scheduled_for_writing(self) -> None:
        read_only = next(point for point in self.client.get(f"/api/scenarios/{self.scenario_id}/io-map").json()["points"]
                         if point["access"] == "read")
        definition = ExerciseDefinition.model_validate({
            "name": "No escribir entradas", "events": [{
                "event_id": "write-input", "at_seconds": 0, "kind": "write_point",
                "node_id": read_only["node_id"], "address": read_only["address"], "value": 42.0,
            }],
        })
        from services.exercise_runtime import create_run
        with Session(self.engine) as session:
            scenario = session.get(__import__("models.scenario", fromlist=["ScenarioRecord"]).ScenarioRecord, self.scenario_id)
            with self.assertRaisesRegex(ValueError, "read-only"):
                create_run(session, self.scenario_id, definition)

    def test_timed_field_control_events_inject_and_clear_process_incidents(self) -> None:
        from services.exercise_runtime import create_run

        with Session(self.engine) as session:
            scenario = session.get(ScenarioRecord, self.scenario_id)
            scenario.status = "running"
            scenario.field_objects_json = json.dumps([
                {"object_id": "tank", "name": "Tank", "type": "tank", "capacity_liters": 1000},
                {"object_id": "pump", "name": "Pump", "type": "pump"},
                {"object_id": "level", "name": "Level", "type": "analog_sensor", "min_value": 0, "max_value": 100},
            ])
            session.add(scenario)
            session.commit()
            definition = ExerciseDefinition.model_validate({
                "name": "Process incident",
                "events": [
                    {"event_id": "demand", "at_seconds": 0, "kind": "field_control",
                     "object_id": "tank", "demand_flow_lpm": 15.0},
                    {"event_id": "leak", "at_seconds": 0, "kind": "field_control",
                     "object_id": "tank", "leak_flow_lpm": 8.0},
                    {"event_id": "jam-pump", "at_seconds": 0, "kind": "field_control",
                     "object_id": "pump", "fault": True},
                    {"event_id": "fault-level", "at_seconds": 0, "kind": "field_control",
                     "object_id": "level", "fault": True},
                    {"event_id": "restore-pump", "at_seconds": 2, "kind": "field_control",
                     "object_id": "pump", "fault": False},
                    {"event_id": "restore-level", "at_seconds": 2, "kind": "field_control",
                     "object_id": "level", "fault": False},
                    {"event_id": "restore-leak", "at_seconds": 2, "kind": "field_control",
                     "object_id": "tank", "leak_flow_lpm": 0.0},
                    {"event_id": "spoof-level", "at_seconds": 2, "kind": "field_control",
                     "object_id": "level", "override": 99.0},
                ],
            })
            run = create_run(session, self.scenario_id, definition)
            start = datetime.now(timezone.utc)
            run.status = "running"
            run.started_at = start
            session.add(run)
            session.commit()

            with patch("services.field_operations.field_operation", return_value={"objects": []}) as control:
                run_due_exercises(session, self.manager, now=start)
                session.refresh(run)
                self.assertEqual(run.status, "running")
                self.assertEqual(run.next_event_index, 4)
                self.assertEqual(control.call_args_list[0].args[2:], ("control", {"object_id": "tank", "demand_flow_lpm": 15.0}))
                self.assertEqual(control.call_args_list[1].args[3], {"object_id": "tank", "leak_flow_lpm": 8.0})
                run_due_exercises(session, self.manager, now=start + timedelta(seconds=3))
                session.refresh(run)
                self.assertEqual(run.status, "completed")
                self.assertEqual(run.next_event_index, 8)
                self.assertEqual(control.call_args_list[4].args[3], {"object_id": "pump", "fault": False})
                self.assertEqual(control.call_args_list[5].args[3], {"object_id": "level", "fault": False})
                self.assertEqual(control.call_args_list[6].args[3], {"object_id": "tank", "leak_flow_lpm": 0.0})
            audit = session.exec(select(ScenarioAuditEvent).where(
                ScenarioAuditEvent.scenario_id == self.scenario_id,
                ScenarioAuditEvent.event_type == "operator.field_control",
            )).all()
            self.assertEqual(len(audit), 8)
            self.assertEqual(json.loads(audit[0].details_json)["source"], "exercise")

    def test_field_control_event_rejects_incompatible_target_and_empty_action(self) -> None:
        with self.assertRaisesRegex(ValueError, "fault, override, demand_flow_lpm or leak_flow_lpm"):
            ExerciseDefinition.model_validate({"name": "empty", "events": [{
                "event_id": "empty-control", "at_seconds": 0, "kind": "field_control", "object_id": "level",
            }]})
        with Session(self.engine) as session:
            scenario = session.get(ScenarioRecord, self.scenario_id)
            scenario.field_objects_json = json.dumps([
                {"object_id": "level", "name": "Level", "type": "analog_sensor", "min_value": 0, "max_value": 100},
            ])
            session.add(scenario)
            session.commit()
            invalid = ExerciseDefinition.model_validate({"name": "bad fault", "events": [{
                "event_id": "fault-sensor", "at_seconds": 0, "kind": "field_control",
                "object_id": "level", "fault": True,
            }]})
            self.assertEqual(create_run(session, self.scenario_id, invalid).status, "ready")
            scenario.field_objects_json = json.dumps([
                {"object_id": "alarm", "name": "Alarm", "type": "alarm"},
            ])
            session.add(scenario)
            session.commit()
            invalid = ExerciseDefinition.model_validate({"name": "bad alarm fault", "events": [{
                "event_id": "fault-alarm", "at_seconds": 0, "kind": "field_control",
                "object_id": "alarm", "fault": True,
            }]})
            with self.assertRaisesRegex(ValueError, "actuator or process sensor"):
                create_run(session, self.scenario_id, invalid)
            invalid_null = ExerciseDefinition.model_validate({"name": "null fault", "events": [{
                "event_id": "fault-null", "at_seconds": 0, "kind": "field_control",
                "object_id": "alarm", "fault": None,
            }]})
            with self.assertRaisesRegex(ValueError, "boolean fault"):
                create_run(session, self.scenario_id, invalid_null)

            scenario.field_objects_json = json.dumps([
                {"object_id": "valve", "name": "Valve", "type": "valve"},
            ])
            session.add(scenario)
            session.commit()
            invalid_leak = ExerciseDefinition.model_validate({"name": "bad leak", "events": [{
                "event_id": "leak-valve", "at_seconds": 0, "kind": "field_control",
                "object_id": "valve", "leak_flow_lpm": 3,
            }]})
            with self.assertRaisesRegex(ValueError, "only change leakage on a tank"):
                create_run(session, self.scenario_id, invalid_leak)

    def test_events_must_be_sorted_and_ids_unique(self) -> None:
        with self.assertRaisesRegex(ValueError, "ordered"):
            ExerciseDefinition.model_validate({"name": "bad", "events": [
                {"event_id": "late", "at_seconds": 2, "kind": "node_lifecycle", "node_id": "plc-1", "action": "stop"},
                {"event_id": "early", "at_seconds": 1, "kind": "node_lifecycle", "node_id": "plc-1", "action": "start"},
            ]})

    def test_weighted_score_and_composite_success_rules(self) -> None:
        definition = ExerciseDefinition.model_validate({
            "name": "Evaluación ponderada", "success_rule": "score", "passing_score": 60,
            "objectives": [
                {"objective_id": "critical", "description": "Critical", "weight": 3,
                 "kind": "audit_event", "event_type": "operator.point_written"},
                {"objective_id": "secondary", "description": "Secondary", "weight": 1,
                 "kind": "audit_event", "event_type": "scenario.node_stop"},
            ],
        })
        outcomes = [
            {"objective_id": "critical", "passed": True},
            {"objective_id": "secondary", "passed": False},
        ]
        self.assertEqual(_score_outcomes(definition, outcomes), (True, 75))
        definition.passing_score = 80
        self.assertEqual(_score_outcomes(definition, outcomes), (False, 75))
        definition.success_rule = "any"
        self.assertEqual(_score_outcomes(definition, outcomes), (True, 75))
        definition.success_rule = "all"
        self.assertEqual(_score_outcomes(definition, outcomes), (False, 75))

    def test_old_exercise_definitions_keep_all_objectives_default(self) -> None:
        definition = ExerciseDefinition.model_validate({"name": "Legacy", "objectives": []})
        self.assertEqual(definition.success_rule, "all")
        self.assertEqual(definition.passing_score, 100)
        self.assertEqual(_score_outcomes(definition, []), (True, 100))

    def test_existing_exercise_database_gets_additive_result_and_pause_columns(self) -> None:
        with self.engine.begin() as connection:
            connection.exec_driver_sql("ALTER TABLE scenario_exercise_runs DROP COLUMN score")
            connection.exec_driver_sql("ALTER TABLE scenario_exercise_runs DROP COLUMN passed")
            connection.exec_driver_sql("ALTER TABLE scenario_exercise_runs DROP COLUMN objective_tracking_json")
            connection.exec_driver_sql("ALTER TABLE scenario_exercise_runs DROP COLUMN paused_at")
            connection.exec_driver_sql("ALTER TABLE scenario_exercise_runs DROP COLUMN paused_duration_seconds")
        create_db_and_tables(self.engine)
        create_db_and_tables(self.engine)
        columns = {item["name"] for item in inspect(self.engine).get_columns("scenario_exercise_runs")}
        self.assertIn("score", columns)
        self.assertIn("passed", columns)
        self.assertIn("objective_tracking_json", columns)
        self.assertIn("paused_at", columns)
        self.assertIn("paused_duration_seconds", columns)

    def test_process_state_objective_reads_field_runtime_snapshot(self) -> None:
        tank = {"object_id": "tank-1", "name": "Tank", "type": "tank",
                "capacity_liters": 1000, "initial_level_liters": 500}
        with Session(self.engine) as session:
            scenario = session.get(ScenarioRecord, self.scenario_id)
            scenario.field_objects_json = json.dumps([tank])
            session.add(scenario)
            session.commit()
            definition = ExerciseDefinition.model_validate({
                "name": "Nivel de proceso", "objectives": [{
                    "objective_id": "level-ok", "description": "Tanque sobre 400 litros",
                    "kind": "field_value", "object_id": "tank-1", "property": "volume_liters",
                    "operator": "gte", "value": 400,
                }],
            })
            run = create_run(session, self.scenario_id, definition)
            with patch("services.field_operations.field_operation", return_value={
                "objects": [{"object_id": "tank-1", "volume_liters": 500}],
            }):
                outcome = _evaluate_objective(session, self.manager, scenario,
                                              definition.objectives[0].model_dump(mode="json"), None)
            self.assertTrue(outcome["passed"])
            self.assertEqual(outcome["actual"], 500)
            self.assertEqual(run.to_read_model().definition.objectives[0].kind, "field_value")

    def test_timed_process_objective_requires_continuous_condition(self) -> None:
        tank = {"object_id": "tank-1", "name": "Tank", "type": "tank",
                "capacity_liters": 1000, "initial_level_liters": 500}
        with Session(self.engine) as session:
            scenario = session.get(ScenarioRecord, self.scenario_id)
            scenario.field_objects_json = json.dumps([tank])
            session.add(scenario)
            session.commit()
            definition = ExerciseDefinition.model_validate({
                "name": "Nivel estable", "objectives": [{
                    "objective_id": "level-hold", "description": "Mantener más de 400 L",
                    "kind": "field_value", "object_id": "tank-1", "property": "volume_liters",
                    "operator": "gte", "value": 400, "duration_seconds": 2,
                }],
            })
            run = create_run(session, self.scenario_id, definition)
            start = datetime.now(timezone.utc)
            snapshot = {"objects": [{"object_id": "tank-1", "volume_liters": 500}]}
            with patch("services.field_operations.field_operation", return_value=snapshot):
                _sample_timed_objectives(session, self.manager, scenario, definition, run, start)
                session.refresh(run)
                first_start = json.loads(run.objective_tracking_json)["level-hold"]
                _sample_timed_objectives(session, self.manager, scenario, definition, run,
                                               start + timedelta(seconds=1))
                tracking = json.loads(run.objective_tracking_json)
                waiting = _evaluate_objective(
                    session, self.manager, scenario, definition.objectives[0].model_dump(mode="json"),
                    start, now=start + timedelta(seconds=1), tracking=tracking,
                )
                self.assertTrue(waiting["waiting_duration"])
                snapshot["objects"][0]["volume_liters"] = 350
                _sample_timed_objectives(session, self.manager, scenario, definition, run,
                                               start + timedelta(seconds=1.5))
                session.refresh(run)
                self.assertIsNone(json.loads(run.objective_tracking_json)["level-hold"])
                snapshot["objects"][0]["volume_liters"] = 500
                _sample_timed_objectives(session, self.manager, scenario, definition, run,
                                               start + timedelta(seconds=2))
                session.refresh(run)
                second_start = json.loads(run.objective_tracking_json)["level-hold"]
                self.assertNotEqual(second_start, first_start)
                outcome = _evaluate_objective(
                    session, self.manager, scenario, definition.objectives[0].model_dump(mode="json"),
                    start, now=start + timedelta(seconds=4.1),
                    tracking=json.loads(run.objective_tracking_json),
                )
            self.assertTrue(outcome["passed"])
            self.assertFalse(outcome["waiting_duration"])

    def test_timed_fault_objective_requires_continuous_fault_and_sequences_fault_states(self) -> None:
        valve = {"object_id": "valve-1", "name": "Valve", "type": "valve"}
        with Session(self.engine) as session:
            scenario = session.get(ScenarioRecord, self.scenario_id)
            scenario.field_objects_json = json.dumps([valve])
            session.add(scenario)
            session.commit()
            definition = ExerciseDefinition.model_validate({
                "name": "Fallo de válvula",
                "objectives": [
                    {"objective_id": "fault-held", "description": "Fallo sostenido",
                     "kind": "field_value", "object_id": "valve-1", "property": "fault",
                     "operator": "eq", "value": True, "duration_seconds": 2},
                    {"objective_id": "fault-cleared", "description": "Fallo activado y liberado",
                     "kind": "field_sequence", "steps": [
                         {"object_id": "valve-1", "property": "fault", "operator": "eq", "value": True},
                         {"object_id": "valve-1", "property": "fault", "operator": "eq", "value": False},
                     ]},
                ],
            })
            run = create_run(session, self.scenario_id, definition)
            start = datetime.now(timezone.utc)
            snapshot = {"objects": [{"object_id": "valve-1", "fault": True}]}
            with patch("services.field_operations.field_operation", return_value=snapshot):
                _sample_timed_objectives(session, self.manager, scenario, definition, run, start)
                session.refresh(run)
                first_start = json.loads(run.objective_tracking_json)["fault-held"]
                _sample_timed_objectives(session, self.manager, scenario, definition, run,
                                         start + timedelta(seconds=1))
                waiting = _evaluate_objective(
                    session, self.manager, scenario,
                    definition.objectives[0].model_dump(mode="json"), start,
                    now=start + timedelta(seconds=1),
                    tracking=json.loads(run.objective_tracking_json),
                )
                self.assertTrue(waiting["waiting_duration"])
                snapshot["objects"][0]["fault"] = False
                _sample_timed_objectives(session, self.manager, scenario, definition, run,
                                         start + timedelta(seconds=1.5))
                session.refresh(run)
                tracking = json.loads(run.objective_tracking_json)
                self.assertIsNone(tracking["fault-held"])
                self.assertEqual(tracking["fault-cleared"]["next_step_index"], 2)

                snapshot["objects"][0]["fault"] = True
                _sample_timed_objectives(session, self.manager, scenario, definition, run,
                                         start + timedelta(seconds=2))
                session.refresh(run)
                second_start = json.loads(run.objective_tracking_json)["fault-held"]
                self.assertNotEqual(second_start, first_start)
                _sample_timed_objectives(session, self.manager, scenario, definition, run,
                                         start + timedelta(seconds=4.1))
                session.refresh(run)
                passed = _evaluate_objective(
                    session, self.manager, scenario,
                    definition.objectives[0].model_dump(mode="json"), start,
                    now=start + timedelta(seconds=4.1),
                    tracking=json.loads(run.objective_tracking_json),
                )
            self.assertTrue(passed["passed"])
            self.assertFalse(passed["waiting_duration"])

    def test_field_trend_objective_uses_persisted_samples_and_window_coverage(self) -> None:
        with Session(self.engine) as session:
            scenario = session.get(ScenarioRecord, self.scenario_id)
            tank = {"object_id": "tank-trend", "name": "Tank", "type": "tank",
                    "capacity_liters": 1000, "initial_level_liters": 500}
            scenario.field_objects_json = json.dumps([tank])
            session.add(scenario)
            session.commit()
            definition = ExerciseDefinition.model_validate({
                "name": "Level rises", "objectives": [{
                    "objective_id": "level-rise", "description": "Tank level rises",
                    "kind": "field_trend", "object_id": "tank-trend",
                    "property": "volume_liters", "direction": "rising",
                    "window_seconds": 2, "minimum_delta": 10,
                }],
            })
            run = create_run(session, self.scenario_id, definition)
            start = datetime.now(timezone.utc)
            volumes = iter((100, 107, 115, 115))

            def snapshot(_manager, _scenario_id):
                return {"objects": [{"object_id": "tank-trend", "volume_liters": next(volumes)}]}

            with patch("services.field_operations.field_operation", side_effect=snapshot):
                for second in (0, 1, 2):
                    _sample_timed_objectives(session, self.manager, scenario, definition, run,
                                             start + timedelta(seconds=second))
                    session.refresh(run)
                tracking = json.loads(run.objective_tracking_json)
                outcome = _evaluate_objective(
                    session, self.manager, scenario, definition.objectives[0].model_dump(mode="json"),
                    start, now=start + timedelta(seconds=2), tracking=tracking,
                )
            self.assertEqual(len(tracking["level-rise"]["samples"]), 3)
            self.assertTrue(outcome["passed"])
            self.assertEqual(outcome["actual_delta"], 15)
            sparse = {"level-rise": {"samples": [
                {"at": (start - timedelta(seconds=3)).isoformat(), "value": 100},
                {"at": start.isoformat(), "value": 115},
            ]}}
            with patch("services.field_operations.field_operation", return_value={
                "objects": [{"object_id": "tank-trend", "volume_liters": 115}],
            }):
                incomplete = _evaluate_objective(
                    session, self.manager, scenario, definition.objectives[0].model_dump(mode="json"),
                    start, now=start, tracking=sparse,
                )
            self.assertFalse(incomplete["passed"])
            self.assertTrue(incomplete["waiting_window"])

    def test_field_trend_requires_numeric_property_and_bounded_window(self) -> None:
        with self.assertRaises(ValidationError):
            ExerciseDefinition.model_validate({"name": "Invalid trend", "objectives": [{
                "objective_id": "trend", "description": "No booleans", "kind": "field_trend",
                "object_id": "valve", "property": "state", "direction": "rising", "window_seconds": 2,
            }]})
        with self.assertRaises(ValidationError):
            ExerciseDefinition.model_validate({"name": "Long trend", "objectives": [{
                "objective_id": "trend", "description": "Bounded window", "kind": "field_trend",
                "object_id": "tank", "property": "volume_liters", "direction": "rising", "window_seconds": 301,
            }]})

    def test_field_trend_supports_rising_falling_and_stable_directions(self) -> None:
        with Session(self.engine) as session:
            scenario = session.get(ScenarioRecord, self.scenario_id)
            now = datetime.now(timezone.utc)
            cases = (("rising", 100, 110, 10, True),
                     ("falling", 110, 100, 10, True),
                     ("stable", 100, 101, 2, True))
            with patch("services.field_operations.field_operation", return_value={
                "objects": [{"object_id": "tank-trend", "volume_liters": 101}],
            }):
                for index, (direction, first, last, threshold, expected) in enumerate(cases):
                    definition = ExerciseDefinition.model_validate({
                        "name": f"{direction} trend", "objectives": [{
                            "objective_id": f"trend-{index}", "description": "Direction check",
                            "kind": "field_trend", "object_id": "tank-trend",
                            "property": "volume_liters", "direction": direction,
                            "window_seconds": 2, "minimum_delta": threshold,
                        }],
                    })
                    tracking = {f"trend-{index}": {"samples": [
                        {"at": (now - timedelta(seconds=2)).isoformat(), "value": first},
                        {"at": now.isoformat(), "value": last},
                    ]}}
                    result = _evaluate_objective(
                        session, self.manager, scenario,
                        definition.objectives[0].model_dump(mode="json"), now - timedelta(seconds=2),
                        now=now, tracking=tracking,
                    )
                    self.assertEqual(result["passed"], expected, direction)

    def test_field_sequence_objective_matches_process_conditions_in_order(self) -> None:
        tank = {"object_id": "tank-sequence", "name": "Tank", "type": "tank",
                "capacity_liters": 1000, "initial_level_liters": 500}
        with Session(self.engine) as session:
            scenario = session.get(ScenarioRecord, self.scenario_id)
            scenario.field_objects_json = json.dumps([tank])
            session.add(scenario)
            session.commit()
            definition = ExerciseDefinition.model_validate({
                "name": "High then low", "objectives": [{
                    "objective_id": "level-cycle", "description": "Level rises and then falls",
                    "kind": "field_sequence", "steps": [
                        {"object_id": "tank-sequence", "property": "volume_liters", "operator": "gte", "value": 600},
                        {"object_id": "tank-sequence", "property": "volume_liters", "operator": "lte", "value": 550},
                    ], "max_gap_seconds": 3,
                }],
            })
            run = create_run(session, self.scenario_id, definition)
            start = datetime.now(timezone.utc)
            volume = {"actual": 500}

            def snapshot(_manager, _scenario_id):
                return {"objects": [{"object_id": "tank-sequence", "volume_liters": volume["actual"]}]}

            with patch("services.field_operations.field_operation", side_effect=snapshot):
                _sample_timed_objectives(session, self.manager, scenario, definition, run, start)
                volume["actual"] = 650
                _sample_timed_objectives(session, self.manager, scenario, definition, run, start + timedelta(seconds=1))
                session.refresh(run)
                tracking = json.loads(run.objective_tracking_json)
                self.assertEqual(tracking["level-cycle"]["next_step_index"], 1)
                volume["actual"] = 520
                _sample_timed_objectives(session, self.manager, scenario, definition, run, start + timedelta(seconds=2))
                session.refresh(run)
                tracking = json.loads(run.objective_tracking_json)
                outcome = _evaluate_objective(
                    session, self.manager, scenario, definition.objectives[0].model_dump(mode="json"),
                    start, now=start + timedelta(seconds=2), tracking=tracking,
                )
            self.assertTrue(outcome["passed"])
            self.assertEqual(len(outcome["matched_sequence"]), 2)
            self.assertEqual(outcome["matched_sequence"][0]["actual"], 650)

            gap_definition = ExerciseDefinition.model_validate({
                "name": "Sequence timeout", "objectives": [{
                    "objective_id": "level-gap", "description": "Gap resets sequence",
                    "kind": "field_sequence", "steps": definition.objectives[0].steps,
                    "max_gap_seconds": 0.5,
                }],
            })
            gap_run = create_run(session, self.scenario_id, gap_definition)
            volume["actual"] = 650
            with patch("services.field_operations.field_operation", side_effect=snapshot):
                _sample_timed_objectives(session, self.manager, scenario, gap_definition, gap_run, start)
                volume["actual"] = 520
                _sample_timed_objectives(session, self.manager, scenario, gap_definition, gap_run,
                                         start + timedelta(seconds=2))
            session.refresh(gap_run)
            self.assertEqual(json.loads(gap_run.objective_tracking_json)["level-gap"]["next_step_index"], 0)

    def test_field_sequence_model_rejects_invalid_boolean_and_step_counts(self) -> None:
        base = {"objective_id": "sequence", "description": "Typed sequence",
                "kind": "field_sequence", "steps": [
                    {"object_id": "valve", "property": "state", "operator": "eq", "value": True},
                    {"object_id": "valve", "property": "state", "operator": "eq", "value": False},
                ]}
        ExerciseDefinition.model_validate({"name": "Valid booleans", "objectives": [base]})
        invalid_boolean = {**base, "steps": [dict(base["steps"][0], operator="gt"), base["steps"][1]]}
        with self.assertRaises(ValidationError):
            ExerciseDefinition.model_validate({"name": "Invalid boolean operator", "objectives": [invalid_boolean]})
        with self.assertRaises(ValidationError):
            ExerciseDefinition.model_validate({"name": "Too few steps", "objectives": [{**base, "steps": base["steps"][:1]}]})
        with self.assertRaises(ValidationError):
            ExerciseDefinition.model_validate({"name": "Too many steps", "objectives": [{**base, "steps": base["steps"] * 9}]})

    def test_timed_s7_point_objective_resets_when_condition_breaks(self) -> None:
        self.client.post(f"/api/scenarios/{self.scenario_id}/deploy")
        with Session(self.engine) as session:
            scenario = session.get(ScenarioRecord, self.scenario_id)
            definition = ExerciseDefinition.model_validate({
                "name": "Stable PLC output",
                "events": [{"event_id": "finish", "at_seconds": 5, "kind": "node_lifecycle",
                            "node_id": "plc-1", "action": "stop"}],
                "objectives": [{"objective_id": "valve-hold", "description": "Valve remains open",
                                "kind": "point_value", "node_id": "plc-1", "address": "DB1.DBX0.0",
                                "operator": "eq", "value": True, "duration_seconds": 2}],
            })
            run = create_run(session, self.scenario_id, definition)
            start = datetime.now(timezone.utc)
            run.status = "running"
            run.started_at = start
            session.add(run)
            session.commit()
            point_read = lambda value: {"values": [{"address": "DB1.DBX0.0", "value": value}]}
            with patch("services.exercise_runtime.read_plc_values", side_effect=[
                point_read(True), point_read(False), point_read(True), point_read(True),
                point_read(True), point_read(True),
            ]):
                run_due_exercises(session, self.manager, now=start)
                session.refresh(run)
                self.assertEqual(run.status, "running")
                self.assertTrue(json.loads(run.objective_tracking_json)["valve-hold"])
                run_due_exercises(session, self.manager, now=start + timedelta(seconds=1))
                session.refresh(run)
                self.assertIsNone(json.loads(run.objective_tracking_json)["valve-hold"])
                run_due_exercises(session, self.manager, now=start + timedelta(seconds=2))
                session.refresh(run)
                condition_since = json.loads(run.objective_tracking_json)["valve-hold"]
                self.assertEqual(datetime.fromisoformat(condition_since), start + timedelta(seconds=2))
                run_due_exercises(session, self.manager, now=start + timedelta(seconds=4.1))
                session.refresh(run)
                self.assertEqual(run.status, "running")
                run_due_exercises(session, self.manager, now=start + timedelta(seconds=6))
                session.refresh(run)
                self.assertEqual(run.status, "completed")
                self.assertTrue(run.passed)
                self.assertEqual(run.score, 100)
                outcome = run.to_read_model().objective_results[0]
            self.assertGreaterEqual(outcome["sustained_seconds"], 2)

    def test_remote_point_objective_does_not_credit_stale_communication(self) -> None:
        with Session(self.engine) as session:
            scenario = session.get(ScenarioRecord, self.scenario_id)
            definition = ExerciseDefinition.model_validate({
                "name": "Fresh remote input only", "objectives": [{
                    "objective_id": "remote-open", "description": "Remote valve is open",
                    "kind": "point_value", "node_id": "plc-1", "address": "DB1.DBX0.0",
                    "operator": "eq", "value": True, "duration_seconds": 2,
                }],
            })
            run = create_run(session, self.scenario_id, definition)
            start = datetime.now(timezone.utc)
            run.status = "running"
            run.started_at = start
            session.add(run)
            session.commit()
            stale = {"values": [{"address": "DB1.DBX0.0", "value": True,
                                 "communication": {"quality": "stale", "age_ms": 1800}}]}
            fresh = {"values": [{"address": "DB1.DBX0.0", "value": True,
                                 "communication": {"quality": "good", "age_ms": 12}}]}
            objective = definition.objectives[0].model_dump(mode="json")
            with patch("services.exercise_runtime.read_plc_values", side_effect=[stale, stale, fresh, stale]):
                outcome = _evaluate_objective(session, self.manager, scenario, objective, start,
                                              now=start + timedelta(seconds=3), tracking={})
                self.assertFalse(outcome["passed"])
                self.assertEqual(outcome["quality"], "stale")

                _sample_timed_objectives(session, self.manager, scenario, definition, run, start)
                session.refresh(run)
                self.assertNotIn("remote-open", json.loads(run.objective_tracking_json))

                _sample_timed_objectives(session, self.manager, scenario, definition, run,
                                         start + timedelta(seconds=1))
                session.refresh(run)
                self.assertIsNotNone(json.loads(run.objective_tracking_json)["remote-open"])

                _sample_timed_objectives(session, self.manager, scenario, definition, run,
                                         start + timedelta(seconds=1.5))
                session.refresh(run)
                self.assertIsNone(json.loads(run.objective_tracking_json)["remote-open"])

            with patch("services.exercise_runtime.read_plc_values", return_value={
                "values": [{"address": "DB1.DBX0.0", "value": True, "communication": {}}],
            }):
                unknown = _evaluate_objective(session, self.manager, scenario, objective, start,
                                              now=start + timedelta(seconds=3), tracking={})
            self.assertFalse(unknown["passed"])
            self.assertEqual(unknown["quality"], "unknown")

    def test_timed_modbus_point_objective_uses_native_point_reader(self) -> None:
        with Session(self.engine) as session:
            created_scenario = ScenarioService(session).create(ScenarioCreate.model_validate({
                "name": "Timed Modbus objective", "network": {"subnet": "172.30.22.0/24"},
                "devices": [{"node_id": "modbus", "device_type": "modbus-tcp", "name": "Modbus",
                             "ip": "172.30.22.10", "config": {"points": [
                                 {"type": "coil", "name": "Pump", "address": 0},
                             ]}}],
            }))
            scenario = session.get(ScenarioRecord, str(created_scenario.id))
            scenario.status = "running"
            session.add(scenario)
            session.commit()
            definition = ExerciseDefinition.model_validate({
                "name": "Stable Modbus coil", "objectives": [{
                    "objective_id": "coil-hold", "description": "Coil remains on",
                    "kind": "point_value", "node_id": "modbus", "address": "coil:0",
                    "operator": "eq", "value": True, "duration_seconds": 3,
                }],
            })
            run = create_run(session, str(scenario.id), definition)
            start = datetime.now(timezone.utc)
            run.status = "running"
            run.started_at = start
            session.add(run)
            session.commit()
            with patch("services.exercise_runtime.read_modbus_values", return_value={
                "values": [{"address": "coil:0", "value": True}],
            }) as read_point:
                _sample_timed_objectives(session, self.manager, scenario, definition, run, start)
                tracking = json.loads(run.objective_tracking_json)
                objective = definition.objectives[0].model_dump(mode="json")
                waiting = _evaluate_objective(session, self.manager, scenario, objective, start,
                                              now=start + timedelta(seconds=2), tracking=tracking)
                passed = _evaluate_objective(session, self.manager, scenario, objective, start,
                                             now=start + timedelta(seconds=3.1), tracking=tracking)
            self.assertTrue(waiting["waiting_duration"])
            self.assertFalse(waiting["passed"])
            self.assertTrue(passed["passed"])
            self.assertGreaterEqual(passed["sustained_seconds"], 3)
            read_point.assert_called()

    def test_scheduler_keeps_run_active_until_timed_condition_is_satisfied(self) -> None:
        tank = {"object_id": "tank-1", "name": "Tank", "type": "tank",
                "capacity_liters": 1000, "initial_level_liters": 500}
        with Session(self.engine) as session:
            scenario = session.get(ScenarioRecord, self.scenario_id)
            scenario.field_objects_json = json.dumps([tank])
            scenario.status = "running"
            session.add(scenario)
            session.commit()
            definition = ExerciseDefinition.model_validate({
                "name": "Estabilidad del tanque", "objectives": [{
                    "objective_id": "level-hold", "description": "Nivel estable dos segundos",
                    "kind": "field_value", "object_id": "tank-1", "property": "volume_liters",
                    "operator": "gte", "value": 400, "duration_seconds": 2,
                }],
            })
            run = create_run(session, self.scenario_id, definition)
            start = datetime.now(timezone.utc)
            run.status = "running"
            run.started_at = start
            session.add(run)
            session.commit()
            with patch("services.field_operations.field_operation", return_value={
                "objects": [{"object_id": "tank-1", "volume_liters": 500}],
            }):
                run_due_exercises(session, self.manager, now=start)
                session.refresh(run)
                self.assertEqual(run.status, "running")
                self.assertTrue(run.to_read_model().objective_results[0]["waiting_duration"])
                run_due_exercises(session, self.manager, now=start + timedelta(seconds=2.1))
                session.refresh(run)
                self.assertEqual(run.status, "completed")
                self.assertTrue(run.passed)
                self.assertEqual(run.score, 100)

    def test_async_scheduler_applies_due_timeline_without_request_polling(self) -> None:
        self.client.post(f"/api/scenarios/{self.scenario_id}/deploy")
        definition = {
            "name": "Scheduler test",
            "events": [{"event_id": "stop", "at_seconds": 0, "kind": "node_lifecycle", "node_id": "plc-1", "action": "stop"}],
            "objectives": [{"objective_id": "logged", "description": "stop audited", "kind": "audit_event", "event_type": "scenario.node_stop"}],
        }
        created = self.client.post(f"/api/scenarios/{self.scenario_id}/exercises", json=definition)
        exercise_id = created.json()["id"]
        self.client.post(f"/api/scenarios/{self.scenario_id}/exercises/{exercise_id}/start")

        async def wait_for_scheduler() -> None:
            stop = asyncio.Event()
            task = asyncio.create_task(exercise_scheduler(stop, self.engine, self.manager))
            try:
                for _ in range(40):
                    await asyncio.sleep(0.05)
                    with Session(self.engine) as session:
                        run = session.get(ExerciseRun, exercise_id)
                        if run.status != "running":
                            self.assertEqual(run.status, "completed")
                            return
                self.fail("scheduler did not execute its due event")
            finally:
                stop.set()
                await task

        asyncio.run(wait_for_scheduler())
        self.assertEqual(self.manager.actions, [(self.scenario_id, "plc-1", "stop")])


if __name__ == "__main__":
    unittest.main()
