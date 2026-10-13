from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from models.audit import ScenarioAuditEvent, ScenarioFieldAuditState  # noqa: E402
from models.auth import PlatformUser  # noqa: E402,F401 — registers the scenario owner FK target.
from models.scenario import ScenarioRecord  # noqa: E402
from services.audit import verify_audit_integrity  # noqa: E402
from services.process_trace_collector import ingest_field_snapshot  # noqa: E402


class ProcessTraceCollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            session.add(ScenarioRecord(
                id="scenario-process", name="process", network_json='{"subnet":"172.30.2.0/24"}',
                devices_json="[]", runtime_devices_json="[]", status="running",
            ))
            session.commit()
        self.configured = [
            {"object_id": "valve-1", "type": "valve"},
            {"object_id": "sensor-1", "type": "analog_sensor"},
            {"object_id": "valve-2", "type": "proportional_valve"},
            {"object_id": "tank-1", "type": "tank"},
        ]

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_audits_discrete_process_transitions_without_telemetry_samples(self) -> None:
        initial = {"objects": [
            {"object_id": "valve-1", "state": False, "fault": False, "command": False},
            {"object_id": "sensor-1", "fault": False, "override": None, "value": 20.0},
            {"object_id": "valve-2", "fault": False, "opening_pct": 0.0},
            {"object_id": "tank-1", "volume_liters": 50.0},
        ]}
        changed = {"objects": [
            {"object_id": "valve-1", "state": True, "fault": False, "command": True},
            {"object_id": "sensor-1", "fault": False, "override": 60.0, "value": 75.0},
            {"object_id": "valve-2", "fault": False, "opening_pct": 35.0},
            {"object_id": "tank-1", "volume_liters": 64.0},
        ]}
        links = [{
            "connection_id": "signal-1",
            "source": {"kind": "device", "node_id": "plc-1", "address": "DB1.DBX0.0"},
            "target": {"kind": "field", "object_id": "valve-1", "port": "command"},
        }]
        with Session(self.engine) as session:
            self.assertEqual(ingest_field_snapshot(session, "scenario-process", self.configured, initial, links), 0)
            self.assertEqual(ingest_field_snapshot(session, "scenario-process", self.configured, changed, links), 3)
            self.assertEqual(ingest_field_snapshot(session, "scenario-process", self.configured, changed, links), 0)
            events = session.exec(select(ScenarioAuditEvent).order_by(ScenarioAuditEvent.id)).all()
            details = [json.loads(event.details_json) for event in events]
            self.assertEqual(len(events), 3)
            self.assertTrue(all(event.event_type == "process.field_state_changed" for event in events))
            valve = next(item for item in details if item["object_id"] == "valve-1")
            self.assertEqual(valve["changes"]["state"], {"from": False, "to": True})
            self.assertEqual(valve["changes"]["command"], {"from": False, "to": True})
            self.assertEqual(valve["control_sources"], [{
                "node_id": "plc-1", "address": "DB1.DBX0.0", "connection_id": "signal-1",
            }])
            proportional = next(item for item in details if item["object_id"] == "valve-2")
            self.assertEqual(proportional["changes"]["open"], {"from": False, "to": True})
            self.assertFalse(any("value" in item or "volume_liters" in item for item in details))
            sensor = next(item for item in details if item["object_id"] == "sensor-1")
            self.assertEqual(sensor["changes"]["override"], {"from": None, "to": 60.0})
            self.assertEqual(session.get(ScenarioFieldAuditState, ("scenario-process", "sensor-1")).state_json,
                             '{"fault":false,"override":60.0}')
            self.assertTrue(verify_audit_integrity(session, "scenario-process").valid)


if __name__ == "__main__":
    unittest.main()
