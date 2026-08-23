# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Queue backlog alert - Celery Beat (S6, concurrency-scaling-plan-2026-08-22.md §3.2/§4).

Compose deployments have no autoscaler layer (principle 4 - the plan
deliberately does not build one: "그것은 오케스트레이터를 다시 만드는 것이라
열지 않는다"). What the product owes a Compose operator instead is a capacity
formula (the installation guide's queue-capacity section) and a signal that
the fixed capacity they sized has been exceeded. This beat task is that
signal, for the two Celery queues S3 split the worker pool into
(``trustedoss.scan``, ``trustedoss.default`` - see ``tasks.celery_app``'s
``_SCAN_QUEUE`` / ``_DEFAULT_QUEUE``).

Dependency on M2
-----------------
This reuses ``services.metrics_service._broker_queue_backlogs`` - the SAME
broker read M2's ``/metrics`` series uses - rather than opening a second
reading of the queue length, so the two can never disagree about what "the
backlog" was at a given tick (hardening rule 2: one owner for one piece of
data). That makes this task's own toggle, ``queue_backlog_alert_enabled()``,
a HARD dependent of M2's ``queue_backlog_metrics_enabled()``: turning this on
while M2's switch is off does not raise or crash the beat - it degrades to a
skip, logged once per tick at WARNING, exactly like every other "nothing
configured" branch this beat pairs with (``vuln_sla_alerts_enabled`` in
``tasks.vuln_sla_sweep``). Both default to off (principle 5); a deployment
that wants the alert must turn both switches on.

Selection contract - what "sustained" means
--------------------------------------------
A momentary spike is not an incident (a burst of webhook-triggered scans can
land on the same beat tick and drain within minutes); a queue that is STILL
over its threshold ``queue_backlog_alert_sustain_seconds()`` after it first
crossed is. Per queue, Redis holds two small pieces of state under
``trustedoss:queue_backlog_alert:{queue}:*``:

  - ``breach_since``  - epoch seconds the backlog was FIRST observed over
                        threshold on an unbroken run of ticks. Cleared the
                        moment a tick observes the backlog back at or under
                        threshold (the incident resolved).
  - ``alerted_at``     - epoch seconds of the last alert sent for this queue.
                        A queue within ``queue_backlog_alert_cooldown_seconds()``
                        of its own last alert is not re-alerted even if still
                        breached - the cooldown is a repeat-reminder interval,
                        not a one-shot suppression, so a queue that outlives
                        the cooldown alerts again.

:func:`_evaluate` is the pure decision function (testable with plain values,
no Redis, no Celery - same shape as ``tasks.vuln_sla_sweep._select_breached``).
:func:`_run_check` is the I/O shell around it. Both state keys carry a TTL
(``_STATE_TTL_SECONDS``, not an env knob - an internal self-healing bound, not
a tuning surface) well past any realistic sustain/cooldown value, so a beat
outage that outlives the TTL loses the in-flight incident's timer rather than
alerting on stale state forever.

Delivery
--------
One ``trustedoss.send_notification`` per breached queue, kind
``queue_backlog_alert`` (``notifications.dispatcher.NotificationKind``),
channels Slack + Teams - the same two channels ``tasks.trivy_db_refresh``
uses for its own operator-facing capacity/health alert, and for the same
reason: this is a deployment-wide signal, not addressed to any one user, so
there is no ``user_id`` to look up a per-user channel preference for (no
``in_app_*`` kwargs either - see the dispatcher kind's own docstring for why
this is deliberately dispatch-only, not an in-app inbox row).

CLAUDE.md compliance:
  - Core rule #3: pure Redis reads/writes + a broker enqueue behind a Celery
    beat; no scan work, no blocking I/O on a request path.
  - Core rule #11: every toggle and threshold is read at call time via
    ``core.config`` accessors, never cached at import time.
  - §5 logging: structlog JSON; no user PII (there is none in this data -
    only queue names, counts and durations).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import structlog

from core.config import (
    queue_backlog_alert_cooldown_seconds,
    queue_backlog_alert_default_queue_threshold,
    queue_backlog_alert_enabled,
    queue_backlog_alert_scan_queue_threshold,
    queue_backlog_alert_sustain_seconds,
    queue_backlog_metrics_enabled,
    redis_url,
)
from tasks.celery_app import celery_app

log = structlog.get_logger("tasks.queue_backlog_alert")

# Redis key TTL for the per-queue breach-tracking state. An internal
# self-healing bound, not an operator-tunable knob (CLAUDE.md core rule #11
# is about environment-driven CONFIGURATION; this is a leak guard). Generous
# relative to any realistic sustain/cooldown setting so state never expires
# mid-incident, but bounded so a beat outage does not leave a stale
# "breached since" timestamp alive forever.
_STATE_TTL_SECONDS = 86_400  # 24h

_REDIS_KEY_PREFIX = "trustedoss:queue_backlog_alert"


# ---------------------------------------------------------------------------
# Pure selection logic (unit-testable without Redis or Celery)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QueueBacklogState:
    """Per-queue breach-tracking state - the two Redis values, as Python."""

    breach_since: float | None = None
    alerted_at: float | None = None


@dataclass(frozen=True)
class QueueBacklogDecision:
    """What :func:`_evaluate` decided for one queue on one tick."""

    next_state: QueueBacklogState
    should_alert: bool
    sustained_seconds: float = 0.0


def _evaluate(
    *,
    backlog: int,
    threshold: int,
    now: float,
    sustain_seconds: int,
    cooldown_seconds: int,
    state: QueueBacklogState,
) -> QueueBacklogDecision:
    """Decide the next state and whether to alert, for one queue on one tick.

    Four branches, in order:

      1. At or under threshold: the incident (if any) is over. Clear both
         timestamps regardless of what they were - a resolved queue does not
         carry a stale ``alerted_at`` into its next, unrelated breach.
      2. Over threshold, no ``breach_since`` yet: this tick is the first
         observation. Start the clock; never alert on a first observation
         (the whole point of "sustained" is not to alert on it).
      3. Over threshold, ``breach_since`` set, but not sustained long enough
         yet, OR sustained long enough but still within the cooldown of the
         last alert: keep the clock running, do not alert again.
      4. Over threshold, sustained long enough, and (never alerted OR past
         cooldown): alert, and stamp ``alerted_at`` = now.

    A due date at exactly ``sustain_seconds`` counts as sustained (``>=``,
    not ``>``) - the same "boundary belongs to the tick that reaches it"
    convention ``tasks.vuln_sla_sweep._select_breached`` uses for its own
    window comparison.
    """
    if backlog <= threshold:
        return QueueBacklogDecision(next_state=QueueBacklogState(), should_alert=False)

    breach_since = state.breach_since if state.breach_since is not None else now
    sustained_for = now - breach_since

    if sustained_for < sustain_seconds:
        return QueueBacklogDecision(
            next_state=QueueBacklogState(breach_since=breach_since, alerted_at=state.alerted_at),
            should_alert=False,
            sustained_seconds=sustained_for,
        )

    if state.alerted_at is not None and (now - state.alerted_at) < cooldown_seconds:
        return QueueBacklogDecision(
            next_state=QueueBacklogState(breach_since=breach_since, alerted_at=state.alerted_at),
            should_alert=False,
            sustained_seconds=sustained_for,
        )

    return QueueBacklogDecision(
        next_state=QueueBacklogState(breach_since=breach_since, alerted_at=now),
        should_alert=True,
        sustained_seconds=sustained_for,
    )


def _threshold_for_queue(queue: str) -> int:
    from tasks.celery_app import _SCAN_QUEUE

    if queue == _SCAN_QUEUE:
        return queue_backlog_alert_scan_queue_threshold()
    return queue_backlog_alert_default_queue_threshold()


# ---------------------------------------------------------------------------
# Redis state I/O
# ---------------------------------------------------------------------------


def _state_key(queue: str, field: str) -> str:
    return f"{_REDIS_KEY_PREFIX}:{queue}:{field}"


def _load_state(client: Any, queue: str) -> QueueBacklogState:
    breach_since_raw = client.get(_state_key(queue, "breach_since"))
    alerted_at_raw = client.get(_state_key(queue, "alerted_at"))
    return QueueBacklogState(
        breach_since=float(breach_since_raw) if breach_since_raw else None,
        alerted_at=float(alerted_at_raw) if alerted_at_raw else None,
    )


def _save_state(client: Any, queue: str, state: QueueBacklogState) -> None:
    breach_key = _state_key(queue, "breach_since")
    alert_key = _state_key(queue, "alerted_at")
    if state.breach_since is None:
        client.delete(breach_key)
    else:
        client.set(breach_key, state.breach_since, ex=_STATE_TTL_SECONDS)
    if state.alerted_at is None:
        client.delete(alert_key)
    else:
        client.set(alert_key, state.alerted_at, ex=_STATE_TTL_SECONDS)


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------


def _dispatch_alert(
    *, queue: str, backlog: int, threshold: int, sustained_seconds: float
) -> bool:
    """Enqueue one ``trustedoss.send_notification`` for a breached queue.

    Best-effort, mirroring ``tasks.trivy_db_refresh._dispatch_failure_notification``
    and ``tasks.vuln_sla_sweep._enqueue_notifications``: a broker hiccup logs
    a WARNING rather than failing the whole sweep. Returns whether the
    enqueue itself succeeded (not whether delivery did - that is the
    dispatcher's own retry envelope).
    """
    from notifications.dispatcher import CHANNEL_SLACK, CHANNEL_TEAMS, NotificationKind
    from tasks.notify import send_notification_task

    context = {
        "queue": queue,
        "backlog": str(backlog),
        "threshold": str(threshold),
        "sustained_minutes": str(round(sustained_seconds / 60, 1)),
    }
    try:
        send_notification_task.delay(
            NotificationKind.QUEUE_BACKLOG_ALERT.value,
            context,
            [CHANNEL_SLACK, CHANNEL_TEAMS],
            [],
        )
        return True
    except Exception as exc:  # noqa: BLE001 - broker failure must not crash the beat
        log.warning(
            "queue_backlog_alert_notification_dispatch_failed",
            queue=queue,
            error=str(exc)[:300],
        )
        return False


# ---------------------------------------------------------------------------
# Beat task
# ---------------------------------------------------------------------------


def _run_check() -> dict[str, Any]:
    """Body of the beat task (testable without Celery's task machinery)."""
    summary: dict[str, Any] = {
        "skipped": False,
        "skipped_reason": None,
        "queues_checked": 0,
        "alerts_sent": 0,
    }
    if not queue_backlog_alert_enabled():
        summary["skipped"] = True
        summary["skipped_reason"] = "disabled"
        log.info("queue_backlog_alert_disabled")
        return summary
    if not queue_backlog_metrics_enabled():
        # Hard dependency on M2 (see module docstring) - never an error, the
        # same "degrade to a skip" contract every optional beat sweep in this
        # codebase follows.
        summary["skipped"] = True
        summary["skipped_reason"] = "metrics_disabled"
        log.warning(
            "queue_backlog_alert_metrics_disabled",
            hint="QUEUE_BACKLOG_ALERT_ENABLED is on but QUEUE_BACKLOG_METRICS_ENABLED "
            "(M2) is off - turn both on for this alert to do anything",
        )
        return summary

    import redis as redis_lib

    from services.metrics_service import _broker_queue_backlogs

    backlogs = _broker_queue_backlogs()
    now = time.time()
    sustain_seconds = queue_backlog_alert_sustain_seconds()
    cooldown_seconds = queue_backlog_alert_cooldown_seconds()

    client = redis_lib.Redis.from_url(redis_url(), decode_responses=True)
    try:
        for queue, backlog in backlogs.items():
            threshold = _threshold_for_queue(queue)
            state = _load_state(client, queue)
            decision = _evaluate(
                backlog=backlog,
                threshold=threshold,
                now=now,
                sustain_seconds=sustain_seconds,
                cooldown_seconds=cooldown_seconds,
                state=state,
            )
            _save_state(client, queue, decision.next_state)
            summary["queues_checked"] += 1
            if decision.should_alert:
                dispatched = _dispatch_alert(
                    queue=queue,
                    backlog=backlog,
                    threshold=threshold,
                    sustained_seconds=decision.sustained_seconds,
                )
                if dispatched:
                    summary["alerts_sent"] += 1
                log.warning(
                    "queue_backlog_alert_fired",
                    queue=queue,
                    backlog=backlog,
                    threshold=threshold,
                    sustained_seconds=round(decision.sustained_seconds, 1),
                )
    finally:
        client.close()  # type: ignore[no-untyped-call]

    log.info(
        "queue_backlog_alert_check_complete",
        queues_checked=summary["queues_checked"],
        alerts_sent=summary["alerts_sent"],
    )
    return summary


@celery_app.task(name="trustedoss.queue_backlog_alert_check")  # type: ignore[misc]
def queue_backlog_alert_check() -> dict[str, Any]:
    """Beat entry - see the module docstring for the full contract.

    Never raises: any unexpected failure degrades to a skip summary + WARNING
    so the beat stays healthy (same convention as ``tasks.vuln_sla_sweep``).
    """
    structlog.contextvars.bind_contextvars(task_name="queue_backlog_alert_check")
    try:
        return _run_check()
    except Exception as exc:  # noqa: BLE001 - beat task must not raise
        log.warning(
            "queue_backlog_alert_check_unexpected_error",
            error=str(exc)[:300],
        )
        return {
            "skipped": True,
            "skipped_reason": f"unexpected:{type(exc).__name__}",
            "queues_checked": 0,
            "alerts_sent": 0,
        }
    finally:
        structlog.contextvars.unbind_contextvars("task_name")


__all__ = [
    "QueueBacklogState",
    "QueueBacklogDecision",
    "queue_backlog_alert_check",
    # Exposed for tests - driven directly without Celery task machinery.
    "_dispatch_alert",
    "_evaluate",
    "_load_state",
    "_run_check",
    "_save_state",
    "_threshold_for_queue",
]
