"""
Resolving a build-gate policy: team over organization, field by field.

The contract worth pinning is the absence of one. A deployment that writes no
rows must evaluate exactly as it did before the table existed, so the first
test here is the one that would catch this whole feature changing a verdict it
was not asked to change.

After that the interesting cases are partial rows. A team writes a policy to
change one thing, and the fields it leaves alone have to keep following the
organization rather than freezing at whatever the organization said on the day
the team's row was created.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from services.gate_policy_service import resolve_for_project
from tests._helpers import make_organization, make_project, make_team

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set: skip gate policy resolution tests")
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


async def _write_policy(session: AsyncSession, *, org, team=None, **fields):
    from models import GatePolicy

    policy = GatePolicy(
        organization_id=org.id,
        team_id=team.id if team is not None else None,
        **fields,
    )
    session.add(policy)
    await session.commit()
    return policy


async def test_no_rows_resolve_to_nothing(db_session: AsyncSession) -> None:
    """The contract the rest of the gate depends on.

    Every field None means the caller keeps its environment answer, so a
    deployment that has written no policy evaluates as it always did.
    """
    _, _, project = await _seed(db_session)

    resolved = await resolve_for_project(db_session, project.id)

    assert resolved.is_empty


async def test_the_organization_row_applies_to_a_project_with_no_team_row(
    db_session: AsyncSession,
) -> None:
    org, _, project = await _seed(db_session)
    await _write_policy(db_session, org=org, epss_threshold=0.5, reachable_critical_only=True)

    resolved = await resolve_for_project(db_session, project.id)

    assert resolved.epss_threshold == 0.5
    assert resolved.reachable_critical_only is True
    assert resolved.malicious_blocks is None


async def test_a_team_row_wins_field_by_field_and_not_wholesale(
    db_session: AsyncSession,
) -> None:
    """The reason every column is nullable.

    The team pinned one threshold. The fields it left alone still follow the
    organization, so a later organization-wide change reaches this team too,
    which is the point of having an organization-wide policy at all.
    """
    org, team, project = await _seed(db_session)
    await _write_policy(
        db_session,
        org=org,
        epss_threshold=0.5,
        reachable_critical_only=True,
        malicious_blocks=True,
    )
    await _write_policy(db_session, org=org, team=team, epss_threshold=0.9)

    resolved = await resolve_for_project(db_session, project.id)

    assert resolved.epss_threshold == 0.9
    assert resolved.reachable_critical_only is True
    assert resolved.malicious_blocks is True


async def test_another_organization_policy_does_not_leak(db_session: AsyncSession) -> None:
    """Scoping is by organization, so a neighbour's policy is not a default."""
    other_org = await make_organization(db_session)
    await _write_policy(db_session, org=other_org, epss_threshold=0.1)
    _, _, project = await _seed(db_session)

    resolved = await resolve_for_project(db_session, project.id)

    assert resolved.is_empty


async def test_a_missing_project_resolves_to_nothing(db_session: AsyncSession) -> None:
    """The gate must not fail because the project went away underneath it.

    Returning empty hands the caller back to its environment answer, which is
    the same verdict it would have reached before this lookup existed.
    """
    resolved = await resolve_for_project(db_session, uuid.uuid4())

    assert resolved.is_empty


async def test_a_false_toggle_is_a_decision_not_an_absence(db_session: AsyncSession) -> None:
    """False has to survive the fall-through.

    Malicious blocking defaults on, so a policy that turns it off is the one
    case where a falsy value must not be mistaken for "unset" and replaced by
    the environment default.
    """
    org, _, project = await _seed(db_session)
    await _write_policy(db_session, org=org, malicious_blocks=False)

    resolved = await resolve_for_project(db_session, project.id)

    assert resolved.malicious_blocks is False
