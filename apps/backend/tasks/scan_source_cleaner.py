"""
Preserved-scan-source retention sweeper — Celery Beat (6 hours). G3.1.

After a successful source scan, ``services.source_preservation_service`` writes a
gzip tarball of the scanned tree (+ the scancode result JSON) under
``{workspace_root()}/scan-sources/{project_id}/{scan_id}.tar.gz`` so a later UI
can render a file tree + per-line license matches.

Retention is **latest-succeeded-per-project**. The preservation stage overwrites
the tarball atomically on re-run, but it does NOT delete a *previous* scan's
tarball — a new scan id produces a new file name. Without a sweep, every
succeeded scan would leave its own tarball forever, an unbounded disk leak. This
beat is the reclaimer.

Deletion policy (conservative — never races a non-terminal scan):
  A ``*.tar.gz`` is removed when EITHER
    (1) its owning project no longer exists, OR
    (2) it is NOT the project's ``latest_scan_id`` tarball AND no scan in a
        non-terminal state (``queued`` / ``running``) carries its ``scan_id``.
  An empty ``scan-sources/{project_id}/`` directory left behind is removed too.

Edge case — ``latest_scan_id`` points at a scan with no tarball: ``latest_scan_id``
tracks the most recent scan *regardless of status* (models/scan.py), but a
tarball is only written for *succeeded* scans. If the latest scan failed / is
running, ``latest_scan_id`` matches no file on disk — naively that would sweep the
previously-preserved succeeded tarball and leave the project with nothing. To
avoid that data loss we KEEP the newest-mtime tarball as a fallback whenever the
``latest_scan_id`` tarball is absent. The result is still "at most one retained
tarball per project" while never deleting the last good one.

CLAUDE.md compliance:
  - Core rule #3: runs in Celery, never on the request path.
  - Core rule #11: every limit / setting is read via ``os.getenv`` at call time.
  - §5: structlog JSON, one event per line; no tarball contents logged.
  - Mirrors ``tasks.source_archive_cleaner`` 1:1 (paged DB reads inside
    ``sync_session_scope``, filesystem-decision helpers isolated for unit tests).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.config import workspace_root
from core.db import sync_session_scope
from models import Project, Scan
from services.source_preservation_service import (
    _unlink_quietly,
    scan_sources_dir_for_project,
)
from tasks.celery_app import celery_app

log = structlog.get_logger("tasks.scan_source_cleaner")

# Scan states in which a tarball may still be in flux — never delete one whose
# scan_id is referenced by a scan in one of these states regardless of retention.
_ACTIVE_SCAN_STATES: frozenset[str] = frozenset({"queued", "running"})


@celery_app.task(name="trustedoss.scan_source_cleaner")  # type: ignore[misc]
def scan_source_cleaner_task() -> dict[str, Any]:
    """Sweep superseded / orphaned preserved-source tarballs off the volume.

    Returns ``{"scanned": N, "deleted": M, "reclaimed_bytes": B}`` so the admin
    disk dashboard can surface the last sweep. Idempotent and safe to re-run.
    """
    structlog.contextvars.bind_contextvars(task_name="scan_source_cleaner")
    sources_root = Path(workspace_root()) / "scan-sources"

    scanned = 0
    deleted = 0
    reclaimed_bytes = 0

    try:
        if not sources_root.is_dir():
            log.info("scan_source_cleaner_no_root", root=str(sources_root))
            return {"scanned": 0, "deleted": 0, "reclaimed_bytes": 0}

        for project_dir in _iter_project_dirs(sources_root):
            project_id = _parse_project_id(project_dir.name)
            if project_id is None:
                # A directory that is not a UUID is not one of ours — leave it.
                continue

            with sync_session_scope() as session:
                project_exists = _project_exists(session, project_id)
                latest_scan_id = (
                    None if not project_exists else _latest_scan_id(session, project_id)
                )
                active_scan_ids = (
                    set()
                    if not project_exists
                    else _active_scan_ids(session, project_id)
                )

            keep = _ids_to_keep(
                project_dir,
                project_exists=project_exists,
                latest_scan_id=latest_scan_id,
                active_scan_ids=active_scan_ids,
            )

            for tar_path in project_dir.glob("*.tar.gz"):
                scanned += 1
                size, removed = _maybe_delete(tar_path, keep=keep)
                if removed:
                    deleted += 1
                    reclaimed_bytes += size

            _remove_dir_if_empty(project_dir)
    finally:
        structlog.contextvars.unbind_contextvars("task_name")

    log.info(
        "scan_source_cleaner_done",
        scanned=scanned,
        deleted=deleted,
        reclaimed_bytes=reclaimed_bytes,
    )
    return {"scanned": scanned, "deleted": deleted, "reclaimed_bytes": reclaimed_bytes}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iter_project_dirs(sources_root: Path) -> list[Path]:
    """Return the per-project subdirectories under ``scan-sources/``."""
    return [child for child in sources_root.iterdir() if child.is_dir()]


def _parse_project_id(name: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(name)
    except (ValueError, TypeError):
        return None


def _project_exists(session: Session, project_id: uuid.UUID) -> bool:
    return (
        session.execute(select(Project.id).where(Project.id == project_id)).first()
        is not None
    )


def _latest_scan_id(session: Session, project_id: uuid.UUID) -> str | None:
    """Return the project's denormalized ``latest_scan_id`` as a string, if set."""
    row = session.execute(
        select(Project.latest_scan_id).where(Project.id == project_id)
    ).scalar_one_or_none()
    return str(row) if row is not None else None


def _active_scan_ids(session: Session, project_id: uuid.UUID) -> set[str]:
    """Return ``scan.id`` values for the project's non-terminal scans.

    A tarball whose stem is a queued/running scan id is protected from the sweep
    — its preservation stage may not have run yet, or a re-run is in flight.
    """
    rows = session.execute(
        select(Scan.id).where(
            Scan.project_id == project_id,
            Scan.status.in_(_ACTIVE_SCAN_STATES),
        )
    ).scalars().all()
    return {str(scan_id) for scan_id in rows}


def _ids_to_keep(
    project_dir: Path,
    *,
    project_exists: bool,
    latest_scan_id: str | None,
    active_scan_ids: set[str],
) -> set[str]:
    """Compute the set of scan-id stems whose tarball must be retained.

    An orphaned project keeps nothing (every tarball is reclaimed). Otherwise we
    keep ``latest_scan_id`` plus every non-terminal scan id. When the
    ``latest_scan_id`` tarball is absent on disk (latest scan failed / running /
    never preserved) we keep the newest-mtime tarball as a fallback so the last
    good preserved source is never the one we delete.
    """
    if not project_exists:
        return set()

    keep: set[str] = set(active_scan_ids)

    on_disk = {p.name[: -len(".tar.gz")] for p in project_dir.glob("*.tar.gz")}

    if latest_scan_id is not None and latest_scan_id in on_disk:
        keep.add(latest_scan_id)
    else:
        newest = _newest_tarball_stem(project_dir)
        if newest is not None:
            keep.add(newest)

    return keep


def _newest_tarball_stem(project_dir: Path) -> str | None:
    """Return the stem of the most-recently-modified ``*.tar.gz``, or None."""
    newest_stem: str | None = None
    newest_mtime = -1.0
    for tar_path in project_dir.glob("*.tar.gz"):
        try:
            mtime = tar_path.stat().st_mtime
        except OSError:  # pragma: no cover — vanished mid-walk
            continue
        if mtime > newest_mtime:
            newest_mtime = mtime
            newest_stem = tar_path.name[: -len(".tar.gz")]
    return newest_stem


def _maybe_delete(tar_path: Path, *, keep: set[str]) -> tuple[int, bool]:
    """Decide + delete one tarball. Returns ``(size_bytes, deleted?)``."""
    try:
        stat = tar_path.stat()
    except OSError:  # pragma: no cover — vanished mid-walk
        return 0, False
    size = stat.st_size

    stem = tar_path.name[: -len(".tar.gz")]
    # Defence in depth: a file whose stem is not a UUID is not one of ours.
    if _parse_project_id(stem) is None:
        return size, False
    if stem in keep:
        return size, False

    _unlink_quietly(tar_path)
    log.info("scan_source_reclaimed", scan_id=stem, bytes=size)
    return size, True


def _remove_dir_if_empty(project_dir: Path) -> None:
    """Best-effort: drop an empty ``scan-sources/{project_id}/`` after a sweep."""
    try:
        if not any(project_dir.iterdir()):
            project_dir.rmdir()
    except OSError:  # pragma: no cover — concurrent write / non-empty race
        pass


__all__ = ["scan_source_cleaner_task", "scan_sources_dir_for_project"]
