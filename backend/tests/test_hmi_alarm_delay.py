"""Tests for persistent HMI alarm on-delay timing and schema migration."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import inspect
from sqlmodel import Session, SQLModel, create_engine, select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from models.db import create_db_and_tables  # noqa: E402
from models.hmi import ScenarioHmiAlarmTransition  # noqa: E402
from models.virtual_devices import HmiAlarmRule  # noqa: E402
from services.hmi_operations import evaluate_hmi_alarms  # noqa: E402


class HmiAlarmDelayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        SQLModel.metadata.create_all(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_alarm_delay_requires_continuous_good_condition_and_persists_transitions(self) -> None:
        rule = {"target_node_id": "plc", "address": "DB1.DBD0", "mode": "threshold",
                "above": 10, "below": None, "delay_ms": 1000}
        values = [{"node_id": "plc", "address": "DB1.DBD0", "value": 12.0, "quality": "good"}]
        current = [datetime(2026, 10, 8, tzinfo=timezone.utc)]
        clock = lambda: current[0]
        with Session(self.engine) as session:
            first = evaluate_hmi_alarms(session, "scenario", "hmi", [rule], values, clock=clock)[0]
            self.assertFalse(first["active"])
            self.assertTrue(first["pending"])
            self.assertEqual(first["delay_ms"], 1000)

            current[0] += timedelta(milliseconds=600)
            still_pending = evaluate_hmi_alarms(session, "scenario", "hmi", [rule], values, clock=clock)[0]
            self.assertTrue(still_pending["pending"])
            self.assertFalse(still_pending["active"])

            current[0] += timedelta(milliseconds=100)
            bad_quality = [{**values[0], "quality": "bad"}]
            stale = evaluate_hmi_alarms(session, "scenario", "hmi", [rule], bad_quality, clock=clock)[0]
            self.assertFalse(stale["active"])
            self.assertTrue(stale["stale"])
            self.assertFalse(stale["pending"])

            current[0] += timedelta(milliseconds=100)
            pending_again = evaluate_hmi_alarms(session, "scenario", "hmi", [rule], values, clock=clock)[0]
            self.assertTrue(pending_again["pending"])
            current[0] += timedelta(milliseconds=999)
            before_deadline = evaluate_hmi_alarms(session, "scenario", "hmi", [rule], values, clock=clock)[0]
            self.assertFalse(before_deadline["active"])
            current[0] += timedelta(milliseconds=1)
            activated = evaluate_hmi_alarms(session, "scenario", "hmi", [rule], values, clock=clock)[0]
            self.assertTrue(activated["active"])
            self.assertFalse(activated["pending"])

            current[0] += timedelta(milliseconds=100)
            stale_active = evaluate_hmi_alarms(session, "scenario", "hmi", [rule], bad_quality, clock=clock)[0]
            self.assertTrue(stale_active["active"])
            self.assertTrue(stale_active["stale"])

            current[0] += timedelta(milliseconds=100)
            normal = [{**values[0], "value": 8.0}]
            cleared = evaluate_hmi_alarms(session, "scenario", "hmi", [rule], normal, clock=clock)[0]
            self.assertFalse(cleared["active"])
            transitions = session.exec(select(ScenarioHmiAlarmTransition).order_by(
                ScenarioHmiAlarmTransition.id
            )).all()
            self.assertEqual([item.transition for item in transitions], ["activated", "cleared"])

    def test_alarm_delay_is_strict_and_bounded(self) -> None:
        base = {"target_node_id": "plc", "address": "DB1.DBD0", "above": 10}
        self.assertEqual(HmiAlarmRule.model_validate(base).delay_ms, 0)
        self.assertEqual(HmiAlarmRule.model_validate({**base, "delay_ms": 86_400_000}).delay_ms, 86_400_000)
        for value in (-1, 86_400_001, True, 1.5, "100"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                HmiAlarmRule.model_validate({**base, "delay_ms": value})

    def test_database_migration_adds_pending_since_to_existing_hmi_alarm_state(self) -> None:
        with self.engine.begin() as connection:
            connection.exec_driver_sql("ALTER TABLE scenario_hmi_alarm_states DROP COLUMN condition_since")
        create_db_and_tables(self.engine)
        create_db_and_tables(self.engine)
        names = {column["name"] for column in inspect(self.engine).get_columns("scenario_hmi_alarm_states")}
        self.assertIn("condition_since", names)


if __name__ == "__main__":
    unittest.main()
