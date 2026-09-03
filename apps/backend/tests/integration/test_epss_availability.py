"""The deployment-level EPSS signal the policy editor shows (ER43).

The gate answers a per-scan question. This answers the one an administrator has
while setting a threshold, before any scan runs: does this deployment collect
EPSS at all.

Both halves matter, and the tests below are mostly about why. "The catalog
holds a score" on its own is the weak signal: a deployment that switched the
sync off months ago still has old values on a handful of CVEs, so a screen
reading only that looks healthy while every CVE found since has come back
unscored. That is the same false reassurance the whole EPSS line of work
exists to remove, so it must not be reintroduced by the fix for it.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from models import EpssSyncState
from models import Vulnerability as VulnerabilityModel

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set, skipping EPSS availability integration")
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
            f"alembic upgrade head failed; EPSS availability tests cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    from core.config import database_url

    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        # A clean slate: both halves of the answer are counts over the whole
        # deployment, so a leftover row from another test is a different answer.
        await session.execute(delete(EpssSyncState))
        await session.execute(delete(VulnerabilityModel))
        await session.commit()
        yield session
    await engine.dispose()


async def _add_scored_cve(session: AsyncSession) -> None:
    session.add(
        VulnerabilityModel(
            external_id=f"CVE-2093-{uuid.uuid4().hex[:8]}",
            source="trivy",
            severity="high",
            epss_score=Decimal("0.50000"),
            epss_percentile=Decimal("0.50000"),
        )
    )
    await session.commit()


async def _set_sync_state(session: AsyncSession, *, synced_at: datetime | None) -> None:
    await session.execute(delete(EpssSyncState))
    session.add(
        EpssSyncState(id=True, last_synced_at=synced_at, last_result="synced")
    )
    await session.commit()


async def test_a_healthy_deployment_reports_usable(
    db_session: AsyncSession, monkeypatch
) -> None:
    from services.epss_availability import get_epss_availability

    monkeypatch.setenv("EPSS_REFRESH_ENABLED", "true")
    await _add_scored_cve(db_session)
    await _set_sync_state(db_session, synced_at=datetime.now(tz=UTC))

    result = await get_epss_availability(db_session)

    assert result.usable is True
    assert result.refresh_enabled is True
    assert result.scored_cves == 1


async def test_old_values_do_not_make_a_switched_off_sync_look_healthy(
    db_session: AsyncSession, monkeypatch
) -> None:
    """The case that makes the value count alone the wrong signal."""
    from services.epss_availability import get_epss_availability

    monkeypatch.setenv("EPSS_REFRESH_ENABLED", "false")
    await _add_scored_cve(db_session)
    await _set_sync_state(db_session, synced_at=datetime.now(tz=UTC))

    result = await get_epss_availability(db_session)

    assert result.usable is False, (
        "scores exist, but nothing is collecting new ones, so a threshold set "
        "here decides nothing for anything found from now on"
    )
    assert result.refresh_enabled is False
    assert result.scored_cves == 1


async def test_a_sync_that_stopped_landing_is_not_usable(
    db_session: AsyncSession, monkeypatch
) -> None:
    """Enabled is not the same as working."""
    from services.epss_availability import get_epss_availability

    monkeypatch.setenv("EPSS_REFRESH_ENABLED", "true")
    await _add_scored_cve(db_session)
    await _set_sync_state(
        db_session, synced_at=datetime.now(tz=UTC) - timedelta(days=30)
    )

    result = await get_epss_availability(db_session)

    assert result.usable is False
    assert result.refresh_enabled is True
    assert result.last_synced_at is not None


async def test_a_healthy_sync_with_nothing_written_yet_is_not_usable(
    db_session: AsyncSession, monkeypatch
) -> None:
    """The operator just switched it on; the first tick has not landed."""
    from services.epss_availability import get_epss_availability

    monkeypatch.setenv("EPSS_REFRESH_ENABLED", "true")
    await _set_sync_state(db_session, synced_at=datetime.now(tz=UTC))

    result = await get_epss_availability(db_session)

    assert result.usable is False
    assert result.scored_cves == 0


async def test_a_deployment_that_never_synced_is_not_usable(
    db_session: AsyncSession, monkeypatch
) -> None:
    from services.epss_availability import get_epss_availability

    monkeypatch.setenv("EPSS_REFRESH_ENABLED", "true")

    result = await get_epss_availability(db_session)

    assert result.usable is False
    assert result.last_synced_at is None
    assert result.scored_cves == 0
