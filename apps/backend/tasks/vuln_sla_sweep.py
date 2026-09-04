# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Vulnerability SLA-breach sweep — Celery Beat (X1 step 2).

Runs daily (02:45 UTC, see ``tasks.celery_app``) and notifies the owning
team's members when OPEN findings on a project's latest succeeded scan have
JUST crossed their per-severity SLA due date. One aggregated in-app alert per
project per member — never one per finding.

Selection contract
------------------
A finding is "breached in this sweep" when ALL of:

  (a) it belongs to the project's LATEST SUCCEEDED scan (the same
      ``ORDER BY created_at DESC, id DESC`` anchor
      ``services.scan_resolution.latest_succeeded_scan_id`` uses — batched
      here as one ``DISTINCT ON (project_id)`` query);
  (b) its status is OPEN — i.e. NOT in the build gate's closed set
      (``services.policy_gate._CLOSED_FINDING_STATUSES``:
      not_affected / fixed / false_positive). ``suppressed`` IS open work,
      exactly as the gate and the upgrade engine count it;
  (c) its severity carries an SLA window (``core.config.vuln_sla_days`` —
      info / unknown have none and never alert);
  (d) its due date crossed within the sweep window::

          now - window <= due < now        (window = 24h, the beat cadence)

Deduplication (why the window IS the dedup)
-------------------------------------------
The sweep keeps NO delivery ledger. Because each daily tick only alerts on
due dates inside the trailing 24h window, a finding's breach is observed by
exactly ONE tick — the next day its due date has fallen out of the window and
it stays silent. This makes the sweep stateless and idempotent-per-day
without a "notified_at" column: a still-open overdue finding is not re-alerted
daily (the Vulnerabilities tab's ``?sla=overdue`` view is the persistent
surface for aged breaches), and a beat outage longer than the window loses
that window's alerts rather than replaying them — an accepted trade for
statelessness at this severity of signal (the in-app list still shows them).
Beat jitter cuts BOTH ways (security review note): if yesterday's tick ran
late (queue delay) and today's runs on time, due dates in the overlap are
alerted twice; if today's runs late, the gap's due dates are missed. Both are
bounded by the jitter size, and neither compounds. If exactly-once ever
matters, anchor the window at the previous tick's timestamp (one Redis key)
instead of ``now - 24h``.
Findings whose due date crossed OUTSIDE any window entirely — e.g. a
vulnerability fixed years ago that REAPPEARS and inherits its original
first_detected_at, landing instantly overdue — are never alerted by the
sweep; they surface only in the ``?sla=overdue`` list. Deliberate: an
instant-overdue re-arrival is triage-list material, not a "just crossed the
deadline" event.

Fan-out
-------
Per breached project we enumerate the owning team's memberships and enqueue
one ``trustedoss.send_notification`` per member with ``channels=[]`` —
in-app only. ``tasks.notify._apply_prefs_filter`` consults each user's
``NotificationPreferences`` row and writes the ``notifications`` row iff
``in_app_enabled`` (a member who muted in-app gets nothing; outbound
channels are not attempted at all). The payload carries the project name,
the crossed-finding count, a severity breakdown, and a deep link to the
project's Vulnerabilities tab pre-filtered to ``?sla=overdue``.

CLAUDE.md compliance:
  - Core rule #3: pure DB reads + broker enqueues behind a Celery beat.
  - Core rule #11: the toggle (``VULN_SLA_ALERTS_ENABLED``) and the SLA
    windows are read at call time via ``core.config`` accessors.
  - §5 logging: structlog JSON; no user emails / tokens logged (only ids
    and counts).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.config import vuln_sla_alerts_enabled, vuln_sla_days
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

# The build gate's closed set is the single owner of "no longer open work"
# (hardening rule #2 — one vocabulary, one owner). Importing it keeps the
# sweep, the gate and the upgrade engine counting the same findings.
from services.due_date import effective_due
from services.policy_gate import _CLOSED_FINDING_STATUSES
from tasks.celery_app import celery_app

log = structlog.get_logger("tasks.vuln_sla_sweep")

# Sweep window — matches the daily beat cadence so every due-date crossing is
# observed by exactly one tick (see the module docstring's dedup rationale).
_SWEEP_WINDOW = timedelta(hours=24)

# Severity display order for the in-app body breakdown (worst first).
_SEVERITY_ORDER = ("critical", "high", "medium", "low")


# ---------------------------------------------------------------------------
# Pure selection logic (unit-testable without a session)
# ---------------------------------------------------------------------------



def _policy_due(severity: str, first_detected: datetime) -> datetime | None:
    """The deadline the policy alone gives a finding, or None.

    Two callers here compute this, and the windows are environment variables
    read at call time, so writing it twice would let a future change to one
    reading drift from the other. ``None`` for severities that carry no window
    (info, unknown): those have no policy deadline, only whatever somebody
    wrote down.
    """
    days = vuln_sla_days(severity)
    return None if days is None else first_detected + timedelta(days=days)

def _select_breached(
    rows: Iterable[
        tuple[uuid.UUID, str, str, datetime, date | None, str, uuid.UUID]
    ],
    *,
    now: datetime,
    window: timedelta = _SWEEP_WINDOW,
) -> dict[uuid.UUID, dict[str, int]]:
    """Reduce candidate rows to ``project_id → {severity: crossed_count}``.

    ``rows`` are ``(project_id, status, severity, first_detected, due_on)``
    tuples for
    the latest-succeeded-scan findings (the SQL side deliberately does NOT
    pre-filter status/severity/window so this function owns the full selection
    contract and unit tests can drive every branch with plain tuples):

      - closed statuses (gate vocabulary) are dropped;
      - the deadline is the EFFECTIVE one (``services.due_date``): a
        written-down ``due_on`` wins when it is earlier than the policy's,
        which also means a finding whose severity has no SLA window still has
        a deadline once somebody writes one down. A row with neither is
        dropped;
      - only due dates inside ``now - window <= due < now`` count — a due
        date at exactly ``now - window`` is INcluded (it belongs to this
        tick), one at exactly ``now`` is EXcluded (it has not crossed yet;
        tomorrow's tick owns it).
    """
    breached: dict[uuid.UUID, dict[str, int]] = {}
    for (
        project_id,
        status,
        severity,
        first_detected,
        due_on,
        timezone,
        organization_id,
    ) in rows:
        if status in _CLOSED_FINDING_STATUSES:
            continue
        policy_due = _policy_due(severity, first_detected)
        due, _source = effective_due(
            sla_due=policy_due,
            due_on=due_on,
            timezone=timezone,
            organization_id=organization_id,
        )
        if due is None:
            continue
        if not (now - window <= due < now):
            continue
        per_project = breached.setdefault(project_id, {})
        per_project[severity] = per_project.get(severity, 0) + 1
    return breached



def _build_in_app_payload(
    *, project_name: str, by_severity: dict[str, int]
) -> tuple[str, str]:
    """Build the (title, body) pair for one project's aggregated alert.

    English on purpose (product default language); the FE renders in-app rows
    verbatim today. Severity breakdown is worst-first and only names buckets
    that actually crossed.
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
    body = (
        f"{total} open {noun} crossed the remediation SLA in the last 24h "
        f"({', '.join(parts)}). Review the overdue list and triage or remediate."
    )
    return title, body


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _candidate_rows(
    session: Session,
) -> list[tuple[uuid.UUID, str, str, datetime, date | None, str, uuid.UUID]]:
    """Fetch one row per finding on every project's latest succeeded scan.

    ``(project_id, status, severity, first_detected, due_on, timezone,
    organization_id)``. The timezone is read per row rather than once for the
    deployment: a written-down deadline expires at the end of that day in the
    owning organization's zone (ER55), and this sweep spans organizations. A
    single value applied to all of them would be wrong for every organization
    but one, and wrong silently.

    The latest-scan anchor mirrors ``scan_resolution.latest_succeeded_scan_id``
    (``created_at DESC, id DESC``, status='succeeded') batched as one
    ``DISTINCT ON (project_id)`` subquery — the same shape the dashboard's
    portfolio resolver uses. ``COALESCE(first_detected_at, created_at)`` folds
    pre-0041 legacy rows onto their insert time (their SLA clock started when
    we first saw them).
    """
    latest = (
        select(Scan.project_id.label("project_id"), Scan.id.label("scan_id"))
        .where(Scan.status == "succeeded")
        .distinct(Scan.project_id)
        .order_by(Scan.project_id, Scan.created_at.desc(), Scan.id.desc())
        .subquery()
    )
    rows = session.execute(
        select(
            latest.c.project_id,
            VulnerabilityFinding.status,
            Vulnerability.severity,
            func.coalesce(
                VulnerabilityFinding.first_detected_at,
                VulnerabilityFinding.created_at,
            ),
            VulnerabilityFinding.due_on,
            Organization.timezone,
            Organization.id,
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
    ).all()
    return [
        (r[0], str(r[1]), str(r[2]), r[3], r[4], str(r[5]), r[6]) for r in rows
    ]


def _project_meta(
    session: Session, project_id: uuid.UUID
) -> tuple[str, uuid.UUID] | None:
    """Resolve (name, team_id) for a breached project. None when the project
    vanished between the candidate query and the fan-out (FK cascade race)."""
    row = session.execute(
        select(Project.name, Project.team_id).where(Project.id == project_id)
    ).first()
    if row is None:
        return None
    return str(row[0]), row[1]


def _team_member_user_ids(session: Session, team_id: uuid.UUID) -> list[uuid.UUID]:
    """Every membership of the owning team — Team Admins and Developers alike
    (an SLA breach is team-wide work, not an admin-only signal). Per-user
    muting happens downstream in ``_apply_prefs_filter``."""
    rows = session.execute(
        # People only. A service account has no inbox and no session that
        # could open one, so a row per alert per automation identity is
        # storage nobody will ever read.
        select(Membership.user_id)
        .join(User, User.id == Membership.user_id)
        .where(Membership.team_id == team_id, User.is_service_account.is_(False))
    ).all()
    return [r[0] for r in rows]


# ---------------------------------------------------------------------------
# Beat task
# ---------------------------------------------------------------------------


def _run_sweep() -> dict[str, Any]:
    """Body of the beat task (testable without Celery's task machinery)."""
    summary: dict[str, Any] = {
        "skipped": False,
        "skipped_reason": None,
        "projects_breached": 0,
        "findings_crossed": 0,
        "notifications_enqueued": 0,
    }
    if not vuln_sla_alerts_enabled():
        summary["skipped"] = True
        summary["skipped_reason"] = "disabled"
        log.info("vuln_sla_sweep_disabled")
        return summary

    # Descriptors are collected inside the session and enqueued AFTER it
    # closes (rematch-beat precedent: a slow broker must not extend the DB
    # transaction window).
    to_enqueue: list[dict[str, Any]] = []
    now = datetime.now(UTC)

    with sync_session_scope() as session:
        breached = _select_breached(_candidate_rows(session), now=now)
        summary["projects_breached"] = len(breached)
        summary["findings_crossed"] = sum(
            sum(by_sev.values()) for by_sev in breached.values()
        )

        for project_id, by_severity in breached.items():
            meta = _project_meta(session, project_id)
            if meta is None:
                continue
            project_name, team_id = meta
            title, body = _build_in_app_payload(
                project_name=project_name, by_severity=by_severity
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
    log.info(
        "vuln_sla_sweep_complete",
        projects_breached=summary["projects_breached"],
        findings_crossed=summary["findings_crossed"],
        notifications_enqueued=summary["notifications_enqueued"],
    )
    return summary


def _enqueue_notifications(descriptors: list[dict[str, Any]]) -> int:
    """One ``trustedoss.send_notification`` per (project × member) descriptor.

    ``channels=[]`` — in-app only. The notify task's prefs filter writes the
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
                [],  # channels — in-app only
                [],  # recipients
                user_id=d["user_id"],
                in_app_title=d["title"],
                in_app_body=d["body"],
                in_app_link=d["link"],
                in_app_target_table="projects",
                in_app_target_id=d["project_id"],
            )
            enqueued += 1
        except Exception as exc:  # noqa: BLE001 — broker failure is per-descriptor
            log.warning(
                "vuln_sla_sweep_notification_dispatch_failed",
                kind=d.get("kind"),
                project_id=d.get("project_id"),
                error=str(exc)[:300],
            )
    return enqueued


@celery_app.task(name="trustedoss.vuln_sla_sweep")  # type: ignore[misc]
def vuln_sla_sweep() -> dict[str, Any]:
    """Daily beat entry — see the module docstring for the selection contract.

    Never raises: any unexpected failure degrades to a skip summary + WARNING
    so the beat stays healthy (rematch-beat convention).
    """
    structlog.contextvars.bind_contextvars(task_name="vuln_sla_sweep")
    try:
        return _run_sweep()
    except Exception as exc:  # noqa: BLE001 — beat task must not raise
        log.warning(
            "vuln_sla_sweep_unexpected_error",
            error=str(exc)[:300],
        )
        return {
            "skipped": True,
            "skipped_reason": f"unexpected:{type(exc).__name__}",
            "projects_breached": 0,
            "findings_crossed": 0,
            "notifications_enqueued": 0,
        }
    finally:
        structlog.contextvars.unbind_contextvars("task_name")


__all__ = [
    "vuln_sla_sweep",
    # Exposed for tests — driven directly without Celery task machinery.
    # ``_CLOSED_FINDING_STATUSES`` is a deliberate re-export: the contract
    # test asserts identity with the gate's set (hardening rule #2).
    "_CLOSED_FINDING_STATUSES",
    "_build_in_app_payload",
    "_candidate_rows",
    "_enqueue_notifications",
    "_run_sweep",
    "_select_breached",
]
