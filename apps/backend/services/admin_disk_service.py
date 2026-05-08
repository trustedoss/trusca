"""
Admin disk-telemetry service — Phase 4 PR #14.

Returns the storage footprint of the four backends the operator cares about:

  - ``workspace``  — host filesystem mount under ``WORKSPACE_HOST_PATH``
                     (cdxgen / ORT scratch dirs).
  - ``dt_volume``  — DT's persistent volume host mount under
                     ``DT_VOLUME_HOST_PATH``.
  - ``postgres``   — total PostgreSQL DB size via ``pg_database_size``.
  - ``redis``      — Redis memory usage via ``INFO memory``.

Threshold model:
  - Each item carries a static 80% warn / 90% critical threshold pair.
  - The ``status`` field is computed per item: ok / degraded (≥80%) /
    down (≥90%). For DB-backed entries (Postgres bytes used, Redis bytes
    used) we have no canonical "total" — those entries get ``used_pct =
    None`` and ``status = "ok"`` unconditionally; the UI renders absolute
    bytes only.

Path traversal is not a threat because every path is read from environment
variables that only super-admin operators can set. We DO catch every OS
error and convert it to a typed exception so a missing mount cannot crash
the endpoint.

CLAUDE.md core rule #11: every accessor reads the env at call time — no
module-level caching.
"""

from __future__ import annotations

import os
import re
import shutil
from datetime import UTC, datetime
from typing import Any

import redis as _redis
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import redis_url
from schemas.admin_ops import (
    AdminDiskItem,
    AdminDiskOut,
    HealthStatus,
)

log = structlog.get_logger("admin.disk.service")

# G4: strip credentials from exception messages before surfacing them in
# API responses. asyncpg / redis-py / httpx errors can embed the full
# connection string (postgresql://user:pass@host/db, redis://:pass@host)
# in the exception message.
_CREDENTIAL_PATTERN = re.compile(
    r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+\-.]*://)"
    r"(?:[^:@/\s]+:[^@/\s]+@)",
    re.IGNORECASE,
)


def _strip_credentials(text_: str) -> str:
    """Replace user:password@ in connection-string fragments with ****@."""
    return _CREDENTIAL_PATTERN.sub(r"\g<scheme>****@", text_)


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class AdminDiskError(Exception):
    """Base class for admin disk telemetry errors mapped to RFC 7807."""

    status_code: int = 500
    title: str = "Admin Disk Error"
    type_uri: str = "about:blank"
    extensions: dict[str, object] = {}


# ---------------------------------------------------------------------------
# Configuration accessors (read env at call time per CLAUDE.md core rule #11)
# ---------------------------------------------------------------------------


def _workspace_path() -> str:
    """Container-side workspace mount; ``WORKSPACE_HOST_PATH`` is the same path."""
    return os.getenv("WORKSPACE_HOST_PATH", "/opt/trustedoss/workspace")


def _dt_volume_path() -> str:
    """DT volume mount path inside the backend container.

    Defaults to ``/var/lib/dependency-track`` which matches the docker-compose
    DT volume host path. Operators override via ``DT_VOLUME_HOST_PATH``.
    """
    return os.getenv("DT_VOLUME_HOST_PATH", "/var/lib/dependency-track")


def _threshold_warning() -> float:
    return float(os.getenv("DISK_THRESHOLD_WARNING_PCT", "80.0"))


def _threshold_critical() -> float:
    return float(os.getenv("DISK_THRESHOLD_CRITICAL_PCT", "90.0"))


def _now() -> datetime:
    return datetime.now(tz=UTC)


# ---------------------------------------------------------------------------
# Per-backend probes
# ---------------------------------------------------------------------------


def _classify(used_pct: float | None, *, warn: float, crit: float) -> HealthStatus:
    """Map a percent-used reading to the ok / degraded / down trichotomy."""
    if used_pct is None:
        return "ok"
    if used_pct >= crit:
        return "down"
    if used_pct >= warn:
        return "degraded"
    return "ok"


def _probe_filesystem(*, name: str, path: str) -> AdminDiskItem:
    """
    Read filesystem capacity for a host path.

    On any OS error (path missing, permission denied, NFS hiccup) we return
    an item with ``error`` set and ``status = "down"`` so the dashboard
    flags the issue without surfacing a 500.
    """
    warn = _threshold_warning()
    crit = _threshold_critical()
    try:
        usage = shutil.disk_usage(path)
    except OSError as exc:
        log.warning(
            "admin.disk.probe_failed",
            name=name,
            path=path,
            error=str(exc),
        )
        return AdminDiskItem(
            name=name,  # type: ignore[arg-type]
            path=path,
            total_bytes=None,
            used_bytes=0,
            free_bytes=None,
            used_pct=None,
            threshold_warning=warn,
            threshold_critical=crit,
            status="down",
            error=_strip_credentials(f"{type(exc).__name__}: {exc}"),
        )

    total = int(usage.total)
    used = int(usage.used)
    free = int(usage.free)
    used_pct: float | None = round(used / total * 100, 1) if total > 0 else None

    return AdminDiskItem(
        name=name,  # type: ignore[arg-type]
        path=path,
        total_bytes=total,
        used_bytes=used,
        free_bytes=free,
        used_pct=used_pct,
        threshold_warning=warn,
        threshold_critical=crit,
        status=_classify(used_pct, warn=warn, crit=crit),
    )


async def _probe_postgres(session: AsyncSession) -> AdminDiskItem:
    """
    Return the active database's total size as ``used_bytes``.

    There is no "total bytes available" for a managed Postgres process —
    the DB grows until the underlying filesystem fills. We therefore only
    report ``used_bytes`` and leave the percent unset; the operator
    correlates against the workspace / DT volume cards above.
    """
    warn = _threshold_warning()
    crit = _threshold_critical()
    try:
        row = await session.execute(text("SELECT pg_database_size(current_database())"))
        used_bytes = int(row.scalar_one())
    except Exception as exc:  # noqa: BLE001 — DB outage shouldn't 500 the endpoint
        log.warning("admin.disk.postgres_probe_failed", error=str(exc))
        return AdminDiskItem(
            name="postgres",
            path=None,
            total_bytes=None,
            used_bytes=0,
            free_bytes=None,
            used_pct=None,
            threshold_warning=warn,
            threshold_critical=crit,
            status="down",
            error=_strip_credentials(f"{type(exc).__name__}: {exc}"),
        )

    return AdminDiskItem(
        name="postgres",
        path=None,
        total_bytes=None,
        used_bytes=used_bytes,
        free_bytes=None,
        used_pct=None,
        threshold_warning=warn,
        threshold_critical=crit,
        status="ok",
    )


def _probe_redis() -> AdminDiskItem:
    """Read ``INFO memory`` from Redis and return ``used_memory`` as bytes."""
    warn = _threshold_warning()
    crit = _threshold_critical()
    try:
        client = _redis.Redis.from_url(redis_url(), decode_responses=True)
        # ``info()`` returns a dict at runtime; mypy sees ``Awaitable | Any``
        # because the redis-py stubs union sync + async paths. The cast is
        # narrow and only runs when the call has already returned.
        raw_info: Any = client.info("memory")
        info: dict[str, Any] = raw_info if isinstance(raw_info, dict) else {}
        used_bytes = int(info.get("used_memory") or 0)
        # ``maxmemory`` may be 0 when the operator has not capped Redis;
        # in that case there is no meaningful percent.
        max_bytes_raw = info.get("maxmemory") or 0
        max_bytes = int(max_bytes_raw)
        client.close()  # type: ignore[no-untyped-call]
    except Exception as exc:  # noqa: BLE001
        log.warning("admin.disk.redis_probe_failed", error=str(exc))
        return AdminDiskItem(
            name="redis",
            path=None,
            total_bytes=None,
            used_bytes=0,
            free_bytes=None,
            used_pct=None,
            threshold_warning=warn,
            threshold_critical=crit,
            status="down",
            error=_strip_credentials(f"{type(exc).__name__}: {exc}"),
        )

    used_pct: float | None
    total: int | None
    free: int | None
    if max_bytes > 0:
        used_pct = round(used_bytes / max_bytes * 100, 1)
        total = max_bytes
        free = max(max_bytes - used_bytes, 0)
    else:
        used_pct = None
        total = None
        free = None

    return AdminDiskItem(
        name="redis",
        path=None,
        total_bytes=total,
        used_bytes=used_bytes,
        free_bytes=free,
        used_pct=used_pct,
        threshold_warning=warn,
        threshold_critical=crit,
        status=_classify(used_pct, warn=warn, crit=crit),
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def get_disk_telemetry(session: AsyncSession) -> AdminDiskOut:
    """
    Return all four storage backends' current usage in one payload.

    Order is fixed (workspace → dt_volume → postgres → redis) so the UI
    can render a stable card layout without mapping by name. Each probe
    is independent — a failure in one is reported via the per-item
    ``error`` field, never raised.
    """
    items = [
        _probe_filesystem(name="workspace", path=_workspace_path()),
        _probe_filesystem(name="dt_volume", path=_dt_volume_path()),
        await _probe_postgres(session),
        _probe_redis(),
    ]
    return AdminDiskOut(items=items, collected_at=_now())


__all__ = [
    "AdminDiskError",
    "_strip_credentials",
    "get_disk_telemetry",
]
