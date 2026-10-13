"""Session authentication, role policy, and scenario-level authorization."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from sqlmodel import Session, select
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from models.auth import PlatformSession, PlatformUser, ScenarioMembership
from models.audit import ScenarioAuditArchive
from models.scenario import ScenarioRecord
from services.auth_service import COOKIE_NAME, public_user, resolve_session
from services.request_context import audit_actor


class PlatformAuthMiddleware:
    def __init__(self, app: Any, *, enabled: bool, database_engine: Any) -> None:
        self.app = app
        self.enabled = enabled
        self.engine = database_engine

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        path = scope.get("path", "")
        if scope["type"] == "http" and path.startswith("/api/"):
            downstream_send = send

            async def no_store_send(message: dict[str, Any]) -> None:
                if message.get("type") == "http.response.start":
                    headers = [
                        (name, value) for name, value in message.get("headers", [])
                        if name.lower() != b"cache-control"
                    ]
                    headers.append((b"cache-control", b"no-store"))
                    message = {**message, "headers": headers}
                await downstream_send(message)

            send = no_store_send
        if not self.enabled or scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        if scope["type"] == "http" and not path.startswith("/api/"):
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        token = _session_token(headers)
        actor_id: str | None = None
        with Session(self.engine) as session:
            resolved = resolve_session(session, token)
            public = scope["type"] == "http" and _is_public(path)
            if public:
                if path in {"/api/auth/login", "/api/auth/bootstrap"} and not _origin_matches(scope, headers):
                    await _reject(scope, receive, send, 403, "Request origin does not match this Anvil host")
                    return
                if resolved is not None:
                    state = scope.setdefault("state", {})
                    state["current_user"] = public_user(resolved[0])
                    state["current_session_id"] = resolved[1].id
                    actor_id = resolved[0].id
            elif resolved is None:
                await _reject(scope, receive, send, 401, "Authentication required")
                return
            else:
                user, session_row = resolved
                if not _origin_matches(scope, headers):
                    await _reject(scope, receive, send, 403, "Request origin does not match this Anvil host")
                    return
                scenario_id = _scenario_id(path)
                effective_role = user.role
                if scenario_id and user.role != "admin":
                    record = session.get(ScenarioRecord, scenario_id)
                    if record is None:
                        archive = session.get(ScenarioAuditArchive, scenario_id)
                        archived_audit_path = bool(re.fullmatch(
                            rf"/api/scenarios/{_ID}/audit-events(?:/(?:export|integrity))?", path
                        ))
                        if archive is None or not archived_audit_path:
                            await _reject(scope, receive, send, 404, "Scenario not found")
                            return
                        archived_memberships = json.loads(archive.memberships_json or "{}")
                        if archive.owner_user_id != user.id:
                            effective_role = archived_memberships.get(user.id, "")
                            if not effective_role:
                                await _reject(scope, receive, send, 404, "Scenario not found")
                                return
                    elif record.owner_user_id != user.id:
                        membership = session.exec(select(ScenarioMembership).where(
                            ScenarioMembership.scenario_id == scenario_id,
                            ScenarioMembership.user_id == user.id,
                        )).first()
                        if membership is None:
                            await _reject(scope, receive, send, 404, "Scenario not found")
                            return
                        effective_role = membership.role
                method = scope.get("method", "GET").upper()
                if not _allowed(effective_role, method, path):
                    await _reject(scope, receive, send, 403, "Role does not allow this operation")
                    return
                state = scope.setdefault("state", {})
                state["current_user"] = public_user(user)
                state["current_session_id"] = session_row.id
                state["effective_role"] = effective_role
                actor_id = user.id
        context_token = audit_actor.set(f"user:{actor_id}" if actor_id else None)
        try:
            await self.app(scope, receive, send)
        finally:
            audit_actor.reset(context_token)


def _session_token(headers: Headers) -> str | None:
    token = headers.get("authorization", "")
    if token.lower().startswith("bearer "):
        return token[7:].strip()
    cookie = headers.get("cookie", "")
    for part in cookie.split(";"):
        name, separator, value = part.strip().partition("=")
        if separator and name == COOKIE_NAME:
            return value
    return None


def _is_public(path: str) -> bool:
    return path in {
        "/api/auth/status", "/api/auth/bootstrap", "/api/auth/login",
        "/api/health", "/api/ready",
    }


def _scenario_id(path: str) -> str | None:
    parts = path.split("/")
    if len(parts) < 4 or parts[1:3] != ["api", "scenarios"]:
        return None
    try:
        return str(UUID(parts[3]))
    except ValueError:
        return None


def _allowed(role: str, method: str, path: str) -> bool:
    if _participant_terminal(path):
        return role in {"admin", "instructor", "participant"}
    if _engineering_terminal(path):
        return role in {"admin", "instructor", "designer"}
    if role == "admin":
        return True
    if path.startswith("/api/auth/"):
        return path in {"/api/auth/me", "/api/auth/logout", "/api/auth/password"}
    if path.startswith("/api/users"):
        return False
    if _component_template_access(path, method):
        return True
    if role == "instructor" and _component_template_manage(path, method):
        return True
    if role == "instructor":
        return _scenario_read(path, method) or _scenario_edit(path, method) or _scenario_operate(path, method, role) or _template_access(role, path, method) or _template_manage(path, method) or _catalog_access(path, method)
    if role == "designer":
        return _scenario_read(path, method) or _scenario_edit(path, method) or _template_access(role, path, method) or _catalog_access(path, method)
    if role == "participant":
        return _scenario_participant_read(path, method) or _participant_write(path, method) or _template_access(role, path, method) or _catalog_access(path, method)
    return False


_ID = r"[0-9a-fA-F-]+"


def _participant_terminal(path: str) -> bool:
    return bool(re.fullmatch(rf"/api/scenarios/{_ID}/participants/[^/]+/terminal", path))


def _engineering_terminal(path: str) -> bool:
    return bool(re.fullmatch(rf"/api/scenarios/{_ID}/engineering/[^/]+/terminal", path))


def _scenario_read(path: str, method: str) -> bool:
    if method != "GET":
        return False
    return path == "/api/scenarios" or bool(re.fullmatch(
        rf"/api/scenarios/{_ID}(?:/(?:export|devices/[^/]+/profile|deployment-bundle|network-routes|services|revisions(?:/{_ID})?|io-map|diagnostics|field-migration-preview|field/values|logs|audit-events(?:/(?:export|integrity))?|status|protocol|events|plcs/[^/]+/values|scada/[^/]+/(?:snapshot|history|history/aggregate|history/export\.csv|alarms)|hmi/[^/]+/values|modbus/[^/]+/values))?",
        path,
    ))


def _scenario_edit(path: str, method: str) -> bool:
    if method == "POST" and path in {"/api/scenarios", "/api/scenarios/import"}:
        return True
    if method == "POST" and re.fullmatch(rf"/api/scenarios/{_ID}/clone", path):
        return True
    if method == "POST" and re.fullmatch(rf"/api/scenarios/{_ID}/field-migration-copy", path):
        return True
    if method == "POST" and re.fullmatch(rf"/api/scenarios/{_ID}/devices/import", path):
        return True
    if method == "POST" and re.fullmatch(rf"/api/scenarios/{_ID}/revisions/{_ID}/restore", path):
        return True
    if method == "POST" and re.fullmatch(rf"/api/scenarios/{_ID}/members", path):
        return True
    if method == "DELETE" and re.fullmatch(rf"/api/scenarios/{_ID}/members/{_ID}", path):
        return True
    if method == "GET" and re.fullmatch(rf"/api/scenarios/{_ID}/members", path):
        return True
    if method == "PUT" and re.fullmatch(rf"/api/scenarios/{_ID}", path):
        return True
    if method == "DELETE" and re.fullmatch(rf"/api/scenarios/{_ID}", path):
        return True
    return False


def _scenario_operate(path: str, method: str, role: str) -> bool:
    if method == "POST":
        if re.fullmatch(rf"/api/scenarios/{_ID}/exercises", path):
            return role == "instructor"
        return bool(re.fullmatch(rf"/api/scenarios/{_ID}/(?:deploy|stop|restart|pause|resume|nodes/[^/]+/lifecycle|captures|exercises/{_ID}/(?:start|cancel)|field/[^/]+/control|plcs/[^/]+/write|modbus/[^/]+/write|hmi/[^/]+/(?:write|alarms/acknowledge)|scada/[^/]+/(?:write|alarms/acknowledge))", path))
    if method == "GET" and re.fullmatch(rf"/api/scenarios/{_ID}/logs", path):
        return True
    if method == "GET" and re.fullmatch(rf"/api/scenarios/{_ID}/node-operations", path):
        return role == "instructor"
    if method == "GET" and re.fullmatch(rf"/api/scenarios/{_ID}/captures/{_ID}/pcap", path):
        return role == "instructor"
    if method == "GET" and re.fullmatch(rf"/api/scenarios/{_ID}/evidence-bundle\.zip", path):
        return role == "instructor"
    if method == "GET" and re.fullmatch(rf"/api/scenarios/{_ID}/events", path):
        return True
    return method == "GET" and bool(re.fullmatch(rf"/api/scenarios/{_ID}/exercises(?:/{_ID})?", path))


def _scenario_participant_read(path: str, method: str) -> bool:
    if method != "GET":
        return False
    if re.fullmatch(rf"/api/scenarios/{_ID}/captures/{_ID}/pcap", path):
        return False
    if re.fullmatch(rf"/api/scenarios/{_ID}/(?:export|devices/[^/]+/profile|deployment-bundle|services|revisions(?:/{_ID})?|audit-events/export)", path):
        return False
    return _scenario_read(path, method) or bool(re.fullmatch(
        rf"/api/scenarios/{_ID}/(?:exercises(?:/{_ID})?|field/values|hmi/[^/]+/values|modbus/[^/]+/values|scada/[^/]+/(?:snapshot|history|alarms))",
        path,
    ))


def _participant_write(path: str, method: str) -> bool:
    if method != "POST":
        return False
    return bool(re.fullmatch(
        rf"/api/scenarios/{_ID}/(?:plcs/[^/]+/write|modbus/[^/]+/write|hmi/[^/]+/(?:write|alarms/acknowledge)|scada/[^/]+/(?:write|alarms/acknowledge))",
        path,
    ))


def _template_access(role: str, path: str, method: str) -> bool:
    if method == "GET" and path == "/api/templates":
        return True
    if method == "GET" and bool(re.fullmatch(r"/api/templates/custom-[0-9a-f]{32}/revisions", path)):
        return True
    return method == "POST" and bool(re.fullmatch(r"/api/templates/[^/]+/instantiate", path))


def _template_manage(path: str, method: str) -> bool:
    return (method == "POST" and path == "/api/templates") or (
        method == "DELETE" and bool(re.fullmatch(r"/api/templates/custom-[0-9a-f]{32}", path))
    ) or (method == "PUT" and bool(re.fullmatch(r"/api/templates/custom-[0-9a-f]{32}", path))) or (
        method == "POST" and bool(re.fullmatch(
            r"/api/templates/custom-[0-9a-f]{32}/revisions/[1-9][0-9]*/restore", path,
        ))
    )


def _component_template_manage(path: str, method: str) -> bool:
    return (method == "POST" and path == "/api/components/templates") or (
        method == "DELETE" and bool(re.fullmatch(r"/api/components/templates/component-[0-9a-f]{32}", path))
    ) or (method == "PUT" and bool(re.fullmatch(r"/api/components/templates/component-[0-9a-f]{32}", path))) or (
        method == "POST" and bool(re.fullmatch(
            r"/api/components/templates/component-[0-9a-f]{32}/revisions/[1-9][0-9]*/restore", path,
        ))
    )


def _component_template_access(path: str, method: str) -> bool:
    return (method == "GET" and path == "/api/components/templates") or (
        method == "GET" and bool(re.fullmatch(
            r"/api/components/templates/component-[0-9a-f]{32}/revisions", path,
        ))
    )


def _catalog_access(path: str, method: str) -> bool:
    return method == "GET" and path == "/api/scenarios/catalog"


def _origin_matches(scope: dict[str, Any], headers: Headers) -> bool:
    origin = headers.get("origin")
    method = scope.get("method", "GET").upper()
    if scope["type"] == "http" and method in {"GET", "HEAD", "OPTIONS"}:
        return True
    if not origin:
        # Browser sessions use cookies, so state-changing requests must carry an
        # Origin header. Bearer-token API clients can omit it.
        if scope["type"] == "http" and method in {"POST", "PUT", "PATCH", "DELETE"}:
            cookie = headers.get("cookie", "")
            return not any(
                name == COOKIE_NAME
                for part in cookie.split(";")
                for name, separator, _value in [part.strip().partition("=")]
                if separator
            )
        return False
    origin_host = urlsplit(origin).netloc.lower()
    request_host = headers.get("host", "").lower()
    return bool(origin_host and request_host and origin_host == request_host)


async def _reject(scope: dict[str, Any], receive: Any, send: Any, code: int, message: str) -> None:
    if scope["type"] == "websocket":
        await send({"type": "websocket.close", "code": 4401 if code == 401 else 4403})
        return
    response = JSONResponse({"detail": message}, status_code=code)
    await response(scope, receive, send)
