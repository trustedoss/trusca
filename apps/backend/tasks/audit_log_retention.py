# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Audit log retention readiness, a Celery Beat task that runs daily. W9
(concurrency-scaling-plan-2026-08-22.md §3.5, §4).

``audit_logs`` is append-only at the database layer: migration 0012 attaches
BEFORE UPDATE/DELETE/TRUNCATE triggers that raise ``SQLSTATE 23000`` on any
mutation, and the admin guide documents the only sanctioned purge as a
manual, two-operator SQL session that drops the triggers, deletes, and
recreates them before commit
(docs-site/docs/admin-guide/audit-log.md#retention). That is a deliberate
compliance control, not an oversight the way the other five W9 tables'
missing sweeps are. An unattended task that can silently mass-delete the
compliance trail removes exactly the human-accountability property the
trigger exists to guarantee. This beat does not attempt to drop the
triggers, and it does not delete anything.

Instead it answers the question the manual procedure needs answered: how
many rows are actually safe to purge right now. A row is purge-ready when it
is BOTH already handed to the configured collector (at or before the
``tasks.audit_export`` cursor) AND older than the retention window
(``AUDIT_LOG_RETENTION_DAYS``, default 90, the figure the ``AuditLog``
model docstring has quoted since Phase 5). Rows the export has not reached
yet are never counted, no matter their age: an unexported row is the one
copy of that compliance record, and losing it before it left the building is
the failure this whole design exists to avoid. See
``services.audit_export_service.purge_ready_count`` for the exact predicate.

Off (0 ready, one INFO line) when no export destination is configured. An
organization that never enabled continuous export has, by construction,
exported nothing, so nothing is ever purge-ready through this signal.
Automatically discarding the only copy of the compliance trail is a
different, larger decision than a Celery Beat task should make on its own.

CLAUDE.md compliance:
  - Core rule #3: runs in Celery, never on the request path.
  - Core rule #11: every setting is read via ``os.getenv`` at call time.
  - §5: structlog JSON, one event per line.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import structlog

from core.config import audit_export_url
from core.db import sync_session_scope
from services.audit_export_service import purge_ready_count
from tasks.celery_app import celery_app

log = structlog.get_logger("tasks.audit_log_retention")


def _retention_days() -> int:
    """Age (days) past which an already-exported audit row is purge-ready.

    Default 90; see ``core.config.audit_log_retention_days`` (the value
    this reads is a thin passthrough kept local so this module follows the
    same "settings live beside the task that owns them" convention as
    ``tasks.scan_retention``). Read at call time (core rule #11).
    """
    return max(int(os.getenv("AUDIT_LOG_RETENTION_DAYS", "90")), 0)


@celery_app.task(name="trustedoss.audit_log_retention_report")  # type: ignore[misc]
def audit_log_retention_report_task() -> dict[str, Any]:
    """Log + return how many audit rows are exported and aged enough to purge.

    Never deletes. Idempotent, safe to re-run: it is a read.
    """
    structlog.contextvars.bind_contextvars(task_name="audit_log_retention_report")
    destination = audit_export_url()
    retention_days = _retention_days()
    try:
        if destination is None:
            log.info(
                "audit_log_retention_report_skipped",
                reason="export_not_configured",
                retention_days=retention_days,
            )
            return {
                "status": "skipped",
                "reason": "export_not_configured",
                "ready_to_purge": 0,
                "retention_days": retention_days,
            }
        with sync_session_scope() as session:
            ready = purge_ready_count(
                session,
                destination=destination,
                retention_days=retention_days,
                now=datetime.now(tz=UTC),
            )
    finally:
        structlog.contextvars.unbind_contextvars("task_name")

    log.info(
        "audit_log_retention_report_done",
        ready_to_purge=ready,
        retention_days=retention_days,
    )
    return {"status": "ran", "ready_to_purge": ready, "retention_days": retention_days}


__all__ = ["audit_log_retention_report_task"]
