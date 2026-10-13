"""Bootstrap, login, logout, and platform user administration."""

from __future__ import annotations

import hashlib
import hmac
import os
import threading
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, select

from models.auth import (
    AdminPasswordReset, BootstrapRequest, LoginRequest, PasswordChange, PlatformRole, PlatformSession,
    PlatformUser, UserCreate, UserRead, UserRoleUpdate,
)
from models.platform_audit import PlatformAuditEventPage, PlatformAuditIntegrity
from models.db import get_session
from services.auth_service import COOKIE_NAME, create_session, hash_password, public_user, resolve_session, revoke_session, verify_password
from services.platform_audit import append_platform_audit, list_platform_audit_events, verify_platform_audit


router = APIRouter(prefix="/api/auth", tags=["authentication"])
users_router = APIRouter(prefix="/api/users", tags=["users"])
platform_audit_router = APIRouter(prefix="/api/admin/audit-events", tags=["platform audit"])
SessionDependency = Annotated[Session, Depends(get_session)]
_RATE_LIMIT_PRUNE_LOCK = threading.Lock()
_RATE_LIMIT_OPERATIONS = 0
_LOGIN_ATTEMPT_LIMIT = 10
_LOGIN_ATTEMPT_WINDOW_SECONDS = 60
_DUMMY_PASSWORD_HASH = hash_password("anvil-invalid-login-dummy-password")


@router.get("/status")
def auth_status(request: Request, session: SessionDependency) -> dict:
    required = bool(getattr(request.app.state, "auth_enabled", False))
    setup_required = session.exec(select(PlatformUser.id).limit(1)).first() is None
    user = getattr(request.state, "current_user", None)
    return {
        "auth_required": required,
        "setup_required": setup_required if required else False,
        "bootstrap_enabled": bool(os.environ.get("ANVIL_BOOTSTRAP_TOKEN")) if required else False,
        "authenticated": user is not None,
        "user": user.model_dump(mode="json") if user is not None else None,
    }


@router.post("/bootstrap", status_code=status.HTTP_201_CREATED)
def bootstrap_account(payload: BootstrapRequest, request: Request, response: Response, session: SessionDependency) -> dict:
    if not getattr(request.app.state, "auth_enabled", False):
        raise HTTPException(status_code=404, detail="Authentication is disabled")
    configured_token = os.environ.get("ANVIL_BOOTSTRAP_TOKEN", "")
    if len(configured_token) < 32:
        raise HTTPException(status_code=503, detail="Initial account setup is disabled; configure a 32+ character bootstrap token")
    remote = request.client.host if request.client else "unknown"
    _limit_login(session, f"bootstrap:{remote}")
    if not hmac.compare_digest(payload.token.get_secret_value(), configured_token):
        raise HTTPException(status_code=403, detail="Invalid bootstrap token")
    if session.exec(select(PlatformUser.id).limit(1)).first() is not None:
        raise HTTPException(status_code=409, detail="Initial setup has already been completed")
    user = PlatformUser(
        email=payload.email,
        password_hash=hash_password(payload.password.get_secret_value()),
        role="admin",
    )
    session.add(user)
    try:
        append_platform_audit(
            session, "account.bootstrap_completed", actor_user_id=user.id,
            target_user_id=user.id, details={"role": "admin"},
        )
        session.commit()
        session.refresh(user)
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(status_code=409, detail="Initial account setup was completed concurrently") from error
    except SQLAlchemyError as error:
        session.rollback()
        raise HTTPException(status_code=503, detail="Initial account setup is temporarily unavailable") from error
    session_row, token = create_session(session, user)
    _clear_login_attempts(session, f"bootstrap:{remote}")
    _set_session_cookie(response, request, token)
    return {"user": public_user(user), "expires_at": session_row.expires_at}


@router.post("/login")
def login(payload: LoginRequest, request: Request, response: Response, session: SessionDependency) -> dict:
    if not getattr(request.app.state, "auth_enabled", False):
        raise HTTPException(status_code=404, detail="Authentication is disabled")
    remote = request.client.host if request.client else "unknown"
    _limit_login(session, remote)
    user = session.exec(select(PlatformUser).where(PlatformUser.email == payload.email)).first()
    encoded = user.password_hash if user else _DUMMY_PASSWORD_HASH
    valid = verify_password(payload.password.get_secret_value(), encoded)
    if user is None or not user.active or not valid:
        raise HTTPException(status_code=401, detail="Email or password is incorrect")
    _clear_login_attempts(session, remote)
    session_row, token = create_session(session, user)
    _set_session_cookie(response, request, token)
    return {"user": public_user(user), "expires_at": session_row.expires_at}


@router.get("/me", response_model=UserRead)
def current_user(request: Request) -> UserRead:
    user = getattr(request.state, "current_user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, session: SessionDependency) -> Response:
    session_id = getattr(request.state, "current_session_id", None)
    if session_id:
        session_row = session.get(PlatformSession, session_id)
        if session_row is not None:
            revoke_session(session, session_row)
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="strict", secure=_secure_cookie(request))
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(payload: PasswordChange, request: Request, session: SessionDependency) -> Response:
    user = _request_user(request)
    record = session.get(PlatformUser, user.id)
    if record is None or not verify_password(payload.current_password.get_secret_value(), record.password_hash):
        raise HTTPException(status_code=403, detail="Current password is incorrect")
    record.password_hash = hash_password(payload.new_password.get_secret_value())
    session.add(record)
    other_sessions = session.exec(select(PlatformSession).where(
        PlatformSession.user_id == user.id,
        PlatformSession.id != getattr(request.state, "current_session_id", ""),
    )).all()
    for row in other_sessions:
        session.delete(row)
    append_platform_audit(
        session, "account.password_changed", actor_user_id=user.id,
        target_user_id=user.id, details={"other_sessions_revoked": len(other_sessions)},
    )
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@users_router.get("", response_model=list[UserRead])
def list_users(session: SessionDependency) -> list[UserRead]:
    users = session.exec(select(PlatformUser).order_by(PlatformUser.email)).all()
    return [public_user(item) for item in users]


@users_router.post("", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def create_user(payload: UserCreate, request: Request, session: SessionDependency) -> UserRead:
    if session.exec(select(PlatformUser.id).where(PlatformUser.email == payload.email)).first():
        raise HTTPException(status_code=409, detail="A user with this email already exists")
    user = PlatformUser(email=payload.email, password_hash=hash_password(payload.password.get_secret_value()), role=payload.role)
    session.add(user)
    try:
        append_platform_audit(
            session, "account.created", actor_user_id=_request_user(request).id,
            target_user_id=user.id, details={"role": user.role},
        )
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(status_code=409, detail="A user with this email already exists") from error
    session.refresh(user)
    return public_user(user)


@users_router.patch("/{user_id}", response_model=UserRead)
def update_user(user_id: str, payload: UserRoleUpdate, request: Request, session: SessionDependency) -> UserRead:
    user = session.get(PlatformUser, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.role == "admin" and (payload.role != "admin" or not payload.active):
        active_admins = session.exec(select(PlatformUser.id).where(PlatformUser.role == "admin", PlatformUser.active == True)).all()  # noqa: E712
        if len(active_admins) <= 1:
            raise HTTPException(status_code=409, detail="The last active administrator cannot be demoted or disabled")
    previous_role = user.role
    previous_active = user.active
    user.role = payload.role
    user.active = payload.active
    session.add(user)
    if not user.active:
        for row in session.exec(select(PlatformSession).where(PlatformSession.user_id == user.id)).all():
            session.delete(row)
    if user.role != previous_role or user.active != previous_active:
        append_platform_audit(
            session, "account.updated", actor_user_id=_request_user(request).id,
            target_user_id=user.id, details={
                "previous_role": previous_role, "role": user.role,
                "previous_active": previous_active, "active": user.active,
            },
        )
    session.commit()
    return public_user(user)


@users_router.post("/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
def admin_reset_password(user_id: str, payload: AdminPasswordReset, request: Request, session: SessionDependency) -> Response:
    """Set a user's new password and revoke every existing session."""
    user = session.get(PlatformUser, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    user.password_hash = hash_password(payload.new_password.get_secret_value())
    session.add(user)
    sessions = session.exec(select(PlatformSession).where(PlatformSession.user_id == user.id)).all()
    for row in sessions:
        session.delete(row)
    append_platform_audit(
        session, "account.password_reset", actor_user_id=_request_user(request).id,
        target_user_id=user.id, details={"sessions_revoked": len(sessions)},
    )
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@platform_audit_router.get("", response_model=PlatformAuditEventPage)
def read_platform_audit(
    session: SessionDependency,
    limit: int = Query(default=100, ge=1, le=1000),
    before_id: int | None = Query(default=None, ge=1),
) -> PlatformAuditEventPage:
    events, next_before_id = list_platform_audit_events(session, limit=limit, before_id=before_id)
    return PlatformAuditEventPage(events=events, next_before_id=next_before_id)


@platform_audit_router.get("/integrity", response_model=PlatformAuditIntegrity)
def platform_audit_integrity(session: SessionDependency) -> PlatformAuditIntegrity:
    return verify_platform_audit(session)


def _request_user(request: Request) -> UserRead:
    user = getattr(request.state, "current_user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def _rate_limit_key(remote: str) -> str:
    return hashlib.sha256(remote.encode("utf-8", errors="replace")).hexdigest()


def _limit_login(session: Session, remote: str, *, now: float | None = None) -> None:
    """Atomically count attempts in the shared SQLite DB, across app workers."""
    global _RATE_LIMIT_OPERATIONS
    current = time.time() if now is None else now
    key_hash = _rate_limit_key(remote)
    try:
        result = session.execute(text("""
            INSERT INTO auth_rate_limit_attempts (key_hash, attempted_at)
            SELECT :key_hash, :now
            WHERE (
              SELECT count(*) FROM auth_rate_limit_attempts
              WHERE key_hash = :key_hash AND attempted_at > :window_floor
            ) < :attempt_limit
            RETURNING id
        """), {
            "key_hash": key_hash,
            "now": current,
            "window_floor": current - _LOGIN_ATTEMPT_WINDOW_SECONDS,
            "attempt_limit": _LOGIN_ATTEMPT_LIMIT,
        })
        admitted = result.scalar_one_or_none() is not None
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        raise HTTPException(status_code=503, detail="Authentication rate limiter is unavailable") from error

    with _RATE_LIMIT_PRUNE_LOCK:
        _RATE_LIMIT_OPERATIONS += 1
        should_prune = _RATE_LIMIT_OPERATIONS % 100 == 0
    if should_prune:
        try:
            session.execute(text(
                "DELETE FROM auth_rate_limit_attempts WHERE attempted_at <= :window_floor"
            ), {"window_floor": current - _LOGIN_ATTEMPT_WINDOW_SECONDS})
            session.commit()
        except SQLAlchemyError:
            session.rollback()
    if not admitted:
        raise HTTPException(status_code=429, detail="Too many login attempts; wait one minute")


def _clear_login_attempts(session: Session, remote: str) -> None:
    try:
        session.execute(text("DELETE FROM auth_rate_limit_attempts WHERE key_hash = :key_hash"), {
            "key_hash": _rate_limit_key(remote),
        })
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        raise HTTPException(status_code=503, detail="Authentication rate limiter is unavailable") from error


def _secure_cookie(request: Request) -> bool:
    explicit = os.environ.get("ANVIL_COOKIE_SECURE")
    return request.url.scheme == "https" or explicit is not None and explicit.strip().lower() in {"1", "true", "yes", "on"}


def _set_session_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME, token, max_age=8 * 60 * 60, path="/", httponly=True,
        secure=_secure_cookie(request), samesite="strict",
    )
