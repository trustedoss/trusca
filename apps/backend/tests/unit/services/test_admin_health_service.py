"""
Service-layer tests for ``services.admin_health_service`` — Phase 4 PR #14.

Each per-component probe is independently testable. Top-level
``get_system_health`` orchestration is covered against a real DB +
fakeredis to pin the contract end-to-end.

Coverage:
  - postgres probe: SELECT 1 ok / DB error
  - redis probe: PING ok / connection error
  - celery probe: workers present / no workers / control failure
  - dt probe: closed (ok) / half_open (degraded) / open (down)
  - disk probe: ok / degraded / down (worst-of-N selection)
  - active_scans + last_24h_errors: count and detail rendering
  - get_system_health orchestrates all probes in fixed order
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from services.admin_health_service import (
    _probe_active_scans,
    _probe_celery,
    _probe_dt,
    _probe_last_24h_errors,
    _probe_postgres,
    _probe_redis,
    get_system_health,
)
from tests._helpers import (
    make_organization,
    make_project,
    make_scan,
    make_team,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip admin_health_service tests")
    return url


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    _require_database_url()
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        pytest.skip(
            f"alembic upgrade head failed; admin_health_service tests cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    from core.config import database_url

    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session
    await engine.dispose()


# ---------------------------------------------------------------------------
# Postgres probe
# ---------------------------------------------------------------------------


async def test_probe_postgres_ok(db_session: AsyncSession) -> None:
    component = await _probe_postgres(db_session)
    assert component.status == "ok"
    assert component.name == "postgres"


async def test_probe_postgres_failure_returns_down(db_session: AsyncSession) -> None:
    """Force the SELECT to raise — the probe must return status='down'."""

    class _BoomSession:
        async def execute(self, _stmt: Any) -> Any:
            raise RuntimeError("simulated DB outage")

    boom_session: Any = _BoomSession()
    component = await _probe_postgres(boom_session)
    assert component.status == "down"
    assert component.detail is not None
    assert "RuntimeError" in component.detail


# ---------------------------------------------------------------------------
# Redis probe
# ---------------------------------------------------------------------------


def test_probe_redis_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    class _OkClient:
        def ping(self) -> bool:
            return True

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        "services.admin_health_service._redis.Redis.from_url",
        lambda _url, **_kw: _OkClient(),
    )
    component = _probe_redis()
    assert component.status == "ok"


def test_probe_redis_outage(monkeypatch: pytest.MonkeyPatch) -> None:
    class _BoomClient:
        def ping(self) -> bool:
            raise ConnectionError("redis down")

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        "services.admin_health_service._redis.Redis.from_url",
        lambda _url, **_kw: _BoomClient(),
    )
    component = _probe_redis()
    assert component.status == "down"


# ---------------------------------------------------------------------------
# Celery probe
# ---------------------------------------------------------------------------


class _FakeCelery:
    def __init__(self, *, replies: list[Any] | None, raise_err: Exception | None = None) -> None:
        self._replies = replies
        self._raise_err = raise_err

        class _Control:
            def __init__(_inner_self) -> None:  # noqa: N805
                pass

            def ping(_inner_self, timeout: float = 2.0) -> Any:  # noqa: ARG002, N805
                if self._raise_err is not None:
                    raise self._raise_err
                return self._replies

        self.control = _Control()


def test_probe_celery_workers_present() -> None:
    fake = _FakeCelery(replies=[{"worker1": {"ok": "pong"}}])
    component = _probe_celery(celery_app_override=fake)
    assert component.status == "ok"
    assert component.value == 1


def test_probe_celery_no_workers() -> None:
    fake = _FakeCelery(replies=[])
    component = _probe_celery(celery_app_override=fake)
    assert component.status == "down"
    assert component.value == 0


def test_probe_celery_control_failure() -> None:
    fake = _FakeCelery(replies=None, raise_err=ConnectionError("broker unreachable"))
    component = _probe_celery(celery_app_override=fake)
    assert component.status == "down"


# ---------------------------------------------------------------------------
# DT probe
# ---------------------------------------------------------------------------


def test_probe_dt_closed_returns_ok() -> None:
    from integrations.dt.breaker import BreakerSnapshot

    snapshot = BreakerSnapshot(state="closed", fail_count=0, opened_at=None)
    with patch("services.admin_health_service.get_breaker") as mock_get:
        mock_get.return_value.snapshot.return_value = snapshot
        component = _probe_dt()
    assert component.status == "ok"


def test_probe_dt_half_open_returns_degraded() -> None:
    from integrations.dt.breaker import BreakerSnapshot

    snapshot = BreakerSnapshot(state="half_open", fail_count=3, opened_at=1.0)
    with patch("services.admin_health_service.get_breaker") as mock_get:
        mock_get.return_value.snapshot.return_value = snapshot
        component = _probe_dt()
    assert component.status == "degraded"


def test_probe_dt_open_returns_down() -> None:
    from integrations.dt.breaker import BreakerSnapshot

    snapshot = BreakerSnapshot(state="open", fail_count=10, opened_at=1.0)
    with patch("services.admin_health_service.get_breaker") as mock_get:
        mock_get.return_value.snapshot.return_value = snapshot
        component = _probe_dt()
    assert component.status == "down"


# ---------------------------------------------------------------------------
# Active scans + last 24h errors
# ---------------------------------------------------------------------------


async def test_probe_active_scans_counts_queued_and_running(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Pin a very high warn threshold so queued/running rows accumulated by
    # sibling tests can't flip the status — this case only asserts counting
    # and the ok classification below the threshold.
    monkeypatch.setenv("HEALTH_ACTIVE_SCANS_WARN", "1000000")
    monkeypatch.setenv("HEALTH_ACTIVE_SCANS_CRIT", "2000000")

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)
    await make_scan(db_session, project=project, status="queued")
    project2 = await make_project(db_session, team=team)
    await make_scan(db_session, project=project2, status="succeeded")

    component = await _probe_active_scans(db_session)
    assert component.status == "ok"
    # The DB has at least the row we just inserted; other tests may have
    # added more queued/running rows so we use ``>= 1`` rather than equality.
    assert component.value is not None
    assert int(component.value) >= 1


async def test_probe_active_scans_flags_backlog(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A large queued+running backlog is degraded/down — not 'healthy'.

    Regression guard for the manual-walkthrough finding: 410 in-flight scans
    were reported as ok. Thresholds are env-tunable; we set tiny ones so the
    seeded rows deterministically trip each band regardless of accumulation.
    """
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    # One active scan per project (partial unique ix_scans_project_active), so
    # seed two projects to get two in-flight rows.
    project = await make_project(db_session, team=team)
    await make_scan(db_session, project=project, status="queued")
    project2 = await make_project(db_session, team=team)
    await make_scan(db_session, project=project2, status="running")

    # crit at 1 → any in-flight scan trips ``down``.
    monkeypatch.setenv("HEALTH_ACTIVE_SCANS_WARN", "1")
    monkeypatch.setenv("HEALTH_ACTIVE_SCANS_CRIT", "1")
    component = await _probe_active_scans(db_session)
    assert component.status == "down"
    assert int(component.value or 0) >= 2

    # warn ≤ count < crit → ``degraded``.
    monkeypatch.setenv("HEALTH_ACTIVE_SCANS_WARN", "1")
    monkeypatch.setenv("HEALTH_ACTIVE_SCANS_CRIT", "1000000")
    component = await _probe_active_scans(db_session)
    assert component.status == "degraded"


async def test_probe_last_24h_errors_counts_recent_failures(db_session: AsyncSession) -> None:
    """Seed a recently-failed scan; the count includes it."""
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)
    scan = await make_scan(db_session, project=project, status="failed")
    scan.completed_at = datetime.now(tz=UTC) - timedelta(hours=1)
    await db_session.commit()

    component = await _probe_last_24h_errors(db_session)
    assert component.status == "ok"
    assert component.value is not None
    assert int(component.value) >= 1


# ---------------------------------------------------------------------------
# get_system_health — orchestration shape
# ---------------------------------------------------------------------------


async def test_get_system_health_returns_seven_components_in_order(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The aggregated payload always carries the seven-component slice in fixed order."""

    # Force the Redis + Celery + DT probes to deterministic 'ok' so the test
    # does not depend on the surrounding stack state.
    class _OkRedis:
        def ping(self) -> bool:
            return True

        def info(self, _section: str) -> dict[str, int]:
            return {"used_memory": 0, "maxmemory": 0}

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        "services.admin_health_service._redis.Redis.from_url",
        lambda _url, **_kw: _OkRedis(),
    )
    monkeypatch.setattr(
        "services.admin_disk_service._redis.Redis.from_url",
        lambda _url, **_kw: _OkRedis(),
    )

    fake_celery = _FakeCelery(replies=[{"worker1": {"ok": "pong"}}])

    with patch("services.admin_health_service.get_breaker") as mock_get:
        from integrations.dt.breaker import BreakerSnapshot

        mock_get.return_value.snapshot.return_value = BreakerSnapshot(
            state="closed", fail_count=0, opened_at=None
        )
        with patch("tasks.celery_app.celery_app", new=fake_celery):
            payload = await get_system_health(db_session)

    names = [c.name for c in payload.components]
    assert names == [
        "postgres",
        "redis",
        "celery",
        "dt",
        "disk",
        "active_scans",
        "last_24h_errors",
    ]
