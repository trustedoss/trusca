"""
Celery task: ``trustedoss.send_notification`` — Phase 6 PR #18.

The task is a thin wrapper around :func:`notifications.dispatcher.dispatch`.
We declare ``autoretry_for=(NotificationDeliveryError,)`` with exponential
backoff (10-minute cap, jittered) per CLAUDE.md §5 — transient SMTP / Slack
5xx failures are retried automatically, permanent 4xx failures are recorded
in the dispatcher report and the task completes successfully (no retry).

CLAUDE.md compliance:
  - **Async + Celery bridge**: Celery executes tasks synchronously. The
    dispatcher coroutine is run with ``asyncio.run`` inside the task body —
    same pattern used by :mod:`tasks.dt_orphan_cleanup` for sync/async glue.
  - **JSON serialization**: ``kind`` and ``context`` are JSON-safe by
    construction (string + dict-of-strings). The Celery app is configured
    with ``task_serializer='json'`` so the broker rejects non-JSON args
    even if a caller forgets.
  - **No PII in logs**: the task binds ``kind`` + ``channel_count`` +
    ``task_id`` to structlog. The dispatcher logs at the channel level
    without leaking subjects / bodies / addresses.
  - **No DB writes**: the audit trail for "a notification was sent" is the
    responsibility of the caller's service layer — the password-reset
    service, the new-CVE detector, etc. Keeping the task DB-free lets the
    Celery worker fail-fast without holding onto a session.

Why bind=True:
  - ``self.request.id`` lets us correlate retries in structlog. Without it
    the operator cannot distinguish the original send from a retry.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from notifications import NotificationDeliveryError
from notifications.dispatcher import dispatch
from tasks.celery_app import celery_app

log = structlog.get_logger("tasks.notify")


def _run_notification(
    self: Any,
    kind: str,
    context: dict[str, Any],
    channels: list[str],
    recipients: list[str] | None = None,
) -> dict[str, Any]:
    """Underlying function (testable without Celery's bind=True self-injection)."""
    structlog.contextvars.bind_contextvars(
        task_name="send_notification",
        task_id=str(self.request.id) if self and self.request else None,
        kind=kind,
        channel_count=len(channels),
        attempt=self.request.retries + 1 if self and self.request else 1,
    )
    try:
        report = asyncio.run(
            dispatch(
                kind=kind,
                context=context,
                channels=channels,
                recipients=recipients,
            )
        )
        if report.get("retryable_failures"):
            # Surface a single retryable error so the autoretry envelope
            # picks this up.
            raise NotificationDeliveryError(
                "one or more channels suffered a transient failure"
            )
        return report
    finally:
        structlog.contextvars.unbind_contextvars(
            "task_name", "task_id", "kind", "channel_count", "attempt"
        )


@celery_app.task(  # type: ignore[misc]
    name="trustedoss.send_notification",
    bind=True,
    autoretry_for=(NotificationDeliveryError,),
    retry_backoff=True,
    retry_backoff_max=600,  # cap exponential backoff at 10 minutes
    retry_jitter=True,
    max_retries=5,
)
def send_notification_task(
    self: Any,
    kind: str,
    context: dict[str, Any],
    channels: list[str],
    recipients: list[str] | None = None,
) -> dict[str, Any]:
    """Dispatch a notification asynchronously with retry-on-transient-failure.

    Thin Celery wrapper around :func:`_run_notification` — kept separate so
    unit tests can call ``_run_notification`` directly without going through
    Celery's bind=True self-injection.
    """
    return _run_notification(self, kind, context, channels, recipients)


__all__ = ["_run_notification", "send_notification_task"]
