# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Shared scan-pipeline orchestration helpers.

These are the self-contained terminal-state writers and the per-stage
progress writer that the source scan pipeline (:mod:`tasks.scan_source`)
uses to drive a :class:`models.Scan` row through ``running`` →
``succeeded`` / ``failed`` while fanning out WebSocket progress frames.

They were extracted verbatim from ``tasks.scan_source`` so a future
SBOM-ingest Celery task can reuse them through a clean public seam — no
``from tasks.scan_source import _private_name`` cross-module reach into a
sibling task module.

Behaviour is byte-identical to the original ``scan_source`` privates:

  - ``mark_failed``            (was ``_mark_failed``)
  - ``record_terminal_failure`` (was ``_record_terminal_failure``)
  - ``mark_succeeded``         (was ``_mark_succeeded``)
  - ``set_stage``              (was ``_set_stage``)

``set_stage`` is the one generalisation: the original ``_set_stage`` pulled
its percent from ``scan_source._STAGE_PROGRESS`` — a mapping that is specific
to the source pipeline and does not belong in a shared module. ``set_stage``
takes ``percent`` as an explicit argument instead. The caller passes
``_STAGE_PROGRESS.get(stage)`` so the original behaviour is preserved exactly:

  - a *known* stage → its mapped int percent (DB + log + publish);
  - an *unknown* stage → ``percent=None`` → the row keeps its prior
    ``progress_percent`` (matching the original ``.get(stage,
    scan.progress_percent)`` fallback), the log line carries the raw ``None``
    (matching the original ``_STAGE_PROGRESS.get(stage)`` log value), and the
    published frame carries the committed (prior) percent.

Import-cycle note: this module depends only on ``models``, ``core.db``,
``tasks._progress`` and ``tasks.scan_retention``. None of those import this
module or ``tasks.scan_source`` at module top, so importing this from
``scan_source`` does not create a cycle (verified at extraction time).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from core.db import sync_session_scope
from models import Membership, Project, Scan, User
from services import scan_outcome
from tasks._progress import publish_progress
from tasks.scan_retention import (
    supersede_prior_ref_scans,
    supersede_prior_release_scans,
)

log = structlog.get_logger("tasks.scan_pipeline")

#: N18 x N9 pairing: nobody is watching a scheduled scan run, unlike a scan a
#: person just triggered from the UI. Only THIS trigger source notifies on
#: completion; a manual/webhook/CI scan still reports through the surface
#: that started it (the UI polling, the Git host's check run), so extending
#: this to every scan would duplicate an answer nobody asked for here.
_SCHEDULE_TRIGGER = "schedule"


def _team_member_user_ids(session: Session, team_id: uuid.UUID) -> list[uuid.UUID]:
    """Every membership of the owning team. Service accounts have no inbox."""
    rows = session.execute(
        select(Membership.user_id)
        .join(User, User.id == Membership.user_id)
        .where(Membership.team_id == team_id, User.is_service_account.is_(False))
    ).all()
    return [r[0] for r in rows]


def _notify_schedule_completion(session: Session, scan: Scan, *, kind: str) -> None:
    """Tell the project's team a schedule-triggered scan finished (N18 x N9).

    Best-effort: never raises. A broker hiccup or a missing project must not
    turn a terminal scan-state write into a failed Celery task.
    """
    metadata = scan.scan_metadata or {}
    if metadata.get("trigger") != _SCHEDULE_TRIGGER:
        return
    try:
        project = session.get(Project, scan.project_id)
        if project is None:
            return
        member_ids = _team_member_user_ids(session, project.team_id)
        if not member_ids:
            return

        from tasks.notify import send_notification_task

        if kind == "scan_completed":
            title = f"Scheduled scan completed: {project.name}"
            body = "The scheduled scan finished. Review the results when you have a moment."
        else:
            title = f"Scheduled scan failed: {project.name}"
            body = (scan.error_message or "The scheduled scan did not complete.")[:1000]

        for user_id in member_ids:
            send_notification_task.delay(
                kind,
                {"project_id": str(project.id), "project_name": project.name},
                [],  # base channels: in-app only; N9 rules may still add outbound ones
                [],
                user_id=str(user_id),
                in_app_title=title,
                in_app_body=body,
                in_app_link=f"/projects/{project.id}",
                in_app_target_table="scans",
                in_app_target_id=str(scan.id),
            )
    except Exception as exc:  # noqa: BLE001 (delivery must not fail the pipeline)
        log.warning(
            "schedule_scan_notify_failed",
            scan_id=str(scan.id),
            kind=kind,
            error=str(exc)[:300],
        )


#: The states a failure may be recorded over. A scan that already ended
#: (cancelled, succeeded, failed) keeps its own ending: a task that notices a
#: problem after an operator cancelled the scan must not report the scan as
#: failed instead (security review on #522, N-L4).
_FAILABLE_STATUSES = ("queued", "running")


def mark_failed(session: Session, scan: Scan, message: str) -> None:
    completed_at = datetime.now(UTC)
    closed = scan_outcome.close_stage_timings(scan.scan_metadata, now=completed_at)
    # Conditional UPDATE, not a read-then-write: the cancel commits from the
    # API process, so the row this session loaded may already be stale.
    changed = session.execute(
        update(Scan)
        .where(Scan.id == scan.id, Scan.status.in_(_FAILABLE_STATUSES))
        .values(
            status="failed",
            error_message=message,
            completed_at=completed_at,
            scan_metadata=closed,
        )
        .execution_options(synchronize_session=False)
    ).rowcount
    session.commit()
    if not changed:
        log.info("scan_failure_not_recorded_over_terminal_state", scan_id=str(scan.id))
        return
    session.refresh(scan)
    _notify_schedule_completion(session, scan, kind="scan_failed")
    # Snapshot the percent under the row (defaults to 0 when None — protects
    # against an early-failure path where progress was never initialised).
    last_percent = scan.progress_percent or 0
    publish_progress(scan.id, step="failed", percent=last_percent)


def record_terminal_failure(scan_uuid: uuid.UUID, message: str) -> None:
    with sync_session_scope() as session:
        scan = session.get(Scan, scan_uuid)
        if scan is None:
            return
        mark_failed(session, scan, message)


def claim_success(session: Session, scan: Scan) -> bool:
    """Flip ``scan`` from ``running`` to ``succeeded``; ``False`` when it was not running.

    A conditional UPDATE, not a read-then-write, for the same reason as
    :func:`mark_failed`: a cancel commits from the API process, and a scan
    cancelled after the pipeline's last status check must stay cancelled rather
    than be reported as succeeded. Nothing is written when the row is no longer
    ``running``; the session is left clean. On success the row is refreshed.
    """
    completed_at = datetime.now(UTC)
    closed = scan_outcome.close_stage_timings(scan.scan_metadata, now=completed_at)
    changed = session.execute(
        update(Scan)
        .where(Scan.id == scan.id, Scan.status == "running")
        .values(
            status="succeeded",
            progress_percent=100,
            current_step="finalize",
            completed_at=completed_at,
            scan_metadata=closed,
        )
        .execution_options(synchronize_session=False)
    ).rowcount
    if not changed:
        session.rollback()
        log.info("scan_success_not_recorded_over_other_state", scan_id=str(scan.id))
        return False
    session.refresh(scan)
    return True


def mark_succeeded(scan_uuid: uuid.UUID) -> None:
    with sync_session_scope() as session:
        scan = session.get(Scan, scan_uuid)
        if scan is None:
            return
        if not claim_success(session, scan):
            return
        # scan-retention Layer 1: this scan is now the live snapshot for its
        # ref, so prior succeeded same-ref scans (without an explicit release
        # label) are superseded in the same transaction. No-op when the scan
        # carries no ref — those are reclaimed by the keep-last/max-age sweep.
        supersede_prior_ref_scans(
            session,
            project_id=scan.project_id,
            winner_scan_id=scan.id,
            ref=scan.ref,
        )
        # Layer 1b: if this scan names a version, it becomes the snapshot that
        # version resolves to — prior succeeded scans carrying the SAME label
        # step aside. Rescanning a shipped version is routine (failed first
        # attempt, improved scanner, corrected typo), so the label moves rather
        # than the second scan being refused. The displaced snapshot is only
        # superseded, never reclaimed: labelled scans are exempt from every
        # sweep, so it stays readable by scan id.
        release_label = (scan.scan_metadata or {}).get("release")
        supersede_prior_release_scans(
            session,
            project_id=scan.project_id,
            winner_scan_id=scan.id,
            release=release_label if isinstance(release_label, str) else None,
        )
        session.commit()
        _notify_schedule_completion(session, scan, kind="scan_completed")
    publish_progress(scan_uuid, step="succeeded", percent=100)


def set_stage(
    scan_uuid: uuid.UUID, stage: str, percent: int | None, *, timed: bool = True
) -> None:
    """Advance a scan to ``stage`` and fan out the progress frame.

    ``percent`` is the stage's progress percent, supplied explicitly by the
    caller (the source pipeline derives it from ``_STAGE_PROGRESS.get(stage)``).
    When ``percent`` is ``None`` the row keeps its existing ``progress_percent``
    — this preserves the original ``_set_stage`` fallback for an unmapped
    stage. The log line carries the raw ``percent`` value (``None`` for an
    unmapped stage, mirroring the original ``_STAGE_PROGRESS.get(stage)`` log
    value). ``timed=False`` skips the timing record for a stage that did no
    work. The publish happens AFTER the DB commit so a subscriber that reads
    the row on receipt sees the same state as the published payload.
    """
    with sync_session_scope() as session:
        scan = session.get(Scan, scan_uuid)
        if scan is None:
            return
        scan.current_step = stage
        scan.progress_percent = percent if percent is not None else scan.progress_percent
        # NF-1: one write path for every pipeline, so the timing record cannot
        # drift between source, container and ingest. ``timed=False`` announces a
        # stage that did no work (the reuse path's "prep") without timing it.
        scan.scan_metadata = scan_outcome.advance_stage_timings(
            scan.scan_metadata, stage=stage, now=datetime.now(UTC), timed=timed
        )
        session.commit()
        committed_percent = scan.progress_percent or 0
    log.info("scan_stage", stage=stage, percent=percent)
    # Publish AFTER the DB commit so a subscriber that reads the row on
    # receipt sees the same state as the published payload.
    publish_progress(scan_uuid, step=stage, percent=committed_percent)


def record_stage_outcome(scan_uuid: uuid.UUID, stage: str, reason: str = "failed") -> None:
    """Record that an optional ``stage`` finished degraded (U3-B).

    Its own transaction and best-effort: it is called from the ``except`` blocks
    that keep a scan alive, and a failure to write the note must not turn that
    survival into a failure.
    """
    try:
        with sync_session_scope() as session:
            scan = session.get(Scan, scan_uuid)
            if scan is None:
                return
            scan.scan_metadata = scan_outcome.add_stage_outcome(
                scan.scan_metadata, stage=stage, reason=reason
            )
            session.commit()
    except Exception:  # noqa: BLE001 - best-effort, never fatal
        log.warning(
            "stage_outcome_persist_failed", scan_id=str(scan_uuid), stage=stage, exc_info=True
        )


__all__ = [
    "claim_success",
    "mark_failed",
    "record_stage_outcome",
    "mark_succeeded",
    "record_terminal_failure",
    "set_stage",
]
