# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Toolchain cache cleaner - Celery Beat.

A scan resolves its dependency graph for real: the prep step runs ``bundle
lock`` / ``go mod tidy`` / ``dotnet restore`` / ``npm install
--package-lock-only``, and cdxgen then shells out to mvn, gradle, npm and go
again. Every artifact those fetch is cached under the worker's ``HOME``,
which is what makes the *next* scan fast.

Nothing bounded that. The caches grow with the number of distinct
dependencies the corpus has ever pulled, which has no ceiling short of the
ecosystems themselves, and no code in this repository ever deleted a byte of
it. Measured on a real corpus: 4.3 GB across two workers in 48 hours and
still climbing, on a 26 GB root partition shared with Postgres. The scan disk
guard did not save it either, because the workspace had been moved to network
storage and the guard was reading that (fixed separately, see
``core.config.disk_guard_extra_paths``).

Reclaim policy (conservative - never pull a cache out from under a scan):

  1. Below ``TOOLCHAIN_CACHE_MAX_BYTES`` in total, nothing is touched. The
     cache earning its keep is the normal state.
  2. Over the cap, whole cache roots are removed, largest first, until the
     total is back under. Whole roots rather than individual artifacts:
     every tool here treats a missing cache as a cache miss and re-downloads,
     but several (npm's content-addressed ``_cacache``, Gradle's lock-guarded
     store) have internal indexes that partial deletion corrupts into
     failures far away from the cause.
  3. A cache whose newest file changed within ``TOOLCHAIN_CACHE_IDLE_SECONDS``
     is never removed, at any size. Dependency resolution writes continuously
     while it runs, so a recently-written cache may belong to a scan that is
     mid-resolve, and pulling it would fail that scan rather than slow it.
  4. If every over-cap cache is busy, the pass reclaims nothing and says so.
     Refusing to break a running scan is the correct outcome; the disk guard
     is what stops the volume actually filling.

The cost of a reclaim is one slow scan afterwards, re-downloading what it
needs. That is the trade this task exists to make.

CLAUDE.md rule #11: the roots, the cap and the grace period are read at call
time, never cached at import.
"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from core.config import (
    toolchain_cache_idle_seconds,
    toolchain_cache_max_bytes,
    toolchain_cache_roots,
)
from tasks.celery_app import celery_app

log = structlog.get_logger("tasks.toolchain_cache_cleaner")


@dataclass(frozen=True)
class _CacheRoot:
    """One cache directory: where it is, how big, and how recently written."""

    path: Path
    size_bytes: int
    #: Seconds since the newest file under ``path`` was written. ``inf`` for
    #: an empty tree, which can never be "in use".
    idle_seconds: float


def _measure(path: Path, *, now: float) -> _CacheRoot | None:
    """Total size and idle time of ``path``, in one walk. ``None`` if absent.

    One walk yields both numbers because walking a Maven repository is not
    cheap - it is six figures of small files - and the alternative is doing
    it twice.

    Files that vanish mid-walk are skipped rather than raised on: a scan
    resolving dependencies is writing into this tree while we read it, and a
    cleaner that fell over on a race would stop reclaiming anything.
    """
    if not path.is_dir():
        return None

    total = 0
    newest = 0.0
    for dirpath, _dirnames, filenames in os.walk(path, onerror=None):
        for name in filenames:
            try:
                stat = os.lstat(os.path.join(dirpath, name))
            except OSError:
                continue
            total += stat.st_size
            if stat.st_mtime > newest:
                newest = stat.st_mtime

    idle = float("inf") if newest == 0.0 else max(0.0, now - newest)
    return _CacheRoot(path=path, size_bytes=total, idle_seconds=idle)


@celery_app.task(name="trustedoss.toolchain_cache_cleaner")  # type: ignore[misc]
def toolchain_cache_cleaner_task() -> dict[str, Any]:
    """
    Hold the toolchain caches under their size cap.

    Returns ``{"total_bytes": N, "cap_bytes": M, "reclaimed": [...],
    "skipped_busy": [...], "reclaimed_bytes": K}`` so an operator can see both
    what went and what was spared for being in use.
    """
    structlog.contextvars.bind_contextvars(task_name="toolchain_cache_cleaner")
    try:
        cap = toolchain_cache_max_bytes()
        if cap <= 0:
            log.info("toolchain_cache_cleaner_disabled")
            return {
                "total_bytes": 0,
                "cap_bytes": 0,
                "reclaimed": [],
                "skipped_busy": [],
                "reclaimed_bytes": 0,
            }

        now = time.time()
        idle_floor = toolchain_cache_idle_seconds()
        roots = [
            measured
            for raw in toolchain_cache_roots()
            if (measured := _measure(Path(raw), now=now)) is not None
        ]
        total = sum(root.size_bytes for root in roots)

        if total <= cap:
            log.info(
                "toolchain_cache_under_cap",
                total_bytes=total,
                cap_bytes=cap,
                roots=len(roots),
            )
            return {
                "total_bytes": total,
                "cap_bytes": cap,
                "reclaimed": [],
                "skipped_busy": [],
                "reclaimed_bytes": 0,
            }

        reclaimed: list[str] = []
        skipped_busy: list[str] = []
        reclaimed_bytes = 0
        remaining = total

        # Largest first: the fewest deletions that get back under the cap is
        # the fewest scans made to re-download.
        for root in sorted(roots, key=lambda r: r.size_bytes, reverse=True):
            if remaining <= cap:
                break
            if root.idle_seconds < idle_floor:
                skipped_busy.append(str(root.path))
                log.info(
                    "toolchain_cache_busy",
                    path=str(root.path),
                    size_bytes=root.size_bytes,
                    idle_seconds=int(root.idle_seconds),
                    idle_floor_seconds=idle_floor,
                )
                continue
            shutil.rmtree(root.path, ignore_errors=True)
            reclaimed.append(str(root.path))
            reclaimed_bytes += root.size_bytes
            remaining -= root.size_bytes
            log.warning(
                "toolchain_cache_reclaimed",
                path=str(root.path),
                size_bytes=root.size_bytes,
                idle_seconds=int(root.idle_seconds),
            )

        if remaining > cap:
            # Everything still over the cap is in use. Saying so is the point:
            # the operator needs to know the cap is not being enforced right
            # now, and why, before the disk guard starts refusing scans.
            log.error(
                "toolchain_cache_still_over_cap",
                total_bytes=remaining,
                cap_bytes=cap,
                skipped_busy=len(skipped_busy),
            )
        else:
            log.warning(
                "toolchain_cache_cleaner_complete",
                reclaimed_count=len(reclaimed),
                reclaimed_bytes=reclaimed_bytes,
                total_bytes=remaining,
                cap_bytes=cap,
            )

        return {
            "total_bytes": remaining,
            "cap_bytes": cap,
            "reclaimed": reclaimed,
            "skipped_busy": skipped_busy,
            "reclaimed_bytes": reclaimed_bytes,
        }
    finally:
        structlog.contextvars.unbind_contextvars("task_name")


__all__ = ["toolchain_cache_cleaner_task"]
