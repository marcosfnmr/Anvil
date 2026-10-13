"""Request-local identity for audit events emitted by service code."""

from contextvars import ContextVar


audit_actor: ContextVar[str | None] = ContextVar("anvil_audit_actor", default=None)
