# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Vulnerability SLA-breach sweep, Celery Beat (X1 step 2).

Runs daily (02:45 UTC, see ``tasks.celery_app``) and notifies the owning
team's members when OPEN findings on a project's latest succeeded scan have
crossed their SLA due date since the previous sweep. One aggregated in-app
alert per project per member, never one per finding.

Selection contract
------------------
A finding is "breached in this sweep" when ALL of:

  (a) it belongs to the project's LATEST SUCCEEDED scan (the same
      ``ORDER BY created_at DESC, id DESC`` anchor
      ``services.scan_resolution.latest_succeeded_scan_id`` uses, batched
      here as one ``DISTINCT ON (project_id)`` subquery);
  (b) its status is OPEN, i.e. NOT in the build gate's closed set
      (``services.policy_gate._CLOSED_FINDING_STATUSES``:
      not_affected / fixed / false_positive). ``suppressed`` IS open work,
      exactly as the gate and the upgrade engine count it;
  (c) it has a deadline: the EFFECTIVE one, the earlier of the policy's
      (``core.config.vuln_sla_days``; info / unknown have none) and a
      written-down ``due_on`` read in the owning organization's timezone
      (``services.due_date``, ER28a / ER55);
  (d) that deadline crossed within the sweep window::

          window_start <= due < now

      where ``window_start`` is the previous succeeded sweep's ``now`` (see
      "Catch-up" below), so consecutive sweeps cover adjacent spans that do
      not overlap. A due date at exactly ``window_start`` belongs to this
      sweep; one at exactly ``now`` has not crossed yet and belongs to the
      next;
  (e) the project is not archived (``projects.archived_at IS NULL``). An
      archived project is read-only history and nobody is being asked to
      fix it.

All five are evaluated in SQL as one grouped aggregate, so the sweep reads
``(project, severity, count)`` rows instead of every finding of every latest
scan. The deadline expression is the Vulnerabilities list's own
(``services.vulnerability_service._sla_expressions``), which the contract test
already holds to the Python twin in ``services.due_date``. The sweep, the list
column and the drawer therefore cannot disagree about when a deadline passed.

Catch-up (why a missed day is not lost)
---------------------------------------
Each sweep returns the instant it ran up to as ``window_end`` in its
``detail_params``, and ``services.task_run_recorder`` stores that in the
run's ``task_runs`` row. The next sweep reads the most recent SUCCEEDED run of
this task and starts its window at that run's ``window_end``. The anchor is
therefore in PostgreSQL, the system of record, and survives a restart of
everything else.

A tick that never ran (beat down, worker down) or that did not succeed (the
entry point turns an unexpected error into a ``skipped`` row, a killed worker
leaves the row unfinished) does not move the anchor, so the next tick covers
the whole gap in one aggregated alert per project. Consecutive successful
runs cover adjacent, half-open spans, so in the normal sequence a due date
falls in exactly one of them and beat jitter does not repeat an alert.

Delivery is at least once, not exactly once. The row is closed by the
``task_postrun`` signal, after the notifications are enqueued, so a run that
dies between the two leaves no succeeded row and the next run repeats its
span. Two runs that overlap in time (a manual run while the beat run is
still going) both start from the same anchor and both alert on it. Both are
rare and the cost is a duplicate in-app row, which is the cheaper failure
than a lost one.

Two fallbacks keep the window bounded:

  - no anchor (first run, or the history row was reclaimed by the
    ``TASK_RUN_RETENTION_DAYS`` sweep) or an anchor that is not an earlier
    instant: the trailing 24h, which is what the sweep did before it kept an
    anchor;
  - an anchor older than :data:`_MAX_CATCH_UP` (7 days): clamped to that, and
    the dropped span is logged. A sweep switched off for a quarter and then
    switched back on should not announce three months of old deadlines as
    news; those findings are on the ``?sla=overdue`` list.

A disabled run is recorded as ``skipped`` and is never an anchor, so
re-enabling the sweep is covered by the second fallback.

Findings whose due date crossed OUTSIDE any window, e.g. a vulnerability fixed
years ago that REAPPEARS and inherits its original first_detected_at, landing
instantly overdue, are never alerted by the sweep; they surface only in the
``?sla=overdue`` list. Deliberate: an instant-overdue re-arrival is
triage-list material, not a "just crossed the deadline" event.

Fan-out
-------
Per breached project we enumerate the owning team's members who can still
read an inbox: people (not service accounts) whose account is active
(``users.is_active``; a deactivated or anonymised account is skipped). One
``trustedoss.send_notification`` per member is enqueued with ``channels=[]``,
in-app only. ``tasks.notify._apply_prefs_filter`` consults each user's
``NotificationPreferences`` row and writes the ``notifications`` row iff
``in_app_enabled`` (a member who muted in-app gets nothing; outbound channels
are not attempted at all). The payload carries the project name, the
crossed-finding count, a severity breakdown, and a deep link to the project's
Vulnerabilities tab pre-filtered to ``?sla=overdue``.

CLAUDE.md compliance:
  - Core rule #1: the window anchor lives in PostgreSQL (``task_runs``).
  - Core rule #3: DB reads + broker enqueues behind a Celery beat.
  - Core rule #11: the toggle (``VULN_SLA_ALERTS_ENABLED``) and the SLA
    windows are read at call time via ``core.config`` accessors.
  - §5 logging: structlog JSON; no user emails / tokens logged (only ids
    and counts).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import String, cast, func, select
from sqlalchemy.orm import Session

from core.config import vuln_sla_alerts_enabled
from core.db import sync_session_scope
from models import (
    Membership,
    Organization,
    Project,
    Scan,
    Team,
    User,
    Vulnerability,
    VulnerabilityFinding,
)
from models.task_run import TaskRun

# The build gate's closed set is the single owner of "no longer open work"
# (hardening rule #2: one vocabulary, one owner). Importing it keeps the
# sweep, the gate and the upgrade engine counting the same findings.
from services.policy_gate import _CLOSED_FINDING_STATUSES
from services.vulnerability_service import _sla_expressions
from tasks.celery_app import celery_app

log = structlog.get_logger("tasks.vuln_sla_sweep")

#: Window used when there is no usable anchor. Matches the daily beat cadence,
#: so a first run covers exactly the span one regular tick would.
_DEFAULT_WINDOW = timedelta(hours=24)

#: Furthest back a catch-up reaches. See the module docstring.
_MAX_CATCH_UP = timedelta(days=7)

#: Registered Celery name; the ``task_runs`` rows the anchor is read from.
_TASK_NAME = "trustedoss.vuln_sla_sweep"

#: ``detail_code`` of a run that covered a window. Only rows carrying it are
#: anchors, so a future change to what the task returns cannot turn an
#: unrelated succeeded row into one by accident.
_DETAIL_CODE = "vuln_sla_sweep.complete"

# Severity display order for the in-app body breakdown (worst first).
_SEVERITY_ORDER = ("critical", "high", "medium", "low")


# ---------------------------------------------------------------------------
# Window (pure, unit-testable)
# ---------------------------------------------------------------------------


def _window_start(anchor: datetime | None, *, now: datetime) -> datetime:
    """Where this sweep's window begins, given the stored anchor.

    ``None`` or an anchor at or after ``now`` (a restored database snapshot, a
    clock that moved backwards) falls back to the trailing 24h. An anchor
    further back than :data:`_MAX_CATCH_UP` is clamped to it.
    """
    if anchor is None or anchor >= now:
        return now - _DEFAULT_WINDOW
    earliest = now - _MAX_CATCH_UP
    if anchor < earliest:
        log.warning(
            "vuln_sla_sweep_catch_up_clamped",
            anchor=anchor.isoformat(),
            window_start=earliest.isoformat(),
            dropped_hours=round((earliest - anchor).total_seconds() / 3600, 1),
        )
        return earliest
    return anchor


def _parse_anchor(raw: Any) -> datetime | None:
    """Read the stored anchor; ``None`` for anything that is not an aware
    ISO 8601 instant (a value that cannot be trusted is treated as absent)."""
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    try:
        parsed = datetime.fromisoformat(str(raw))
    except ValueError:
        log.warning("vuln_sla_sweep_anchor_unparseable", value=str(raw)[:64])
        return None
    if parsed.tzinfo is None:
        log.warning("vuln_sla_sweep_anchor_unparseable", value=str(raw)[:64])
        return None
    return parsed.astimezone(UTC)


def _load_anchor(session: Session) -> datetime | None:
    """``window_end`` of the most recent succeeded run, or ``None``.

    Only ``outcome = 'succeeded'`` counts: the recorder writes ``skipped``
    for a disabled run and for one the entry point caught an error in, and
    ``failed`` or an unfinished row for a run that died. None of those
    covered their span.
    """
    raw = session.execute(
        select(TaskRun.result["detail_params"]["window_end"].astext)
        .where(
            TaskRun.task_name == _TASK_NAME,
            TaskRun.outcome == "succeeded",
            TaskRun.result["detail_code"].astext == _DETAIL_CODE,
        )
        .order_by(TaskRun.started_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    return _parse_anchor(raw)


def _build_in_app_payload(
    *, project_name: str, by_severity: dict[str, int], window_start: datetime
) -> tuple[str, str]:
    """Build the (title, body) pair for one project's aggregated alert.

    English on purpose (product default language); the FE renders in-app rows
    verbatim today. Severity breakdown is worst-first and only names buckets
    that actually crossed. The body names the start of the span because after
    a missed day the span is longer than a day, and "in the last 24h" would
    then be false.
    """
    total = sum(by_severity.values())
    noun = "finding" if total == 1 else "findings"
    title = f"SLA breach: {total} {noun} overdue in {project_name}"
    parts = [
        f"{sev} {by_severity[sev]}" for sev in _SEVERITY_ORDER if sev in by_severity
    ]
    # Defensive: an unexpected severity key (future enum growth) still renders.
    parts.extend(
        f"{sev} {count}"
        for sev, count in sorted(by_severity.items())
        if sev not in _SEVERITY_ORDER
    )
    since = window_start.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    body = (
        f"{total} open {noun} crossed the remediation SLA since {since} "
        f"({', '.join(parts)}). Review the overdue list and triage or remediate."
    )
    return title, body


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _breached_counts(
    session: Session, *, window_start: datetime, now: datetime
) -> dict[uuid.UUID, tuple[str, uuid.UUID, dict[str, int]]]:
    """``project_id -> (project_name, team_id, {severity: crossed_count})``.

    One grouped query over the latest succeeded scan of every unarchived
    project, filtered on status and on the effective deadline in SQL.
    ``window_start`` and ``now`` are bound parameters rather than the
    database clock, so one sweep judges every row against the same instant
    and tests can place a deadline exactly on either bound.
    """
    latest = (
        select(Scan.project_id.label("project_id"), Scan.id.label("scan_id"))
        .where(Scan.status == "succeeded")
        .distinct(Scan.project_id)
        .order_by(Scan.project_id, Scan.created_at.desc(), Scan.id.desc())
        .subquery()
    )
    (_first_detected, _sla_due, _status, effective_due, _source) = _sla_expressions(
        Organization.timezone
    )
    severity = cast(Vulnerability.severity, String)
    rows = session.execute(
        select(
            Project.id,
            Project.name,
            Project.team_id,
            severity,
            func.count(),
        )
        .select_from(VulnerabilityFinding)
        .join(latest, latest.c.scan_id == VulnerabilityFinding.scan_id)
        .join(
            Vulnerability,
            Vulnerability.id == VulnerabilityFinding.vulnerability_id,
        )
        .join(Project, Project.id == latest.c.project_id)
        .join(Team, Team.id == Project.team_id)
        .join(Organization, Organization.id == Team.organization_id)
        .where(Project.archived_at.is_(None))
        .where(
            cast(VulnerabilityFinding.status, String).notin_(
                _CLOSED_FINDING_STATUSES
            )
        )
        # NULL (no deadline of either kind) fails both comparisons.
        .where(effective_due >= window_start)
        .where(effective_due < now)
        .group_by(Project.id, Project.name, Project.team_id, severity)
    ).all()
    breached: dict[uuid.UUID, tuple[str, uuid.UUID, dict[str, int]]] = {}
    for project_id, name, team_id, sev, count in rows:
        _name, _team, by_severity = breached.setdefault(
            project_id, (str(name), team_id, {})
        )
        by_severity[str(sev)] = int(count)
    return breached


def _team_member_user_ids(session: Session, team_id: uuid.UUID) -> list[uuid.UUID]:
    """Every member of the owning team who can read an alert, Team Admins and
    Developers alike (an SLA breach is team-wide work, not an admin-only
    signal). Per-user muting happens downstream in ``_apply_prefs_filter``."""
    rows = session.execute(
        # People only. A service account has no inbox and no session that
        # could open one, so a row per alert per automation identity is
        # storage nobody will ever read. A deactivated account cannot sign
        # in either, and an anonymised one belongs to nobody any more.
        select(Membership.user_id)
        .join(User, User.id == Membership.user_id)
        .where(
            Membership.team_id == team_id,
            User.is_service_account.is_(False),
            User.is_active.is_(True),
        )
    ).all()
    return [r[0] for r in rows]


# ---------------------------------------------------------------------------
# Beat task
# ---------------------------------------------------------------------------


def _run_sweep(*, now: datetime | None = None) -> dict[str, Any]:
    """Body of the beat task.

    Called directly, nothing is recorded and the anchor does not move: the
    anchor is written by the task-run recorder on the Celery path only.
    ``now`` is injectable so unit tests can pin it.
    """
    summary: dict[str, Any] = {
        "skipped": False,
        "skipped_reason": None,
        "window_start": None,
        "window_end": None,
        "projects_breached": 0,
        "findings_crossed": 0,
        "notifications_enqueued": 0,
    }
    if not vuln_sla_alerts_enabled():
        summary["skipped"] = True
        summary["skipped_reason"] = "disabled"
        log.info("vuln_sla_sweep_disabled")
        return summary

    now = (now or datetime.now(UTC)).astimezone(UTC)

    # Descriptors are collected inside the session and enqueued AFTER it
    # closes (rematch-beat precedent: a slow broker must not extend the DB
    # transaction window).
    to_enqueue: list[dict[str, Any]] = []

    with sync_session_scope() as session:
        window_start = _window_start(_load_anchor(session), now=now)
        summary["window_start"] = window_start.isoformat()
        summary["window_end"] = now.isoformat()
        breached = _breached_counts(session, window_start=window_start, now=now)
        summary["projects_breached"] = len(breached)
        summary["findings_crossed"] = sum(
            sum(by_sev.values()) for _n, _t, by_sev in breached.values()
        )

        for project_id, (project_name, team_id, by_severity) in breached.items():
            title, body = _build_in_app_payload(
                project_name=project_name,
                by_severity=by_severity,
                window_start=window_start,
            )
            link = f"/projects/{project_id}?tab=vulnerabilities&sla=overdue"
            # Context mirrors the in-app payload for a future outbound-channel
            # builder; with ``channels=[]`` today it is carried but unused.
            context = {
                # The routing rules (N9) match on this. It was already carried
                # alongside for the in-app row; naming it in the context is
                # what lets a rule say "this project only".
                "project_id": str(project_id),
                "project_name": project_name,
                "breach_count": str(sum(by_severity.values())),
                "severity_breakdown": ", ".join(
                    f"{sev}={by_severity[sev]}"
                    for sev in _SEVERITY_ORDER
                    if sev in by_severity
                ),
            }
            for user_id in _team_member_user_ids(session, team_id):
                to_enqueue.append(
                    {
                        "kind": "vuln_sla_breach",
                        "context": context,
                        "user_id": str(user_id),
                        "title": title,
                        "body": body,
                        "link": link,
                        "project_id": str(project_id),
                    }
                )

    summary["notifications_enqueued"] = _enqueue_notifications(to_enqueue)
    # Read by ``task_run_recorder`` when the task returns; the next run's
    # anchor. Nothing here writes it, so a run that died above leaves its
    # span to the next run.
    summary["detail_code"] = _DETAIL_CODE
    summary["detail_params"] = {
        "window_start": summary["window_start"],
        "window_end": summary["window_end"],
        "projects_breached": summary["projects_breached"],
        "findings_crossed": summary["findings_crossed"],
        "notifications_enqueued": summary["notifications_enqueued"],
    }
    log.info(
        "vuln_sla_sweep_complete",
        window_start=summary["window_start"],
        window_end=summary["window_end"],
        projects_breached=summary["projects_breached"],
        findings_crossed=summary["findings_crossed"],
        notifications_enqueued=summary["notifications_enqueued"],
    )
    return summary


def _enqueue_notifications(descriptors: list[dict[str, Any]]) -> int:
    """One ``trustedoss.send_notification`` per (project x member) descriptor.

    ``channels=[]``: in-app only. The notify task's prefs filter writes the
    in-app row (iff ``in_app_enabled``) and then short-circuits on the empty
    channel list, so no outbound template for ``vuln_sla_breach`` is needed
    yet. Late import + per-descriptor exception swallow mirror the rematch
    beat's dispatch helper (a broker hiccup must not fail the whole sweep).
    """
    from tasks.notify import send_notification_task

    enqueued = 0
    for d in descriptors:
        try:
            send_notification_task.delay(
                d["kind"],
                d["context"],
                [],  # channels: in-app only
                [],  # recipients
                user_id=d["user_id"],
                in_app_title=d["title"],
                in_app_body=d["body"],
                in_app_link=d["link"],
                in_app_target_table="projects",
                in_app_target_id=d["project_id"],
            )
            enqueued += 1
        except Exception as exc:  # noqa: BLE001 - broker failure is per-descriptor
            log.warning(
                "vuln_sla_sweep_notification_dispatch_failed",
                kind=d.get("kind"),
                project_id=d.get("project_id"),
                error=str(exc)[:300],
            )
    return enqueued


@celery_app.task(name="trustedoss.vuln_sla_sweep")  # type: ignore[misc]
def vuln_sla_sweep() -> dict[str, Any]:
    """Daily beat entry; see the module docstring for the selection contract.

    Never raises: any unexpected failure degrades to a skip summary + WARNING
    so the beat stays healthy (rematch-beat convention). That summary
    carries a ``skipped_reason`` and no ``detail_code``, so the recorder
    writes a ``skipped`` row that is never an anchor and the next run covers
    this run's span.
    """
    structlog.contextvars.bind_contextvars(task_name="vuln_sla_sweep")
    try:
        return _run_sweep()
    except Exception as exc:  # noqa: BLE001 - beat task must not raise
        log.warning(
            "vuln_sla_sweep_unexpected_error",
            error=str(exc)[:300],
        )
        return {
            "skipped": True,
            "skipped_reason": f"unexpected:{type(exc).__name__}",
            "window_start": None,
            "window_end": None,
            "projects_breached": 0,
            "findings_crossed": 0,
            "notifications_enqueued": 0,
        }
    finally:
        structlog.contextvars.unbind_contextvars("task_name")


__all__ = [
    "vuln_sla_sweep",
    # Exposed for tests: driven directly without Celery task machinery.
    # ``_CLOSED_FINDING_STATUSES`` is a deliberate re-export: the contract
    # test asserts identity with the gate's set (hardening rule #2).
    "_CLOSED_FINDING_STATUSES",
    "_DEFAULT_WINDOW",
    "_DETAIL_CODE",
    "_MAX_CATCH_UP",
    "_breached_counts",
    "_build_in_app_payload",
    "_enqueue_notifications",
    "_load_anchor",
    "_parse_anchor",
    "_run_sweep",
    "_TASK_NAME",
    "_window_start",
]
