"""Platform users, opaque sessions, and scenario memberships."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, SecretStr, field_validator
from sqlalchemy import Index, UniqueConstraint
from sqlmodel import Field as SqlField, SQLModel


PlatformRole = Literal["admin", "instructor", "designer", "participant"]
ScenarioRole = Literal["instructor", "designer", "participant"]


class PlatformUser(SQLModel, table=True):
    __tablename__ = "platform_users"

    id: str = SqlField(default_factory=lambda: str(uuid4()), primary_key=True)
    email: str = SqlField(index=True, unique=True, max_length=254)
    password_hash: str
    role: str = SqlField(default="participant", max_length=24, index=True)
    active: bool = SqlField(default=True, index=True)
    created_at: datetime = SqlField(default_factory=lambda: datetime.now(timezone.utc))


class PlatformSession(SQLModel, table=True):
    __tablename__ = "platform_sessions"

    id: str = SqlField(default_factory=lambda: str(uuid4()), primary_key=True)
    user_id: str = SqlField(index=True, foreign_key="platform_users.id")
    token_hash: str = SqlField(index=True, unique=True, max_length=64)
    created_at: datetime = SqlField(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime = SqlField(index=True)


class ScenarioMembership(SQLModel, table=True):
    __tablename__ = "scenario_memberships"
    __table_args__ = (UniqueConstraint("scenario_id", "user_id", name="uq_scenario_membership_user"),)

    id: int | None = SqlField(default=None, primary_key=True)
    scenario_id: str = SqlField(index=True, foreign_key="scenarios.id")
    user_id: str = SqlField(index=True, foreign_key="platform_users.id")
    role: str = SqlField(max_length=24, index=True)
    created_at: datetime = SqlField(default_factory=lambda: datetime.now(timezone.utc))


class AuthRateLimitAttempt(SQLModel, table=True):
    """One successful rate-window admission, keyed by a one-way origin digest."""

    __tablename__ = "auth_rate_limit_attempts"
    __table_args__ = (
        Index("ix_auth_rate_limit_key_time", "key_hash", "attempted_at"),
        Index("ix_auth_rate_limit_attempted_at", "attempted_at"),
    )

    id: int | None = SqlField(default=None, primary_key=True)
    key_hash: str = SqlField(max_length=64)
    attempted_at: float


class TerminalSessionLease(SQLModel, table=True):
    """Cross-worker lease preventing duplicate terminals and bounding sessions."""

    __tablename__ = "terminal_session_leases"
    __table_args__ = (
        UniqueConstraint("scenario_id", "node_id", name="uq_terminal_lease_station"),
        UniqueConstraint("worker_id", "slot", name="uq_terminal_lease_worker_slot"),
        Index("ix_terminal_lease_expiry", "expires_at"),
    )

    id: str = SqlField(default_factory=lambda: str(uuid4()), primary_key=True)
    scenario_id: str = SqlField(index=True, foreign_key="scenarios.id")
    node_id: str = SqlField(max_length=128)
    worker_id: str = SqlField(max_length=128, index=True)
    slot: int = SqlField(ge=0, le=15)
    expires_at: float = SqlField(index=True)


class UserCreate(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: SecretStr = Field(min_length=12, max_length=128)
    role: PlatformRole = "participant"

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if "@" not in normalized or normalized.startswith("@") or normalized.endswith("@"):
            raise ValueError("A valid email address is required")
        return normalized


class UserRead(BaseModel):
    id: str
    email: str
    role: PlatformRole
    active: bool
    created_at: datetime


class UserRoleUpdate(BaseModel):
    role: PlatformRole
    active: bool = True


class AdminPasswordReset(BaseModel):
    new_password: SecretStr = Field(min_length=12, max_length=128)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: SecretStr

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if "@" not in normalized or normalized.startswith("@") or normalized.endswith("@"):
            raise ValueError("A valid email address is required")
        return normalized


class BootstrapRequest(LoginRequest):
    token: SecretStr = Field(min_length=32, max_length=256)


class PasswordChange(BaseModel):
    current_password: SecretStr
    new_password: SecretStr = Field(min_length=12, max_length=128)


class ScenarioMembershipCreate(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    role: ScenarioRole

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if "@" not in normalized or normalized.startswith("@") or normalized.endswith("@"):
            raise ValueError("A valid email address is required")
        return normalized


class ScenarioMembershipRead(BaseModel):
    user_id: str
    email: str
    role: ScenarioRole
    created_at: datetime
