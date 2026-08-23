# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Paged search API — integration tests (S3).

Three concerns.

**Team isolation**, as with every cross-project surface: a leak here is a P0,
and this endpoint fans out across projects by design.

**The palette contract is untouched.** ``GET /v1/search`` and
``GET /v1/search/results`` are separate endpoints precisely so the palette's
response shape stays fixed; a test here asserts the palette still answers
exactly what it answered before, so a future edit that "unifies" them has to
break this deliberately rather than by accident.

**Which scan each kind reads.** Components, vulnerabilities, and licences all
read only each project's current (latest succeeded) scan
(:func:`services.scan_resolution.latest_succeeded_scan_select`). Before the
concurrency-scaling plan's Q2 (unit 22, 2026-08-22), components searched a
project's whole scan history while vulnerabilities and licences read only the
current scan: a deliberate asymmetry. Q2 closed it: a component that only
ever existed in an older, since-superseded scan now drops out exactly like a
CVE a newer scan already cleared.

Isolation note: the integration database is not truncated between tests and a
super-admin sees everything in it, so each test embeds a unique token in the
names it seeds and searches for that token alone.
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from core.security import create_access_token
from models import User
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_scan,
    make_team,
    make_user,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
PROBLEM_JSON = "application/problem+json"
# Real captured CycloneDX fixtures the SBOM-ingest pipeline tests already use
# (CLAUDE.md testing-guide rule 3: realistic-density scan history, not a
# hand-built single-component blob), reused here for the Q2 contract test.
_SBOM_FIXTURES = BACKEND_ROOT / "tests" / "fixtures" / "sbom_ingest"

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip search-results API tests")
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
            "alembic upgrade head failed; search-results tests cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture
def app():
    from main import app as fastapi_app

    return fastapi_app


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


def _token() -> str:
    return "sr" + uuid.uuid4().hex[:10]


def _bearer_for(user: User) -> dict[str, str]:
    role = "super_admin" if user.is_superuser else None
    return {
        "Authorization": f"Bearer {create_access_token(subject=str(user.id), role=role)}"
    }


async def _factory(client: AsyncClient):
    app = client._transport.app  # type: ignore[attr-defined]
    factory = getattr(app.state, "session_factory", None)
    if factory is None:
        from core.db import _ensure_state

        factory = _ensure_state(app)
    return factory


async def _seed_team_with_user(client: AsyncClient, *, is_superuser: bool = False):
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session, is_superuser=is_superuser)
        if not is_superuser:
            await make_membership(session, user=user, team=team, role="developer")
    return team, user


async def _seed_project(client: AsyncClient, *, team_id: uuid.UUID, name: str):
    factory = await _factory(client)
    async with factory() as session:
        from sqlalchemy import select

        from models import Team

        team = (
            await session.execute(select(Team).where(Team.id == team_id))
        ).scalar_one()
        project = await make_project(session, team=team, name=name)
        scan = await make_scan(session, project=project, status="succeeded")
        project.latest_scan_id = scan.id
        await session.commit()
        return project.id, scan.id


async def _add_scan(client: AsyncClient, *, project_id: uuid.UUID):
    factory = await _factory(client)
    async with factory() as session:
        from sqlalchemy import select

        from models import Project

        project = (
            await session.execute(select(Project).where(Project.id == project_id))
        ).scalar_one()
        scan = await make_scan(session, project=project, status="succeeded")
        scan.created_at = datetime.now(tz=UTC) + timedelta(minutes=10)
        project.latest_scan_id = scan.id
        await session.commit()
        await session.refresh(scan)
        return scan.id


def _persist_sbom_fixture(*, scan_id: uuid.UUID, fixture_name: str) -> None:
    """Persist a REAL captured CycloneDX SBOM fixture's components onto
    ``scan_id`` through the shipped persist function, not a hand-built
    minimal Component/ComponentVersion/ScanComponent row (CLAUDE.md
    testing-guide rule 3: realistic density, from
    ``tests/fixtures/sbom_ingest/``, is where defects like this one hide).

    Synchronous (:func:`tasks.scan_source.persist_sbom_components` takes a
    sync ``Session``), so this opens its own short-lived sync engine rather
    than reusing the test's async session factory, the same split
    ``tests/integration/scan/test_ingest_sbom_pipeline.py`` uses.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from core.config import database_url_sync
    from tasks.scan_source import persist_sbom_components

    sbom = json.loads((_SBOM_FIXTURES / fixture_name).read_text())
    engine = create_engine(database_url_sync(), pool_pre_ping=True, future=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = factory()
    try:
        persist_sbom_components(session, scan_uuid=scan_id, sbom=sbom)
        session.commit()
    finally:
        session.close()
        engine.dispose()


async def _seed_component(
    client: AsyncClient, *, scan_id: uuid.UUID, name: str, package_type: str = "npm"
) -> uuid.UUID:
    factory = await _factory(client)
    async with factory() as session:
        from models import Component, ComponentVersion, ScanComponent

        suffix = uuid.uuid4().hex[:8]
        purl = f"pkg:{package_type}/{name}-{suffix}"
        component = Component(purl=purl, package_type=package_type, name=name)
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
        session.add(
            ScanComponent(scan_id=scan_id, component_version_id=cv.id, direct=True)
        )
        await session.commit()
        return cv.id


async def _seed_vuln(
    client: AsyncClient,
    *,
    scan_id: uuid.UUID,
    cv_id: uuid.UUID,
    cve_id: str,
    severity: str = "high",
) -> None:
    factory = await _factory(client)
    async with factory() as session:
        from models import Vulnerability, VulnerabilityFinding

        vuln = Vulnerability(
            external_id=cve_id, source="NVD", severity=severity, summary=cve_id
        )
        session.add(vuln)
        await session.commit()
        await session.refresh(vuln)
        session.add(
            VulnerabilityFinding(
                scan_id=scan_id,
                component_version_id=cv_id,
                vulnerability_id=vuln.id,
                status="new",
            )
        )
        await session.commit()


async def _get(client: AsyncClient, user: User, **params):
    return await client.get(
        "/v1/search/results", params=params, headers=_bearer_for(user)
    )


# ---------------------------------------------------------------------------
# Team isolation
# ---------------------------------------------------------------------------


async def test_components_are_team_scoped_with_no_cross_leak(
    client: AsyncClient,
) -> None:
    token = _token()
    team_a, user_a = await _seed_team_with_user(client)
    team_b, user_b = await _seed_team_with_user(client)
    _, admin = await _seed_team_with_user(client, is_superuser=True)

    _, scan_a = await _seed_project(client, team_id=team_a.id, name=f"{token}-a")
    _, scan_b = await _seed_project(client, team_id=team_b.id, name=f"{token}-b")
    await _seed_component(client, scan_id=scan_a, name=f"{token}-alpha")
    await _seed_component(client, scan_id=scan_b, name=f"{token}-bravo")

    def names(payload):
        return {row["component_name"] for row in payload["items_components"]}

    res_a = await _get(client, user_a, kind="components", q=token)
    res_b = await _get(client, user_b, kind="components", q=token)
    res_admin = await _get(client, admin, kind="components", q=token)
    assert res_a.status_code == 200, res_a.text

    assert names(res_a.json()) == {f"{token}-alpha"}
    assert names(res_b.json()) == {f"{token}-bravo"}
    assert names(res_admin.json()) == {f"{token}-alpha", f"{token}-bravo"}


async def test_projects_kind_is_team_scoped(client: AsyncClient) -> None:
    token = _token()
    team_a, user_a = await _seed_team_with_user(client)
    team_b, _ = await _seed_team_with_user(client)
    await _seed_project(client, team_id=team_a.id, name=f"{token}-mine")
    await _seed_project(client, team_id=team_b.id, name=f"{token}-theirs")

    res = await _get(client, user_a, kind="projects", q=token)
    assert {row["project_name"] for row in res.json()["items_projects"]} == {
        f"{token}-mine"
    }


async def test_requires_authentication(client: AsyncClient) -> None:
    res = await client.get("/v1/search/results", params={"kind": "components", "q": "x"})
    assert res.status_code == 401


# ---------------------------------------------------------------------------
# The palette contract is untouched
# ---------------------------------------------------------------------------


async def test_the_palette_endpoint_still_answers_its_old_shape(
    client: AsyncClient,
) -> None:
    """`/v1/search` gained nothing. S3 added a sibling, not a parameter.

    Pinned here as well as in the palette's own suite so that a later change
    which folds the two together has to delete this test on purpose.
    """
    _, user = await _seed_team_with_user(client)
    res = await client.get(
        "/v1/search", params={"q": "a"}, headers=_bearer_for(user)
    )
    assert res.status_code == 200
    assert res.json() == {"query": "a", "components": [], "vulnerabilities": []}


# ---------------------------------------------------------------------------
# Which scan each kind reads
# ---------------------------------------------------------------------------


async def test_components_and_vulnerabilities_read_only_the_current_scan(
    client: AsyncClient,
) -> None:
    """A CVE, and since Q2 a component too, absent from the newest scan
    drops out.

    Before the concurrency-scaling plan's Q2 (unit 22), components searched a
    project's whole history while vulnerabilities read only the current scan:
    a deliberate asymmetry the service documented. Q2 closed it: a
    component that only ever existed in the OLDER scan must now drop out
    exactly like a cleared CVE does, while a component in the NEWER (current)
    scan stays findable.
    """
    token = _token()
    team, user = await _seed_team_with_user(client)
    project_id, old_scan = await _seed_project(client, team_id=team.id, name=f"{token}-p")
    cv_id = await _seed_component(client, scan_id=old_scan, name=f"{token}-oldpkg")
    cve_id = f"CVE-2099-{uuid.uuid4().hex[:8]}"
    await _seed_vuln(client, scan_id=old_scan, cv_id=cv_id, cve_id=cve_id)

    # A newer succeeded scan carrying a DIFFERENT, non-overlapping component
    # (not just the same name again): old and new must be distinguishable.
    new_scan = await _add_scan(client, project_id=project_id)
    await _seed_component(client, scan_id=new_scan, name=f"{token}-newpkg")

    vulns = await _get(client, user, kind="vulnerabilities", q=cve_id)
    assert vulns.json()["total"] == 0, "a cleared finding must not resurface"

    old_components = await _get(client, user, kind="components", q=f"{token}-oldpkg")
    assert old_components.json()["total"] == 0, (
        "an old-scan-only component must drop out of search (Q2 contract change)"
    )

    new_components = await _get(client, user, kind="components", q=f"{token}-newpkg")
    assert new_components.json()["total"] >= 1, "the current scan's package stays findable"


async def test_components_from_an_old_real_scan_drop_out_of_search(
    client: AsyncClient,
) -> None:
    """The Q2 contract change on the results page, from two REAL scans built
    off two disjoint captured CycloneDX fixtures (realistic multi-ecosystem
    density, CLAUDE.md testing-guide rule 3): the OLD scan carries
    ``realistic.cdx.json`` (lodash, minimist, conditional-lib, jinja2); the
    NEW (latest) scan carries ``centos7-rpm-no-os.cdx.json`` (openssl, glibc,
    curl), with no overlapping purls so presence/absence is unambiguous.
    """
    token = _token()
    team, user = await _seed_team_with_user(client)
    project_id, old_scan = await _seed_project(client, team_id=team.id, name=f"{token}-p")
    _persist_sbom_fixture(scan_id=old_scan, fixture_name="realistic.cdx.json")

    new_scan = await _add_scan(client, project_id=project_id)
    _persist_sbom_fixture(scan_id=new_scan, fixture_name="centos7-rpm-no-os.cdx.json")

    old_components = await _get(client, user, kind="components", q="lodash")
    assert old_components.json()["total"] == 0

    new_components = await _get(client, user, kind="components", q="openssl")
    names = {row["component_name"] for row in new_components.json()["items_components"]}
    assert "openssl" in names


async def test_components_with_no_current_scan_returns_empty_page(
    client: AsyncClient,
) -> None:
    """A team with no succeeded scan anywhere resolves to an empty scan-id
    set from ``latest_succeeded_scan_select``: the short-circuit branch
    ``_components`` takes before ever building the main join.
    """
    token = _token()
    _, user = await _seed_team_with_user(client)

    resp = await _get(client, user, kind="components", q=token)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 0
    assert body["items_components"] == []


# ---------------------------------------------------------------------------
# Paging, facets, validation
# ---------------------------------------------------------------------------


async def test_paging_reports_total_and_does_not_repeat_rows(
    client: AsyncClient,
) -> None:
    token = _token()
    team, user = await _seed_team_with_user(client)
    _, scan = await _seed_project(client, team_id=team.id, name=f"{token}-p")
    for index in range(3):
        await _seed_component(client, scan_id=scan, name=f"{token}-c{index}")

    first = await _get(client, user, kind="components", q=token, page=1, size=2)
    second = await _get(client, user, kind="components", q=token, page=2, size=2)
    assert first.json()["total"] == 3
    assert len(first.json()["items_components"]) == 2
    assert len(second.json()["items_components"]) == 1

    def ids(payload):
        return {
            (row["component_id"], row["version"]) for row in payload["items_components"]
        }

    assert not (ids(first.json()) & ids(second.json()))


async def test_component_facets_count_the_whole_match_not_the_page(
    client: AsyncClient,
) -> None:
    """A facet that only counted visible rows would misreport what a click does."""
    token = _token()
    team, user = await _seed_team_with_user(client)
    _, scan = await _seed_project(client, team_id=team.id, name=f"{token}-p")
    await _seed_component(client, scan_id=scan, name=f"{token}-js", package_type="npm")
    await _seed_component(client, scan_id=scan, name=f"{token}-py", package_type="pypi")
    await _seed_component(client, scan_id=scan, name=f"{token}-py2", package_type="pypi")

    res = await _get(client, user, kind="components", q=token, size=1)
    buckets = {
        bucket["value"]: bucket["count"]
        for bucket in res.json()["facets"]["package_type"]
    }
    assert buckets == {"npm": 1, "pypi": 2}
    assert len(res.json()["items_components"]) == 1


async def test_package_type_filter_narrows(client: AsyncClient) -> None:
    token = _token()
    team, user = await _seed_team_with_user(client)
    _, scan = await _seed_project(client, team_id=team.id, name=f"{token}-p")
    await _seed_component(client, scan_id=scan, name=f"{token}-js", package_type="npm")
    await _seed_component(client, scan_id=scan, name=f"{token}-py", package_type="pypi")

    res = await _get(client, user, kind="components", q=token, package_type="pypi")
    assert {row["component_name"] for row in res.json()["items_components"]} == {
        f"{token}-py"
    }


async def test_short_query_is_an_empty_200_not_a_422(client: AsyncClient) -> None:
    """The page fires this as the user types; an error after one keystroke is noise."""
    _, user = await _seed_team_with_user(client)
    res = await _get(client, user, kind="components", q="a")
    assert res.status_code == 200
    assert res.json()["total"] == 0
    assert res.json()["items_components"] == []


async def test_two_char_query_is_now_below_the_floor(client: AsyncClient) -> None:
    """Concurrency-scaling plan Q1: the floor moved from 2 to 3, here too.

    A 2-char query never reaches the database (the length gate short-circuits
    first), so seeding a component that would otherwise match and still
    getting an empty page is the point.
    """
    query = "ab"
    team, user = await _seed_team_with_user(client)
    _, scan = await _seed_project(client, team_id=team.id, name=f"{query}-p")
    await _seed_component(client, scan_id=scan, name=f"{query}-comp")

    res = await _get(client, user, kind="components", q=query)
    assert res.status_code == 200
    assert res.json()["total"] == 0
    assert res.json()["items_components"] == []


async def test_three_char_query_meets_the_floor(client: AsyncClient) -> None:
    """The other half of the Q1 contract: 3 characters still works normally.

    Asserts the seeded row is present rather than an exact total, since the
    integration DB is not truncated between tests and a 3-char substring has
    only 16**3 slots in the token alphabet, so an incidental match from
    unrelated data is plausible over a long-lived DB.
    """
    token = _token()
    query = token[2:5]  # 3 chars from the random part, not the fixed "sr" prefix
    team, user = await _seed_team_with_user(client)
    _, scan = await _seed_project(client, team_id=team.id, name=f"{token}-p")
    await _seed_component(client, scan_id=scan, name=f"{token}-comp")

    res = await _get(client, user, kind="components", q=query)
    assert res.status_code == 200
    names = {row["component_name"] for row in res.json()["items_components"]}
    assert f"{token}-comp" in names


async def test_unknown_kind_is_422(client: AsyncClient) -> None:
    _, user = await _seed_team_with_user(client)
    res = await _get(client, user, kind="bogus", q="lodash")
    assert res.status_code == 422


async def test_like_wildcards_are_escaped(client: AsyncClient) -> None:
    token = _token()
    team, user = await _seed_team_with_user(client)
    _, scan = await _seed_project(client, team_id=team.id, name=f"{token}-p")
    await _seed_component(client, scan_id=scan, name=f"{token}-100%-off")
    await _seed_component(client, scan_id=scan, name=f"{token}-plain")

    res = await _get(client, user, kind="components", q=f"{token}-100%")
    names = {row["component_name"] for row in res.json()["items_components"]}
    assert names == {f"{token}-100%-off"}, "'%' must match a percent sign, not anything"
