# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Operational-history retention sweeper, a Celery Beat task that runs daily.
W9 (concurrency-scaling-plan-2026-08-22.md §3.5, §4).

Three append-mostly tables have no sweep today, so each grows with traffic
rather than with anything the operator controls:

  - ``notifications``: one row per in-app notification per recipient.
    ``models.notification.Notification``'s own docstring already promises
    this: "we never hard-delete read rows, retention is handled separately
    by a future Celery sweeper." This is that sweeper. 180 days is long
    enough that "what did I miss last quarter" still works from the bell
    icon, and bounds a table that grows with every scan result, SLA breach,
    and approval request fanned out per team member.
  - ``webhook_deliveries``: one row per inbound GitHub/GitLab webhook, kept
    for CI debugging ("why didn't my push trigger a scan"). 90 days matches
    the audit log's own retention window (``AUDIT_LOG_RETENTION_DAYS``),
    long enough to cover a sprint's worth of CI incidents, short enough that
    a busy monorepo's push volume does not accumulate forever.
  - ``report_downloads``: one row per emitted SBOM / NOTICE / vulnerability
    report, a deliberate design choice (see the model's own docstring) to
    keep download history queryable without widening the polymorphic audit
    log. 365 days because this history backs "who downloaded what" for an
    annual compliance cycle, longer than the other two because it is the
    one of the three an auditor is likeliest to ask for by name.

All three cutoffs are occurrence-time (``created_at`` / ``received_at``), not
export- or usage-gated: none of the three carries a downstream consumer with
a cursor the way ``audit_logs`` does (see ``tasks.audit_log_retention``), so
there is nothing to wait on before a row is safe to reclaim.

CLAUDE.md compliance:
  - Core rule #3: runs in Celery, never on the request path.
  - Core rule #11: every setting is read via ``os.getenv`` at call time.
  - §5: structlog JSON, one event per line.
  - §6: forward-only, no schema change. ``webhook_deliveries`` already has a
    plain ``received_at`` index (``ix_webhook_deliveries_received_at``);
    ``notifications`` and ``report_downloads`` only have composite indexes
    led by ``user_id`` / ``project_id`` / ``team_id``, so this sweep's plain
    ``created_at`` predicate falls back to a sequential scan on those two.
    Acceptable for a once-daily background sweep at today's scale: the
    concurrency plan's §0.5 puts this whole unit at "1-2 years out, not
    urgent". A plain ``created_at`` index on either table is a follow-up for
    db-designer if a deployment's table size makes the daily scan costly.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import delete
from sqlalchemy.orm import Session

from core.db import sync_session_scope
from models import Notification, ReportDownload, WebhookDelivery
from tasks.celery_app import celery_app

log = structlog.get_logger("tasks.operational_retention")


def _notification_retention_days() -> int:
    """Age (days) past which a notification row is reclaimed (default 180)."""
    return max(int(os.getenv("NOTIFICATION_RETENTION_DAYS", "180")), 0)


def _webhook_delivery_retention_days() -> int:
    """Age (days) past which a webhook-delivery row is reclaimed (default 90)."""
    return max(int(os.getenv("WEBHOOK_DELIVERY_RETENTION_DAYS", "90")), 0)


def _report_download_retention_days() -> int:
    """Age (days) past which a report-download row is reclaimed (default 365)."""
    return max(int(os.getenv("REPORT_DOWNLOAD_RETENTION_DAYS", "365")), 0)


@celery_app.task(name="trustedoss.operational_retention")  # type: ignore[misc]
def operational_retention_task() -> dict[str, Any]:
    """Delete aged notifications / webhook deliveries / report downloads.

    Idempotent, safe to re-run. Returns per-table deleted counts so the
    sweep's effect is visible in the task result / admin logs.
    """
    structlog.contextvars.bind_contextvars(task_name="operational_retention")
    now = datetime.now(UTC)
    notification_cutoff = now - timedelta(days=_notification_retention_days())
    webhook_cutoff = now - timedelta(days=_webhook_delivery_retention_days())
    report_cutoff = now - timedelta(days=_report_download_retention_days())

    deleted_notifications = 0
    deleted_webhook_deliveries = 0
    deleted_report_downloads = 0
    try:
        with sync_session_scope() as session:
            deleted_notifications = _delete_aged(
                session, Notification, Notification.created_at, notification_cutoff
            )
        with sync_session_scope() as session:
            deleted_webhook_deliveries = _delete_aged(
                session, WebhookDelivery, WebhookDelivery.received_at, webhook_cutoff
            )
        with sync_session_scope() as session:
            deleted_report_downloads = _delete_aged(
                session, ReportDownload, ReportDownload.created_at, report_cutoff
            )
    finally:
        structlog.contextvars.unbind_contextvars("task_name")

    log.info(
        "operational_retention_done",
        deleted_notifications=deleted_notifications,
        deleted_webhook_deliveries=deleted_webhook_deliveries,
        deleted_report_downloads=deleted_report_downloads,
    )
    return {
        "deleted_notifications": deleted_notifications,
        "deleted_webhook_deliveries": deleted_webhook_deliveries,
        "deleted_report_downloads": deleted_report_downloads,
    }


def _delete_aged(session: Session, model: Any, column: Any, cutoff: datetime) -> int:
    result = session.execute(delete(model).where(column < cutoff))
    count = int(result.rowcount or 0)
    session.commit()
    return count


__all__ = ["operational_retention_task"]
