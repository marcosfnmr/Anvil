"""Password hashing and opaque server-side session helpers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from models.auth import PlatformSession, PlatformUser, UserRead


PBKDF2_ITERATIONS = 600_000
SESSION_HOURS = 8
COOKIE_NAME = "anvil_session"


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(digest).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt_encoded, digest_encoded = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_encoded)
        expected = base64.urlsafe_b64decode(digest_encoded)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(rounds))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def public_user(user: PlatformUser) -> UserRead:
    return UserRead(id=user.id, email=user.email, role=user.role, active=user.active, created_at=user.created_at)


def create_session(session: Session, user: PlatformUser) -> tuple[PlatformSession, str]:
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    row = PlatformSession(
        user_id=user.id,
        token_hash=hashlib.sha256(token.encode("ascii")).hexdigest(),
        created_at=now,
        expires_at=now + timedelta(hours=SESSION_HOURS),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row, token


def resolve_session(session: Session, token: str | None) -> tuple[PlatformUser, PlatformSession] | None:
    if not token or len(token) > 256:
        return None
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    session_row = session.exec(select(PlatformSession).where(PlatformSession.token_hash == digest)).first()
    if session_row is None:
        return None
    expires_at = session_row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        session.delete(session_row)
        session.commit()
        return None
    user = session.get(PlatformUser, session_row.user_id)
    if user is None or not user.active:
        session.delete(session_row)
        session.commit()
        return None
    return user, session_row


def revoke_session(session: Session, session_row: PlatformSession) -> None:
    session.delete(session_row)
    session.commit()
