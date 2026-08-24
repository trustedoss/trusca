# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Celery task: ``trustedoss.webhook_capacity_retry`` (S7, concurrency-scaling-
plan-2026-08-22.md §3.2/§4).

Before this task existed, a webhook-triggered scan turned away by the team
concurrency cap or the disk guard (``services.scan_service.
capacity_guard_reason``) stayed dropped until an operator noticed and resent
the delivery from the Git host's UI. The plan names this the most painful of
the three capacity-exhaustion branches (§1.1: "재시도 큐가 없어 운영자가 훅을
다시 보내야 복구된다"). This task is that missing retry queue.

``services.webhook_service._schedule_capacity_retry`` dispatches this task the
moment a delivery is skipped for capacity, with a countdown so the first
attempt does not re-check an answer this request just got a moment ago. Each
attempt:

  1. Loads the delivery + project rows with a plain sync session
     (``core.db.sync_session_scope`` - this is a Celery worker, not the async
     request path; CLAUDE.md forbids an asyncpg engine there).
  2. Bails out quietly if the delivery has already moved on (manual
     redelivery, or a previous attempt in a race, got there first) - see
     ``_process_capacity_retry``'s docstring.
  3. Re-runs the SAME capacity guard the original request ran
     (``services.scan_service.capacity_guard_reason_sync``, the sync twin
     ``tasks.scan_scheduler`` already uses for the same reason). Clear:
     enqueues the scan via ``enqueue_system_triggered_scan_sync`` (the exact
     guard-and-insert sequence every other system-triggered scan uses) and
     stamps the delivery's outcome. Still blocked and attempts remain: raises
     the internal retriable signal so Celery's declarative backoff schedules
     the next attempt. Still blocked and attempts are exhausted: stamps
     ``capacity_retry_exhausted`` and fires a best-effort operator
     notification.

Bounded, not infinite (task requirement: "무한 재시도는 안 된다"). The retry
count and backoff shape are fixed module constants, not environment-tunable
knobs - the same choice ``tasks.notify`` / ``tasks.ticket_webhook`` /
``tasks.audit_export`` already made for their own "retry a transient failure"
tasks (CLAUDE.md core rule #11 governs environment-driven CONFIGURATION; a
Celery decorator's ``max_retries`` / ``retry_backoff_max`` are evaluated once
at import time, so making them env-driven would only produce a value that
looks live but is not).
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy.orm import Session

from core.db import sync_session_scope
from models import Project, WebhookDelivery
from tasks.celery_app import celery_app

log = structlog.get_logger("tasks.webhook_capacity_retry")

# Internal self-healing bounds, not operator-tunable knobs (see the module
# docstring). ``RETRY_BACKOFF_BASE_SECONDS`` is exported because
# ``services.webhook_service._schedule_capacity_retry`` uses the same value
# for the FIRST attempt's countdown (one owner for one number - hardening
# rule 2 - rather than a second constant that could drift from this one).
RETRY_BACKOFF_BASE_SECONDS = 60
# Celery's ``retry_backoff`` doubles this base each attempt
# (60, 120, 240, 480, 960, capped) with full jitter, so no attempt lands on
# exactly this schedule - jitter is the point (thundering-herd avoidance when
# many deliveries were turned away by the same incident).
_RETRY_BACKOFF_MAX_SECONDS = 1800  # cap any single gap at 30 minutes
# Total attempts including the first: 1 initial dispatch + 6 retries. At the
# backoff shape above that spans roughly one to three hours end to end
# (jitter widens the range) - long enough to cover several scan-completion
# cycles at the plan's own 20-minute average (core.config.
# scan_average_duration_seconds()) without holding a delivery's fate open
# indefinitely.
_MAX_RETRY_ATTEMPTS = 6


class _WebhookStillAtCapacity(Exception):
    """Internal retry signal only - never raised past this module.

    Celery's declarative ``autoretry_for`` catches this, computes the next
    backoff, and reschedules. It is not a "real" error: it means
    ``_process_capacity_retry`` checked and the guard is still blocked with
    attempts remaining, which is the expected, ordinary shape of most
    attempts in a real incident.
    """


# ---------------------------------------------------------------------------
# Pure(ish) decision + I/O, split out for direct testing without Celery
# machinery (the same shape tasks.scan_scheduler.poll_due_schedules and
# tasks.queue_backlog_alert._run_check already use).
# ---------------------------------------------------------------------------


def _process_capacity_retry(
    session: Session,
    *,
    delivery_id: uuid.UUID,
    project_id: uuid.UUID,
    metadata: dict[str, Any],
    attempt: int,
) -> dict[str, Any]:
    """Run one attempt. Returns a result dict; never raises for an ordinary
    "still blocked" outcome (the Celery wrapper below turns that into the
    retry signal).

    ``attempt`` is ``self.request.retries`` at the Celery layer: 0 on the
    first (non-retry) execution, incrementing by one each time Celery
    reschedules. Passed in rather than read from a bound task so this
    function is callable with a bare ``Session`` and plain values, no Celery
    task context required.
    """
    from core.config import webhook_capacity_retry_enabled
    from services.scan_service import (
        capacity_guard_reason_sync,
        enqueue_system_triggered_scan_sync,
    )
    from services.webhook_service import _SUPERSEDABLE_OUTCOMES

    if not webhook_capacity_retry_enabled():
        # An operator can flip this off mid-flight (already-dispatched Celery
        # messages sitting in Redis do not know that). Respecting the CURRENT
        # value here, not the value at dispatch time, means turning the
        # toggle off actually stops the retry chain rather than merely
        # hiding it from new deliveries. Manual redelivery is unaffected.
        log.warning(
            "webhook.capacity_retry.disabled_mid_flight",
            delivery_id=str(delivery_id),
            attempt=attempt,
        )
        return {"outcome": "disabled"}

    delivery = session.get(WebhookDelivery, delivery_id)
    project = session.get(Project, project_id)
    if delivery is None or project is None:
        log.warning(
            "webhook.capacity_retry.target_missing",
            delivery_id=str(delivery_id),
            project_id=str(project_id),
            delivery_found=delivery is not None,
            project_found=project is not None,
        )
        return {"outcome": "target_missing"}

    if delivery.outcome not in _SUPERSEDABLE_OUTCOMES:
        # Somebody else already resolved this delivery - a manual redelivery
        # (which resets outcome to None and reprocesses from scratch, see
        # services.webhook_service._record_delivery) or, in a tight race, an
        # earlier scheduled attempt. Either way, re-processing here would
        # risk a second scan for the same push; the delivery's current
        # outcome is definitive.
        log.info(
            "webhook.capacity_retry.already_resolved",
            delivery_id=str(delivery_id),
            current_outcome=delivery.outcome,
            attempt=attempt,
        )
        return {"outcome": "already_resolved", "current_outcome": delivery.outcome}

    reason = capacity_guard_reason_sync(session, team_id=project.team_id)
    if reason is None:
        scan_id = enqueue_system_triggered_scan_sync(session, project, metadata=metadata)
        status = "enqueued" if scan_id else "skipped_active_scan"
        delivery.outcome = status
        if scan_id is not None:
            delivery.enqueued_scan_id = scan_id
        session.commit()
        log.info(
            "webhook.capacity_retry.resolved",
            delivery_id=str(delivery_id),
            outcome=status,
            scan_id=str(scan_id) if scan_id else None,
            attempt=attempt,
        )
        return {"outcome": status, "scan_id": scan_id}

    if attempt >= _MAX_RETRY_ATTEMPTS:
        delivery.outcome = "capacity_retry_exhausted"
        session.commit()
        log.warning(
            "webhook.capacity_retry.exhausted",
            delivery_id=str(delivery_id),
            project_id=str(project_id),
            reason=reason,
            attempts=attempt,
        )
        _dispatch_exhausted_notification(
            delivery_id=delivery_id,
            project_id=project_id,
            reason=reason,
            attempts=attempt,
        )
        return {"outcome": "capacity_retry_exhausted", "reason": reason}

    log.info(
        "webhook.capacity_retry.still_blocked",
        delivery_id=str(delivery_id),
        reason=reason,
        attempt=attempt,
    )
    return {"outcome": "still_blocked", "reason": reason}


def _dispatch_exhausted_notification(
    *, delivery_id: uuid.UUID, project_id: uuid.UUID, reason: str, attempts: int
) -> None:
    """Best-effort operator alert once retries are exhausted.

    Mirrors ``tasks.queue_backlog_alert._dispatch_alert``: a broker hiccup
    here logs a WARNING rather than failing the task (the delivery's outcome
    is already committed by the caller before this runs, so a dispatch
    failure loses only the alert, never the outcome record an operator can
    still find with a direct query).

    Dispatch-only notification kind (``NotificationKind.
    WEBHOOK_CAPACITY_RETRY_EXHAUSTED`` - no ``user_id``, so no in-app inbox
    row; see that kind's own docstring for why, same reasoning as S6's
    ``queue_backlog_alert``). Uses whatever email/Slack/Teams channels this
    deployment already has configured - no new channel.
    """
    from notifications.dispatcher import CHANNEL_SLACK, CHANNEL_TEAMS, NotificationKind
    from tasks.notify import send_notification_task

    context = {
        "delivery_id": str(delivery_id),
        "project_id": str(project_id),
        "reason": reason,
        "attempts": str(attempts),
    }
    try:
        send_notification_task.delay(
            NotificationKind.WEBHOOK_CAPACITY_RETRY_EXHAUSTED.value,
            context,
            [CHANNEL_SLACK, CHANNEL_TEAMS],
            [],
        )
    except Exception as exc:  # noqa: BLE001 - broker failure must not crash the task
        log.warning(
            "webhook.capacity_retry.exhausted_notification_dispatch_failed",
            delivery_id=str(delivery_id),
            error=str(exc)[:300],
        )


def _translate_result(result: dict[str, Any]) -> dict[str, Any]:
    """Raise the internal retry signal for a "still blocked" result; pass
    every other result through unchanged.

    Split out from the Celery-decorated wrapper below so this translation is
    directly unit-testable without Celery's ``bind=True`` / ``autoretry_for``
    machinery in the loop (the same reason ``tasks.notify`` keeps
    ``_run_notification`` separate from the decorated task).
    """
    if result["outcome"] == "still_blocked":
        raise _WebhookStillAtCapacity(result.get("reason", "unknown"))
    return result


# ---------------------------------------------------------------------------
# Celery task
# ---------------------------------------------------------------------------


@celery_app.task(  # type: ignore[misc]
    name="trustedoss.webhook_capacity_retry",
    bind=True,
    autoretry_for=(_WebhookStillAtCapacity,),
    retry_backoff=RETRY_BACKOFF_BASE_SECONDS,
    retry_backoff_max=_RETRY_BACKOFF_MAX_SECONDS,
    retry_jitter=True,
    max_retries=_MAX_RETRY_ATTEMPTS,
)
def webhook_capacity_retry_task(
    self: Any,
    *,
    delivery_id: str,
    project_id: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """Thin Celery wrapper around :func:`_process_capacity_retry`.

    ``self.request.retries`` is Celery's own attempt counter (0 on the first,
    non-retry execution), which doubles as ``attempt`` for the exhaustion
    check - no separate counter to keep in sync. The exhaustion check inside
    ``_process_capacity_retry`` fires at ``attempt >= _MAX_RETRY_ATTEMPTS``,
    strictly before this wrapper would ever call ``self.retry()`` on the
    (_MAX_RETRY_ATTEMPTS + 1)-th execution - so Celery's OWN
    ``MaxRetriesExceededError`` path is never reached; the exhausted state is
    always the one this module writes deliberately, not one Celery's retry
    bookkeeping produces as a side effect.
    """
    structlog.contextvars.bind_contextvars(
        task_name="webhook_capacity_retry", delivery_id=delivery_id
    )
    try:
        with sync_session_scope() as session:
            result = _process_capacity_retry(
                session,
                delivery_id=uuid.UUID(delivery_id),
                project_id=uuid.UUID(project_id),
                metadata=metadata,
                attempt=self.request.retries,
            )
        return _translate_result(result)
    finally:
        structlog.contextvars.unbind_contextvars("task_name", "delivery_id")


__all__ = [
    "RETRY_BACKOFF_BASE_SECONDS",
    "_process_capacity_retry",
    "_translate_result",
    "webhook_capacity_retry_task",
]
