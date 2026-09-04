# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Integration tests for ``services.external_package_usage.internal_usage_by_purl``.

Real DB, real ``team_scope_filter`` / ``latest_succeeded_scan_select`` --
the same primitives ``search_results_service._components`` already has full
team-isolation coverage for (``tests/integration/test_search_results_api.py``),
so this file's job is narrower: prove the one thing that function's ILIKE
match cannot give us -- exact-purl equality, not substring -- and the basic
found/not-found/multi-project shapes on top of it.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.security import CurrentUser
from models import Component, ComponentVersion, ScanComponent
from services.external_package_usage import internal_usage_by_purl
from tests._db_required import migrate_to_head
from tests._helpers import (
    make_organization,
    make_project,
    make_scan,
    make_team,
    make_user,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def _schema() -> None:
    """This module used to build no schema of its own.

    It opened an engine and queried tables that some earlier module's fixture
    had happened to create, so it passed in a full run and failed all six ways
    when run alone. ER66.
    """
    migrate_to_head()


@pytest.fixture
async def db_session(_schema):  # noqa: ANN001
    from core.config import database_url

    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _actor_for(session: AsyncSession, *, team_id: uuid.UUID) -> CurrentUser:
    user = await make_user(session)
    return CurrentUser(
        id=user.id,
        email=user.email,
        role="developer",
        team_ids=[team_id],
        team_roles={team_id: "developer"},
    )


async def _seed_component_in_project(
    session: AsyncSession, *, project, purl: str, version: str = "1.0.0"
) -> None:
    from sqlalchemy import select

    scan = await make_scan(session, project=project, status="succeeded")

    # Component.purl is unique across the whole table -- a real scan
    # pipeline shares one Component row across every project that uses the
    # same package, so two seed calls for the same purl (different
    # projects) must reuse it rather than insert a duplicate.
    existing = (
        await session.execute(select(Component).where(Component.purl == purl))
    ).scalar_one_or_none()
    if existing is not None:
        component = existing
    else:
        component = Component(purl=purl, package_type="npm", name=purl.rsplit("/", 1)[-1])
        session.add(component)
        await session.commit()
        await session.refresh(component)

    # Same reuse reasoning as Component: purl_with_version is also unique
    # across the whole table.
    purl_with_version = f"{purl}@{version}"
    existing_cv = (
        await session.execute(
            select(ComponentVersion).where(ComponentVersion.purl_with_version == purl_with_version)
        )
    ).scalar_one_or_none()
    if existing_cv is not None:
        cv = existing_cv
    else:
        cv = ComponentVersion(
            component_id=component.id, version=version, purl_with_version=purl_with_version
        )
        session.add(cv)
        await session.commit()
        await session.refresh(cv)

    session.add(ScanComponent(scan_id=scan.id, component_version_id=cv.id, direct=True))
    await session.commit()


async def test_exact_match_does_not_pick_up_similarly_named_package(
    db_session: AsyncSession,
) -> None:
    """The regression this file exists to catch: an ILIKE '%purl%' match
    would also hit `pkg:npm/lodash-es` when searching for `pkg:npm/lodash`.
    Equality must not."""
    suffix = uuid.uuid4().hex[:8]
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)
    actor = await _actor_for(db_session, team_id=team.id)

    target_purl = f"pkg:npm/lodash-{suffix}"
    decoy_purl = f"pkg:npm/lodash-{suffix}-es"

    await _seed_component_in_project(db_session, project=project, purl=decoy_purl)

    result = await internal_usage_by_purl(db_session, actor=actor, purl=target_purl)

    assert result == []


async def test_found_returns_project_and_version(db_session: AsyncSession) -> None:
    suffix = uuid.uuid4().hex[:8]
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team, name=f"Usage Project {suffix}")
    actor = await _actor_for(db_session, team_id=team.id)

    purl = f"pkg:npm/exact-hit-{suffix}"
    await _seed_component_in_project(db_session, project=project, purl=purl, version="2.3.4")

    result = await internal_usage_by_purl(db_session, actor=actor, purl=purl)

    assert len(result) == 1
    assert result[0].project_id == project.id
    assert result[0].version == "2.3.4"


async def test_multiple_projects_all_returned(db_session: AsyncSession) -> None:
    suffix = uuid.uuid4().hex[:8]
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project_a = await make_project(db_session, team=team, name=f"Multi A {suffix}")
    project_b = await make_project(db_session, team=team, name=f"Multi B {suffix}")
    actor = await _actor_for(db_session, team_id=team.id)

    purl = f"pkg:npm/shared-dep-{suffix}"
    await _seed_component_in_project(db_session, project=project_a, purl=purl)
    await _seed_component_in_project(db_session, project=project_b, purl=purl)

    result = await internal_usage_by_purl(db_session, actor=actor, purl=purl)

    assert {row.project_id for row in result} == {project_a.id, project_b.id}


async def test_not_used_anywhere_returns_empty(db_session: AsyncSession) -> None:
    suffix = uuid.uuid4().hex[:8]
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    actor = await _actor_for(db_session, team_id=team.id)

    result = await internal_usage_by_purl(
        db_session, actor=actor, purl=f"pkg:npm/nothing-uses-this-{suffix}"
    )

    assert result == []


async def test_other_teams_project_is_not_visible(db_session: AsyncSession) -> None:
    """team_scope_filter must actually be applied: a non-super-admin actor
    on team A must not see team B's usage of the same purl."""
    suffix = uuid.uuid4().hex[:8]
    org = await make_organization(db_session)
    team_a = await make_team(db_session, organization=org)
    team_b = await make_team(db_session, organization=org)
    project_b = await make_project(db_session, team=team_b, name=f"Other Team {suffix}")
    actor_a = await _actor_for(db_session, team_id=team_a.id)

    purl = f"pkg:npm/team-b-only-{suffix}"
    await _seed_component_in_project(db_session, project=project_b, purl=purl)

    result = await internal_usage_by_purl(db_session, actor=actor_a, purl=purl)

    assert result == []


async def test_super_admin_sees_every_team(db_session: AsyncSession) -> None:
    suffix = uuid.uuid4().hex[:8]
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team, name=f"Admin Visible {suffix}")
    admin_user = await make_user(db_session, is_superuser=True)
    actor = CurrentUser(
        id=admin_user.id,
        email=admin_user.email,
        role="super_admin",
        team_ids=[],
        team_roles={},
        is_superuser=True,
    )

    purl = f"pkg:npm/admin-visible-{suffix}"
    await _seed_component_in_project(db_session, project=project, purl=purl)

    result = await internal_usage_by_purl(db_session, actor=actor, purl=purl)

    assert len(result) == 1
    assert result[0].project_id == project.id
