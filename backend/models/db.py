"""SQLite engine and FastAPI session dependency."""

from __future__ import annotations

import os
import hashlib
import json
import logging
from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import inspect, text

from sqlmodel import Session, SQLModel, create_engine, select
from models.audit import ScenarioAuditArchive, ScenarioAuditCheckpoint, ScenarioAuditEvent, ScenarioFieldAuditState, ScenarioNodeRuntimeState, ScenarioProtocolReadState, ScenarioProtocolTraceCursor  # noqa: F401 — register table metadata before create_all.
from models.platform_audit import PlatformAuditEvent  # noqa: F401 — register platform account audit metadata.
from models.revision import ScenarioRevision  # noqa: F401 — register table metadata before create_all.
from models.exercise import ExerciseRun  # noqa: F401 — register exercise metadata before create_all.
from models.auth import AuthRateLimitAttempt, PlatformUser, PlatformSession, ScenarioMembership, TerminalSessionLease  # noqa: F401 — register account and terminal lease metadata.
from models.template import SharedComponentPreset, SharedComponentPresetRevision, SharedScenarioTemplate, SharedScenarioTemplateRevision  # noqa: F401 — register shared catalog metadata.
from models.scada import ScenarioScadaAlarmPending, ScenarioScadaAlarmState, ScenarioScadaSample  # noqa: F401 — register durable SCADA history and alarm state.
from models.hmi import ScenarioHmiAlarmState, ScenarioHmiAlarmTransition  # noqa: F401 — register durable HMI alarms and transitions.
from models.scenario import ScenarioNodeOperation, ScenarioOperationLease, ScenarioRecord  # noqa: F401 — register scenario tables before account FKs.


DATABASE_URL = os.environ.get("ANVIL_DATABASE_URL", "sqlite:///./anvil.db")
CONNECT_ARGS = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=CONNECT_ARGS)
LOGGER = logging.getLogger("anvil.database")


def protect_sqlite_database_files(database_engine=engine) -> None:
    """Restrict local SQLite files and future journal creation to this process user.

    This is filesystem permission hardening, not encryption at rest. SQLite URI
    paths and ordinary absolute/relative file paths are supported; in-memory and
    non-SQLite databases need no local file permissions.
    """
    if database_engine.dialect.name != "sqlite":
        return
    database = database_engine.url.database
    if not database or database == ":memory:" or database.startswith("file::memory:"):
        return
    if database.startswith("file:"):
        database = database[5:].split("?", 1)[0]
    path = Path(database)
    if not path.is_absolute():
        path = Path.cwd() / path

    # SQLite creates rollback journals, WAL and shared-memory files lazily.
    # A restrictive process umask protects those future files too.
    os.umask(0o077)
    for candidate in (path, Path(f"{path}-journal"), Path(f"{path}-wal"), Path(f"{path}-shm")):
        try:
            candidate.chmod(0o600)
            mode = candidate.stat().st_mode & 0o777
            if mode & 0o077:
                LOGGER.warning("SQLite file permissions remain broader than 0600: %s (%04o)", candidate, mode)
        except FileNotFoundError:
            continue
        except OSError as error:
            LOGGER.warning("Could not restrict SQLite file permissions for %s: %s", candidate, error)


def create_db_and_tables(database_engine=engine) -> None:
    SQLModel.metadata.create_all(database_engine)
    protocol_cursor_columns = {
        column["name"] for column in inspect(database_engine).get_columns("scenario_protocol_trace_cursors")
    }
    with database_engine.begin() as connection:
        if "dropped_events_seen" not in protocol_cursor_columns:
            connection.execute(text(
                "ALTER TABLE scenario_protocol_trace_cursors ADD COLUMN dropped_events_seen INTEGER NOT NULL DEFAULT 0"
            ))
        if "last_queue_id" not in protocol_cursor_columns:
            connection.execute(text(
                "ALTER TABLE scenario_protocol_trace_cursors ADD COLUMN last_queue_id INTEGER NOT NULL DEFAULT 0"
            ))
    node_operation_columns = {
        column["name"] for column in inspect(database_engine).get_columns("scenario_node_operations")
    }
    if "precondition_json" not in node_operation_columns:
        with database_engine.begin() as connection:
            connection.execute(text(
                "ALTER TABLE scenario_node_operations ADD COLUMN precondition_json TEXT NOT NULL DEFAULT '{}'"
            ))
    audit_columns = {column["name"] for column in inspect(database_engine).get_columns("scenario_audit_events")}
    with database_engine.begin() as connection:
        for name in ("previous_hash", "event_hash"):
            if name not in audit_columns:
                connection.execute(text(f"ALTER TABLE scenario_audit_events ADD COLUMN {name} VARCHAR(64)"))
        rows = connection.execute(text(
            "SELECT id, scenario_id, timestamp, event_type, actor, details_json, previous_hash, event_hash "
            "FROM scenario_audit_events ORDER BY scenario_id, id"
        )).mappings().all()
        # Only migrate a wholly legacy table. A partially hashed log can indicate an
        # interrupted/tampered chain and must be surfaced by verification, not repaired.
        if rows and all(row["previous_hash"] is None and row["event_hash"] is None for row in rows):
            previous_by_scenario: dict[str, str] = {}
            for row in rows:
                scenario_id = row["scenario_id"]
                previous = previous_by_scenario.get(
                    scenario_id,
                    hashlib.sha256(f"anvil-audit-v1:{scenario_id}".encode("utf-8")).hexdigest(),
                )
                timestamp = row["timestamp"]
                if isinstance(timestamp, str):
                    timestamp = datetime.fromisoformat(timestamp)
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
                else:
                    timestamp = timestamp.astimezone(timezone.utc)
                payload = {
                    "scenario_id": scenario_id,
                    "timestamp": timestamp.isoformat(timespec="microseconds"),
                    "event_type": row["event_type"],
                    "actor": row["actor"],
                    "details_json": row["details_json"] or "{}",
                    "previous_hash": previous,
                }
                canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                event_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
                connection.execute(text(
                    "UPDATE scenario_audit_events SET previous_hash=:previous, event_hash=:event_hash WHERE id=:id"
                ), {"previous": previous, "event_hash": event_hash, "id": row["id"]})
                previous_by_scenario[scenario_id] = event_hash
    template_columns = {column["name"] for column in inspect(database_engine).get_columns("shared_scenario_templates")}
    if "schema_version" not in template_columns:
        with database_engine.begin() as connection:
            connection.execute(text(
                "ALTER TABLE shared_scenario_templates ADD COLUMN schema_version INTEGER NOT NULL DEFAULT 1"
            ))
    if "revision" not in template_columns:
        with database_engine.begin() as connection:
            connection.execute(text(
                "ALTER TABLE shared_scenario_templates ADD COLUMN revision INTEGER NOT NULL DEFAULT 1"
            ))
    with Session(database_engine) as session:
        for template in session.exec(select(SharedScenarioTemplate)).all():
            existing = session.exec(select(SharedScenarioTemplateRevision.id).where(
                SharedScenarioTemplateRevision.template_id == template.id,
                SharedScenarioTemplateRevision.version == template.revision,
            )).first()
            if existing is None:
                session.add(SharedScenarioTemplateRevision(
                    template_id=template.id, version=template.revision,
                    title=template.title, category=template.category, summary=template.summary,
                    complexity=template.complexity, tags_json=template.tags_json,
                    schema_version=template.schema_version, scenario_json=template.scenario_json,
                    created_by=template.created_by, created_at=template.updated_at,
                ))
        session.commit()
    component_columns = {column["name"] for column in inspect(database_engine).get_columns("shared_component_presets")}
    for name, column_type in (("revision", "INTEGER NOT NULL DEFAULT 1"), ("updated_at", "DATETIME")):
        if name not in component_columns:
            with database_engine.begin() as connection:
                connection.execute(text(f"ALTER TABLE shared_component_presets ADD COLUMN {name} {column_type}"))
    with Session(database_engine) as session:
        for preset in session.exec(select(SharedComponentPreset)).all():
            if preset.updated_at is None:
                preset.updated_at = preset.created_at
                session.add(preset)
            existing = session.exec(select(SharedComponentPresetRevision.id).where(
                SharedComponentPresetRevision.preset_id == preset.id,
                SharedComponentPresetRevision.version == preset.revision,
            )).first()
            if existing is None:
                session.add(SharedComponentPresetRevision(
                    preset_id=preset.id, version=preset.revision, title=preset.title,
                    description=preset.description, device_type=preset.device_type,
                    component_type=preset.component_type, config_json=preset.config_json,
                    schema_version=preset.schema_version, created_by=preset.created_by,
                    created_at=preset.updated_at,
                ))
        session.commit()
    membership_indexes = {item["name"] for item in inspect(database_engine).get_indexes("scenario_memberships")}
    membership_constraints = {item.get("name") for item in inspect(database_engine).get_unique_constraints("scenario_memberships")}
    if "uq_scenario_membership_user" not in membership_indexes and "uq_scenario_membership_user" not in membership_constraints:
        with database_engine.begin() as connection:
            connection.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_scenario_membership_user "
                "ON scenario_memberships (scenario_id, user_id)"
            ))
    columns = {column["name"] for column in inspect(database_engine).get_columns("scenarios")}
    if "canvas_json" not in columns:
        with database_engine.begin() as connection:
            connection.execute(text("ALTER TABLE scenarios ADD COLUMN canvas_json TEXT"))
    for name in ("field_objects_json", "signal_connections_json", "process_connections_json", "network_routes_json", "network_connections_json"):
        if name not in columns:
            with database_engine.begin() as connection:
                connection.execute(text(f"ALTER TABLE scenarios ADD COLUMN {name} TEXT NOT NULL DEFAULT '[]'"))
    if "owner_user_id" not in columns:
        with database_engine.begin() as connection:
            connection.execute(text("ALTER TABLE scenarios ADD COLUMN owner_user_id VARCHAR"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_scenarios_owner_user_id ON scenarios (owner_user_id)"))
    exercise_columns = {column["name"] for column in inspect(database_engine).get_columns("scenario_exercise_runs")}
    for name, sql_type in (("score", "INTEGER"), ("passed", "BOOLEAN"),
                           ("objective_tracking_json", "TEXT NOT NULL DEFAULT '{}'"),
                           ("paused_at", "DATETIME"),
                           ("paused_duration_seconds", "FLOAT NOT NULL DEFAULT 0")):
        if name not in exercise_columns:
            with database_engine.begin() as connection:
                connection.execute(text(f"ALTER TABLE scenario_exercise_runs ADD COLUMN {name} {sql_type}"))
    hmi_alarm_columns = {column["name"] for column in inspect(database_engine).get_columns("scenario_hmi_alarm_states")}
    if "condition_since" not in hmi_alarm_columns:
        with database_engine.begin() as connection:
            connection.execute(text("ALTER TABLE scenario_hmi_alarm_states ADD COLUMN condition_since VARCHAR(40)"))


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
