"""
Workspace orphan cleaner — Celery Beat (PR-A1 scan stability).

Each scan task creates ``${WORKSPACE_HOST_PATH}/<scan_id>/`` and removes it in
its own ``finally`` block. That ``finally`` is reliable for the in-process
exit paths (success, ordinary failure, SoftTimeLimitExceeded) but NOT for:

  - **Cancellation** — admin / user cancel ``revoke(terminate=True,
    signal="SIGTERM")``. SIGTERM can land while the worker is inside a
    subprocess (cdxgen / scancode / Trivy) and the ``finally`` may not run to
    completion before the process dies.
  - **Hard time limit (SIGKILL)** — when a task ignores the soft limit, Celery
    sends SIGKILL at the hard limit. SIGKILL is uncatchable; no ``finally``
    runs at all.
  - **Worker crash / OOM kill / host reboot** — the worker process vanishes
    mid-scan, leaving the tree behind.

Without a reaper these orphaned trees accumulate and eventually trip the disk
hard limit (``DISK_HARD_LIMIT_PCT``), which blocks *all* new scans. This task
is the safety net that reclaims them.

Reclaim policy (conservative — never delete an in-flight scan's workspace):
  A workspace directory ``<root>/<scan_id>/`` is eligible for deletion only
  when its scan state + directory age clear the relevant grace bar:

    1. **Terminal scan row exists** (succeeded / failed / cancelled) AND the
       directory mtime is older than ``WORKSPACE_ORPHAN_MAX_AGE_SECONDS``. This
       avoids racing the worker's own ``finally: rmtree`` immediately after the
       row flips terminal.
    2. **No scan row at all** (the scan + its project were deleted, OR the row
       has not been committed / was briefly unreadable) AND the directory mtime
       is older than ``2 * WORKSPACE_ORPHAN_MAX_AGE_SECONDS`` (Low #4). The
       "no row" branch is the riskier one: a young directory with no row may be
       a scan whose INSERT is still in flight, or a transient DB read miss. We
       therefore demand a *longer* grace before reclaiming an unattributable
       tree, so we never delete the workspace of a scan that is about to start.
    3. A queued / running scan's workspace is NEVER touched, at any age.

  Directory names that are not valid UUIDs are skipped entirely — the
  workspace root may legitimately contain non-scan subdirectories (e.g. a
  backup-restore staging dir), and we refuse to delete anything we cannot
  positively attribute to a scan.

CLAUDE.md rule #11: ``workspace_root()`` and the grace period are read at call
time, never cached at import.
"""

from __future__ import annotations

import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.config import workspace_orphan_max_age_seconds, workspace_root
from core.db import sync_session_scope
from models import Scan
from tasks.celery_app import celery_app

log = structlog.get_logger("tasks.workspace_cleaner")

# Mirrors services.admin_scan_service._TERMINAL_STATUSES. A workspace whose
# scan is in one of these states is finished — its tree is safe to reclaim.
_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})


@dataclass(frozen=True)
class _AgedDir:
    """An aged candidate workspace dir + its age in seconds at scan time."""

    path: Path
    age: float


@celery_app.task(name="trustedoss.workspace_cleaner")  # type: ignore[misc]
def workspace_cleaner_task() -> dict[str, Any]:
    """
    Reclaim per-scan workspace directories left behind by terminal scans.

    Returns ``{"scanned": N, "reclaimed": [<scan_id>, ...], "skipped": M}``
    so the admin disk dashboard can surface the last reclaim pass.
    """
    structlog.contextvars.bind_contextvars(task_name="workspace_cleaner")
    root = Path(workspace_root())
    max_age = workspace_orphan_max_age_seconds()
    now = time.time()

    # Low #4: the "no owning row" branch needs a longer grace than the
    # terminal-row branch, so a scan whose INSERT is still in flight (or a
    # transient DB read miss) cannot have its about-to-be-used workspace
    # reclaimed. Read at call time off the same env-driven base (rule #11).
    orphan_max_age = max_age * 2

    reclaimed: list[str] = []
    scanned = 0
    skipped = 0

    try:
        if not root.exists():
            log.info("workspace_cleaner_root_missing", root=str(root))
            return {"scanned": 0, "reclaimed": [], "skipped": 0}

        candidates = _collect_candidates(root, now=now, max_age=max_age)
        scanned = candidates["scanned"]
        skipped = candidates["skipped_young"] + candidates["skipped_nonuuid"]
        aged: dict[uuid.UUID, _AgedDir] = candidates["aged"]

        if not aged:
            log.info("workspace_cleaner_nothing_aged", scanned=scanned, skipped=skipped)
            return {"scanned": scanned, "reclaimed": [], "skipped": skipped}

        # One DB round-trip: classify each aged scan id as active (queued /
        # running), terminal, or absent (no row). ``active`` must never be
        # reclaimed; ``terminal`` reclaims at ``max_age``; ``absent`` reclaims
        # only past the longer ``orphan_max_age`` (Low #4).
        with sync_session_scope() as session:
            active, present = _scan_id_states(session, aged.keys())

        for scan_uuid, aged_dir in aged.items():
            path = aged_dir.path
            if scan_uuid in active:
                # Queued / running — leave it alone.
                skipped += 1
                continue
            if scan_uuid not in present:
                # No owning row. Demand the longer grace before reclaiming an
                # unattributable tree (Low #4): a young no-row dir may be a
                # scan whose row has not committed yet.
                if aged_dir.age < orphan_max_age:
                    skipped += 1
                    log.info(
                        "workspace_cleaner_orphan_young",
                        scan_id=str(scan_uuid),
                        age_seconds=int(aged_dir.age),
                        orphan_max_age_seconds=orphan_max_age,
                    )
                    continue
            # Terminal row (aged past max_age) OR no row aged past orphan grace.
            _reclaim(path)
            reclaimed.append(str(scan_uuid))
            log.warning(
                "workspace_reclaimed",
                scan_id=str(scan_uuid),
                path=str(path),
                had_row=scan_uuid in present,
            )
    finally:
        structlog.contextvars.unbind_contextvars("task_name")

    if reclaimed:
        log.warning(
            "workspace_cleaner_complete",
            scanned=scanned,
            reclaimed_count=len(reclaimed),
            skipped=skipped,
        )
    else:
        log.info(
            "workspace_cleaner_clean",
            scanned=scanned,
            skipped=skipped,
        )
    return {"scanned": scanned, "reclaimed": reclaimed, "skipped": skipped}


def _collect_candidates(root: Path, *, now: float, max_age: int) -> dict[str, Any]:
    """Walk ``root`` once and bucket its immediate children.

    Returns a dict with:
      - ``scanned``         : total immediate subdirectories inspected
      - ``aged``            : {scan_uuid: _AgedDir} for UUID-named dirs old
                              enough (carries each dir's age so the reclaim loop
                              can apply the longer no-row grace, Low #4)
      - ``skipped_young``   : count of UUID-named dirs inside the grace window
      - ``skipped_nonuuid`` : count of non-UUID-named entries
    """
    aged: dict[uuid.UUID, _AgedDir] = {}
    scanned = 0
    skipped_young = 0
    skipped_nonuuid = 0

    for child in root.iterdir():
        if not child.is_dir():
            # Stray files at the workspace root are not our concern.
            continue
        scanned += 1
        try:
            scan_uuid = uuid.UUID(child.name)
        except ValueError:
            # Non-UUID directory — not a per-scan workspace. Never delete.
            skipped_nonuuid += 1
            continue

        try:
            age = now - child.stat().st_mtime
        except OSError as exc:
            # Cannot stat (permissions / disappeared mid-walk) — skip safely.
            log.warning("workspace_cleaner_stat_failed", path=str(child), error=str(exc))
            skipped_nonuuid += 1
            continue

        if age < max_age:
            skipped_young += 1
            continue
        aged[scan_uuid] = _AgedDir(path=child, age=age)

    return {
        "scanned": scanned,
        "aged": aged,
        "skipped_young": skipped_young,
        "skipped_nonuuid": skipped_nonuuid,
    }


def _scan_id_states(
    session: Session, scan_uuids: Any
) -> tuple[set[uuid.UUID], set[uuid.UUID]]:
    """Classify ``scan_uuids`` by their scan-row state in one query.

    Returns ``(active, present)`` where:
      - ``active``  : ids whose row exists and is still non-terminal
                      (queued / running) — NEVER reclaimable.
      - ``present`` : ids that have a row at all (terminal OR active). Any id
                      NOT in ``present`` has no owning row and is subject to the
                      longer no-row grace before reclaim (Low #4).

    Reclaim classification at the call site:
      - in ``active``                       → skip (in flight).
      - in ``present`` but not ``active``    → terminal, reclaim at ``max_age``.
      - not in ``present``                   → no row, reclaim at ``2*max_age``.
    """
    ids = list(scan_uuids)
    if not ids:
        return set(), set()
    rows = session.execute(
        select(Scan.id, Scan.status).where(Scan.id.in_(ids))
    ).all()
    active = {row[0] for row in rows if row[1] not in _TERMINAL_STATUSES}
    present = {row[0] for row in rows}
    return active, present


def _reclaim(path: Path) -> None:
    """Best-effort recursive delete. ``ignore_errors`` matches the worker's own
    cleanup contract (user policy forbids ``rm`` shell calls)."""
    shutil.rmtree(path, ignore_errors=True)


__all__ = ["workspace_cleaner_task"]
