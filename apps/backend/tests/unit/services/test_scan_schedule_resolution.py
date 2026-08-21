"""
Resolving a scheduled scan cadence: project row over organization default,
whole-row rather than field-by-field (N18).

The contract worth pinning is the absence of one: a deployment that writes no
rows must resolve to "no automatic scan" exactly as if this table did not
exist. After that, the interesting case is a project that has decided
something — even "off" — because that decision must never be blended with the
organization's cadence the way gate policy fields are.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from services.scan_schedule_service import resolve_for_project
from tests._helpers import make_organization, make_project, make_team

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set: skip scan schedule resolution tests")
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
        pytest.skip(f"alembic upgrade head failed\n{result.stdout}\n{result.stderr}")


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    from core.config import database_url

    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed(session: AsyncSession):
    org = await make_organization(session)
    team = await make_team(session, organization=org)
    project = await make_project(session, team=team)
    return org, team, project


async def _write_schedule(session: AsyncSession, *, org, project=None, **fields):
    from models import ScanSchedule

    row = ScanSchedule(
        organization_id=org.id,
        project_id=project.id if project is not None else None,
        **fields,
    )
    session.add(row)
    await session.commit()
    return row


async def test_no_rows_resolve_to_nothing(db_session: AsyncSession) -> None:
    """The contract every other N18 test builds on: no rows, no schedule."""
    _org, _team, project = await _seed(db_session)

    resolved = await resolve_for_project(db_session, project.id)

    assert resolved.source == "none"
    assert resolved.fires is False


async def test_the_organization_default_applies_with_no_project_row(
    db_session: AsyncSession,
) -> None:
    org, _team, project = await _seed(db_session)
    await _write_schedule(db_session, org=org, cadence="daily", hour=9, timezone="UTC")

    resolved = await resolve_for_project(db_session, project.id)

    assert resolved.source == "organization"
    assert resolved.cadence == "daily"
    assert resolved.hour == 9
    assert resolved.fires is True


async def test_a_project_row_wins_wholesale_not_field_by_field(
    db_session: AsyncSession,
) -> None:
    """Unlike gate policy, a project decision is never blended with the org's."""
    org, _team, project = await _seed(db_session)
    await _write_schedule(
        db_session, org=org, cadence="weekly", hour=2, day_of_week=6, timezone="UTC"
    )
    await _write_schedule(db_session, org=org, project=project, cadence="daily", hour=14)

    resolved = await resolve_for_project(db_session, project.id)

    assert resolved.source == "project"
    assert resolved.cadence == "daily"
    assert resolved.hour == 14
    # The org's weekly/day-of-week did not leak into the project's row.
    assert resolved.day_of_week is None


async def test_a_project_can_opt_out_of_the_organization_default(
    db_session: AsyncSession,
) -> None:
    """is_active=false on the project row wins outright — no fall-through."""
    org, _team, project = await _seed(db_session)
    await _write_schedule(db_session, org=org, cadence="daily", hour=9, timezone="UTC")
    await _write_schedule(db_session, org=org, project=project, is_active=False)

    resolved = await resolve_for_project(db_session, project.id)

    assert resolved.source == "project"
    assert resolved.fires is False


async def test_another_organizations_default_does_not_leak(
    db_session: AsyncSession,
) -> None:
    other_org = await make_organization(db_session)
    await _write_schedule(db_session, org=other_org, cadence="daily", hour=9, timezone="UTC")
    _org, _team, project = await _seed(db_session)

    resolved = await resolve_for_project(db_session, project.id)

    assert resolved.source == "none"


async def test_a_missing_project_resolves_to_nothing(db_session: AsyncSession) -> None:
    resolved = await resolve_for_project(db_session, uuid.uuid4())

    assert resolved.source == "none"
    assert resolved.fires is False
