from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from models.audit import ScenarioAuditEvent, ScenarioProtocolTraceCursor  # noqa: E402
from models.auth import PlatformUser  # noqa: E402,F401 — registers the scenarios.owner_user_id FK target.
from models.scenario import ScenarioRecord  # noqa: E402
from services.audit import verify_audit_integrity  # noqa: E402
from services.protocol_trace_collector import (  # noqa: E402
    collect_protocol_traces_once,
    flush_protocol_traces_best_effort,
    ingest_container_lines,
    _parse_interception_line,
)


def _line(timestamp: str, message: str) -> str:
    return f"{timestamp} 2026-10-09 01:31:10,123 INFO anvil.modbus: {message}"


def _s7_line(timestamp: str, message: str) -> str:
    return f"{timestamp} 2026-10-09 01:31:10,123 INFO anvil.plc.protocol: {message}"


class ProtocolTraceCollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            session.add(ScenarioRecord(
                id="scenario-trace", name="trace", network_json='{"subnet":"172.30.1.0/24"}',
                devices_json="[]", runtime_devices_json=json.dumps([{
                    "node_id": "modbus-1", "device_type": "modbus-tcp",
                    "config": {"transport": "tcp"},
                }]), status="running",
            ))
            session.commit()

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_ingestion_is_idempotent_and_audits_writes_without_values(self) -> None:
        lines = [
            _line("2026-10-09T01:31:10.123456789Z", "Modbus request source=172.30.1.55 unit_id=1 function=6 operation=write_holding_register space=holding_register address=8 count=1 result=ok"),
            _line("2026-10-09T01:31:11.123456789Z", "Modbus request source=172.30.1.55 unit_id=1 function=3 operation=read_holding_registers space=holding_register address=8 count=1 result=ok"),
            _line("2026-10-09T01:31:12.123456789Z", "Modbus request source=172.30.1.55 unit_id=1 function=3 operation=read_holding_registers space=holding_register address=9 count=1 result=exception code=2"),
        ]
        with Session(self.engine) as session:
            self.assertEqual(ingest_container_lines(session, "scenario-trace", "modbus-1", lines), 2)
        with Session(self.engine) as session:
            self.assertEqual(ingest_container_lines(session, "scenario-trace", "modbus-1", lines), 0)
            events = session.exec(select(ScenarioAuditEvent).order_by(ScenarioAuditEvent.id)).all()
            self.assertEqual([item.event_type for item in events], [
                "protocol.modbus_operation", "protocol.modbus_operation",
            ])
            details = [json.loads(item.details_json) for item in events]
            self.assertEqual(details[0]["operation"], "write_holding_register")
            self.assertEqual(details[0]["address"], 8)
            self.assertIs(type(details[0]["address"]), int)
            self.assertEqual(details[1]["result"], "exception code=2")
            self.assertNotIn("value", details[0])
            self.assertTrue(all(item.actor == "modbus-client" for item in events))
            self.assertTrue(verify_audit_integrity(session, "scenario-trace").valid)

    def test_modbus_interception_queue_creates_filtered_durable_match_audit(self) -> None:
        event = {
            "event": "modbus_response_rewritten", "route_id": "route-1",
            "rule_ids": ["pressure-rule"], "count": 1,
            "exercise_id": "exercise-1", "event_id": "rewrite-1",
            "unit_id": 2, "function_code": 3, "address": 10,
            "direction": "response",
            "timestamp": "2026-10-09T01:31:14Z",
        }
        line = json.dumps(event, separators=(",", ":"))
        queue_events = [{"id": 1, "line": line}]
        with Session(self.engine) as session:
            self.assertEqual(ingest_container_lines(
                session, "scenario-trace", "__anvil_route_route-1", [line],
                protocol="modbus-interception", queue_events=queue_events,
            ), 1)
        with Session(self.engine) as session:
            self.assertEqual(ingest_container_lines(
                session, "scenario-trace", "__anvil_route_route-1", [line],
                protocol="modbus-interception", queue_events=queue_events,
            ), 0)
            [audit] = session.exec(select(ScenarioAuditEvent)).all()
            details = json.loads(audit.details_json)
            self.assertEqual(audit.event_type, "security.modbus_interception_match")
            self.assertEqual(audit.actor, "modbus-interception-gateway")
            self.assertEqual(details["exercise_id"], "exercise-1")
            self.assertEqual(details["event_id"], "rewrite-1")
            self.assertEqual(details["rule_id"], "pressure-rule")
            self.assertEqual(details["direction"], "response")
            self.assertNotIn("replacement_value", details)
            cursor = session.get(ScenarioProtocolTraceCursor,
                                 ("scenario-trace", "__anvil_route_route-1"))
            self.assertEqual(cursor.last_queue_id, 1)
            self.assertTrue(verify_audit_integrity(session, "scenario-trace").valid)

    def test_modbus_interception_queue_records_fc06_request_without_values(self) -> None:
        event = {
            "event": "modbus_request_rewritten", "route_id": "route-1",
            "rule_ids": ["setpoint-rule"], "count": 1,
            "exercise_id": "exercise-1", "event_id": "rewrite-write",
            "unit_id": 2, "function_code": 6, "address": 10,
            "direction": "request", "timestamp": "2026-10-09T01:31:15Z",
        }
        line = json.dumps(event, separators=(",", ":"))
        with Session(self.engine) as session:
            self.assertEqual(ingest_container_lines(
                session, "scenario-trace", "__anvil_route_route-1", [line],
                protocol="modbus-interception", queue_events=[{"id": 1, "line": line}],
            ), 1)
        with Session(self.engine) as session:
            [audit] = session.exec(select(ScenarioAuditEvent)).all()
            details = json.loads(audit.details_json)
            self.assertEqual(details["function_code"], 6)
            self.assertEqual(details["direction"], "request")
            self.assertEqual(details["rule_id"], "setpoint-rule")
            self.assertNotIn("replacement_value", details)

    def test_modbus_interception_queue_accepts_fc05_request_without_values(self) -> None:
        event = {
            "event": "modbus_request_rewritten", "route_id": "route-1",
            "rule_ids": ["single-coil-rule"], "count": 1,
            "exercise_id": "exercise-1", "event_id": "rewrite-single-coil",
            "unit_id": 2, "function_code": 5, "address": 10,
            "direction": "request", "timestamp": "2026-10-09T01:31:16Z",
        }
        line = json.dumps(event, separators=(",", ":"))
        with Session(self.engine) as session:
            self.assertEqual(ingest_container_lines(
                session, "scenario-trace", "__anvil_route_route-1", [line],
                protocol="modbus-interception", queue_events=[{"id": 5, "line": line}],
            ), 1)
        with Session(self.engine) as session:
            [audit] = session.exec(select(ScenarioAuditEvent)).all()
            details = json.loads(audit.details_json)
            self.assertEqual(details["function_code"], 5)
            self.assertEqual(details["direction"], "request")
            self.assertEqual(details["rule_id"], "single-coil-rule")
            self.assertNotIn("replacement_value", details)

    def test_modbus_interception_queue_accepts_fc22_request_without_values(self) -> None:
        event = {
            "event": "modbus_request_rewritten", "route_id": "route-1",
            "rule_ids": ["mask-rule"], "count": 1,
            "exercise_id": "exercise-1", "event_id": "rewrite-mask-write",
            "unit_id": 2, "function_code": 22, "address": 10,
            "direction": "request", "timestamp": "2026-10-09T01:31:16Z",
        }
        line = json.dumps(event, separators=(",", ":"))
        with Session(self.engine) as session:
            self.assertEqual(ingest_container_lines(
                session, "scenario-trace", "__anvil_route_route-1", [line],
                protocol="modbus-interception", queue_events=[{"id": 6, "line": line}],
            ), 1)
        with Session(self.engine) as session:
            [audit] = session.exec(select(ScenarioAuditEvent)).all()
            details = json.loads(audit.details_json)
            self.assertEqual(details["function_code"], 22)
            self.assertEqual(details["direction"], "request")
            self.assertEqual(details["rule_id"], "mask-rule")
            self.assertNotIn("replacement_value", details)

    def test_modbus_interception_queue_accepts_fc23_response_without_values(self) -> None:
        event = {
            "event": "modbus_response_rewritten", "route_id": "route-1",
            "rule_ids": ["combined-read-rule"], "count": 1,
            "exercise_id": "exercise-1", "event_id": "rewrite-combined-read",
            "unit_id": 2, "function_code": 23, "address": 10,
            "direction": "response", "timestamp": "2026-10-09T01:31:16Z",
        }
        line = json.dumps(event, separators=(",", ":"))
        with Session(self.engine) as session:
            self.assertEqual(ingest_container_lines(
                session, "scenario-trace", "__anvil_route_route-1", [line],
                protocol="modbus-interception", queue_events=[{"id": 7, "line": line}],
            ), 1)
        with Session(self.engine) as session:
            [audit] = session.exec(select(ScenarioAuditEvent)).all()
            details = json.loads(audit.details_json)
            self.assertEqual(details["function_code"], 23)
            self.assertEqual(details["direction"], "response")
            self.assertEqual(details["rule_id"], "combined-read-rule")
            self.assertNotIn("replacement_value", details)

    def test_modbus_interception_queue_accepts_fc16_request_without_values(self) -> None:
        event = {
            "event": "modbus_request_rewritten", "route_id": "route-1",
            "rule_ids": ["setpoint-rule"], "count": 1,
            "exercise_id": "exercise-1", "event_id": "rewrite-multiple-write",
            "unit_id": 2, "function_code": 16, "address": 10,
            "direction": "request", "timestamp": "2026-10-09T01:31:17Z",
        }
        line = json.dumps(event, separators=(",", ":"))
        with Session(self.engine) as session:
            self.assertEqual(ingest_container_lines(
                session, "scenario-trace", "__anvil_route_route-1", [line],
                protocol="modbus-interception", queue_events=[{"id": 2, "line": line}],
            ), 1)
        with Session(self.engine) as session:
            [audit] = session.exec(select(ScenarioAuditEvent)).all()
            details = json.loads(audit.details_json)
            self.assertEqual(details["function_code"], 16)
            self.assertEqual(details["direction"], "request")
            self.assertNotIn("replacement_value", details)

    def test_modbus_interception_queue_accepts_fc15_request_without_values(self) -> None:
        event = {
            "event": "modbus_request_rewritten", "route_id": "route-1",
            "rule_ids": ["coil-rule"], "count": 1,
            "exercise_id": "exercise-1", "event_id": "rewrite-coil",
            "unit_id": 2, "function_code": 15, "address": 10,
            "direction": "request", "timestamp": "2026-10-09T01:31:18Z",
        }
        line = json.dumps(event, separators=(",", ":"))
        with Session(self.engine) as session:
            self.assertEqual(ingest_container_lines(
                session, "scenario-trace", "__anvil_route_route-1", [line],
                protocol="modbus-interception", queue_events=[{"id": 3, "line": line}],
            ), 1)
        with Session(self.engine) as session:
            [audit] = session.exec(select(ScenarioAuditEvent)).all()
            details = json.loads(audit.details_json)
            self.assertEqual(details["function_code"], 15)
            self.assertEqual(details["direction"], "request")
            self.assertNotIn("replacement_value", details)

    def test_modbus_interception_parser_keeps_legacy_response_queue_compatible(self) -> None:
        legacy = json.dumps({
            "event": "modbus_response_rewritten", "route_id": "route-1",
            "rule_ids": ["legacy-rule"], "count": 1,
            "exercise_id": "exercise-1", "event_id": "legacy-event",
            "unit_id": 1, "function_code": 4, "address": 12,
            "timestamp": "2026-10-09T01:31:16Z",
        }, separators=(",", ":"))
        parsed = _parse_interception_line(legacy)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["direction"], "response")

    def test_collector_scans_only_running_modbus_tcp_scenarios(self) -> None:
        manager = Mock()
        manager.logs.return_value = {"containers": [{
            "node_id": "modbus-1",
            "lines": [_line("2026-10-09T01:31:13.123456789Z",
                "Modbus request source=172.30.1.55 unit_id=1 function=5 operation=write_coil space=coil address=2 count=1 result=ok")],
        }]}
        self.assertEqual(collect_protocol_traces_once(self.engine, manager), 1)
        manager.logs.assert_called_once_with(
            "scenario-trace", tail=1000, since_by_node={}
        )
        with Session(self.engine) as session:
            events = session.exec(select(ScenarioAuditEvent)).all()
            cursor = session.get(ScenarioProtocolTraceCursor, ("scenario-trace", "modbus-1"))
            self.assertEqual(len(events), 1)
            self.assertTrue(cursor.last_timestamp.startswith("2026-10-09T01:31:13"))

    def test_collector_backfills_protocol_logs_after_scenario_stops(self) -> None:
        with Session(self.engine) as session:
            scenario = session.get(ScenarioRecord, "scenario-trace")
            scenario.status = "stopped"
            session.add(scenario)
            session.commit()
        manager = Mock()
        manager.logs.return_value = {"containers": [{
            "node_id": "modbus-1",
            "lines": [_line("2026-10-09T01:31:17.123456789Z",
                "Modbus request source=172.30.1.55 unit_id=1 function=5 operation=write_coil space=coil address=2 count=1 result=ok")],
        }]}

        self.assertEqual(collect_protocol_traces_once(self.engine, manager), 1)
        manager.logs.assert_called_once_with("scenario-trace", tail=1000, since_by_node={})
        with Session(self.engine) as session:
            events = session.exec(select(ScenarioAuditEvent)).all()
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].event_type, "protocol.modbus_operation")

    def test_global_collector_keeps_each_scenario_node_scope_independent(self) -> None:
        with Session(self.engine) as session:
            session.add(ScenarioRecord(
                id="scenario-trace-2", name="trace 2", network_json='{"subnet":"172.30.2.0/24"}',
                devices_json="[]", runtime_devices_json=json.dumps([{
                    "node_id": "modbus-2", "device_type": "modbus-tcp", "config": {"transport": "tcp"},
                }]), status="running",
            ))
            session.commit()
        manager = Mock()
        manager.logs.side_effect = [
            {"containers": [{"node_id": "modbus-1", "lines": [_line(
                "2026-10-09T01:31:18.123456789Z",
                "Modbus request source=172.30.1.55 unit_id=1 function=5 operation=write_coil space=coil address=2 count=1 result=ok")]}]},
            {"containers": [{"node_id": "modbus-2", "lines": [_line(
                "2026-10-09T01:31:19.123456789Z",
                "Modbus request source=172.30.2.55 unit_id=1 function=5 operation=write_coil space=coil address=2 count=1 result=ok")]}]},
        ]

        self.assertEqual(collect_protocol_traces_once(self.engine, manager), 2)
        self.assertEqual([call.args[0] for call in manager.logs.call_args_list],
                         ["scenario-trace", "scenario-trace-2"])

    def test_best_effort_flush_does_not_propagate_audit_failure(self) -> None:
        with patch("services.protocol_trace_collector.collect_protocol_traces_once",
                   side_effect=RuntimeError("audit database unavailable")):
            self.assertEqual(flush_protocol_traces_best_effort(
                self.engine, manager=Mock(), scenario_id="scenario-trace",
            ), 0)

    def test_collector_uses_an_overlapping_incremental_cursor_after_first_scan(self) -> None:
        # Seed the cursor as if the initial bounded bootstrap already ran.
        with Session(self.engine) as session:
            session.add(ScenarioProtocolTraceCursor(
                scenario_id="scenario-trace", node_id="modbus-1",
                last_timestamp="2026-10-09T01:31:13.123456Z",
                same_timestamp_hashes_json="[]",
            ))
            session.commit()
        manager = Mock()
        manager.logs.return_value = {"containers": []}

        self.assertEqual(collect_protocol_traces_once(self.engine, manager), 0)

        kwargs = manager.logs.call_args.kwargs
        self.assertEqual(kwargs["tail"], 1000)
        self.assertEqual(set(kwargs["since_by_node"]), {"modbus-1"})
        self.assertAlmostEqual(
            kwargs["since_by_node"]["modbus-1"],
            datetime(2026, 10, 9, 1, 31, 12, 123456, tzinfo=timezone.utc).timestamp(),
            places=3,
        )

    def test_active_field_runtime_process_states_flow_into_audit(self) -> None:
        with Session(self.engine) as session:
            scenario = session.get(ScenarioRecord, "scenario-trace")
            scenario.runtime_devices_json = json.dumps([{
                "node_id": "__anvil_field__", "device_type": "field-runtime",
                "config": {
                    "objects": [{"object_id": "valve-1", "type": "valve"}],
                    "connections": [{
                        "connection_id": "signal-1",
                        "source": {"kind": "device", "node_id": "plc-1", "address": "DB1.DBX0.0"},
                        "target": {"kind": "field", "object_id": "valve-1", "port": "command"},
                    }],
                },
            }])
            session.add(scenario)
            session.commit()
        manager = Mock()
        manager.logs.return_value = {"containers": []}
        initial = {"objects": [{"object_id": "valve-1", "state": False, "command": False, "fault": False}]}
        opened = {"objects": [{"object_id": "valve-1", "state": True, "command": True, "fault": False}]}
        with patch("services.field_operations.field_operation", side_effect=[initial, opened]):
            self.assertEqual(collect_protocol_traces_once(self.engine, manager), 0)
            self.assertEqual(collect_protocol_traces_once(self.engine, manager), 1)
        manager.logs.assert_not_called()
        with Session(self.engine) as session:
            events = session.exec(select(ScenarioAuditEvent)).all()
            process_events = [item for item in events if item.event_type == "process.field_state_changed"]
            self.assertEqual(len(process_events), 1)
            details = json.loads(process_events[0].details_json)
            self.assertEqual(details["object_id"], "valve-1")
            self.assertEqual(details["control_sources"][0]["address"], "DB1.DBX0.0")

    def test_s7_audit_ingestion_keeps_metadata_without_process_bytes(self) -> None:
        lines = [
            _s7_line("2026-10-09T01:31:14.123456789Z", "S7 audit operation=read source=172.30.1.55 area=DB db=1 byte_offset=0 byte_count=4 result=ok result_code=0"),
            _s7_line("2026-10-09T01:31:15.123456789Z", "S7 audit operation=write source=172.30.1.55 area=DB db=1 byte_offset=4 byte_count=1 result=ok result_code=0"),
            _s7_line("2026-10-09T01:31:16.123456789Z", "S7 audit operation=write source=172.30.1.55 area=Q db=- byte_offset=0 byte_count=1 result=error result_code=5"),
        ]
        with Session(self.engine) as session:
            self.assertEqual(ingest_container_lines(session, "scenario-trace", "plc-1", lines, protocol="s7"), 2)
            events = session.exec(select(ScenarioAuditEvent)).all()
            details = [json.loads(item.details_json) for item in events]
            self.assertEqual([item.event_type for item in events], [
                "protocol.s7_operation", "protocol.s7_operation",
            ])
            self.assertEqual(details[0]["operation"], "write")
            self.assertEqual(details[1]["result"], "error")
            self.assertNotIn("raw_hex", details[0])
            self.assertNotIn("data", details[0])
            self.assertTrue(verify_audit_integrity(session, "scenario-trace").valid)

    def test_durable_queue_commits_before_ack_and_replay_is_idempotent(self) -> None:
        event_line = _line(
            "2026-10-09T01:31:20.123456789Z",
            "Modbus request source=172.30.1.55 unit_id=1 function=5 operation=write_coil space=coil address=2 count=1 result=ok",
        )

        class QueueManager:
            def __init__(self):
                self.acks = []
                self.batch = {"events": [{"id": 17, "line": event_line}],
                              "cursor": 17, "dropped_events": 0, "pending_count": 1}

            def protocol_audit_queue_nodes(self, scenario_id):
                return {"modbus-1"}

            def read_protocol_audit_queue(self, scenario_id, node_id, *, limit):
                return self.batch

            def acknowledge_protocol_audit_queue(self, scenario_id, node_id, cursor):
                with Session(test.engine) as session:
                    state = session.get(ScenarioProtocolTraceCursor, (scenario_id, node_id))
                    assert state.last_queue_id >= cursor
                self.acks.append(cursor)

            def logs(self, *args, **kwargs):
                raise AssertionError("durable queue nodes must not fall back to Docker logs")

        test = self
        manager = QueueManager()
        self.assertEqual(collect_protocol_traces_once(self.engine, manager), 1)
        self.assertEqual(manager.acks, [17])
        # Simulate a crash after database commit and before queue acknowledgement.
        self.assertEqual(collect_protocol_traces_once(self.engine, manager), 0)
        self.assertEqual(manager.acks, [17, 17])
        with Session(self.engine) as session:
            events = session.exec(select(ScenarioAuditEvent)).all()
            cursor = session.get(ScenarioProtocolTraceCursor, ("scenario-trace", "modbus-1"))
            self.assertEqual(len(events), 1)
            self.assertEqual(cursor.last_queue_id, 17)

    def test_durable_queue_records_capacity_loss_as_auditable_gap(self) -> None:
        with Session(self.engine) as session:
            self.assertEqual(ingest_container_lines(
                session, "scenario-trace", "modbus-1", [], dropped_events=3,
            ), 0)
        with Session(self.engine) as session:
            events = session.exec(select(ScenarioAuditEvent)).all()
            self.assertEqual([event.event_type for event in events], ["protocol.audit_queue_overflow"])
            self.assertEqual(json.loads(events[0].details_json)["dropped_events"], 3)
            cursor = session.get(ScenarioProtocolTraceCursor, ("scenario-trace", "modbus-1"))
            self.assertEqual(cursor.dropped_events_seen, 3)

    def test_rtu_queue_events_are_collected_and_acknowledged(self) -> None:
        with Session(self.engine) as session:
            scenario = session.get(ScenarioRecord, "scenario-trace")
            scenario.runtime_devices_json = json.dumps([{
                "node_id": "modbus-1", "device_type": "modbus-tcp",
                "config": {"transport": "rtu"},
            }])
            session.add(scenario)
            session.commit()
        line = _line(
            "2026-10-09T01:31:21.123456789Z",
            "Modbus request source=rtu-serial unit_id=17 function=5 operation=write_coil space=coil address=0 count=1 result=ok",
        )
        manager = Mock()
        manager.protocol_audit_queue_nodes.return_value = {"modbus-1"}
        manager.read_protocol_audit_queue.return_value = {
            "events": [{"id": 8, "line": line}], "cursor": 8,
            "dropped_events": 0, "pending_count": 1,
        }

        def acknowledge(scenario_id, node_id, cursor):
            with Session(self.engine) as session:
                state = session.get(ScenarioProtocolTraceCursor, (scenario_id, node_id))
                self.assertEqual(state.last_queue_id, cursor)

        manager.acknowledge_protocol_audit_queue.side_effect = acknowledge
        self.assertEqual(collect_protocol_traces_once(self.engine, manager), 1)
        manager.logs.assert_not_called()
        with Session(self.engine) as session:
            events = session.exec(select(ScenarioAuditEvent)).all()
            self.assertEqual(len(events), 1)
            details = json.loads(events[0].details_json)
            self.assertEqual(details["node_id"], "modbus-1")
            self.assertEqual(details["source"], "rtu-serial")
            self.assertEqual(details["operation"], "write_coil")
        manager.acknowledge_protocol_audit_queue.assert_called_once_with("scenario-trace", "modbus-1", 8)


if __name__ == "__main__":
    unittest.main()
