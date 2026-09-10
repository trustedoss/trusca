# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Group-hierarchy permission cascade, Phase 2 PR 2-D.

PR 2-C wired the cascade into every *fan-out* read (``core.authz.
team_scope_filter``) and into the seven local single-resource
reimplementations it replaced with ``core.authz.can_access_group``. It left
``core.authz.can_access_team`` / ``assert_team_access`` (the single-resource
gate ~22 other service modules use) deliberately flat, because converting
those meant every caller becoming ``async`` + session-carrying. That asymmetry
was a real, reachable defect once the flag was on: a fan-out list (Overview's
sibling tabs, the project list, a project's Vulnerabilities/Licenses tab list)
would show an item reached only through an ancestor group's membership, and
opening that same item (the single-resource gate) would 403, the exact High
finding the PR 2-C security review raised.

PR 2-D closed the asymmetry: ``assert_team_access`` is now ``async``, takes a
session, and delegates to ``can_access_group``, the SAME cascade-aware
primitive the fan-out lists already used. This file is PR 2-D's own
verification that the gap is actually closed, on three domains:

  1. Project, ``services.project_service.list_projects`` (fan-out) against
     ``services.project_service.get_project`` (single-resource).
  2. Vulnerability, ``services.vulnerability_service.list_project_vulnerabilities``
     against ``get_vulnerability_detail``.
  3. License, ``services.license_service.list_project_licenses`` against
     ``get_license_finding_detail``.

For (2) and (3) the "list" is itself a single-resource-gated call (one
project's tab), so the property under test is literally the task's own
phrasing: every id the list returns must also open through the matching
single-resource read. For (1) the list is the fan-out surface proper, the
same shape as the Overview-tab bug report that started this PR.

The second half of this file reproduces PR 2-A / PR 2-C's four destructive
sibling / inherit / no-upward / deep-inherit combos directly against two of
the surfaces PR 2-D newly made cascade-aware (``get_project`` and
``get_vulnerability_detail``), the same tree, the same vocabulary, on the
gate this PR actually changed.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._db_required import migrate_to_head
from tests._helpers import (
    make_organization,
    make_project,
    make_scan,
    make_team,
    make_user,
    principal_for,
    unique_suffix,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    from core.config import database_url

    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture(params=[True, False], ids=["cascade_on", "cascade_off"])
def cascade_enabled(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> bool:
    enabled: bool = request.param
    monkeypatch.setenv("GROUP_CASCADE_ENABLED", "true" if enabled else "false")
    return enabled


@pytest.fixture
def cascade_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROUP_CASCADE_ENABLED", "true")


# ---------------------------------------------------------------------------
# Tree: P (root) -> C (child of P) -> G (grandchild of P via C)
#              \-> S (sibling of C, also a child of P)
#
# One project per group, mirrors PR 2-A / PR 2-C's tree shape/vocabulary so
# the same combo names carry across every file in this series.
# ---------------------------------------------------------------------------


class _Fixture:
    def __init__(
        self,
        *,
        p: uuid.UUID,
        c: uuid.UUID,
        g: uuid.UUID,
        s: uuid.UUID,
        project_p: uuid.UUID,
        project_c: uuid.UUID,
        project_g: uuid.UUID,
        project_s: uuid.UUID,
    ) -> None:
        self.p = p
        self.c = c
        self.g = g
        self.s = s
        self.project_p = project_p
        self.project_c = project_c
        self.project_g = project_g
        self.project_s = project_s


@pytest.fixture
async def fixture(db_session: AsyncSession) -> _Fixture:
    org = await make_organization(db_session)
    p = await make_team(db_session, organization=org)
    c = await make_team(db_session, organization=org, parent=p)
    g = await make_team(db_session, organization=org, parent=c)
    s = await make_team(db_session, organization=org, parent=p)

    project_p = await make_project(db_session, team=p)
    project_c = await make_project(db_session, team=c)
    project_g = await make_project(db_session, team=g)
    project_s = await make_project(db_session, team=s)

    return _Fixture(
        p=p.id,
        c=c.id,
        g=g.id,
        s=s.id,
        project_p=project_p.id,
        project_c=project_c.id,
        project_g=project_g.id,
        project_s=project_s.id,
    )


# ---------------------------------------------------------------------------
# Finding seed helpers: a minimal succeeded scan with one finding, so
# `resolve_snapshot_scan_id` (which every list/detail pair below anchors on)
# has something to resolve.
# ---------------------------------------------------------------------------


async def _seed_succeeded_scan(session: AsyncSession, project_id: uuid.UUID) -> uuid.UUID:
    from sqlalchemy import select

    from models import Project

    project = (
        await session.execute(select(Project).where(Project.id == project_id))
    ).scalar_one()
    scan = await make_scan(session, project=project, status="succeeded")
    return scan.id


async def _seed_vulnerability_finding(
    session: AsyncSession, *, scan_id: uuid.UUID, severity: str = "high"
) -> uuid.UUID:
    from models import Component, ComponentVersion, Vulnerability, VulnerabilityFinding

    suffix = unique_suffix()
    purl = f"pkg:npm/pkg-{suffix}"
    component = Component(purl=purl, package_type="npm", name=f"pkg-{suffix}")
    session.add(component)
    await session.commit()
    await session.refresh(component)

    cv = ComponentVersion(
        component_id=component.id,
        version="1.0.0",
        purl_with_version=f"{purl}@1.0.0",
    )
    session.add(cv)
    await session.commit()
    await session.refresh(cv)

    vuln = Vulnerability(
        external_id=f"CVE-2099-PARITY-{suffix}",
        source="NVD",
        severity=severity,
        summary=f"summary {suffix}",
    )
    session.add(vuln)
    await session.commit()
    await session.refresh(vuln)

    finding = VulnerabilityFinding(
        scan_id=scan_id,
        component_version_id=cv.id,
        vulnerability_id=vuln.id,
        status="new",
        analysis_state="new",
    )
    session.add(finding)
    await session.commit()
    await session.refresh(finding)
    return finding.id


async def _seed_license_finding(
    session: AsyncSession, *, scan_id: uuid.UUID, category: str = "allowed"
) -> uuid.UUID:
    from models import Component, ComponentVersion, License, LicenseFinding

    suffix = unique_suffix()
    purl = f"pkg:npm/pkg-{suffix}"
    component = Component(purl=purl, package_type="npm", name=f"pkg-{suffix}")
    session.add(component)
    await session.commit()
    await session.refresh(component)

    cv = ComponentVersion(
        component_id=component.id,
        version="1.0.0",
        purl_with_version=f"{purl}@1.0.0",
    )
    session.add(cv)
    await session.commit()
    await session.refresh(cv)

    lic = License(spdx_id=f"SPDX-{suffix}", name=f"License {suffix}", category=category)
    session.add(lic)
    await session.commit()
    await session.refresh(lic)

    lf = LicenseFinding(
        scan_id=scan_id,
        component_version_id=cv.id,
        license_id=lic.id,
        kind="concluded",
        source_path=f"path/{suffix}",
        raw_data={},
    )
    session.add(lf)
    await session.commit()
    await session.refresh(lf)
    return lf.id


# ---------------------------------------------------------------------------
# List-detail parity guard (cascade ON), three domains.
#
# Actor is a direct member of P ONLY. project_c (P's child) is reachable for
# READ solely through the cascade. Each domain's list against project_c must
# succeed (proving the fan-out / project-scoped list is cascade-aware, that
# much PR 2-C already guaranteed), AND every id the list returns must also
# open through the matching single-resource detail read (the property PR 2-D
# adds).
# ---------------------------------------------------------------------------


async def test_project_list_detail_parity(
    db_session: AsyncSession, fixture: _Fixture, cascade_on: None
) -> None:
    """Every project `list_projects` (fan-out) returns must also `get_project`.

    This is the literal shape of the originally reported bug: an ancestor
    (`p`) member sees `project_c` / `project_g` in the portfolio list, and
    must be able to open each one, not 403 on some of them.
    """
    from services.project_service import get_project, list_projects

    user = await make_user(db_session)
    actor = principal_for(user, team_ids=[fixture.p])

    rows, _total = await list_projects(db_session, actor=actor)
    visible_ids = {row.id for row in rows}
    assert {fixture.project_p, fixture.project_c, fixture.project_g} <= visible_ids

    for project_id in visible_ids:
        project = await get_project(db_session, project_id=project_id, actor=actor)
        assert project.id == project_id


async def test_vulnerability_list_detail_parity(
    db_session: AsyncSession, fixture: _Fixture, cascade_on: None
) -> None:
    """Every id `list_project_vulnerabilities(project_c)` returns must also
    `get_vulnerability_detail`, for an actor who only reaches `project_c`
    through `p`'s membership."""
    from services.vulnerability_service import (
        get_vulnerability_detail,
        list_project_vulnerabilities,
    )

    scan_id = await _seed_succeeded_scan(db_session, fixture.project_c)
    finding_id = await _seed_vulnerability_finding(db_session, scan_id=scan_id)

    user = await make_user(db_session)
    actor = principal_for(user, team_ids=[fixture.p])

    items, total, _dist = await list_project_vulnerabilities(
        db_session, project_id=fixture.project_c, actor=actor
    )
    assert total == 1
    returned_ids = {item["id"] for item in items}
    assert returned_ids == {finding_id}

    for returned_id in returned_ids:
        detail = await get_vulnerability_detail(db_session, finding_id=returned_id, actor=actor)
        assert detail["id"] == returned_id


async def test_license_list_detail_parity(
    db_session: AsyncSession, fixture: _Fixture, cascade_on: None
) -> None:
    """Every id `list_project_licenses(project_c)` returns must also
    `get_license_finding_detail`, for an actor who only reaches `project_c`
    through `p`'s membership."""
    from services.license_service import get_license_finding_detail, list_project_licenses

    scan_id = await _seed_succeeded_scan(db_session, fixture.project_c)
    finding_id = await _seed_license_finding(db_session, scan_id=scan_id)

    user = await make_user(db_session)
    actor = principal_for(user, team_ids=[fixture.p])

    page = await list_project_licenses(db_session, project_id=fixture.project_c, actor=actor)
    assert page.total == 1
    # `id` in the raw list-item dict is whatever the SQL layer produced for
    # `sample_finding_id` (a string, not a `uuid.UUID` instance), normalize
    # before comparing/round-tripping through `get_license_finding_detail`,
    # which takes a real `uuid.UUID`.
    returned_ids = {uuid.UUID(str(item["id"])) for item in page.items}
    assert returned_ids == {finding_id}

    for returned_id in returned_ids:
        detail = await get_license_finding_detail(
            db_session, finding_id=returned_id, actor=actor
        )
        assert detail["id"] == returned_id


# ---------------------------------------------------------------------------
# Destructive combo reproduction, surfaces PR 2-D newly made cascade-aware.
#
# Surface A: services.project_service.get_project (single-resource gate via
# assert_team_access, flat before this PR).
# Surface B: services.vulnerability_service.get_vulnerability_detail (same).
# ---------------------------------------------------------------------------


_COMBOS = [
    ("1_sibling", "c", "project_s", False),
    ("2_inherit", "p", "project_c", True),
    ("4_no_upward", "g", "project_p", False),
    ("5_deep_inherit", "p", "project_g", True),
]


@pytest.mark.parametrize(
    ("combo", "actor_group_attr", "target_project_attr", "cascade_on_access"),
    _COMBOS,
)
async def test_get_project_cascade_matrix(
    db_session: AsyncSession,
    fixture: _Fixture,
    cascade_enabled: bool,
    combo: str,
    actor_group_attr: str,
    target_project_attr: str,
    cascade_on_access: bool,
) -> None:
    """`get_project`, was flat regardless of the flag before PR 2-D."""
    from services.project_service import ProjectForbidden, get_project

    user = await make_user(db_session)
    actor_group_id: uuid.UUID = getattr(fixture, actor_group_attr)
    target_project_id: uuid.UUID = getattr(fixture, target_project_attr)
    actor = principal_for(user, team_ids=[actor_group_id])

    should_succeed = cascade_on_access if cascade_enabled else (
        actor_group_attr == target_project_attr.removeprefix("project_")
    )

    if should_succeed:
        project = await get_project(db_session, project_id=target_project_id, actor=actor)
        assert project.id == target_project_id
    else:
        with pytest.raises(ProjectForbidden):
            await get_project(db_session, project_id=target_project_id, actor=actor)


@pytest.mark.parametrize(
    ("combo", "actor_group_attr", "target_project_attr", "cascade_on_access"),
    _COMBOS,
)
async def test_get_vulnerability_detail_cascade_matrix(
    db_session: AsyncSession,
    fixture: _Fixture,
    cascade_enabled: bool,
    combo: str,
    actor_group_attr: str,
    target_project_attr: str,
    cascade_on_access: bool,
) -> None:
    """`get_vulnerability_detail`, resolves finding -> scan -> project ->
    team, then the same `assert_team_access` gate `get_project` uses."""
    from services.vulnerability_service import VulnerabilityNotFound, get_vulnerability_detail

    target_project_id: uuid.UUID = getattr(fixture, target_project_attr)
    scan_id = await _seed_succeeded_scan(db_session, target_project_id)
    finding_id = await _seed_vulnerability_finding(db_session, scan_id=scan_id)

    user = await make_user(db_session)
    actor_group_id: uuid.UUID = getattr(fixture, actor_group_attr)
    actor = principal_for(user, team_ids=[actor_group_id])

    should_succeed = cascade_on_access if cascade_enabled else (
        actor_group_attr == target_project_attr.removeprefix("project_")
    )

    if should_succeed:
        detail = await get_vulnerability_detail(db_session, finding_id=finding_id, actor=actor)
        assert detail["id"] == finding_id
    else:
        # Existence-hide: cross-team reads 404, not 403 (see
        # `get_vulnerability_detail`'s own docstring).
        with pytest.raises(VulnerabilityNotFound):
            await get_vulnerability_detail(db_session, finding_id=finding_id, actor=actor)


async def test_get_project_super_admin_bypasses_both_flag_states(
    db_session: AsyncSession, fixture: _Fixture, cascade_enabled: bool
) -> None:
    from services.project_service import get_project

    user = await make_user(db_session, is_superuser=True)
    actor = principal_for(user, team_ids=[], role="super_admin")

    project = await get_project(db_session, project_id=fixture.project_s, actor=actor)
    assert project.id == fixture.project_s
