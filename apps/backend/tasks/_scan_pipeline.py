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
from sqlalchemy.orm import Session

from core.db import sync_session_scope
from models import Scan
from tasks._progress import publish_progress
from tasks.scan_retention import supersede_prior_ref_scans

log = structlog.get_logger("tasks.scan_pipeline")


def mark_failed(session: Session, scan: Scan, message: str) -> None:
    scan.status = "failed"
    scan.error_message = message
    scan.completed_at = datetime.now(UTC)
    session.commit()
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


def mark_succeeded(scan_uuid: uuid.UUID) -> None:
    with sync_session_scope() as session:
        scan = session.get(Scan, scan_uuid)
        if scan is None:
            return
        scan.status = "succeeded"
        scan.progress_percent = 100
        scan.current_step = "finalize"
        scan.completed_at = datetime.now(UTC)
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
        session.commit()
    publish_progress(scan_uuid, step="succeeded", percent=100)


def set_stage(scan_uuid: uuid.UUID, stage: str, percent: int | None) -> None:
    """Advance a scan to ``stage`` and fan out the progress frame.

    ``percent`` is the stage's progress percent, supplied explicitly by the
    caller (the source pipeline derives it from ``_STAGE_PROGRESS.get(stage)``).
    When ``percent`` is ``None`` the row keeps its existing ``progress_percent``
    — this preserves the original ``_set_stage`` fallback for an unmapped
    stage. The log line carries the raw ``percent`` value (``None`` for an
    unmapped stage, mirroring the original ``_STAGE_PROGRESS.get(stage)`` log
    value). The publish happens AFTER the DB commit so a subscriber that reads
    the row on receipt sees the same state as the published payload.
    """
    with sync_session_scope() as session:
        scan = session.get(Scan, scan_uuid)
        if scan is None:
            return
        scan.current_step = stage
        scan.progress_percent = percent if percent is not None else scan.progress_percent
        session.commit()
        committed_percent = scan.progress_percent or 0
    log.info("scan_stage", stage=stage, percent=percent)
    # Publish AFTER the DB commit so a subscriber that reads the row on
    # receipt sees the same state as the published payload.
    publish_progress(scan_uuid, step=stage, percent=committed_percent)


__all__ = [
    "mark_failed",
    "mark_succeeded",
    "record_terminal_failure",
    "set_stage",
]
