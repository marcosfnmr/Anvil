from __future__ import annotations

import sys
import threading
import time
import unittest
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select
BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from devices import DEVICE_REGISTRY  # noqa: E402
from models.scenario import ScenarioCreate  # noqa: E402
from models.scada import (ScadaConfigInput, ScenarioScadaAlarmPending, ScenarioScadaAlarmState,
                          ScenarioScadaSample)  # noqa: E402
from models.audit import ScenarioAuditEvent  # noqa: E402
from services.scada_historian import (  # noqa: E402
    collect_scada_history_once,
    merge_persisted_scada_alarms,
    persist_scada_alarm_acknowledgement,
    persist_scada_alarms,
    read_scada_alarm_pending,
    persist_scada_samples,
    read_scada_aggregates,
    read_scada_samples,
)
from services.scada_operations import scada_operation  # noqa: E402
from services.scenario_service import ScenarioService  # noqa: E402
from models.db import get_session  # noqa: E402
from dependencies import get_optional_docker_manager  # noqa: E402
from routes.scenarios import get_docker_manager  # noqa: E402
from main import create_app  # noqa: E402
from plc.scada_server import Collector  # noqa: E402


def sample_scenario(*, scada_zone: str = "default", plc_zone: str = "default",
                    history_capacity: int = 600) -> ScenarioCreate:
    return ScenarioCreate.model_validate({
        "name": "SCADA test", "network": {"subnet": "172.30.10.0/24", "zones": ([
            {"zone_id": "ops", "name": "Operations", "purdue_level": "L2", "subnet": "172.30.20.0/24"},
            {"zone_id": "control", "name": "Control", "purdue_level": "L1", "subnet": "172.30.21.0/24"},
        ] if scada_zone != "default" or plc_zone != "default" else [])},
        "devices": [
            {"node_id": "scada", "device_type": "scada", "name": "SCADA", "ip": "172.30.20.10" if scada_zone == "ops" else "172.30.10.10", "zone_id": scada_zone,
             "config": {"target_node_ids": "plc", "poll_interval_ms": 250,
                        "history_capacity": history_capacity, "alarms": []}},
            {"node_id": "plc", "device_type": "plc-s7", "name": "PLC", "ip": "172.30.21.10" if plc_zone == "control" else "172.30.10.11", "zone_id": plc_zone,
             "config": {"components": [{"type": "analog_sensor", "name": "Level"}]}},
        ],
    })


class ScadaTests(unittest.TestCase):
    def test_pending_alarm_delay_survives_runtime_recreation(self) -> None:
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(engine)
        pending_since = "2026-10-08T12:00:00+00:00"
        pending_alarm = {
            "target_node_id": "plc", "address": "DB1.DBD0", "active": False,
            "pending": True, "condition_since": pending_since, "quality": "good",
            "triggered_at": None,
        }
        try:
            with Session(engine) as session:
                persist_scada_alarms(session, "scenario", "scada", [pending_alarm])
                restored = read_scada_alarm_pending(session, "scenario", "scada")
                self.assertEqual(restored, [{"target_node_id": "plc", "address": "DB1.DBD0",
                                             "condition_since": pending_since}])
                self.assertEqual(len(session.exec(select(ScenarioScadaAlarmPending)).all()), 1)

            restarted = Collector.__new__(Collector)
            restarted.config = {"alarms": [{"target_node_id": "plc", "address": "DB1.DBD0",
                                               "above": 10, "below": None, "delay_ms": 5000}]}
            restarted.alarms = {}
            restarted.lock = threading.RLock()
            self.assertEqual(restarted.restore_pending_alarms(restored), {"restored": 1})
            self.assertEqual(restarted.alarms["plc|DB1.DBD0"]["condition_since"], pending_since)
            restarted._evaluate_alarms([
                {"node_id": "plc", "address": "DB1.DBD0", "value": 12.0, "quality": "good"}
            ], "2026-10-08T12:00:06+00:00")
            self.assertTrue(restarted.alarms["plc|DB1.DBD0"]["active"])

            with Session(engine) as session:
                warming_up = {**pending_alarm, "quality": "unknown", "value": None}
                persist_scada_alarms(session, "scenario", "scada", [warming_up])
                self.assertEqual(read_scada_alarm_pending(session, "scenario", "scada")[0]["condition_since"],
                                 pending_since)
                bad_quality = {**pending_alarm, "pending": False, "condition_since": None,
                               "quality": "bad", "value": None}
                persist_scada_alarms(session, "scenario", "scada", [bad_quality])
                self.assertEqual(read_scada_alarm_pending(session, "scenario", "scada"), [])
                persist_scada_alarms(session, "scenario", "scada", [pending_alarm])
                normal = {**pending_alarm, "pending": False, "condition_since": None,
                          "quality": "good", "value": 5.0}
                persist_scada_alarms(session, "scenario", "scada", [normal])
                self.assertEqual(read_scada_alarm_pending(session, "scenario", "scada"), [])
                self.assertEqual(session.exec(select(ScenarioScadaAlarmPending)).all(), [])
        finally:
            engine.dispose()

    def test_durable_history_deduplicates_runtime_samples_and_groups_points(self) -> None:
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(engine)
        sample = {"timestamp": "2026-10-07T12:00:00+00:00", "values": [
            {"node_id": "plc", "address": "DB1.DBD0", "name": "Level", "value": 12.5, "quality": "good"},
            {"node_id": "plc", "address": "DB1.DBX4.0", "name": "Pump", "value": True, "quality": "good"},
        ]}
        with Session(engine) as session:
            self.assertEqual(persist_scada_samples(session, "scenario", "scada", [sample]), 2)
            self.assertEqual(persist_scada_samples(session, "scenario", "scada", [sample]), 0)
            history = read_scada_samples(session, "scenario", "scada", 10)
            self.assertEqual(len(history), 1)
            self.assertEqual({item["address"] for item in history[0]["values"]}, {"DB1.DBD0", "DB1.DBX4.0"})
            level = next(item for item in history[0]["values"] if item["address"] == "DB1.DBD0")
            self.assertEqual(level["value"], 12.5)
        engine.dispose()

    def test_aggregated_history_groups_numeric_and_bool_values_and_excludes_bad_quality(self) -> None:
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(engine)
        start = datetime(2026, 10, 7, 12, tzinfo=timezone.utc)
        samples = []
        for offset, level, quality, running in (
            (1, 12.0, "good", True), (5, 16.0, "good", False),
            (8, 1000.0, "bad", True), (22, 24.0, "stale", False),
        ):
            samples.append({"timestamp": (start + timedelta(seconds=offset)).isoformat(), "values": [
                {"node_id": "plc", "address": "DB1.DBD0", "name": "Level", "data_type": "REAL",
                 "unit": "%", "value": level, "quality": quality},
                {"node_id": "plc", "address": "DB1.DBX4.0", "name": "Running", "data_type": "BOOL",
                 "value": running, "quality": "good"},
                {"node_id": "unselected", "address": "DB1.DBD0", "name": "Other", "value": 99.0,
                 "quality": "good"},
            ]})
        with Session(engine) as session:
            persist_scada_samples(session, "scenario", "scada", samples)
            result = read_scada_aggregates(session, "scenario", "scada", start, start + timedelta(seconds=40),
                                           20, {"plc"})
            level = next(item for item in result["series"] if item["address"] == "DB1.DBD0")
            first, second = level["buckets"]
            self.assertEqual((first["samples"], first["good_samples"], first["bad_samples"]), (3, 2, 1))
            self.assertEqual((first["min"], first["max"], first["average"]), (12.0, 16.0, 14.0))
            self.assertEqual((second["good_samples"], second["bad_samples"], second["average"]), (0, 1, None))
            running = next(item for item in result["series"] if item["address"] == "DB1.DBX4.0")
            self.assertEqual((running["buckets"][0]["true_samples"], running["buckets"][0]["false_samples"]), (2, 1))
            with self.assertRaisesRegex(ValueError, "more than 1 point observations"):
                read_scada_aggregates(session, "scenario", "scada", start, start + timedelta(seconds=40),
                                      20, {"plc"}, max_rows=1)
        engine.dispose()

    def test_historian_collects_only_running_scenarios_and_is_idempotent(self) -> None:
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(engine)
        with Session(engine) as session:
            scenario = ScenarioService(session).create(sample_scenario(history_capacity=4096))
            ScenarioService(session).set_status(str(scenario.id), "running")
        runtime_sample = {"timestamp": "2026-10-07T12:00:00+00:00", "values": [
            {"node_id": "plc", "address": "DB1.DBD0", "name": "Level", "value": 50.0, "quality": "good"},
        ]}
        with patch("services.scada_historian.scada_operation", return_value={"samples": [runtime_sample]}) as poll:
            self.assertEqual(collect_scada_history_once(engine, Mock()), 1)
            self.assertEqual(collect_scada_history_once(engine, Mock()), 0)
        self.assertTrue(any(item.args[2:4] == ("scada", "history/pending")
                            and item.args[4] == {"limit": 10_000} for item in poll.call_args_list))
        self.assertTrue(any(item.args[2:4] == ("scada", "alarms") for item in poll.call_args_list))
        with Session(engine) as session:
            history = read_scada_samples(session, str(scenario.id), "scada", 10)
            self.assertEqual(history[0]["values"][0]["value"], 50.0)
            ScenarioService(session).set_status(str(scenario.id), "stopped")
            self.assertEqual(collect_scada_history_once(engine, Mock()), 0)
        engine.dispose()

    def test_historian_acknowledges_durable_buffer_only_after_sqlite_persist(self) -> None:
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(engine)
        with Session(engine) as session:
            scenario = ScenarioService(session).create(sample_scenario())
            scenario_id = str(scenario.id)
            ScenarioService(session).set_status(scenario_id, "running")
        item = {"timestamp": "2026-10-09T12:00:00+00:00", "values": [
            {"node_id": "plc", "address": "DB1.DBD0", "value": 42.0, "quality": "good"},
        ]}
        ack_calls = 0

        def operation(_manager, _scenario, _node, path, _payload=None):
            nonlocal ack_calls
            if path == "history/pending":
                return {"samples": [item], "cursor": 7, "dropped_samples": 0}
            if path == "history/acknowledge":
                with Session(engine) as session:
                    rows = session.exec(select(ScenarioScadaSample).where(
                        ScenarioScadaSample.scenario_id == scenario_id)).all()
                self.assertEqual(len(rows), 1, "the backend must commit samples before acknowledging the cursor")
                ack_calls += 1
                if ack_calls == 1:
                    raise RuntimeError("temporary acknowledgement failure")
                return {"pending_count": 0, "dropped_samples": 0}
            return {"alarms": [], "ready": True}

        with patch("services.scada_historian.scada_operation", side_effect=operation):
            collect_scada_history_once(engine, Mock())
            with Session(engine) as session:
                self.assertEqual(len(session.exec(select(ScenarioScadaSample).where(
                    ScenarioScadaSample.scenario_id == scenario_id)).all()), 1)
            collect_scada_history_once(engine, Mock())
        with Session(engine) as session:
            rows = session.exec(select(ScenarioScadaSample).where(
                ScenarioScadaSample.scenario_id == scenario_id)).all()
        self.assertEqual(len(rows), 1, "retry after a lost ack must deduplicate the persisted sample")
        self.assertEqual(ack_calls, 2)
        engine.dispose()

    def test_historian_audits_protocol_quality_transitions_without_poll_duplicates(self) -> None:
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(engine)
        with Session(engine) as session:
            scenario = ScenarioService(session).create(sample_scenario())
            scenario_id = str(scenario.id)
            ScenarioService(session).set_status(scenario_id, "running")
        timestamp = "2026-10-08T10:00:00+00:00"
        good = {"samples": [{"timestamp": timestamp, "values": [
            {"node_id": "plc", "address": "DB1.DBD0", "value": 20.0, "quality": "good"},
        ]}]}
        degraded = {"samples": [{"timestamp": "2026-10-08T10:00:05+00:00", "values": [
            {"node_id": "plc", "address": "DB1.DBD0", "value": None, "quality": "stale"},
        ]}]}
        history_results = [good, good, degraded, RuntimeError("protocol unavailable"), good]

        def operation(_manager, _scenario, _node, path, _payload=None):
            if path == "history/pending":
                result = history_results.pop(0)
                if isinstance(result, Exception):
                    raise result
                return result
            return {"alarms": [], "ready": True}

        with patch("services.scada_historian.scada_operation", side_effect=operation):
            for _ in range(5):
                collect_scada_history_once(engine, Mock())
        with Session(engine) as session:
            events = session.exec(select(ScenarioAuditEvent).where(
                ScenarioAuditEvent.scenario_id == scenario_id,
                ScenarioAuditEvent.event_type.like("protocol.read_%"),
            ).order_by(ScenarioAuditEvent.id)).all()
        self.assertEqual([event.event_type for event in events], [
            "protocol.read_good", "protocol.read_degraded", "protocol.read_failed", "protocol.read_good",
        ])
        self.assertTrue(all("value" not in json.loads(event.details_json) for event in events))
        engine.dispose()

    def test_schema_rejects_empty_duplicate_targets_and_invalid_alarms(self) -> None:
        for config in (
            {"target_node_ids": "", "alarms": []},
            {"target_node_ids": "plc,plc", "alarms": []},
            {"target_node_ids": "plc", "alarms": [{"target_node_id": "plc", "address": "DB1.DBD0"}]},
        ):
            with self.subTest(config=config), self.assertRaises(ValueError):
                ScadaConfigInput.model_validate(config)
        base_rule = {"target_node_id": "plc", "address": "DB1.DBD0", "above": 10}
        valid = ScadaConfigInput.model_validate({"target_node_ids": "plc", "alarms": [base_rule]})
        self.assertEqual(valid.alarms[0].delay_ms, 0)
        self.assertEqual(valid.durable_history_capacity, 100_000)
        self.assertEqual(valid.durable_history_max_bytes, 134_217_728)
        for invalid in ({"durable_history_capacity": 999}, {"durable_history_max_bytes": 536_870_913}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                ScadaConfigInput.model_validate({"target_node_ids": "plc", **invalid})
        for delay in (-1, 86_400_001, True, 1.5, "100"):
            with self.subTest(delay_ms=delay), self.assertRaises(ValueError):
                ScadaConfigInput.model_validate({"target_node_ids": "plc", "alarms": [{**base_rule, "delay_ms": delay}]})

    def test_scada_alarm_delay_requires_sustained_good_condition(self) -> None:
        collector = Collector.__new__(Collector)
        collector.config = {"alarms": [{"target_node_id": "plc", "address": "DB1.DBD0",
                                         "above": 10, "below": None, "delay_ms": 1000}]}
        collector.alarms = {}
        values = [{"node_id": "plc", "address": "DB1.DBD0", "value": 12.5, "quality": "good"}]
        timestamp = datetime(2026, 10, 8, tzinfo=timezone.utc)
        stamp = lambda value: value.isoformat()
        collector._evaluate_alarms(values, stamp(timestamp))
        self.assertTrue(collector.alarms["plc|DB1.DBD0"]["pending"])
        self.assertFalse(collector.alarms["plc|DB1.DBD0"]["active"])
        timestamp += timedelta(milliseconds=900)
        collector._evaluate_alarms(values, stamp(timestamp))
        self.assertTrue(collector.alarms["plc|DB1.DBD0"]["pending"])
        timestamp += timedelta(milliseconds=100)
        collector._evaluate_alarms(values, stamp(timestamp))
        self.assertTrue(collector.alarms["plc|DB1.DBD0"]["active"])
        timestamp += timedelta(milliseconds=100)
        collector._evaluate_alarms([{**values[0], "quality": "bad", "value": None}], stamp(timestamp))
        timestamp += timedelta(milliseconds=100)
        collector._evaluate_alarms(values, stamp(timestamp))
        self.assertTrue(collector.alarms["plc|DB1.DBD0"]["pending"])
        self.assertFalse(collector.alarms["plc|DB1.DBD0"]["active"])

    def test_scada_is_registered_as_an_independent_device(self) -> None:
        self.assertIn("scada", DEVICE_REGISTRY)
        self.assertEqual(DEVICE_REGISTRY["scada"].label, "SCADA")

    def test_target_in_another_zone_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "different zones"):
            sample_scenario(scada_zone="ops", plc_zone="control")

    def test_runtime_compiles_native_tag_map_for_selected_target(self) -> None:
        runtime = ScenarioService._build_runtime_devices(sample_scenario())
        scada = next(item for item in runtime if item["device_type"] == "scada")
        self.assertEqual(scada["config"]["targets"][0]["host"], "172.30.10.11")
        tag = scada["config"]["targets"][0]["tags"][0]
        self.assertEqual((tag["name"], tag["address"], tag["data_type"]), ("Level", "DB1.DBD0", "REAL"))

    def test_runtime_preserves_rtu_serial_transport_for_scada_target(self) -> None:
        scenario = ScenarioCreate.model_validate({
            "name": "RTU SCADA", "network": {"subnet": "172.30.10.0/24"},
            "devices": [
                {"node_id": "scada", "device_type": "scada", "name": "SCADA", "ip": "172.30.10.10",
                 "config": {"target_node_ids": "rtu", "alarms": []}},
                {"node_id": "rtu", "device_type": "modbus-tcp", "name": "RTU", "ip": "172.30.10.11",
                 "config": {"transport": "rtu", "port": 1502, "baudrate": 9600, "points": [
                     {"type": "holding_register", "name": "Speed"},
                 ]}},
            ],
        })
        runtime = ScenarioService._build_runtime_devices(scenario)
        target = next(item for item in runtime if item["device_type"] == "scada")["config"]["targets"][0]
        self.assertEqual((target["transport"], target["port"], target["baudrate"]), ("rtu", 1502, 9600))

    def test_collector_keeps_bounded_history_evaluates_and_acknowledges_alarm(self) -> None:
        config = {"poll_interval_ms": 250, "history_capacity": 2, "alarms": [
            {"target_node_id": "plc", "address": "DB1.DBD0", "above": 10, "below": None}],
            "targets": [{"node_id": "plc", "tags": [{"address": "DB1.DBD0", "name": "Level", "data_type": "REAL"}]}]}
        with patch.object(Collector, "_read_target", return_value=[{"node_id": "plc", "address": "DB1.DBD0", "name": "Level", "value": 12.5, "quality": "good"}]):
            collector = Collector(config)
            try:
                deadline = time.monotonic() + 1
                while collector.snapshot()["timestamp"] is None and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(collector.snapshot()["alarms"][0]["active"])
                alarm = collector.acknowledge("plc", "DB1.DBD0")
                self.assertTrue(alarm["acknowledged"])
                time.sleep(0.55)
                self.assertEqual(len(collector.read_history(10)["samples"]), 2)
            finally:
                collector.close()

    def test_alarm_acknowledgement_survives_runtime_recreation_until_alarm_clears(self) -> None:
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(engine)
        first_trigger = "2026-10-07T12:00:00+00:00"
        active = {"target_node_id": "plc", "address": "DB1.DBD0", "active": True,
                  "triggered_at": first_trigger, "acknowledged": False, "acknowledged_at": None}
        with Session(engine) as session:
            persist_scada_alarms(session, "scenario", "scada", [active])
            persist_scada_alarm_acknowledgement(session, "scenario", "scada", {
                **active, "acknowledged": True, "acknowledged_at": "2026-10-07T12:00:01+00:00",
            })
        # A recreated collector reports the still active alarm as new and unacknowledged.
        restarted = {**active, "triggered_at": "2026-10-07T12:01:00+00:00"}
        with Session(engine) as session:
            persist_scada_alarms(session, "scenario", "scada", [], ready=False)
            during_startup = merge_persisted_scada_alarms(session, "scenario", "scada", [], ready=False)
            self.assertTrue(during_startup[0]["stale"])
            self.assertTrue(during_startup[0]["acknowledged"])
            persist_scada_alarms(session, "scenario", "scada", [restarted])
            visible = merge_persisted_scada_alarms(session, "scenario", "scada", [restarted])[0]
            self.assertTrue(visible["acknowledged"])
            self.assertEqual(visible["triggered_at"], first_trigger)
            self.assertEqual(len(session.execute(select(ScenarioScadaAlarmState)).scalars().all()), 1)
            # Once normal is observed, the next activation is a fresh alarm.
            bad_quality = {**restarted, "active": False, "value": None, "quality": "bad"}
            persist_scada_alarms(session, "scenario", "scada", [bad_quality])
            still_stale = merge_persisted_scada_alarms(session, "scenario", "scada", [bad_quality])[0]
            self.assertTrue(still_stale["stale"])
            normal = {**restarted, "active": False, "quality": "good", "value": 5.0}
            persist_scada_alarms(session, "scenario", "scada", [normal])
            reactivated = {**active, "triggered_at": "2026-10-07T12:02:00+00:00"}
            persist_scada_alarms(session, "scenario", "scada", [reactivated])
            visible = merge_persisted_scada_alarms(session, "scenario", "scada", [reactivated])[0]
            self.assertFalse(visible["acknowledged"])
            self.assertEqual(visible["triggered_at"], reactivated["triggered_at"])
        engine.dispose()

    def test_operation_uses_managed_container_and_bounds_history_limit(self) -> None:
        manager = Mock()
        container = Mock()
        manager.client.containers.list.return_value = [container]
        container.labels = {"anvil.node_id": "scada"}
        container.exec_run.return_value = Mock(exit_code=0, output=b'{"samples": []}')
        result = scada_operation(manager, "scenario", "scada", "history", {"limit": 99999})
        self.assertEqual(result, {"samples": []})
        self.assertIn("limit=10000", container.exec_run.call_args.args[0][3])
        container.exec_run.return_value = Mock(exit_code=0, output=b'{"pending_count":0,"dropped_samples":0}')
        acknowledged = scada_operation(manager, "scenario", "scada", "history/acknowledge", {"cursor": 12})
        self.assertEqual(acknowledged["pending_count"], 0)
        command = container.exec_run.call_args.args[0]
        self.assertEqual(command[3], "history/acknowledge")
        self.assertEqual(json.loads(command[4]), {"cursor": 12})
        self.assertIn("SCADA_HISTORY_ACK_TOKEN", command[2])
        self.assertIn("headers['Authorization']='Bearer '+token", command[2])

    def test_api_reads_scada_snapshot_and_audits_alarm_acknowledgement(self) -> None:
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(engine)
        snapshot = {"timestamp": "2026-10-06T12:00:00+00:00", "values": [{
            "node_id": "plc", "address": "DB1.DBX4.0", "name": "Bomba", "data_type": "BOOL", "value": False, "quality": "good",
        }, {
            "node_id": "plc", "address": "DB1.DBD6", "name": "Consigna", "data_type": "REAL", "value": 0.0, "quality": "good",
        }, {
            "node_id": "plc", "address": "DB1.DBD10", "name": "Nivel", "data_type": "REAL", "value": 120.0, "quality": "good",
        }], "alarms": [
            {"target_node_id": "plc", "address": "DB1.DBD0", "active": True, "acknowledged": False}], "errors": {}}
        container = Mock()
        container.labels = {"anvil.node_id": "scada"}
        persisted_sample = {"timestamp": "2026-10-06T12:00:01+00:00", "values": [
            {"node_id": "plc", "address": "DB1.DBD0", "name": "=Level", "data_type": "REAL",
             "unit": "L", "value": 12.5, "quality": "good"},
        ]}
        container.exec_run.side_effect = [Mock(exit_code=0, output=b'{"restored": 1}'),
                                          Mock(exit_code=0, output=json.dumps(snapshot).encode()),
                                          Mock(exit_code=0, output=json.dumps({"samples": [persisted_sample]}).encode()),
                                          Mock(exit_code=0, output=b'{"acknowledged": true}')]
        manager = Mock()
        manager.deploy.side_effect = lambda scenario_id, *_: {"scenario_id": scenario_id, "status": "running", "containers": []}
        manager.client.containers.list.return_value = [container]
        app = create_app(initialize_database=False)

        def session_override():
            with Session(engine) as session:
                yield session

        app.dependency_overrides[get_session] = session_override
        app.dependency_overrides[get_docker_manager] = lambda: manager
        app.dependency_overrides[get_optional_docker_manager] = lambda: manager
        with TestClient(app) as client:
            created = client.post("/api/scenarios", json={
                "name": "SCADA", "network": {"subnet": "172.30.10.0/24"},
                "devices": [
                    {"node_id": "scada", "device_type": "scada", "name": "SCADA", "ip": "172.30.10.10",
                     "config": {"target_node_ids": "plc", "alarms": [{"target_node_id": "plc", "address": "DB1.DBD0", "above": 10}]}},
                    {"node_id": "plc", "device_type": "plc-s7", "name": "PLC", "ip": "172.30.10.11",
                    "config": {"components": [
                        {"type": "analog_sensor", "name": "Level"},
                        {"type": "valve", "name": "Bomba"},
                        {"type": "io_channel", "name": "Consigna", "direction": "output", "data_type": "REAL", "unit": "%"},
                        {"type": "io_channel", "name": "Nivel", "direction": "input", "data_type": "REAL", "unit": "%", "min_value": 0, "max_value": 100},
                    ]}},
                ],
            })
            self.assertEqual(created.status_code, 201, created.text)
            scenario_id = created.json()["id"]
            manager.stop.return_value = {"scenario_id": scenario_id, "status": "stopped", "containers": []}
            deployed = client.post(f"/api/scenarios/{scenario_id}/deploy")
            self.assertEqual(deployed.status_code, 200, deployed.text)
            with Session(engine) as session:
                session.add(ScenarioScadaAlarmPending(
                    scenario_id=scenario_id, scada_node_id="scada", target_node_id="plc",
                    address="DB1.DBD0", condition_since="2026-10-06T11:59:59+00:00",
                ))
                session.commit()
            read = client.get(f"/api/scenarios/{scenario_id}/scada/scada/snapshot")
            self.assertEqual(read.status_code, 200, read.text)
            self.assertTrue(any(call.args[0][3] == "alarms/restore" for call in container.exec_run.call_args_list))
            self.assertEqual(read.json()["values"][0]["access"], "read_write")
            out_of_range = next(item for item in read.json()["values"] if item.get("name", item.get("component_name")) == "Nivel")
            self.assertEqual(out_of_range["quality"], "out_of_range")
            self.assertEqual(out_of_range["protocol_quality"], "good")
            self.assertEqual(out_of_range["range_diagnostic"]["maximum"], 100)
            history = client.get(f"/api/scenarios/{scenario_id}/scada/scada/history")
            self.assertEqual(history.status_code, 200, history.text)
            self.assertTrue(history.json()["live"])
            self.assertEqual(history.json()["samples"][0]["values"][0]["value"], 12.5)
            aggregate = client.get(
                f"/api/scenarios/{scenario_id}/scada/scada/history/aggregate",
                params={"start": "2026-10-06T12:00:00Z", "end": "2026-10-06T12:01:00Z", "bucket_seconds": 10},
            )
            self.assertEqual(aggregate.status_code, 200, aggregate.text)
            self.assertEqual(aggregate.json()["observations"], 1)
            self.assertEqual(aggregate.json()["series"][0]["buckets"][0]["average"], 12.5)
            self.assertEqual(aggregate.json()["series"][0]["buckets"][0]["good_samples"], 1)
            invalid_aggregate = client.get(
                f"/api/scenarios/{scenario_id}/scada/scada/history/aggregate",
                params={"start": "2026-10-06T12:00:00", "end": "2026-10-06T12:01:00Z"},
            )
            self.assertEqual(invalid_aggregate.status_code, 422)
            with patch("routes.plc_operations.scada_operation") as write_point:
                write_point.side_effect = [
                    {"node_id": "plc", "address": "DB1.DBX4.0", "value": True, "quality": "good"},
                    {"node_id": "plc", "address": "DB1.DBD6", "value": 47.5, "quality": "good"},
                ]
                write = client.post(f"/api/scenarios/{scenario_id}/scada/scada/write", json={
                    "target_node_id": "plc", "address": "DB1.DBX4.0", "value": True,
                })
                real_write = client.post(f"/api/scenarios/{scenario_id}/scada/scada/write", json={
                    "target_node_id": "plc", "address": "DB1.DBD6", "value": 47.5,
                })
                unselected = client.post(f"/api/scenarios/{scenario_id}/scada/scada/write", json={
                    "target_node_id": "not-selected", "address": "DB1.DBX4.0", "value": True,
                })
                read_only = client.post(f"/api/scenarios/{scenario_id}/scada/scada/write", json={
                    "target_node_id": "plc", "address": "DB1.DBD0", "value": True,
                })
                invalid_real = client.post(f"/api/scenarios/{scenario_id}/scada/scada/write", json={
                    "target_node_id": "plc", "address": "DB1.DBD6", "value": 1e40,
                })
            self.assertEqual(write.status_code, 200, write.text)
            self.assertEqual(real_write.status_code, 200, real_write.text)
            self.assertEqual(real_write.json()["value"], 47.5)
            self.assertEqual(unselected.status_code, 403, unselected.text)
            self.assertEqual(read_only.status_code, 403, read_only.text)
            self.assertEqual(invalid_real.status_code, 409, invalid_real.text)
            self.assertEqual(write_point.call_count, 2)
            self.assertTrue(all(call.args[2] == "scada" and call.args[3] == "write" for call in write_point.call_args_list))
            ack = client.post(f"/api/scenarios/{scenario_id}/scada/scada/alarms/acknowledge", json={
                "target_node_id": "plc", "address": "DB1.DBD0"})
            self.assertEqual(ack.status_code, 200, ack.text)
            events = client.get(f"/api/scenarios/{scenario_id}/audit-events")
            self.assertEqual(events.status_code, 200, events.text)
            event_types = [event["event_type"] for event in events.json()["events"]]
            self.assertIn("scada.alarm_acknowledged", event_types)
            written = next(event for event in events.json()["events"] if event["event_type"] == "operator.point_written")
            self.assertEqual(written["details"]["source"], "scada")
            self.assertEqual(len([event for event in events.json()["events"] if event["event_type"] == "operator.point_written"]), 2)
            stopped = client.post(f"/api/scenarios/{scenario_id}/stop")
            self.assertEqual(stopped.status_code, 200, stopped.text)
            app.dependency_overrides[get_optional_docker_manager] = lambda: None
            archived = client.get(f"/api/scenarios/{scenario_id}/scada/scada/history")
            self.assertEqual(archived.status_code, 200, archived.text)
            self.assertFalse(archived.json()["live"])
            self.assertEqual(archived.json()["samples"][0]["values"][0]["value"], 12.5)
            exported = client.get(f"/api/scenarios/{scenario_id}/scada/scada/history/export.csv")
            self.assertEqual(exported.status_code, 200, exported.text)
            self.assertIn("text/csv", exported.headers["content-type"])
            self.assertEqual(exported.headers["x-exported-rows"], "1")
            self.assertIn("timestamp,target_node_id,address,name,data_type,unit,value,quality", exported.text)
            self.assertIn("plc,DB1.DBD0,'=Level,REAL,L,12.5,good", exported.text)
            deleted_without_docker = client.delete(f"/api/scenarios/{scenario_id}")
            self.assertEqual(deleted_without_docker.status_code, 503, deleted_without_docker.text)
            cleanup_manager = Mock()
            app.dependency_overrides[get_optional_docker_manager] = lambda: cleanup_manager
            deleted = client.delete(f"/api/scenarios/{scenario_id}")
            self.assertEqual(deleted.status_code, 204, deleted.text)
            cleanup_manager.delete_retention_volumes.assert_called_once_with(scenario_id)
            with Session(engine) as session:
                self.assertEqual(read_scada_samples(session, scenario_id, "scada", 10), [])
                self.assertEqual(session.execute(select(ScenarioScadaAlarmState).where(
                    ScenarioScadaAlarmState.scenario_id == scenario_id)).scalars().all(), [])
                self.assertEqual(session.execute(select(ScenarioScadaAlarmPending).where(
                    ScenarioScadaAlarmPending.scenario_id == scenario_id)).scalars().all(), [])
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
