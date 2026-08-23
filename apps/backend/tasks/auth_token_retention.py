# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Auth token retention sweeper, a Celery Beat task that runs daily. W9
(concurrency-scaling-plan-2026-08-22.md §3.5, §4).

``refresh_tokens`` and ``password_reset_tokens`` both carry an ``expires_at``
set at issuance and nothing that deletes a row once it stops being useful.
Every login rotation (``refresh_tokens``) or reset request
(``password_reset_tokens``) leaves a row behind forever today, so both grow
with traffic rather than with anything the operator controls.

Deletion policy, one predicate per table, both driven by the TTL already on
the row:

  - ``refresh_tokens``: ``expires_at < now() - grace``. A rotated /
    logged-out / reuse-revoked row keeps its ORIGINAL ``expires_at``
    (revocation does not move it, see ``services.auth_service``), so a
    revoked row is caught by this SAME predicate within one TTL window
    (``REFRESH_TOKEN_EXPIRE_DAYS``, default 7) of being revoked. There is
    deliberately no separate ``revoked_at``-driven pass: adding one would
    need a new index (the existing ``ix_refresh_tokens_user_revoked`` is
    ``(user_id, revoked_at)``, useless for a table-wide revoked_at scan), and
    every revoked row is already reachable via expiry alone, just up to one
    TTL later, which stays within the same bound the table already has. The
    grace period is past EXPIRY, not past revocation, and exists only so a
    support investigation opened the day a token expired still finds the row.
  - ``password_reset_tokens``: ``expires_at < now() - grace``, same
    reasoning. ``used_at`` / ``invalidated_at`` do not gate the delete
    either: every row, consumed or not, already carries the short TTL that
    bounds it, and ``services.password_reset_service.consume_reset_token``'s
    candidate scan (``used_at IS NULL AND invalidated_at IS NULL``, no
    expiry filter) only shrinks as a side effect of this sweep removing
    long-expired never-used rows.

Neither table is audit-relevant on its own terms. The audit listener
already records the login / reset-request mutations that create and rotate
these rows, so deleting an expired token row removes nothing the audit
trail needs.

CLAUDE.md compliance:
  - Core rule #3: runs in Celery, never on the request path.
  - Core rule #11: every setting is read via ``os.getenv`` at call time.
  - §5: structlog JSON, one event per line; no token material logged (only
    row counts; the columns here are never plaintext tokens to begin with,
    see ``models.auth.RefreshToken`` / ``PasswordResetToken`` docstrings).
  - §6: forward-only, no schema change. Both indexes this task relies on
    (``ix_refresh_tokens_expires_at``, ``ix_password_reset_tokens_expires_at``)
    already exist.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import delete
from sqlalchemy.orm import Session

from core.db import sync_session_scope
from models import PasswordResetToken, RefreshToken
from tasks.celery_app import celery_app

log = structlog.get_logger("tasks.auth_token_retention")


def _refresh_token_grace_days() -> int:
    """Days past ``expires_at`` a refresh-token row survives (default 1)."""
    return max(int(os.getenv("REFRESH_TOKEN_RETENTION_GRACE_DAYS", "1")), 0)


def _password_reset_token_grace_days() -> int:
    """Days past ``expires_at`` a password-reset-token row survives (default 1)."""
    return max(int(os.getenv("PASSWORD_RESET_TOKEN_RETENTION_GRACE_DAYS", "1")), 0)


@celery_app.task(name="trustedoss.auth_token_retention")  # type: ignore[misc]
def auth_token_retention_task() -> dict[str, Any]:
    """Delete expired refresh + password-reset token rows. Idempotent.

    Returns ``{"deleted_refresh_tokens": N, "deleted_password_reset_tokens": M}``
    so the sweep's effect is visible in the task result / admin logs.
    """
    structlog.contextvars.bind_contextvars(task_name="auth_token_retention")
    now = datetime.now(UTC)
    refresh_cutoff = now - timedelta(days=_refresh_token_grace_days())
    reset_cutoff = now - timedelta(days=_password_reset_token_grace_days())

    deleted_refresh = 0
    deleted_reset = 0
    try:
        with sync_session_scope() as session:
            deleted_refresh = _delete_expired_refresh_tokens(session, cutoff=refresh_cutoff)
        with sync_session_scope() as session:
            deleted_reset = _delete_expired_password_reset_tokens(session, cutoff=reset_cutoff)
    finally:
        structlog.contextvars.unbind_contextvars("task_name")

    log.info(
        "auth_token_retention_done",
        deleted_refresh_tokens=deleted_refresh,
        deleted_password_reset_tokens=deleted_reset,
    )
    return {
        "deleted_refresh_tokens": deleted_refresh,
        "deleted_password_reset_tokens": deleted_reset,
    }


def _delete_expired_refresh_tokens(session: Session, *, cutoff: datetime) -> int:
    result = session.execute(delete(RefreshToken).where(RefreshToken.expires_at < cutoff))
    count = int(result.rowcount or 0)
    session.commit()
    return count


def _delete_expired_password_reset_tokens(session: Session, *, cutoff: datetime) -> int:
    result = session.execute(
        delete(PasswordResetToken).where(PasswordResetToken.expires_at < cutoff)
    )
    count = int(result.rowcount or 0)
    session.commit()
    return count


__all__ = ["auth_token_retention_task"]
