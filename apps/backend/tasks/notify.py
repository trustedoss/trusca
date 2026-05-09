"""
Celery task: ``trustedoss.send_notification`` — Phase 6 PR #18 + Chore A2.

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
  - **In-app fan-out (A2)**: when the caller supplies ``user_id`` the task
    consults the user's :class:`models.NotificationPreferences` row,
    drops disabled outbound channels from the dispatch list, and writes
    a row to ``notifications`` when ``in_app_enabled`` is true. The
    fan-out runs in :func:`core.db.sync_session_scope` so the worker
    keeps a single sync DB transaction per task. ``user_id`` is
    backwards-compatible (defaults to None) — the password-reset path
    still calls the task without it for the time being.

Why bind=True:
  - ``self.request.id`` lets us correlate retries in structlog. Without it
    the operator cannot distinguish the original send from a retry.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import structlog

from notifications import NotificationDeliveryError
from notifications.dispatcher import (
    CHANNEL_EMAIL,
    CHANNEL_SLACK,
    CHANNEL_TEAMS,
    dispatch,
)
from tasks.celery_app import celery_app

log = structlog.get_logger("tasks.notify")


# Map outbound channel name to the prefs attribute that gates it.
_CHANNEL_TO_PREF_ATTR: dict[str, str] = {
    CHANNEL_EMAIL: "email_enabled",
    CHANNEL_SLACK: "slack_enabled",
    CHANNEL_TEAMS: "teams_enabled",
}


def _apply_prefs_filter(
    *,
    user_id: uuid.UUID,
    kind: str,
    title: str,
    body: str,
    link: str | None,
    target_table: str | None,
    target_id: uuid.UUID | None,
    channels: list[str],
) -> list[str]:
    """Consult the user's prefs, write the in-app row, return the
    outbound-channel subset to actually dispatch.

    Runs in a sync DB session because the Celery worker is sync. Imports
    the DB layer lazily so unit tests that monkeypatch this helper do not
    pay the import cost.

    - Channels whose ``*_enabled`` pref is ``False`` are dropped from the
      returned list (the dispatcher therefore never tries them).
    - When ``in_app_enabled`` is ``True`` we INSERT a row into
      ``notifications`` for the user. When ``False`` we skip the insert.
    - ``user_id`` rows that do not yet have a prefs row get the defaults
      via ``get_prefs_sync`` (in-app on, email on, slack/teams off).
    """
    # Late imports — keep the Celery module importable in environments
    # that don't have the DB layer wired (unit tests stub at this seam).
    from core.db import sync_session_scope
    from services.notification_service import (
        create_notification_sync,
        get_prefs_sync,
    )

    with sync_session_scope() as session:
        prefs = get_prefs_sync(session, user_id=user_id)

        if prefs.in_app_enabled:
            create_notification_sync(
                session,
                user_id=user_id,
                kind=kind,
                title=title,
                body=body,
                link=link,
                target_table=target_table,
                target_id=target_id,
            )

        # Drop outbound channels the user has disabled. Unknown channel
        # names (a future kind that adds e.g. "sms") pass through —
        # leave the gate decision to the dispatcher.
        filtered: list[str] = []
        for channel in channels:
            attr = _CHANNEL_TO_PREF_ATTR.get(channel)
            if attr is None:
                filtered.append(channel)
                continue
            if getattr(prefs, attr, True):
                filtered.append(channel)
        return filtered


def _coerce_uuid(value: Any) -> uuid.UUID | None:
    """Best-effort UUID coercion for JSON-serialized Celery args."""
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def _run_notification(
    self: Any,
    kind: str,
    context: dict[str, Any],
    channels: list[str],
    recipients: list[str] | None = None,
    *,
    user_id: str | uuid.UUID | None = None,
    in_app_title: str | None = None,
    in_app_body: str | None = None,
    in_app_link: str | None = None,
    in_app_target_table: str | None = None,
    in_app_target_id: str | uuid.UUID | None = None,
) -> dict[str, Any]:
    """Underlying function (testable without Celery's bind=True self-injection).

    The ``user_id`` / ``in_app_*`` kwargs are the Chore A2 fan-out hook. When
    ``user_id`` is supplied:
      1. The user's prefs row is fetched (creating defaults if missing).
      2. ``channels`` is filtered to drop disabled outbound channels.
      3. An in-app ``notifications`` row is written iff ``in_app_enabled``.

    Callers without a target user (legacy password-reset path) omit
    ``user_id`` and the function behaves exactly as in PR #18.
    """
    structlog.contextvars.bind_contextvars(
        task_name="send_notification",
        task_id=str(self.request.id) if self and self.request else None,
        kind=kind,
        channel_count=len(channels),
        attempt=self.request.retries + 1 if self and self.request else 1,
    )
    try:
        effective_channels = list(channels)
        target_user = _coerce_uuid(user_id)
        if target_user is not None:
            effective_channels = _apply_prefs_filter(
                user_id=target_user,
                kind=kind,
                title=in_app_title or kind,
                body=in_app_body or "",
                link=in_app_link,
                target_table=in_app_target_table,
                target_id=_coerce_uuid(in_app_target_id),
                channels=effective_channels,
            )

        if not effective_channels:
            # Nothing to dispatch — the user has disabled every outbound
            # channel for this kind. Return a synthetic empty report so
            # callers can still inspect ``delivered_count`` etc.
            log.info(
                "notification_dispatch_skipped_all_channels_disabled",
                kind=kind,
            )
            return {
                "kind": kind,
                "channels": [],
                "delivered_count": 0,
                "skipped_count": 0,
                "failed_count": 0,
                "retryable_failures": False,
            }

        report = asyncio.run(
            dispatch(
                kind=kind,
                context=context,
                channels=effective_channels,
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
    *,
    user_id: str | uuid.UUID | None = None,
    in_app_title: str | None = None,
    in_app_body: str | None = None,
    in_app_link: str | None = None,
    in_app_target_table: str | None = None,
    in_app_target_id: str | uuid.UUID | None = None,
) -> dict[str, Any]:
    """Dispatch a notification asynchronously with retry-on-transient-failure.

    Thin Celery wrapper around :func:`_run_notification` — kept separate so
    unit tests can call ``_run_notification`` directly without going through
    Celery's bind=True self-injection.
    """
    return _run_notification(
        self,
        kind,
        context,
        channels,
        recipients,
        user_id=user_id,
        in_app_title=in_app_title,
        in_app_body=in_app_body,
        in_app_link=in_app_link,
        in_app_target_table=in_app_target_table,
        in_app_target_id=in_app_target_id,
    )


__all__ = ["_apply_prefs_filter", "_run_notification", "send_notification_task"]
