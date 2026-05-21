"""
Source-archive retention sweeper — Celery Beat (6 hours).

H-fix (security review, feat/zip-upload). Three layers already bound the
disk footprint of uploaded archives:

  - the single-upload size cap (``SOURCE_ARCHIVE_MAX_BYTES``),
  - the per-project storage quota (``SOURCE_ARCHIVE_PROJECT_QUOTA_BYTES``),
  - eager deletion the moment a scan extracts an archive
    (``tasks.scan_source._fetch_uploaded_archive``).

This beat is the backstop for the gaps those three cannot close:

  - an archive uploaded but whose scan was never triggered (the developer
    closed the tab between ``POST /source-archive`` and ``POST /scans``);
  - an archive whose scan was killed (SIGKILL) before the extract-delete ran;
  - archives belonging to a project that was deleted (the per-project
    ``archives/{project_id}/`` tree is orphaned — no FK cascade reaches the
    filesystem).

Deletion policy (conservative — never races a pending scan):
  A ``*.zip`` is removed when EITHER
    (1) its owning project no longer exists, OR
    (2) it is older than ``SOURCE_ARCHIVE_RETENTION_HOURS`` (default 24h) AND
        no scan in a non-terminal state (``queued`` / ``running``) references
        its ``archive_id`` in ``metadata->>'archive_id'``.
  An empty ``archives/{project_id}/`` directory left behind is also removed.

CLAUDE.md compliance:
  - Core rule #3: runs in Celery, never on the request path.
  - Core rule #11: every limit / TTL is read via ``os.getenv`` at call time.
  - §5: structlog JSON, one event per line; no archive contents logged.
  - This mirrors the existing ``dt_orphan_cleaner`` beat pattern (paged DB
    reads inside ``sync_session_scope``); the orphan-workspace cleaner lives
    on a different branch, so we follow the orphan-DT pattern here.
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.config import workspace_root
from core.db import sync_session_scope
from models import Project, Scan
from services.source_archive_service import _unlink_quietly
from tasks.celery_app import celery_app

log = structlog.get_logger("tasks.source_archive_cleaner")

# Scan states in which an archive may still be consumed — never delete one of
# these regardless of age. A "succeeded" / "failed" / "cancelled" scan has
# already had its eager delete (or never will), so age alone governs those.
_ACTIVE_SCAN_STATES: frozenset[str] = frozenset({"queued", "running"})


def _retention_seconds() -> int:
    """Age (seconds) past which an unreferenced archive is reclaimable.

    Default 24h: long enough that a developer who uploads then triggers a scan
    minutes later is never affected, short enough that an abandoned upload does
    not linger for days. Read at call time (core rule #11).
    """
    hours = float(os.getenv("SOURCE_ARCHIVE_RETENTION_HOURS", "24"))
    return int(hours * 3600)


@celery_app.task(name="trustedoss.source_archive_cleaner")  # type: ignore[misc]
def source_archive_cleaner_task() -> dict[str, Any]:
    """Sweep stale / orphaned uploaded archives off the workspace volume.

    Returns ``{"scanned": N, "deleted": M, "reclaimed_bytes": B}`` so the admin
    disk dashboard (Phase 3) can surface the last sweep. Idempotent and safe to
    re-run; a concurrent eager-delete that wins the race just means this pass
    counts one fewer file.
    """
    structlog.contextvars.bind_contextvars(task_name="source_archive_cleaner")
    archives_root = Path(workspace_root()) / "archives"
    retention_seconds = _retention_seconds()
    now = time.time()

    scanned = 0
    deleted = 0
    reclaimed_bytes = 0

    try:
        if not archives_root.is_dir():
            log.info("source_archive_cleaner_no_root", root=str(archives_root))
            return {"scanned": 0, "deleted": 0, "reclaimed_bytes": 0}

        for project_dir in _iter_project_dirs(archives_root):
            project_id = _parse_project_id(project_dir.name)
            if project_id is None:
                # A directory that is not a UUID is not one of ours — leave it.
                continue

            with sync_session_scope() as session:
                project_exists = _project_exists(session, project_id)
                active_archive_ids = (
                    set()
                    if not project_exists
                    else _active_archive_ids(session, project_id)
                )

            for zip_path in project_dir.glob("*.zip"):
                scanned += 1
                size, removed = _maybe_delete(
                    zip_path,
                    project_exists=project_exists,
                    active_archive_ids=active_archive_ids,
                    retention_seconds=retention_seconds,
                    now=now,
                )
                if removed:
                    deleted += 1
                    reclaimed_bytes += size

            _remove_dir_if_empty(project_dir)
    finally:
        structlog.contextvars.unbind_contextvars("task_name")

    log.info(
        "source_archive_cleaner_done",
        scanned=scanned,
        deleted=deleted,
        reclaimed_bytes=reclaimed_bytes,
    )
    return {"scanned": scanned, "deleted": deleted, "reclaimed_bytes": reclaimed_bytes}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iter_project_dirs(archives_root: Path) -> list[Path]:
    """Return the per-project subdirectories under ``archives/``."""
    return [child for child in archives_root.iterdir() if child.is_dir()]


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


def _active_archive_ids(session: Session, project_id: uuid.UUID) -> set[str]:
    """Return ``archive_id`` values referenced by a non-terminal scan.

    We read ``metadata->>'archive_id'`` directly so a queued upload-scan whose
    worker has not started (and so has not eagerly deleted the zip) protects its
    archive from this sweep regardless of age.
    """
    rows = session.execute(
        select(Scan.scan_metadata).where(
            Scan.project_id == project_id,
            Scan.status.in_(_ACTIVE_SCAN_STATES),
        )
    ).scalars().all()
    active: set[str] = set()
    for metadata in rows:
        if not isinstance(metadata, dict):
            continue
        archive_id = metadata.get("archive_id")
        if isinstance(archive_id, str) and archive_id:
            active.add(archive_id)
    return active


def _maybe_delete(
    zip_path: Path,
    *,
    project_exists: bool,
    active_archive_ids: set[str],
    retention_seconds: int,
    now: float,
) -> tuple[int, bool]:
    """Decide + delete one archive. Returns ``(size_bytes, deleted?)``."""
    try:
        stat = zip_path.stat()
    except OSError:  # pragma: no cover — vanished mid-walk
        return 0, False
    size = stat.st_size

    # The file stem is the archive_id; if a non-terminal scan references it we
    # never delete, no matter the age or project state.
    archive_id = zip_path.stem
    if archive_id in active_archive_ids:
        return size, False

    # Orphaned project → always reclaim. Otherwise reclaim only past the TTL.
    if not project_exists:
        reason = "project_deleted"
    elif (now - stat.st_mtime) >= retention_seconds:
        reason = "stale_unreferenced"
    else:
        return size, False

    _unlink_quietly(zip_path)
    log.info(
        "source_archive_reclaimed",
        archive_id=archive_id,
        reason=reason,
        bytes=size,
    )
    return size, True


def _remove_dir_if_empty(project_dir: Path) -> None:
    """Best-effort: drop an empty ``archives/{project_id}/`` after a sweep."""
    try:
        if not any(project_dir.iterdir()):
            project_dir.rmdir()
    except OSError:  # pragma: no cover — concurrent write / non-empty race
        pass


__all__ = ["source_archive_cleaner_task"]
