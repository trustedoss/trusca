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

import hashlib
import uuid
from contextvars import ContextVar
from decimal import Decimal
from typing import Any

from sqlalchemy import event, inspect
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import InstanceState, Session

from core.pii_mask import mask_git_url

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
        # Phase 5 PR #16 — API Key + Webhook secret columns. ``key_hash`` is
        # the bcrypt hash of an API key plaintext (never the plaintext, but
        # masking in audit diff is defence-in-depth: a future code path that
        # mutates this column on a soft-revoke etc. must not write the hash
        # into ``audit_logs.diff``). ``webhook_secret`` IS the plaintext shared
        # secret used for HMAC verification — masking is mandatory.
        "key_hash",
        "webhook_secret",
        # v2.2-b1 — GitHub App credential columns. ``private_key_encrypted`` is
        # the Fernet ciphertext of the App PEM private key and
        # ``webhook_secret_encrypted`` the ciphertext of the webhook HMAC secret.
        # Both are already encrypted at rest, but masking them out of the audit
        # diff is defence-in-depth: a register / soft-revoke / re-link UPDATE
        # must never copy credential ciphertext into ``audit_logs.diff``.
        "private_key_encrypted",
        "webhook_secret_encrypted",
        # Feature #18 Part B — per-project git credential for private-repo
        # scanning. ``git_credential_encrypted`` is the Fernet ciphertext of a
        # user-supplied PAT / deploy token (later an SSH key). Encrypted at
        # rest; masking it out of the audit diff is defence-in-depth so a
        # credential add / rotate / clear UPDATE on ``projects`` never copies
        # the ciphertext into ``audit_logs.diff``.
        "git_credential_encrypted",
    }
)


# PII columns that we DO want the audit trail to capture (so admins can prove
# "user X changed Y to Z at time T") but whose plaintext value must never be
# persisted (CWE-359 Exposure of Private Personal Information; security-
# reviewer F4). We replace the value with ``{"sha256": "<hex>"}`` so two
# distinct values produce distinct hashes (membership testing still works for
# audit-replay forensics) without retaining the plaintext at rest.
#
# Why hash instead of mask with ``***``?
#   - Investigators need to correlate "the email this row referenced"
#     across multiple audit rows without having to hold the plaintext.
#   - Hashing is deterministic + irreversible (modulo dictionary attack
#     against a known address space — out of scope for our threat model).
#   - The audit trail keeps the user's ID + the request_id, so the
#     correlation carries the actor without the PII.
#
# Why not use ``_SENSITIVE_COLUMNS`` mask=`***`?
#   - All emails would collapse to the same value, defeating the audit
#     trail's "what changed" semantics. ``email`` and ``full_name`` aren't
#     credentials — they're regulated data we just don't want lying around.
_PII_COLUMNS = frozenset({"email", "full_name"})


# URL columns whose userinfo segment may carry a credential (e.g. a PAT a user
# embedded as ``https://<token>@github.com/...`` in ``projects.git_url``). We do
# NOT blanket-mask these to ``***`` like ``_SENSITIVE_COLUMNS`` — the host/path
# is the very thing the audit trail needs ("which repo did this row point at").
# Instead we strip only the userinfo via :func:`mask_git_url`, keeping the
# diff useful while never persisting the token (C-2). New rows only — existing
# immutable audit rows are not rewritten.
_URL_REDACT_COLUMNS = frozenset({"git_url"})


# Tables we never audit. `audit_logs` itself would otherwise recurse, and
# `alembic_version` is operational metadata. `report_downloads` (M-36) is an
# access-log side-effect of SBOM/NOTICE export — the spec says an export leaves
# only a structlog line, not an audit_logs row, so the report_downloads INSERT
# must not trip the listener.
_NON_AUDITED_TABLES = frozenset({"audit_logs", "alembic_version", "report_downloads"})


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def get_audit_context() -> dict[str, Any]:
    """Return a shallow copy of the current audit context (never None)."""
    raw = audit_context.get()
    return dict(raw) if raw else {}


def bind_audit_team(team_id: uuid.UUID) -> None:
    """Attach ``team_id`` to the audit ContextVar so audit rows pick it up.

    The ``before_flush`` listener reads ``team_id`` from this ContextVar at
    flush time (see :func:`_build_audit_row`). Request-time middleware /
    ``get_current_user`` bind ``user_id`` / ``request_id`` / ``ip`` etc., but
    the *team* scope of a mutation is only known once the service has resolved
    which team owns the resource being written. Services therefore call this
    after their team-access gate and before the mutating ``commit`` so the
    resulting ``audit_logs.team_id`` is non-NULL.

    This is the canonical implementation; ``services.scan_service`` and
    ``services.project_service`` re-export / delegate to it so the three write
    paths never drift on the contextvar key name or copy semantics.
    """
    ctx = dict(audit_context.get() or {})
    ctx["team_id"] = str(team_id)
    audit_context.set(ctx)


def _read_ctx() -> dict[str, Any]:
    """Internal helper: snapshot for the listener (defensive copy)."""
    return get_audit_context()


def is_audited_table(name: str) -> bool:
    """True for domain tables, False for the audit table + alembic metadata."""
    return name not in _NON_AUDITED_TABLES


def mask_sensitive_columns(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Return a copy of `payload` with sensitive / PII keys redacted.

    Four cases:
      - ``_SENSITIVE_COLUMNS`` (credentials, hashes) → replaced with ``"***"``.
        Plaintext that we genuinely never want to retain at audit time.
      - ``_PII_COLUMNS`` (``email``, ``full_name``) → replaced with
        ``{"sha256": "<hex>"}``. Investigators can still match identical
        values across audit rows but the plaintext is gone (CWE-359).
      - ``_URL_REDACT_COLUMNS`` (``git_url``) → userinfo segment stripped via
        :func:`redact_url_userinfo`, keeping host/path so the diff stays useful
        while never persisting an embedded token (C-2).
      - Everything else → passed through unchanged.

    We replace rather than delete so the diff still records that the column
    changed — useful for "this user rotated their password at T" or "this
    user's email was edited at T" without retaining the plaintext.
    """
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if key in _SENSITIVE_COLUMNS:
            out[key] = "***"
        elif key in _PII_COLUMNS:
            out[key] = _hash_pii(value)
        elif key in _URL_REDACT_COLUMNS and isinstance(value, str):
            out[key] = mask_git_url(value)
        else:
            out[key] = value
    return out


def _hash_pii(value: Any) -> dict[str, str] | None:
    """
    Hash a PII value to ``{"sha256": "<hex>"}``.

    Returns None when the input is None (column was unset / nulled). Non-
    string values are coerced via ``str()`` before hashing — the audit
    listener feeds us the raw column value, which for ``email`` / ``full_name``
    is always either ``str`` or ``None`` in our schema, but the coercion
    keeps us robust if a future column joins ``_PII_COLUMNS``.
    """
    if value is None:
        return None
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()
    return {"sha256": digest}


def build_audit_action(op: str) -> str:
    """Map ORM operation names to audit-log action verbs."""
    return {
        "insert": "create",
        "update": "update",
        "delete": "delete",
    }[op]


def _soft_delete_action(instance: object, diff: dict[str, Any]) -> str | None:
    """Detect a soft-delete / restore transition on ``archived_at`` (M-5).

    A project "delete" is an UPDATE that fills ``archived_at``, so the plain
    op→verb mapping recorded it as ``action=update`` and the guide's
    "who deleted this project" query (``action=delete``) matched nothing.
    Any audited table with an ``archived_at`` column gets the same treatment:

      - NULL → timestamp  ⇒ ``archive``
      - timestamp → NULL  ⇒ ``unarchive``

    Returns None when ``archived_at`` did not change (caller keeps the
    default verb).
    """
    if "archived_at" not in diff:
        return None
    state: InstanceState[Any] = inspect(instance)  # type: ignore[assignment]
    history = state.attrs["archived_at"].history
    previous = history.deleted[0] if history.deleted else None
    current = diff["archived_at"]
    if previous is None and current is not None:
        return "archive"
    if previous is not None and current is None:
        return "unarchive"
    return None


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


def _augment_status_transition(instance: object, diff: dict[str, Any]) -> dict[str, Any]:
    """Record previous_status / new_status when a ``status`` column changed (M-7).

    The base update diff stores only the new value under the column name. State
    transitions (approval disposition, vulnerability-finding status, scan status)
    also want the prior value so an audit query can answer "what did this move
    from/to" without stitching adjacent rows together. Additive — the existing
    ``status`` key is preserved. No-op when ``status`` did not change.
    """
    if "status" not in diff:
        return diff
    state: InstanceState[Any] = inspect(instance)  # type: ignore[assignment]
    history = state.attrs["status"].history
    previous = history.deleted[0] if history.deleted else None
    out = dict(diff)
    out["previous_status"] = previous
    out["new_status"] = diff["status"]
    return out


def _serialize_value(value: Any) -> Any:
    """Make a value JSON-safe for the JSONB diff column."""
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        # Numeric columns (e.g. vulnerabilities.cvss_score / epss_score) arrive
        # as Decimal, which the stdlib json encoder cannot serialize. Coerce to
        # float so the diff lands in JSONB. We accept the float round-trip:
        # the audit diff is a human-readable record, not a source of truth for
        # re-deriving the exact Numeric scale. Guard non-finite Decimals
        # (NaN/Inf): json.dumps would emit tokens PostgreSQL JSONB rejects, and
        # a failed audit INSERT aborts the business txn it records — fall back
        # to str so the diff still lands. (Bounded Numeric cols can't reach this
        # today; the guard hardens the general Decimal path.)
        return float(value) if value.is_finite() else str(value)
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

    action = build_audit_action(op)
    if op == "update":
        diff = _changed_columns(instance)
        diff = _augment_status_transition(instance, diff)
        # M-5: a soft delete (archived_at NULL→ts) is semantically a delete;
        # record it as ``archive`` (and the reverse as ``unarchive``) so the
        # "who deleted this project" audit query has a row to find.
        action = _soft_delete_action(instance, diff) or action

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
        "action": action,
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


# session.info key holding this flush's pending CREATE audit payloads. Each
# entry is ``(row_kwargs, instance)`` — the instance is kept so after_flush
# can backfill the server-generated PK (M-4).
_PENDING_AUDIT_KEY = "_pending_audit_rows"


def _before_flush(session: Session, _flush_context: Any, _instances: Any) -> None:
    """SQLAlchemy event hook (stage 1): emit/stash audit rows per mutated row.

    update/delete audit rows are added as ORM rows right here, exactly as
    before — the unit of work executes their INSERTs *before* any DELETE in
    the same flush, so a delete-op audit row referencing a team that is being
    deleted in the same flush is first inserted and then cascade-nulled by
    the ``ON DELETE SET NULL`` FK (PR #48 semantics).

    create audit rows are *stashed* for :func:`_after_flush` instead (M-4): a
    created row's PK is server-generated (``gen_random_uuid()``), so at this
    point ``target_id`` would always be NULL. The diff is still captured HERE
    because attribute history resets with the flush.
    """
    from models import AuditLog

    ctx = get_audit_context()

    pending: list[tuple[dict[str, Any], object]] = []
    for instance in session.new:
        if isinstance(instance, AuditLog):
            continue
        row = _build_audit_row(op="insert", instance=instance, ctx=ctx)
        if row is not None:
            # Defer to after_flush so the PK can be backfilled (M-4).
            pending.append((row, instance))

    rows: list[dict[str, Any]] = []
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

    if pending:
        session.info.setdefault(_PENDING_AUDIT_KEY, []).extend(pending)


def _after_flush(session: Session, _flush_context: Any) -> None:
    """SQLAlchemy event hook (stage 2): write CREATE audit rows (M-4).

    Runs after the flush emitted its SQL, so the server-generated PK is
    populated on each created instance (via INSERT .. RETURNING) and
    ``target_id`` can be backfilled. Note ``state.identity`` is NOT yet
    registered at after_flush time — the PK must be read off the mapped
    attributes (``primary_key_from_instance``). The rows are written with a
    Core INSERT on the session's connection — the documented-safe way to emit
    SQL from after_flush — and share the business transaction, so the audit
    row and the change it records commit or roll back together.
    """
    from sqlalchemy import insert as sa_insert

    from models import AuditLog

    pending = session.info.pop(_PENDING_AUDIT_KEY, None)
    if not pending:
        return

    rows: list[dict[str, Any]] = []
    for row, instance in pending:
        if row["target_id"] is None:
            state: InstanceState[Any] = inspect(instance)
            pk = state.mapper.primary_key_from_instance(instance)
            if pk and pk[0] is not None:
                row = {**row, "target_id": str(pk[0])}
                # The captured insert diff carries the pre-flush ``id: None``;
                # mirror the backfilled PK so the diff is self-consistent.
                if row["diff"].get("id") is None:
                    row["diff"] = {**row["diff"], "id": row["target_id"]}
        rows.append(row)

    session.connection().execute(sa_insert(AuditLog), rows)


__all__ = [
    "audit_context",
    "bind_audit_team",
    "build_audit_action",
    "get_audit_context",
    "install_audit_listeners",
    "is_audited_table",
    "mask_sensitive_columns",
]


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
    if not event.contains(sync_session_class, "after_flush", _after_flush):
        event.listen(sync_session_class, "after_flush", _after_flush)
