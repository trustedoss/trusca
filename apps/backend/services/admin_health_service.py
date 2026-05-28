"""
Admin system-health service — Phase 4 PR #14.

Aggregates the per-component probes the ops dashboard polls every 30s:

  - postgres        — ``SELECT 1``.
  - redis           — ``PING``.
  - celery          — ``celery_app.control.ping(timeout=2)``; worker count ≥ 1.
  - disk            — derived from :func:`services.admin_disk_service.get_disk_telemetry`.
  - active_scans    — count of ``status IN ('queued', 'running')``.
  - last_24h_errors — count of ``status='failed' AND finished_at > now()-24h``.

W6-#43a (ADR-0001): the DT breaker probe was removed when DT was replaced
by Trivy; the Trivy DB health panel is a separate W6-#43e follow-up.

Each probe is independent: a failed probe contributes a single ``down`` /
``degraded`` component without raising, so a partial outage produces a
useful response instead of a 500.

CLAUDE.md core rule #11: every accessor reads env at call time. We do not
cache the redis URL / celery app reference at module level.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

import redis as _redis
import structlog
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import redis_url
from models import Scan
from schemas.admin_ops import (
    HealthComponent,
    HealthStatus,
    SystemHealthOut,
)
from services.admin_disk_service import _strip_credentials, get_disk_telemetry

log = structlog.get_logger("admin.health.service")


def _now() -> datetime:
    return datetime.now(tz=UTC)


# ---------------------------------------------------------------------------
# Per-component probes
# ---------------------------------------------------------------------------


async def _probe_postgres(session: AsyncSession) -> HealthComponent:
    try:
        await session.execute(text("SELECT 1"))
        return HealthComponent(name="postgres", status="ok", detail=None, value=None)
    except Exception as exc:  # noqa: BLE001
        log.warning("admin.health.postgres_failed", error=str(exc))
        return HealthComponent(
            name="postgres",
            status="down",
            detail=_strip_credentials(f"{type(exc).__name__}: {exc}"),
            value=None,
        )


def _probe_redis() -> HealthComponent:
    try:
        client = _redis.Redis.from_url(redis_url(), decode_responses=True)
        ok = bool(client.ping())
        client.close()  # type: ignore[no-untyped-call]
    except Exception as exc:  # noqa: BLE001
        log.warning("admin.health.redis_failed", error=str(exc))
        return HealthComponent(
            name="redis",
            status="down",
            detail=_strip_credentials(f"{type(exc).__name__}: {exc}"),
            value=None,
        )
    return HealthComponent(
        name="redis",
        status="ok" if ok else "down",
        detail=None if ok else "PING returned False",
        value=None,
    )


def _probe_celery(*, celery_app_override: Any | None = None) -> HealthComponent:
    """
    Send ``control.ping`` with a 2-second budget and count alive workers.

    The Celery control client returns a list of ``{worker_name: {"ok": "pong"}}``
    dicts. We treat ``len(replies) >= 1`` as ok, 0 as down. The injection
    point is for unit tests — pass an object with the same ``.control.ping``
    surface.
    """
    if celery_app_override is None:
        from tasks.celery_app import celery_app

        celery_app_override = celery_app
    try:
        replies = celery_app_override.control.ping(timeout=2.0) or []
    except Exception as exc:  # noqa: BLE001
        log.warning("admin.health.celery_failed", error=str(exc))
        return HealthComponent(
            name="celery",
            status="down",
            detail=_strip_credentials(f"{type(exc).__name__}: {exc}"),
            value=None,
        )
    count = len(replies)
    if count >= 1:
        return HealthComponent(
            name="celery",
            status="ok",
            detail=f"{count} worker(s) responded",
            value=count,
        )
    return HealthComponent(
        name="celery",
        status="down",
        detail="no workers responded",
        value=0,
    )


async def _probe_disk(session: AsyncSession) -> HealthComponent:
    """Derive a one-line disk summary from the per-mount telemetry."""
    try:
        telemetry = await get_disk_telemetry(session)
    except Exception as exc:  # noqa: BLE001
        log.warning("admin.health.disk_failed", error=str(exc))
        return HealthComponent(
            name="disk",
            status="down",
            detail=_strip_credentials(f"{type(exc).__name__}: {exc}"),
            value=None,
        )

    # Worst-case status across all items wins. ``down`` > ``degraded`` > ``ok``.
    rank = {"ok": 0, "degraded": 1, "down": 2}
    worst: HealthStatus = "ok"
    worst_name = ""
    worst_pct: float | None = None
    for item in telemetry.items:
        if rank[item.status] > rank[worst]:
            worst = item.status
            worst_name = item.name
            worst_pct = item.used_pct

    detail: str | None = None
    if worst != "ok":
        detail = f"{worst_name}: {worst_pct}% used" if worst_pct is not None else worst_name
    return HealthComponent(name="disk", status=worst, detail=detail, value=worst_pct)


def _active_scans_warn() -> int:
    """Queued+running count at/above which the queue is flagged ``degraded``."""
    return int(os.getenv("HEALTH_ACTIVE_SCANS_WARN", "50"))


def _active_scans_crit() -> int:
    """Queued+running count at/above which the queue is flagged ``down`` —
    a backlog this large almost always means stuck / zombie scans rather than
    legitimate load (worker concurrency is small)."""
    return int(os.getenv("HEALTH_ACTIVE_SCANS_CRIT", "200"))


async def _probe_active_scans(session: AsyncSession) -> HealthComponent:
    stmt = select(func.count()).select_from(Scan).where(Scan.status.in_(("queued", "running")))
    try:
        count = int((await session.execute(stmt)).scalar_one())
    except Exception as exc:  # noqa: BLE001
        log.warning("admin.health.active_scans_failed", error=str(exc))
        return HealthComponent(
            name="active_scans",
            status="down",
            detail=_strip_credentials(f"{type(exc).__name__}: {exc}"),
            value=None,
        )

    # A perpetually-large in-flight queue is a real operational signal (stuck
    # dispatch, dead workers, zombie scans), not "healthy". Classify against
    # env-tunable thresholds instead of always reporting ok.
    warn = _active_scans_warn()
    crit = _active_scans_crit()
    if count >= crit:
        status: HealthStatus = "down"
    elif count >= warn:
        status = "degraded"
    else:
        status = "ok"

    return HealthComponent(
        name="active_scans",
        status=status,
        detail=f"{count} scan(s) queued or running",
        value=count,
    )


async def _probe_last_24h_errors(session: AsyncSession) -> HealthComponent:
    cutoff = _now() - timedelta(hours=24)
    stmt = (
        select(func.count())
        .select_from(Scan)
        .where(Scan.status == "failed", Scan.completed_at > cutoff)
    )
    try:
        count = int((await session.execute(stmt)).scalar_one())
    except Exception as exc:  # noqa: BLE001
        log.warning("admin.health.last_24h_errors_failed", error=str(exc))
        return HealthComponent(
            name="last_24h_errors",
            status="down",
            detail=_strip_credentials(f"{type(exc).__name__}: {exc}"),
            value=None,
        )

    # No fixed threshold — the operator decides what "too many" means. We
    # report ok for any count and surface the number; the UI colours the
    # card based on local heuristics if it wants to.
    return HealthComponent(
        name="last_24h_errors",
        status="ok",
        detail=f"{count} failed scan(s) in the last 24h",
        value=count,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def get_system_health(session: AsyncSession) -> SystemHealthOut:
    """Run every probe in sequence and return the aggregated payload.

    Sequence rather than ``asyncio.gather`` because each probe touches a
    single resource and the real bottleneck is the Redis / Celery network
    hop — fanning out via gather adds complexity without measurable
    speedup at the per-poll cadence the dashboard uses.
    """
    components = [
        await _probe_postgres(session),
        _probe_redis(),
        _probe_celery(),
        await _probe_disk(session),
        await _probe_active_scans(session),
        await _probe_last_24h_errors(session),
    ]
    return SystemHealthOut(components=components, updated_at=_now())


__all__ = [
    "get_system_health",
]
