"""
Audit log infrastructure.

Phase 1 PR #5 — task 1.4.

Goal: every domain INSERT/UPDATE/DELETE produces a row in `audit_logs` with
the actor (user_id), team scope, request id, IP, and user-agent. Achieved via
a SQLAlchemy `before_flush` event listener that walks `session.new`,
`session.dirty`, and `session.deleted` and inserts an `AuditLog` for each
affected non-audit row.

The actor / request context flows in via `audit_context` (a ContextVar) which
the request middleware (`AuditContextMiddleware` in core/middleware.py) and
the `get_current_user` dependency populate at the boundary. ContextVars
propagate cleanly across async hops, so the listener can read them at flush
time without explicit threading.

Sensitive columns (password hashes, refresh-token hashes, etc.) are stripped
from the diff payload before insertion — see `mask_sensitive_columns`. The
SCA portal must never persist a credential into the audit trail.

Quality standard §5 (CLAUDE.md): the audit row carries `request_id` so log
lines emitted during the request can be correlated with the audit entry by id.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from typing import Any

from sqlalchemy import event, inspect
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import InstanceState, Session

# Context variable populated by the request middleware / auth dependency.
# Keys: user_id (str | None), team_id (str | None), request_id (str | None),
#       ip (str | None), user_agent (str | None).
#
# Default is None (not a shared mutable dict — that would let unrelated tasks
# accidentally observe each other's audit metadata). `get_audit_context()`
# returns a fresh empty dict when unbound; callers always work with copies.
audit_context: ContextVar[dict[str, Any] | None] = ContextVar("audit_context", default=None)


# Columns that must never appear in the audit diff. We strip them before
# storing the row. The list is keyed off domain knowledge, not introspection,
# so adding a new sensitive column requires updating both the model and this
# set — by design.
_SENSITIVE_COLUMNS = frozenset(
    {
        "password",
        "hashed_password",
        "password_hash",
        "secret",
        "api_key",
        "token",
        "token_hash",
        "refresh_token",
        "refresh_token_hash",
        "jti",
    }
)


# Tables we never audit. `audit_logs` itself would otherwise recurse, and
# `alembic_version` is operational metadata.
_NON_AUDITED_TABLES = frozenset({"audit_logs", "alembic_version"})


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def get_audit_context() -> dict[str, Any]:
    """Return a shallow copy of the current audit context (never None)."""
    raw = audit_context.get()
    return dict(raw) if raw else {}


def _read_ctx() -> dict[str, Any]:
    """Internal helper: snapshot for the listener (defensive copy)."""
    return get_audit_context()


def is_audited_table(name: str) -> bool:
    """True for domain tables, False for the audit table + alembic metadata."""
    return name not in _NON_AUDITED_TABLES


def mask_sensitive_columns(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Return a copy of `payload` with sensitive keys replaced by '***'.

    We replace rather than delete so the diff still records that the column
    changed — useful for "this user rotated their password at T" without
    leaking the hash itself.
    """
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if key in _SENSITIVE_COLUMNS:
            out[key] = "***"
        else:
            out[key] = value
    return out


def build_audit_action(op: str) -> str:
    """Map ORM operation names to audit-log action verbs."""
    return {
        "insert": "create",
        "update": "update",
        "delete": "delete",
    }[op]


# ---------------------------------------------------------------------------
# Listener
# ---------------------------------------------------------------------------


def _column_dict(instance: object) -> dict[str, Any]:
    """Return {column_name: current_value} for a mapped instance."""
    state: InstanceState[Any] = inspect(instance)  # type: ignore[assignment]
    out: dict[str, Any] = {}
    for attr in state.mapper.column_attrs:
        out[attr.key] = state.attrs[attr.key].value
    return out


def _changed_columns(instance: object) -> dict[str, Any]:
    """Return only attributes whose values were modified in this session."""
    state: InstanceState[Any] = inspect(instance)  # type: ignore[assignment]
    out: dict[str, Any] = {}
    for attr in state.mapper.column_attrs:
        history = state.attrs[attr.key].history
        if history.has_changes():
            out[attr.key] = state.attrs[attr.key].value
    return out


def _serialize_value(value: Any) -> Any:
    """Make a value JSON-safe for the JSONB diff column."""
    if isinstance(value, uuid.UUID):
        return str(value)
    if hasattr(value, "isoformat"):  # datetime/date
        return value.isoformat()
    return value


def _serialize_dict(d: dict[str, Any]) -> dict[str, Any]:
    return {k: _serialize_value(v) for k, v in d.items()}


def _build_audit_row(*, op: str, instance: object, ctx: dict[str, Any]) -> dict[str, Any] | None:
    """Construct the kwargs for an AuditLog row, or None if the table is skipped."""
    # Local import to avoid circular dependency at module import time
    # (models depend on Base which lives next to the audit code in some layouts).
    from models import AuditLog  # noqa: F401  (imported for type clarity / contract)

    table = instance.__class__.__table__.name  # type: ignore[attr-defined]
    if not is_audited_table(table):
        return None

    if op == "update":
        diff = _changed_columns(instance)
    else:
        diff = _column_dict(instance)

    diff = mask_sensitive_columns(diff)
    diff = _serialize_dict(diff)

    pk_state: InstanceState[Any] = inspect(instance)  # type: ignore[assignment]
    pk_value = pk_state.identity
    target_id_raw: Any = pk_value[0] if pk_value else diff.get("id")
    target_id = str(target_id_raw) if target_id_raw is not None else None

    return {
        "actor_user_id": _coerce_uuid(ctx.get("user_id")),
        "team_id": _coerce_uuid(ctx.get("team_id")),
        "action": build_audit_action(op),
        "target_table": table,
        "target_id": target_id,
        "request_id": ctx.get("request_id"),
        "ip": ctx.get("ip"),
        "user_agent": ctx.get("user_agent"),
        "diff": diff,
    }


def _coerce_uuid(value: Any) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


def _before_flush(session: Session, _flush_context: Any, _instances: Any) -> None:
    """SQLAlchemy event hook: emit AuditLog rows for every mutated mapped row."""
    from models import AuditLog

    ctx = get_audit_context()

    rows: list[dict[str, Any]] = []
    for instance in session.new:
        if isinstance(instance, AuditLog):
            continue
        row = _build_audit_row(op="insert", instance=instance, ctx=ctx)
        if row is not None:
            rows.append(row)

    for instance in session.dirty:
        if isinstance(instance, AuditLog):
            continue
        # Skip un-modified objects that ended up in `dirty` due to attribute
        # touching.
        if not session.is_modified(instance, include_collections=False):
            continue
        row = _build_audit_row(op="update", instance=instance, ctx=ctx)
        if row is not None:
            rows.append(row)

    for instance in session.deleted:
        if isinstance(instance, AuditLog):
            continue
        row = _build_audit_row(op="delete", instance=instance, ctx=ctx)
        if row is not None:
            rows.append(row)

    for row in rows:
        session.add(AuditLog(**row))


def install_audit_listeners(session_factory: async_sessionmaker[Any]) -> None:
    """
    Register the before_flush listener on the session factory's sync session.

    Async sessions delegate flush to a synchronous Session under the hood, so we
    bind to the sync mapper class. Calling this at startup is idempotent — we
    deduplicate by checking the listener registry first.
    """
    sync_session_class = session_factory.kw.get("sync_session_class") or Session

    if not event.contains(sync_session_class, "before_flush", _before_flush):
        event.listen(sync_session_class, "before_flush", _before_flush)
