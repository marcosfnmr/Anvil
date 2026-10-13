from __future__ import annotations

import json
import os
import socket
import threading
import re
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch
from sqlalchemy import inspect
from sqlalchemy.exc import SQLAlchemyError

from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from main import create_app  # noqa: E402
from models.auth import AuthRateLimitAttempt, PlatformUser, PlatformSession  # noqa: E402
from models.audit import ScenarioAuditEvent  # noqa: E402
from models.platform_audit import PlatformAuditEvent  # noqa: E402
from models.template import (
    SharedComponentPreset, SharedComponentPresetRevision, SharedScenarioTemplate,
    SharedScenarioTemplateRevision,
)  # noqa: E402
from models.db import create_db_and_tables, get_session  # noqa: E402
from routes.scenarios import get_docker_manager  # noqa: E402
from dependencies import get_optional_docker_manager  # noqa: E402
from middleware.auth import _allowed, _participant_write  # noqa: E402
from routes.auth import _clear_login_attempts, _limit_login, _rate_limit_key  # noqa: E402
from routes import auth as auth_routes  # noqa: E402


BOOTSTRAP = "local-test-bootstrap-token-never-used-in-production"


class AuthManager:
    def logs(self, scenario_id, tail=1000):
        del scenario_id, tail
        return {"containers": []}

    def deploy(self, scenario_id, subnet, devices):
        return {"scenario_id": scenario_id, "status": "running", "containers": []}

    def delete_retention_volumes(self, scenario_id):
        del scenario_id


class AuthenticationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.engine = create_engine(
            f"sqlite:///{Path(self.temp_dir.name) / 'auth.db'}",
            connect_args={"check_same_thread": False},
        )
        SQLModel.metadata.create_all(self.engine)
        app = create_app(initialize_database=False, auth_enabled=True, auth_engine=self.engine)
        self.app = app

        def session_override():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_session] = session_override
        app.dependency_overrides[get_docker_manager] = AuthManager
        app.dependency_overrides[get_optional_docker_manager] = AuthManager
        self.client_context = TestClient(app, headers={"Origin": "http://testserver"})
        self.client = self.client_context.__enter__()
        self.env = patch.dict(os.environ, {"ANVIL_BOOTSTRAP_TOKEN": BOOTSTRAP})
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.client_context.__exit__(None, None, None)
        self.engine.dispose()
        self.temp_dir.cleanup()

    def bootstrap(self):
        result = self.client.post("/api/auth/bootstrap", json={
            "email": "admin@example.test", "password": "admin-password-strong-1", "token": BOOTSTRAP,
        })
        self.assertEqual(result.status_code, 201, result.text)
        self.assertNotIn("password", result.text)
        self.assertTrue(self.client.cookies.get("anvil_session"))
        return result.json()["user"]

    def create_user(self, email: str, role: str = "participant"):
        response = self.client.post("/api/users", json={
            "email": email, "password": "user-password-strong-1", "role": role,
        })
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def login(self, email: str, password: str):
        context = TestClient(self.client.app, headers={"Origin": "http://testserver"})
        client = context.__enter__()
        response = client.post("/api/auth/login", json={"email": email, "password": password})
        self.assertEqual(response.status_code, 200, response.text)
        return context, client

    def test_bootstrap_login_logout_and_password_storage(self) -> None:
        unauthorized = self.client.get("/api/scenarios")
        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(unauthorized.headers["cache-control"], "no-store")
        status_response = self.client.get("/api/auth/status")
        self.assertEqual(status_response.headers["cache-control"], "no-store")
        status = status_response.json()
        self.assertTrue(status["setup_required"])
        bad = self.client.post("/api/auth/bootstrap", json={
            "email": "admin@example.test", "password": "admin-password-strong-1", "token": "x" * 40,
        })
        self.assertEqual(bad.status_code, 403)
        self.assertEqual(bad.headers["cache-control"], "no-store")
        user = self.bootstrap()
        self.assertEqual(user["role"], "admin")
        self.assertEqual(self.client.get("/api/users").headers["cache-control"], "no-store")
        with Session(self.engine) as session:
            record = session.exec(select(PlatformUser).where(PlatformUser.email == user["email"])).one()
            self.assertNotEqual(record.password_hash, "admin-password-strong-1")
            self.assertTrue(record.password_hash.startswith("pbkdf2_sha256$"))
        self.assertEqual(len(session.exec(select(PlatformSession)).all()), 1)
        self.assertEqual(self.client.get("/api/auth/status").json()["authenticated"], True)
        self.assertEqual(self.client.post("/api/auth/logout").status_code, 204)
        self.assertEqual(self.client.get("/api/scenarios").status_code, 401)
        context, client = self.login("admin@example.test", "admin-password-strong-1")
        self.assertEqual(client.get("/api/auth/me").json()["role"], "admin")
        context.__exit__(None, None, None)

    def test_secure_cookie_requires_https_or_explicit_proxy_termination_setting(self) -> None:
        def request(scheme: str, forwarded_proto: str | None = None):
            headers = []
            if forwarded_proto:
                headers.append((b"x-forwarded-proto", forwarded_proto.encode("ascii")))
            return auth_routes.Request({
                "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                "method": "GET", "scheme": scheme, "path": "/", "raw_path": b"/",
                "query_string": b"", "headers": headers, "client": ("127.0.0.1", 1234),
                "server": ("testserver", 80),
            })

        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(auth_routes._secure_cookie(request("https")))
            self.assertFalse(auth_routes._secure_cookie(request("http")))
            self.assertFalse(auth_routes._secure_cookie(request("http", "https")))
        with patch.dict(os.environ, {"ANVIL_COOKIE_SECURE": "true"}, clear=True):
            self.assertTrue(auth_routes._secure_cookie(request("http")))
        with patch.dict(os.environ, {"ANVIL_COOKIE_SECURE": "false"}, clear=True):
            self.assertFalse(auth_routes._secure_cookie(request("http")))

    def test_bootstrap_rolls_back_user_when_audit_storage_fails(self) -> None:
        with patch.object(auth_routes, "append_platform_audit", side_effect=SQLAlchemyError("storage unavailable")):
            response = self.client.post("/api/auth/bootstrap", json={
                "email": "admin@example.test", "password": "admin-password-strong-1", "token": BOOTSTRAP,
            })

        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()["detail"], "Initial account setup is temporarily unavailable")
        with Session(self.engine) as session:
            self.assertIsNone(session.exec(select(PlatformUser.id)).first())
            self.assertEqual(session.exec(select(PlatformAuditEvent.id)).all(), [])

    def test_admin_password_reset_revokes_sessions_and_rejects_short_passwords(self) -> None:
        self.bootstrap()
        account = self.create_user("reset-target@example.test")
        user_context, user_client = self.login("reset-target@example.test", "user-password-strong-1")
        short = self.client.post(
            f"/api/users/{account['id']}/password", json={"new_password": "too-short"},
        )
        self.assertEqual(short.status_code, 422)
        new_password = "admin-provided-temporary-password"
        reset = self.client.post(
            f"/api/users/{account['id']}/password", json={"new_password": new_password},
        )
        self.assertEqual(reset.status_code, 204, reset.text)
        self.assertEqual(user_client.get("/api/auth/me").status_code, 401)
        user_context.__exit__(None, None, None)
        with TestClient(self.client.app, headers={"Origin": "http://testserver"}) as rejected_client:
            rejected = rejected_client.post("/api/auth/login", json={
                "email": "reset-target@example.test", "password": "user-password-strong-1",
            })
            self.assertEqual(rejected.status_code, 401)
        renewed_context, renewed_client = self.login("reset-target@example.test", new_password)
        self.assertEqual(renewed_client.get("/api/auth/me").status_code, 200)
        renewed_context.__exit__(None, None, None)

    def test_non_admin_cannot_reset_another_users_password(self) -> None:
        self.bootstrap()
        account = self.create_user("reset-denied@example.test")
        participant = self.create_user("reset-actor@example.test", "participant")
        context, client = self.login(participant["email"], "user-password-strong-1")
        response = client.post(
            f"/api/users/{account['id']}/password", json={"new_password": "another-temporary-password"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(client.get("/api/admin/audit-events").status_code, 403)
        context.__exit__(None, None, None)

    def test_platform_account_audit_is_chained_paginated_and_secret_free(self) -> None:
        self.bootstrap()
        account = self.create_user("platform-audited@example.test")
        account_update = self.client.patch(f"/api/users/{account['id']}", json={
            "role": "designer", "active": True,
        })
        self.assertEqual(account_update.status_code, 200, account_update.text)
        user_context, user_client = self.login(account["email"], "user-password-strong-1")
        reset = self.client.post(f"/api/users/{account['id']}/password", json={
            "new_password": "temporary-platform-password-2026",
        })
        self.assertEqual(reset.status_code, 204)
        self.assertEqual(user_client.get("/api/auth/me").status_code, 401)
        user_context.__exit__(None, None, None)

        first_page = self.client.get("/api/admin/audit-events?limit=2")
        self.assertEqual(first_page.status_code, 200, first_page.text)
        self.assertIsNotNone(first_page.json()["next_before_id"])
        events = self.client.get("/api/admin/audit-events?limit=100")
        self.assertEqual(events.status_code, 200, events.text)
        event_types = {event["event_type"] for event in events.json()["events"]}
        self.assertTrue({
            "account.bootstrap_completed", "account.created", "account.updated", "account.password_reset",
        }.issubset(event_types))
        self.assertNotIn("user-password-strong-1", events.text)
        self.assertNotIn("temporary-platform-password-2026", events.text)
        integrity = self.client.get("/api/admin/audit-events/integrity")
        self.assertEqual(integrity.status_code, 200, integrity.text)
        self.assertTrue(integrity.json()["valid"])

        with Session(self.engine) as session:
            first = session.exec(select(PlatformAuditEvent).order_by(PlatformAuditEvent.id)).first()
            self.assertIsNotNone(first)
            first.details_json = '{"tampered":true}'
            session.add(first)
            session.commit()
        damaged = self.client.get("/api/admin/audit-events/integrity")
        self.assertFalse(damaged.json()["valid"])
        self.assertEqual(damaged.json()["first_invalid_event_id"], 1)

    def test_participant_can_acknowledge_hmi_alarm_only_with_post(self) -> None:
        self.assertTrue(_participant_write(
            "/api/scenarios/123e4567-e89b-12d3-a456-426614174000/hmi/hmi-1/alarms/acknowledge", "POST"))
        self.assertFalse(_participant_write(
            "/api/scenarios/123e4567-e89b-12d3-a456-426614174000/hmi/hmi-1/alarms/acknowledge", "GET"))

    def test_login_rate_limit_is_shared_persisted_hashed_and_expires(self) -> None:
        remote = "192.0.2.15"
        with Session(self.engine) as session:
            for _ in range(10):
                _limit_login(session, remote, now=1000)
            with self.assertRaises(HTTPException) as limited:
                _limit_login(session, remote, now=1000)
            self.assertEqual(limited.exception.status_code, 429)
            attempts = session.exec(select(AuthRateLimitAttempt).where(
                AuthRateLimitAttempt.key_hash == _rate_limit_key(remote)
            )).all()
            self.assertEqual(len(attempts), 10)
            self.assertNotEqual(attempts[0].key_hash, remote)
            self.assertTrue(all(item.attempted_at == 1000 for item in attempts))

            with self.assertRaises(HTTPException):
                _limit_login(session, remote, now=1059)
            _limit_login(session, remote, now=1061)
            attempts = session.exec(select(AuthRateLimitAttempt).where(
                AuthRateLimitAttempt.key_hash == _rate_limit_key(remote)
            )).all()
            self.assertEqual(len(attempts), 11)
            _clear_login_attempts(session, remote)
            self.assertEqual(session.exec(select(AuthRateLimitAttempt).where(
                AuthRateLimitAttempt.key_hash == _rate_limit_key(remote)
            )).all(), [])

    def test_login_rate_limit_is_atomic_across_database_sessions(self) -> None:
        with tempfile.TemporaryDirectory(prefix="anvil-auth-limit-") as temp_dir:
            engine = create_engine(f"sqlite:///{temp_dir}/auth.db", connect_args={"check_same_thread": False, "timeout": 10})
            SQLModel.metadata.create_all(engine)

            def attempt(_: int) -> int:
                with Session(engine) as session:
                    try:
                        _limit_login(session, "198.51.100.44", now=2000)
                        return 200
                    except HTTPException as error:
                        return error.status_code

            with ThreadPoolExecutor(max_workers=12) as workers:
                outcomes = list(workers.map(attempt, range(12)))
            self.assertEqual(outcomes.count(200), 10)
            self.assertEqual(outcomes.count(429), 2)
            with Session(engine) as session:
                self.assertEqual(len(session.exec(select(AuthRateLimitAttempt)).all()), 10)
            engine.dispose()

    def test_login_rate_limiter_fails_closed_when_storage_is_unavailable(self) -> None:
        session = Mock()
        session.execute.side_effect = SQLAlchemyError("database unavailable")

        with self.assertRaises(HTTPException) as unavailable:
            _limit_login(session, "203.0.113.5", now=3000)

        self.assertEqual(unavailable.exception.status_code, 503)
        session.rollback.assert_called_once_with()

    def test_database_startup_creates_shared_component_catalog_schema(self) -> None:
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        try:
            create_db_and_tables(engine)
            tables = set(inspect(engine).get_table_names())
            self.assertIn("shared_component_presets", tables)
            self.assertIn("shared_scenario_templates", tables)
            self.assertIn("auth_rate_limit_attempts", tables)
            self.assertIn("platform_audit_events", tables)
            component_columns = {column["name"] for column in inspect(engine).get_columns("shared_component_presets")}
            self.assertTrue({"device_type", "component_type", "config_json", "schema_version", "revision", "updated_at", "created_by"} <= component_columns)
            self.assertIn("shared_component_preset_revisions", tables)
        finally:
            engine.dispose()

    def test_legacy_shared_templates_receive_one_baseline_revision_idempotently(self) -> None:
        admin = self.bootstrap()
        with Session(self.engine) as session:
            session.add(SharedScenarioTemplate(
                id="custom-0123456789abcdef0123456789abcdef", title="Legacy template",
                category="Test", summary="A legacy shared template used for migration coverage.",
                complexity="Básica", tags_json='["legacy"]', schema_version=1,
                scenario_json='{"format":"anvil-scenario-template","schema_version":1,"scenario":{}}',
                created_by=admin["id"],
            ))
            session.commit()
        with self.engine.begin() as connection:
            connection.exec_driver_sql("DROP TABLE shared_scenario_template_revisions")
            connection.exec_driver_sql("ALTER TABLE shared_scenario_templates DROP COLUMN revision")

        create_db_and_tables(self.engine)
        create_db_and_tables(self.engine)
        with Session(self.engine) as session:
            template = session.get(SharedScenarioTemplate, "custom-0123456789abcdef0123456789abcdef")
            revisions = session.exec(select(SharedScenarioTemplateRevision).where(
                SharedScenarioTemplateRevision.template_id == template.id,
            )).all()
            self.assertEqual(template.revision, 1)
            self.assertEqual(len(revisions), 1)
            self.assertEqual(revisions[0].version, 1)
            self.assertEqual(revisions[0].title, template.title)
            self.assertEqual(revisions[0].scenario_json, template.scenario_json)

    def test_legacy_component_presets_receive_one_baseline_revision_idempotently(self) -> None:
        admin = self.bootstrap()
        with Session(self.engine) as session:
            session.add(SharedComponentPreset(
                id="component-0123456789abcdef0123456789abcdef", title="Legacy valve",
                description="Existing preset before its revision history was introduced.",
                device_type="plc-s7", component_type="valve",
                config_json='{"type":"valve","name":"legacy","db":1,"initial_state":false}',
                schema_version=1, created_by=admin["id"],
            ))
            session.commit()
        with self.engine.begin() as connection:
            connection.exec_driver_sql("DROP TABLE shared_component_preset_revisions")
            connection.exec_driver_sql("ALTER TABLE shared_component_presets DROP COLUMN revision")
            connection.exec_driver_sql("ALTER TABLE shared_component_presets DROP COLUMN updated_at")

        create_db_and_tables(self.engine)
        create_db_and_tables(self.engine)
        with Session(self.engine) as session:
            preset = session.get(SharedComponentPreset, "component-0123456789abcdef0123456789abcdef")
            revisions = session.exec(select(SharedComponentPresetRevision).where(
                SharedComponentPresetRevision.preset_id == preset.id,
            )).all()
            self.assertEqual(preset.revision, 1)
            self.assertEqual(preset.updated_at, preset.created_at)
            self.assertEqual(len(revisions), 1)
            self.assertEqual(revisions[0].version, 1)
            self.assertEqual(revisions[0].config_json, preset.config_json)

    def test_scenario_ownership_membership_and_role_policy(self) -> None:
        self.bootstrap()
        designer = self.create_user("designer@example.test", "designer")
        participant = self.create_user("participant@example.test")
        self.create_user("outsider@example.test")
        context, design_client = self.login("designer@example.test", "user-password-strong-1")
        scenario = design_client.post("/api/scenarios", json={
            "name": "Owned by designer", "network": {"subnet": "172.31.22.0/24"},
            "devices": [{"node_id": "plc", "device_type": "plc-s7", "name": "PLC",
                         "ip": "172.31.22.10", "config": {"components": [{"type": "valve", "name": "V"}]}}],
        })
        self.assertEqual(scenario.status_code, 201, scenario.text)
        scenario_id = scenario.json()["id"]
        self.assertEqual(scenario.json()["owner_user_id"], designer["id"])
        audit = design_client.get(f"/api/scenarios/{scenario_id}/audit-events")
        self.assertEqual(audit.status_code, 200, audit.text)
        created_event = next(item for item in audit.json()["events"] if item["event_type"] == "scenario.created")
        self.assertEqual(created_event["actor"], f"user:{designer['id']}")
        self.assertEqual(design_client.post(f"/api/scenarios/{scenario_id}/deploy").status_code, 403)
        self.assertEqual(design_client.get(f"/api/scenarios/{scenario_id}").status_code, 200)
        self.assertEqual(design_client.get(f"/api/scenarios/{scenario_id}/network-routes").status_code, 200)
        self.assertEqual(design_client.get("/api/scenarios").json()[0]["id"], scenario_id)
        context.__exit__(None, None, None)

        other_context, other_client = self.login("participant@example.test", "user-password-strong-1")
        self.assertEqual(other_client.get(f"/api/scenarios/{scenario_id}").status_code, 404)
        self.assertEqual(other_client.get(f"/api/scenarios/{scenario_id}/network-routes").status_code, 404)
        self.assertEqual(other_client.post(f"/api/scenarios/{scenario_id}/nodes/plc/lifecycle", json={"action": "stop"}).status_code, 404)
        other_context.__exit__(None, None, None)

        membership = self.client.post(f"/api/scenarios/{scenario_id}/members", json={
            "email": participant["email"], "role": "participant",
        })
        self.assertEqual(membership.status_code, 201, membership.text)
        self.assertEqual(self.client.get("/api/scenarios").json()[0]["access_role"], "admin")
        joined_context, joined_client = self.login("participant@example.test", "user-password-strong-1")
        self.assertEqual(joined_client.get(f"/api/scenarios/{scenario_id}").status_code, 200)
        self.assertEqual(joined_client.get("/api/scenarios").json()[0]["access_role"], "participant")
        self.assertEqual(joined_client.post(f"/api/scenarios/{scenario_id}/nodes/plc/lifecycle", json={"action": "stop"}).status_code, 403)
        self.assertEqual(joined_client.post(f"/api/scenarios/{scenario_id}/plcs/plc/write", json={"address": "DB1.DBX0.0", "value": True}).status_code, 409)
        joined_context.__exit__(None, None, None)

        owner_context, owner_client = self.login("designer@example.test", "user-password-strong-1")
        self.assertEqual(owner_client.delete(f"/api/scenarios/{scenario_id}").status_code, 204)
        self.assertEqual(owner_client.get(f"/api/scenarios/{scenario_id}").status_code, 404)
        archived = owner_client.get(f"/api/scenarios/{scenario_id}/audit-events")
        self.assertEqual(archived.status_code, 200, archived.text)
        self.assertIn("scenario.deleted", {event["event_type"] for event in archived.json()["events"]})
        owner_context.__exit__(None, None, None)

        member_context, member_client = self.login("participant@example.test", "user-password-strong-1")
        self.assertEqual(member_client.get(f"/api/scenarios/{scenario_id}/audit-events/integrity").status_code, 200)
        member_context.__exit__(None, None, None)

        outsider_context, outsider_client = self.login("outsider@example.test", "user-password-strong-1")
        self.assertEqual(outsider_client.get(f"/api/scenarios/{scenario_id}/audit-events").status_code, 404)
        outsider_context.__exit__(None, None, None)

        self.assertEqual(designer["role"], "designer")

    def test_evidence_bundle_download_is_limited_to_admin_and_scenario_instructor(self) -> None:
        self.bootstrap()
        owner = self.create_user("evidence-owner@example.test", "designer")
        instructor = self.create_user("evidence-instructor@example.test", "instructor")
        designer = self.create_user("evidence-designer@example.test", "designer")
        participant = self.create_user("evidence-participant@example.test", "participant")
        owner_context, owner_client = self.login(owner["email"], "user-password-strong-1")
        created = owner_client.post("/api/scenarios", json={
            "name": "Evidence role policy", "network": {"subnet": "172.31.35.0/24"},
            "devices": [{"node_id": "plc", "device_type": "plc-s7", "name": "PLC",
                         "ip": "172.31.35.10", "config": {"components": []}}],
        })
        self.assertEqual(created.status_code, 201, created.text)
        scenario_id = created.json()["id"]
        owner_context.__exit__(None, None, None)

        for user, role in ((instructor, "instructor"), (designer, "designer"), (participant, "participant")):
            membership = self.client.post(f"/api/scenarios/{scenario_id}/members", json={
                "email": user["email"], "role": role,
            })
            self.assertEqual(membership.status_code, 201, membership.text)

        owner_context, owner_client = self.login(owner["email"], "user-password-strong-1")
        self.assertEqual(owner_client.get(f"/api/scenarios/{scenario_id}/evidence-bundle.zip").status_code, 403)
        owner_context.__exit__(None, None, None)

        instructor_context, instructor_client = self.login(instructor["email"], "user-password-strong-1")
        instructor_response = instructor_client.get(f"/api/scenarios/{scenario_id}/evidence-bundle.zip")
        self.assertEqual(instructor_response.status_code, 200, instructor_response.text)
        self.assertTrue(instructor_response.content.startswith(b"PK\x03\x04"))
        self.assertRegex(instructor_response.headers.get("x-anvil-manifest-sha256", ""), r"^[a-f0-9]{64}$")
        instructor_context.__exit__(None, None, None)

        participant_context, participant_client = self.login(participant["email"], "user-password-strong-1")
        self.assertEqual(participant_client.get(f"/api/scenarios/{scenario_id}/evidence-bundle.zip").status_code, 403)
        participant_context.__exit__(None, None, None)

        self.assertEqual(self.client.get(f"/api/scenarios/{scenario_id}/evidence-bundle.zip").status_code, 200)

    def test_unowned_scenario_is_hidden_on_every_registered_http_route(self) -> None:
        self.bootstrap()
        owner_account = self.create_user("route-owner@example.test", "designer")
        outsider_account = self.create_user("route-outsider@example.test", "participant")
        owner_context, owner = self.login(owner_account["email"], "user-password-strong-1")
        created = owner.post("/api/scenarios", json={
            "name": "Object-level route coverage", "network": {"subnet": "172.31.34.0/24"},
            "devices": [{"node_id": "plc", "device_type": "plc-s7", "name": "PLC",
                         "ip": "172.31.34.10", "config": {"components": []}}],
        })
        self.assertEqual(created.status_code, 201, created.text)
        scenario_id = created.json()["id"]
        owner_context.__exit__(None, None, None)

        outsider_context, outsider = self.login(outsider_account["email"], "user-password-strong-1")
        listing = outsider.get("/api/scenarios")
        self.assertEqual(listing.status_code, 200, listing.text)
        self.assertNotIn(scenario_id, {item["id"] for item in listing.json()})

        checked = 0
        for template, operations in self.app.openapi()["paths"].items():
            if not template.startswith("/api/scenarios/{scenario_id}"):
                continue
            path = template.replace("{scenario_id}", scenario_id)
            for parameter in re.findall(r"\{([^}]+)\}", path):
                path = path.replace("{" + parameter + "}", "00000000-0000-0000-0000-000000000001"
                                    if parameter.endswith("_id") else "missing-node")
            for method in operations:
                if method not in {"get", "post", "put", "patch", "delete"}:
                    continue
                response = outsider.request(method.upper(), path, json={})
                self.assertEqual(response.status_code, 404,
                                 f"{method.upper()} {path} exposed an unowned scenario: {response.text}")
                checked += 1
        self.assertGreater(checked, 50)
        with self.assertRaises(WebSocketDisconnect) as disconnected:
            with outsider.websocket_connect(
                f"/api/scenarios/{scenario_id}/events", headers={"Origin": "http://testserver"},
            ):
                self.fail("An outsider connected to an owned scenario WebSocket")
        self.assertEqual(disconnected.exception.code, 4403)
        outsider_context.__exit__(None, None, None)

    def test_csrf_origin_mismatch_and_user_listing_excludes_secret_hashes(self) -> None:
        self.bootstrap()
        self.create_user("operator@example.test")
        forbidden = self.client.post("/api/users", headers={"Origin": "https://evil.example"}, json={
            "email": "blocked@example.test", "password": "user-password-strong-1", "role": "participant",
        })
        self.assertEqual(forbidden.status_code, 403)
        users = self.client.get("/api/users")
        self.assertEqual(users.status_code, 200)
        self.assertNotIn("password_hash", users.text)

    def test_cookie_authenticated_mutations_require_origin_but_bearer_clients_do_not(self) -> None:
        self.bootstrap()
        created = self.client.post("/api/scenarios", json={
            "name": "Origin protected", "network": {"subnet": "172.31.25.0/24"},
            "devices": [{"node_id": "plc", "device_type": "plc-s7", "name": "PLC",
                         "ip": "172.31.25.10", "config": {"components": []}}],
        })
        self.assertEqual(created.status_code, 201, created.text)

        cookie = self.client.cookies.get("anvil_session")
        missing_origin = TestClient(self.client.app, cookies={"anvil_session": cookie})
        rejected = missing_origin.post("/api/scenarios", json={
            "name": "No origin", "network": {"subnet": "172.31.26.0/24"}, "devices": [],
        })
        self.assertEqual(rejected.status_code, 403)

        context = TestClient(self.client.app, base_url="http://testserver")
        bearer_client = context.__enter__()
        token = self.client.cookies.get("anvil_session")
        accepted = bearer_client.post("/api/scenarios", headers={
            "Authorization": f"Bearer {token}", "Host": "testserver",
        }, json={
            "name": "Bearer client", "network": {"subnet": "172.31.27.0/24"},
            "devices": [{"node_id": "plc", "device_type": "plc-s7", "name": "PLC",
                         "ip": "172.31.27.10", "config": {"components": []}}],
        })
        self.assertEqual(accepted.status_code, 201, accepted.text)
        context.__exit__(None, None, None)

    def test_member_routes_require_owner_and_websocket_origin_is_checked(self) -> None:
        self.bootstrap()
        created = self.client.post("/api/scenarios", json={
            "name": "Protected members", "network": {"subnet": "172.31.23.0/24"},
            "devices": [{"node_id": "plc", "device_type": "plc-s7", "name": "PLC",
                         "ip": "172.31.23.10", "config": {"components": []}}],
        })
        self.assertEqual(created.status_code, 201, created.text)
        scenario_id = created.json()["id"]
        self.create_user("visitor@example.test", "participant")
        context, visitor = self.login("visitor@example.test", "user-password-strong-1")
        self.assertEqual(visitor.get(f"/api/scenarios/{scenario_id}/members").status_code, 404)
        with self.assertRaises(WebSocketDisconnect):
            with visitor.websocket_connect(
                f"/api/scenarios/{scenario_id}/events", headers={"Origin": "https://evil.example"},
            ):
                self.fail("Cross-origin WebSocket unexpectedly connected")
        context.__exit__(None, None, None)

    def test_participant_terminal_is_limited_to_opted_in_station_and_relays_bytes(self) -> None:
        self.bootstrap()
        created = self.client.post("/api/scenarios", json={
            "name": "Participant terminal",
            "network": {"subnet": "172.31.28.0/24"},
            "devices": [
                {"node_id": "attacker-on", "device_type": "attacker-station", "name": "Station on",
                 "ip": "172.31.28.10", "config": {"role": "attacker", "summary": "test", "participant_access_enabled": True}},
                {"node_id": "attacker-off", "device_type": "attacker-station", "name": "Station off",
                 "ip": "172.31.28.11", "config": {"role": "attacker", "summary": "test", "participant_access_enabled": False}},
                {"node_id": "engineering-on", "device_type": "engineering-station", "name": "Engineering on",
                 "ip": "172.31.28.12", "config": {"role": "engineering", "summary": "test", "engineering_access_enabled": True}},
                {"node_id": "engineering-off", "device_type": "engineering-station", "name": "Engineering off",
                 "ip": "172.31.28.13", "config": {"role": "engineering", "summary": "test", "engineering_access_enabled": False}},
            ],
        })
        self.assertEqual(created.status_code, 201, created.text)
        scenario_id = created.json()["id"]
        participant = self.create_user("terminal-user@example.test", "participant")
        membership = self.client.post(f"/api/scenarios/{scenario_id}/members", json={
            "email": participant["email"], "role": "participant",
        })
        self.assertEqual(membership.status_code, 201, membership.text)
        context, client = self.login(participant["email"], "user-password-strong-1")

        host_stream, participant_stream = socket.socketpair()
        closed = threading.Event()

        class TerminalManager:
            def __init__(self):
                self.opened = []
                self.engineering_stream = None

            def open_participant_terminal(self, sid, node_id):
                self.opened.append((sid, node_id))
                return "exec-test", host_stream

            def close_participant_terminal(self, exec_id):
                self.assert_exec_id = exec_id
                closed.set()

            def open_engineering_terminal(self, sid, node_id):
                self.opened.append((sid, node_id))
                return "engineering-exec", self.engineering_stream

        manager = TerminalManager()
        self.app.dependency_overrides[get_docker_manager] = lambda: manager
        with self.assertRaises(WebSocketDisconnect):
            with client.websocket_connect(
                f"/api/scenarios/{scenario_id}/participants/attacker-off/terminal",
                headers={"Origin": "http://testserver"},
            ):
                self.fail("A terminal connected to a station without network opt-in")
        self.assertEqual(manager.opened, [])

        with client.websocket_connect(
            f"/api/scenarios/{scenario_id}/participants/attacker-on/terminal",
            headers={"Origin": "http://testserver"},
        ) as websocket:
            self.assertIn("UID 65534", websocket.receive_text())
            participant_stream.sendall(b"shell-ready")
            self.assertEqual(websocket.receive_bytes(), b"shell-ready")
            websocket.send_bytes(b"id\n")
            self.assertEqual(participant_stream.recv(16), b"id\n")
            websocket.send_bytes(b"whoami\n")
            self.assertEqual(participant_stream.recv(16), b"whoami\n")
            host_stream.shutdown(socket.SHUT_RDWR)
            websocket.close()
        participant_stream.close()
        self.assertTrue(closed.wait(15), "terminal exec should be released when the browser disconnects")
        deadline = time.monotonic() + 5
        terminal_closed_audited = False
        while time.monotonic() < deadline:
            with Session(self.engine) as session:
                terminal_closed_audited = session.exec(select(ScenarioAuditEvent).where(
                    ScenarioAuditEvent.scenario_id == scenario_id,
                    ScenarioAuditEvent.event_type == "participant.terminal_closed",
                )).first() is not None
            if terminal_closed_audited:
                break
            time.sleep(0.01)
        self.assertTrue(terminal_closed_audited, "terminal close audit should finish before the app fixture is torn down")
        with Session(self.engine) as session:
            terminal_event = session.exec(select(ScenarioAuditEvent).where(
                ScenarioAuditEvent.scenario_id == scenario_id,
                ScenarioAuditEvent.event_type == "participant.terminal_closed",
            )).one()
            terminal_details = json.loads(terminal_event.details_json)
        self.assertEqual(terminal_details["input_frames"], 2)
        self.assertEqual(terminal_details["input_bytes"], len(b"id\nwhoami\n"))
        self.assertEqual(terminal_details["input_line_breaks"], 2)
        self.assertNotIn("whoami", terminal_event.details_json)
        self.assertNotIn("command", terminal_event.details_json)
        self.assertEqual(manager.opened, [(scenario_id, "attacker-on")])
        self.assertEqual(manager.assert_exec_id, "exec-test")
        context.__exit__(None, None, None)

        designer = self.create_user("engineering-user@example.test", "designer")
        membership = self.client.post(f"/api/scenarios/{scenario_id}/members", json={
            "email": designer["email"], "role": "designer",
        })
        self.assertEqual(membership.status_code, 201, membership.text)
        designer_context, designer_client = self.login(designer["email"], "user-password-strong-1")
        with self.assertRaises(WebSocketDisconnect):
            with designer_client.websocket_connect(
                f"/api/scenarios/{scenario_id}/engineering/engineering-off/terminal",
                headers={"Origin": "http://testserver"},
            ):
                self.fail("An engineering terminal connected without network opt-in")
        engineering_host, engineering_client = socket.socketpair()
        manager.engineering_stream = engineering_host
        closed.clear()
        with designer_client.websocket_connect(
            f"/api/scenarios/{scenario_id}/engineering/engineering-on/terminal",
            headers={"Origin": "http://testserver"},
        ) as websocket:
            self.assertIn("UID 65534", websocket.receive_text())
            websocket.send_bytes(b"id\n")
            self.assertEqual(engineering_client.recv(16), b"id\n")
            engineering_host.shutdown(socket.SHUT_RDWR)
            websocket.close()
        engineering_client.close()
        self.assertEqual(manager.opened[-1], (scenario_id, "engineering-on"))
        self.assertTrue(closed.wait(15), "engineering exec should be released when the browser disconnects")
        self.assertEqual(manager.assert_exec_id, "engineering-exec")
        designer_context.__exit__(None, None, None)

    def test_scenario_member_management_role_and_removal(self) -> None:
        self.bootstrap()
        created = self.client.post("/api/scenarios", json={
            "name": "Shared scenario", "network": {"subnet": "172.31.24.0/24"},
            "devices": [{"node_id": "plc", "device_type": "plc-s7", "name": "PLC",
                         "ip": "172.31.24.10", "config": {"components": []}}],
        })
        self.assertEqual(created.status_code, 201, created.text)
        scenario_id = created.json()["id"]
        self.create_user("member@example.test")
        granted = self.client.post(f"/api/scenarios/{scenario_id}/members", json={
            "email": "member@example.test", "role": "designer",
        })
        self.assertEqual(granted.status_code, 201, granted.text)
        self.assertEqual(self.client.get(f"/api/scenarios/{scenario_id}/members").json()[0]["role"], "designer")
        changed = self.client.post(f"/api/scenarios/{scenario_id}/members", json={
            "email": "member@example.test", "role": "participant",
        })
        self.assertEqual(changed.status_code, 201, changed.text)
        self.assertEqual(changed.json()["role"], "participant")
        removed = self.client.delete(f"/api/scenarios/{scenario_id}/members/{granted.json()['user_id']}")
        self.assertEqual(removed.status_code, 204)
        self.assertEqual(self.client.get(f"/api/scenarios/{scenario_id}/members").json(), [])

    def test_instructor_can_publish_shared_template_participants_can_instantiate(self) -> None:
        self.bootstrap()
        instructor = self.create_user("instructor@example.test", "instructor")
        participant = self.create_user("template-user@example.test", "participant")
        instructor_context, instructor_client = self.login(instructor["email"], "user-password-strong-1")
        scenario = instructor_client.post("/api/scenarios", json={
            "name": "Reusable line", "network": {"subnet": "172.31.28.0/24"},
            "devices": [{"node_id": "plc", "device_type": "plc-s7", "name": "PLC",
                         "ip": "172.31.28.10", "config": {"components": []}}],
        })
        self.assertEqual(scenario.status_code, 201, scenario.text)
        template = instructor_client.post("/api/templates", json={
            "scenario_id": scenario.json()["id"], "title": "Reusable line", "category": "Prueba",
            "summary": "Plantilla compartida para validar la biblioteca.", "tags": ["PLC", "demo"],
        })
        self.assertEqual(template.status_code, 201, template.text)
        template_id = template.json()["id"]
        self.assertEqual(template.json()["revision"], 1)
        with Session(self.engine) as session:
            saved_template = session.get(SharedScenarioTemplate, template_id)
            stored = json.loads(saved_template.scenario_json)
            self.assertEqual(saved_template.schema_version, 1)
            self.assertEqual(stored["format"], "anvil-scenario-template")
            self.assertEqual(stored["schema_version"], 1)
        self.assertIn(template.json(), instructor_client.get("/api/templates").json())
        instructor_context.__exit__(None, None, None)

        participant_context, participant_client = self.login(participant["email"], "user-password-strong-1")
        self.assertEqual(participant_client.post("/api/templates", json={
            "scenario_id": scenario.json()["id"], "title": "Unauthorized", "category": "Test",
            "summary": "No se debe permitir publicar.",
        }).status_code, 403)
        instantiated = participant_client.post(f"/api/templates/{template_id}/instantiate")
        self.assertEqual(instantiated.status_code, 201, instantiated.text)
        self.assertEqual(instantiated.json()["name"], "Reusable line")
        self.assertNotEqual(instantiated.json()["id"], scenario.json()["id"])
        self.assertEqual(participant_client.get(f"/api/templates/{template_id}/revisions").status_code, 200)
        self.assertEqual(participant_client.put(f"/api/templates/{template_id}", json={
            "expected_revision": 1, "title": "Unauthorized update",
        }).status_code, 403)
        self.assertEqual(participant_client.post(f"/api/templates/{template_id}/revisions/1/restore").status_code, 403)
        participant_context.__exit__(None, None, None)

        publisher_context, publisher_client = self.login(instructor["email"], "user-password-strong-1")
        updated = publisher_client.put(f"/api/templates/{template_id}", json={
            "expected_revision": 1, "title": "Reusable water line", "tags": ["water", "v2"],
        })
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["revision"], 2)
        self.assertEqual(updated.json()["title"], "Reusable water line")
        self.assertEqual(publisher_client.put(f"/api/templates/{template_id}", json={
            "expected_revision": 2, "title": None,
        }).status_code, 422)
        self.assertEqual(publisher_client.put(f"/api/templates/{template_id}", json={
            "expected_revision": 2,
        }).status_code, 422)
        self.assertEqual(publisher_client.put(f"/api/templates/{template_id}", json={
            "expected_revision": 1, "title": "Stale edit",
        }).status_code, 409)
        revisions = publisher_client.get(f"/api/templates/{template_id}/revisions")
        self.assertEqual([item["version"] for item in revisions.json()], [2, 1])
        restored = publisher_client.post(f"/api/templates/{template_id}/revisions/1/restore")
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(restored.json()["revision"], 3)
        self.assertEqual(restored.json()["title"], "Reusable line")
        self.assertEqual(publisher_client.delete(f"/api/templates/{template_id}").status_code, 204)
        self.assertNotIn(template_id, [item["id"] for item in publisher_client.get("/api/templates").json()])
        with Session(self.engine) as session:
            self.assertEqual(session.exec(select(SharedScenarioTemplateRevision).where(
                SharedScenarioTemplateRevision.template_id == template_id,
            )).all(), [])
        publisher_context.__exit__(None, None, None)

    def test_shared_component_presets_are_schema_validated_and_role_protected(self) -> None:
        self.bootstrap()
        instructor = self.create_user("components@example.test", "instructor")
        participant = self.create_user("component-reader@example.test", "participant")
        context, publisher = self.login(instructor["email"], "user-password-strong-1")
        valid = publisher.post("/api/components/templates", json={
            "title": "Válvula de proceso", "description": "Válvula compartida para ensayos de bombeo.",
            "device_type": "plc-s7", "component_type": "valve",
            "config": {"type": "valve", "name": "valvula_proceso", "db": 1, "initial_state": False},
        })
        self.assertEqual(valid.status_code, 201, valid.text)
        preset = valid.json()
        self.assertEqual(preset["schema_version"], 1)
        self.assertEqual(preset["revision"], 1)
        self.assertEqual(preset["config"]["type"], "valve")
        invalid = publisher.post("/api/components/templates", json={
            "title": "Tipo incorrecto", "description": "La configuración no coincide con el componente.",
            "device_type": "plc-s7", "component_type": "valve",
            "config": {"type": "alarm", "name": "alarma", "db": 1, "initial_state": False},
        })
        self.assertEqual(invalid.status_code, 422)
        bad_config = publisher.post("/api/components/templates", json={
            "title": "Config inválida", "description": "El estado inicial debe tener tipo booleano.",
            "device_type": "plc-s7", "component_type": "valve",
            "config": {"type": "valve", "name": "valvula_invalida", "db": 1, "initial_state": "false"},
        })
        self.assertEqual(bad_config.status_code, 422)
        unknown = publisher.post("/api/components/templates", json={
            "title": "Campo desconocido", "description": "Se debe rechazar un ajuste no reconocido.",
            "device_type": "plc-s7", "component_type": "analog_sensor",
            "config": {"type": "analog_sensor", "name": "nivel", "db": 1,
                       "simulation": {"mode": "constant", "noise": 0, "mystery": True}},
        })
        self.assertEqual(unknown.status_code, 422)
        self.assertIn(preset["id"], [item["id"] for item in publisher.get("/api/components/templates").json()])
        initial_history = publisher.get(f"/api/components/templates/{preset['id']}/revisions")
        self.assertEqual([item["version"] for item in initial_history.json()], [1])
        bad_update = publisher.put(f"/api/components/templates/{preset['id']}", json={
            "expected_revision": 1, "config": {"type": "valve", "name": "invalid", "db": 1, "initial_state": "false"},
        })
        self.assertEqual(bad_update.status_code, 422)
        context.__exit__(None, None, None)

        reader_context, reader = self.login(participant["email"], "user-password-strong-1")
        self.assertIn(preset["id"], [item["id"] for item in reader.get("/api/components/templates").json()])
        self.assertEqual(reader.get(f"/api/components/templates/{preset['id']}/revisions").status_code, 200)
        self.assertEqual(reader.put(f"/api/components/templates/{preset['id']}", json={
            "expected_revision": 1, "title": "Unauthorized edit",
        }).status_code, 403)
        self.assertEqual(reader.post(f"/api/components/templates/{preset['id']}/revisions/1/restore").status_code, 403)
        self.assertEqual(reader.post("/api/components/templates", json={
            "title": "No permitido", "description": "El participante solo puede usar presets.",
            "device_type": "plc-s7", "component_type": "valve",
            "config": {"type": "valve", "name": "valvula", "db": 1, "initial_state": False},
        }).status_code, 403)
        self.assertEqual(reader.delete(f"/api/components/templates/{preset['id']}").status_code, 403)
        reader_context.__exit__(None, None, None)

        context, publisher = self.login(instructor["email"], "user-password-strong-1")
        updated = publisher.put(f"/api/components/templates/{preset['id']}", json={
            "expected_revision": 1, "title": "Válvula de proceso revisada",
            "config": {"type": "valve", "name": "valvula_proceso", "db": 1, "initial_state": True},
        })
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["revision"], 2)
        self.assertTrue(updated.json()["config"]["initial_state"])
        self.assertEqual(publisher.put(f"/api/components/templates/{preset['id']}", json={
            "expected_revision": 1, "title": "Edición obsoleta",
        }).status_code, 409)
        self.assertEqual(publisher.put(f"/api/components/templates/{preset['id']}", json={
            "expected_revision": 2,
        }).status_code, 422)
        self.assertEqual(publisher.put(f"/api/components/templates/{preset['id']}", json={
            "expected_revision": 2, "title": None,
        }).status_code, 422)
        history = publisher.get(f"/api/components/templates/{preset['id']}/revisions")
        self.assertEqual([item["version"] for item in history.json()], [2, 1])
        restored = publisher.post(f"/api/components/templates/{preset['id']}/revisions/1/restore")
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(restored.json()["revision"], 3)
        self.assertFalse(restored.json()["config"]["initial_state"])
        self.assertEqual(publisher.delete(f"/api/components/templates/{preset['id']}").status_code, 204)
        self.assertEqual(publisher.get(f"/api/components/templates/{preset['id']}/revisions").status_code, 404)
        context.__exit__(None, None, None)
        with Session(self.engine) as session:
            self.assertEqual(session.exec(select(SharedComponentPresetRevision).where(
                SharedComponentPresetRevision.preset_id == preset["id"],
            )).all(), [])

    def test_role_route_policy_defaults_to_deny_for_unlisted_scenario_operations(self) -> None:
        base = "/api/scenarios/123e4567-e89b-12d3-a456-426614174000"
        self.assertTrue(_allowed("instructor", "POST", "/api/scenarios/123e4567-e89b-12d3-a456-426614174000/deploy"))
        self.assertTrue(_allowed("instructor", "POST", "/api/scenarios/123e4567-e89b-12d3-a456-426614174000/restart"))
        self.assertTrue(_allowed("instructor", "POST", "/api/scenarios/123e4567-e89b-12d3-a456-426614174000/pause"))
        self.assertTrue(_allowed("admin", "POST", "/api/scenarios/123e4567-e89b-12d3-a456-426614174000/resume"))
        self.assertTrue(_allowed("instructor", "GET", f"{base}/node-operations"))
        self.assertFalse(_allowed("participant", "GET", f"{base}/node-operations"))
        self.assertFalse(_allowed("designer", "POST", "/api/scenarios/123e4567-e89b-12d3-a456-426614174000/pause"))
        self.assertFalse(_allowed("designer", "POST", "/api/scenarios/123e4567-e89b-12d3-a456-426614174000/restart"))
        self.assertFalse(_allowed("participant", "POST", "/api/scenarios/123e4567-e89b-12d3-a456-426614174000/restart"))
        self.assertFalse(_allowed("instructor", "POST", "/api/scenarios/123e4567-e89b-12d3-a456-426614174000/unknown-admin-action"))
        self.assertFalse(_allowed("participant", "POST", "/api/scenarios/123e4567-e89b-12d3-a456-426614174000/field/pump/control"))
        self.assertFalse(_allowed("participant", "GET", "/api/scenarios/123e4567-e89b-12d3-a456-426614174000/captures/123e4567-e89b-12d3-a456-426614174000/pcap"))
        self.assertFalse(_allowed("participant", "GET", "/api/scenarios/123e4567-e89b-12d3-a456-426614174000/deployment-bundle"))
        evidence_path = f"{base}/evidence-bundle.zip"
        self.assertTrue(_allowed("instructor", "GET", evidence_path))
        self.assertFalse(_allowed("designer", "GET", evidence_path))
        self.assertFalse(_allowed("participant", "GET", evidence_path))
        terminal_path = f"{base}/participants/attacker-1/terminal"
        self.assertTrue(_allowed("participant", "GET", terminal_path))
        self.assertTrue(_allowed("instructor", "GET", terminal_path))
        self.assertTrue(_allowed("admin", "GET", terminal_path))
        self.assertFalse(_allowed("designer", "GET", terminal_path))
        engineering_terminal_path = f"{base}/engineering/engineering-1/terminal"
        self.assertTrue(_allowed("designer", "GET", engineering_terminal_path))
        self.assertTrue(_allowed("instructor", "GET", engineering_terminal_path))
        self.assertTrue(_allowed("admin", "GET", engineering_terminal_path))
        self.assertFalse(_allowed("participant", "GET", engineering_terminal_path))
        self.assertTrue(_allowed("participant", "POST", "/api/scenarios/123e4567-e89b-12d3-a456-426614174000/plcs/plc/write"))
        self.assertTrue(_allowed("instructor", "POST", "/api/scenarios/123e4567-e89b-12d3-a456-426614174000/field/pump/control"))
        self.assertTrue(_allowed("instructor", "POST", "/api/scenarios/123e4567-e89b-12d3-a456-426614174000/exercises"))
        self.assertFalse(_allowed("designer", "POST", "/api/scenarios/123e4567-e89b-12d3-a456-426614174000/exercises"))
        self.assertTrue(_allowed("designer", "POST", "/api/templates/water-pump/instantiate"))
        self.assertTrue(_allowed("participant", "POST", "/api/templates/water-pump/instantiate"))
        component_id = "component-0123456789abcdef0123456789abcdef"
        self.assertTrue(_allowed("participant", "GET", f"/api/components/templates/{component_id}/revisions"))
        self.assertTrue(_allowed("instructor", "PUT", f"/api/components/templates/{component_id}"))
        self.assertTrue(_allowed("instructor", "POST", f"/api/components/templates/{component_id}/revisions/1/restore"))
        self.assertTrue(_allowed("designer", "GET", f"/api/components/templates/{component_id}/revisions"))
        self.assertFalse(_allowed("participant", "PUT", f"/api/components/templates/{component_id}"))
        self.assertFalse(_allowed("designer", "POST", "/api/scenarios/123e4567-e89b-12d3-a456-426614174000/deploy"))
        self.assertTrue(_allowed("designer", "GET", f"{base}/network-routes"))
        self.assertTrue(_allowed("designer", "GET", f"{base}/devices/plc-1/profile"))
        self.assertTrue(_allowed("instructor", "POST", f"{base}/devices/import"))
        self.assertFalse(_allowed("participant", "GET", f"{base}/devices/plc-1/profile"))
        self.assertFalse(_allowed("participant", "POST", f"{base}/devices/import"))
        self.assertTrue(_allowed("designer", "GET", f"{base}/services"))
        self.assertTrue(_allowed("instructor", "GET", f"{base}/services"))
        self.assertFalse(_allowed("participant", "GET", f"{base}/services"))
        self.assertTrue(_allowed("participant", "GET", f"{base}/scada/scada/history/export.csv"))
        self.assertTrue(_allowed("participant", "GET", f"{base}/scada/scada/history/aggregate"))

    def test_each_scenario_http_route_is_reachable_by_an_explicit_non_admin_role(self) -> None:
        roles = ("instructor", "designer", "participant")
        scenario_id = "123e4567-e89b-12d3-a456-426614174000"
        checked = 0
        for template, operations in self.app.openapi()["paths"].items():
            if not template.startswith("/api/scenarios/"):
                continue
            path = template
            for name in re.findall(r"\{([^}]+)\}", template):
                value = scenario_id if name in {"scenario_id", "revision_id", "exercise_id", "capture_id", "user_id"} else "node"
                path = path.replace("{" + name + "}", value)
            for method in operations:
                checked += 1
                self.assertTrue(
                    any(_allowed(role, method.upper(), path) for role in roles),
                    f"No non-admin role can access registered route {method} {path}",
                )
        self.assertGreater(checked, 40)

    def test_owned_scenario_member_can_download_scada_history_csv(self) -> None:
        self.bootstrap()
        designer = self.create_user("history-owner@example.test", "designer")
        self.create_user("history-outsider@example.test", "participant")
        context, owner = self.login(designer["email"], "user-password-strong-1")
        created = owner.post("/api/scenarios", json={
            "name": "SCADA export auth", "network": {"subnet": "172.31.24.0/24"},
            "devices": [
                {"node_id": "scada", "device_type": "scada", "name": "SCADA", "ip": "172.31.24.10",
                 "config": {"target_node_ids": "plc"}},
                {"node_id": "plc", "device_type": "plc-s7", "name": "PLC", "ip": "172.31.24.11",
                 "config": {"components": [{"type": "analog_sensor", "name": "Level"}]}},
            ],
        })
        self.assertEqual(created.status_code, 201, created.text)
        scenario_id = created.json()["id"]
        csv_response = owner.get(f"/api/scenarios/{scenario_id}/scada/scada/history/export.csv")
        self.assertEqual(csv_response.status_code, 200, csv_response.text)
        self.assertIn("timestamp,target_node_id,address", csv_response.text)
        context.__exit__(None, None, None)

        outsider_context, outsider = self.login("history-outsider@example.test", "user-password-strong-1")
        self.assertEqual(outsider.get(f"/api/scenarios/{scenario_id}/scada/scada/history/export.csv").status_code, 404)
        outsider_context.__exit__(None, None, None)


if __name__ == "__main__":
    unittest.main()
