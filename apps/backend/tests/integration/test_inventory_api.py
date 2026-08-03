# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Organization-wide inventory API — integration tests (S2).

The dominant concern is TEAM ISOLATION. Every route here fans out across
projects on purpose, so the scope predicate is the only thing between an actor
and the rest of the deployment; a cross-team leak is a P0. The headline cases
seed matching data in two teams and assert each actor sees only its own, with
super-admin seeing both.

Second concern is the "in use" definition. A project whose newest scan attempt
FAILED must still contribute its last succeeded scan, and a package that was
removed in a newer scan must stop being reported. Those two are asserted
directly because they are the difference between this surface and a naive join
across every scan ever run.

Isolation note: the integration database is not truncated between tests and a
super-admin sees every project in it, so each test embeds a unique token in the
names it seeds and filters by that token — never by "the only row".
"""

from __future__ import annotations

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

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip inventory API tests")
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
            "alembic upgrade head failed; inventory API tests cannot run\n"
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
    """Short unique token embedded in seeded names + filtered on."""
    return "inv" + uuid.uuid4().hex[:10]


def _bearer_for(user: User) -> dict[str, str]:
    role = "super_admin" if user.is_superuser else None
    token = create_access_token(subject=str(user.id), role=role)
    return {"Authorization": f"Bearer {token}"}


async def _factory(client: AsyncClient):
    app = client._transport.app  # type: ignore[attr-defined]
    factory = getattr(app.state, "session_factory", None)
    if factory is None:
        from core.db import _ensure_state

        factory = _ensure_state(app)
    return factory


async def _seed_team_with_user(
    client: AsyncClient, *, role: str = "developer", is_superuser: bool = False
):
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session, is_superuser=is_superuser)
        if not is_superuser:
            await make_membership(session, user=user, team=team, role=role)
    return org, team, user


async def _seed_project(
    client: AsyncClient,
    *,
    team_id: uuid.UUID,
    scan_status: str = "succeeded",
    archived: bool = False,
):
    """A project with one scan of *scan_status*, plus the latest-attempt pointer."""
    factory = await _factory(client)
    async with factory() as session:
        from sqlalchemy import select

        from models import Team

        team = (
            await session.execute(select(Team).where(Team.id == team_id))
        ).scalar_one()
        project = await make_project(session, team=team, archived=archived)
        scan = await make_scan(session, project=project, status=scan_status)
        project.latest_scan_id = scan.id
        project.updated_at = datetime.now(tz=UTC)
        await session.commit()
        await session.refresh(project)
        return project.id, scan.id


async def _add_scan(
    client: AsyncClient,
    *,
    project_id: uuid.UUID,
    status: str,
    minutes_newer: int = 10,
):
    """Append a newer scan to an existing project and repoint latest_scan_id."""
    factory = await _factory(client)
    async with factory() as session:
        from sqlalchemy import select

        from models import Project

        project = (
            await session.execute(select(Project).where(Project.id == project_id))
        ).scalar_one()
        scan = await make_scan(session, project=project, status=status)
        scan.created_at = datetime.now(tz=UTC) + timedelta(minutes=minutes_newer)
        project.latest_scan_id = scan.id
        await session.commit()
        await session.refresh(scan)
        return scan.id


async def _seed_component(
    client: AsyncClient,
    *,
    scan_id: uuid.UUID,
    name: str,
    version: str = "1.0.0",
    package_type: str = "npm",
    component_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Component + ComponentVersion + ScanComponent attached to *scan_id*.

    Pass ``component_id`` to attach a second version (or the same package in a
    second project) to an existing component instead of minting a new one.
    """
    factory = await _factory(client)
    async with factory() as session:
        from sqlalchemy import select

        from models import Component, ComponentVersion, ScanComponent

        if component_id is None:
            suffix = uuid.uuid4().hex[:8]
            purl = f"pkg:{package_type}/{name}-{suffix}"
            component = Component(purl=purl, package_type=package_type, name=name)
            session.add(component)
            await session.commit()
            await session.refresh(component)
        else:
            component = (
                await session.execute(
                    select(Component).where(Component.id == component_id)
                )
            ).scalar_one()

        cv = (
            await session.execute(
                select(ComponentVersion)
                .where(ComponentVersion.component_id == component.id)
                .where(ComponentVersion.version == version)
            )
        ).scalar_one_or_none()
        if cv is None:
            cv = ComponentVersion(
                component_id=component.id,
                version=version,
                purl_with_version=f"{component.purl}@{version}",
            )
            session.add(cv)
            await session.commit()
            await session.refresh(cv)

        session.add(
            ScanComponent(scan_id=scan_id, component_version_id=cv.id, direct=True)
        )
        await session.commit()
        return component.id


async def _seed_finding(
    client: AsyncClient,
    *,
    scan_id: uuid.UUID,
    component_id: uuid.UUID,
    version: str,
    cve_id: str,
    severity: str = "high",
) -> None:
    """A CVE finding on an existing (component, version) inside *scan_id*."""
    factory = await _factory(client)
    async with factory() as session:
        from sqlalchemy import select

        from models import ComponentVersion, Vulnerability, VulnerabilityFinding

        cv = (
            await session.execute(
                select(ComponentVersion)
                .where(ComponentVersion.component_id == component_id)
                .where(ComponentVersion.version == version)
            )
        ).scalar_one()

        vuln = (
            await session.execute(
                select(Vulnerability).where(Vulnerability.external_id == cve_id)
            )
        ).scalar_one_or_none()
        if vuln is None:
            vuln = Vulnerability(
                external_id=cve_id, source="NVD", severity=severity, summary=cve_id
            )
            session.add(vuln)
            await session.commit()
            await session.refresh(vuln)

        session.add(
            VulnerabilityFinding(
                scan_id=scan_id,
                component_version_id=cv.id,
                vulnerability_id=vuln.id,
                status="new",
            )
        )
        await session.commit()


def _names(payload: dict) -> set[str]:
    return {item["name"] for item in payload["items"]}


# ---------------------------------------------------------------------------
# Team isolation — the P0 concern
# ---------------------------------------------------------------------------


async def test_inventory_is_team_scoped_with_no_cross_leak(client: AsyncClient) -> None:
    """Each team sees only its own packages; super-admin sees both."""
    token = _token()
    _, team_a, user_a = await _seed_team_with_user(client)
    _, team_b, user_b = await _seed_team_with_user(client)
    _, _, admin = await _seed_team_with_user(client, is_superuser=True)

    _, scan_a = await _seed_project(client, team_id=team_a.id)
    _, scan_b = await _seed_project(client, team_id=team_b.id)
    await _seed_component(client, scan_id=scan_a, name=f"{token}-alpha")
    await _seed_component(client, scan_id=scan_b, name=f"{token}-bravo")

    res_a = await client.get(
        "/v1/inventory/components", params={"q": token}, headers=_bearer_for(user_a)
    )
    res_b = await client.get(
        "/v1/inventory/components", params={"q": token}, headers=_bearer_for(user_b)
    )
    res_admin = await client.get(
        "/v1/inventory/components", params={"q": token}, headers=_bearer_for(admin)
    )
    assert res_a.status_code == 200, res_a.text
    assert res_b.status_code == 200
    assert res_admin.status_code == 200

    assert _names(res_a.json()) == {f"{token}-alpha"}
    assert _names(res_b.json()) == {f"{token}-bravo"}
    assert _names(res_admin.json()) == {f"{token}-alpha", f"{token}-bravo"}


async def test_component_usage_hides_another_teams_component(
    client: AsyncClient,
) -> None:
    """Probing a component only another team uses is 404, not 403 or 200.

    Permission must beat state: the actor learns nothing about whether the id
    exists elsewhere in the deployment.
    """
    token = _token()
    _, team_a, user_a = await _seed_team_with_user(client)
    _, team_b, user_b = await _seed_team_with_user(client)
    _, scan_b = await _seed_project(client, team_id=team_b.id)
    component_id = await _seed_component(client, scan_id=scan_b, name=f"{token}-only-b")

    owner = await client.get(
        f"/v1/inventory/components/{component_id}/projects",
        headers=_bearer_for(user_b),
    )
    assert owner.status_code == 200
    assert owner.json()["total"] == 1

    outsider = await client.get(
        f"/v1/inventory/components/{component_id}/projects",
        headers=_bearer_for(user_a),
    )
    assert outsider.status_code == 404
    assert outsider.headers["content-type"].startswith(PROBLEM_JSON)


async def test_vulnerability_impact_hides_another_teams_cve(
    client: AsyncClient,
) -> None:
    """Same existence-hiding contract on the CVE reverse lookup."""
    token = _token()
    cve_id = f"CVE-2099-{uuid.uuid4().hex[:8]}"
    _, team_a, user_a = await _seed_team_with_user(client)
    _, team_b, user_b = await _seed_team_with_user(client)
    _, scan_b = await _seed_project(client, team_id=team_b.id)
    component_id = await _seed_component(client, scan_id=scan_b, name=f"{token}-vuln")
    await _seed_finding(
        client,
        scan_id=scan_b,
        component_id=component_id,
        version="1.0.0",
        cve_id=cve_id,
    )

    owner = await client.get(
        f"/v1/inventory/vulnerabilities/{cve_id}/projects", headers=_bearer_for(user_b)
    )
    assert owner.status_code == 200
    assert owner.json()["total"] == 1

    outsider = await client.get(
        f"/v1/inventory/vulnerabilities/{cve_id}/projects", headers=_bearer_for(user_a)
    )
    assert outsider.status_code == 404
    assert outsider.headers["content-type"].startswith(PROBLEM_JSON)


async def test_unknown_ids_are_404_not_500(client: AsyncClient) -> None:
    _, _, user = await _seed_team_with_user(client)
    missing = await client.get(
        f"/v1/inventory/components/{uuid.uuid4()}/projects", headers=_bearer_for(user)
    )
    assert missing.status_code == 404
    no_cve = await client.get(
        "/v1/inventory/vulnerabilities/CVE-1999-00000/projects",
        headers=_bearer_for(user),
    )
    assert no_cve.status_code == 404


async def test_requires_authentication(client: AsyncClient) -> None:
    res = await client.get("/v1/inventory/components")
    assert res.status_code == 401


# ---------------------------------------------------------------------------
# "In use" definition — the difference from a naive all-scans join
# ---------------------------------------------------------------------------


async def test_a_failed_newer_scan_does_not_empty_the_inventory(
    client: AsyncClient,
) -> None:
    """The last SUCCEEDED scan is the posture, not the last attempt.

    Anchoring on ``Project.latest_scan_id`` would report zero components here —
    the verified bug that made ``scan_resolution`` exist.
    """
    token = _token()
    _, team, user = await _seed_team_with_user(client)
    project_id, good_scan = await _seed_project(client, team_id=team.id)
    await _seed_component(client, scan_id=good_scan, name=f"{token}-survivor")
    await _add_scan(client, project_id=project_id, status="failed")

    res = await client.get(
        "/v1/inventory/components", params={"q": token}, headers=_bearer_for(user)
    )
    assert res.status_code == 200
    assert _names(res.json()) == {f"{token}-survivor"}


async def test_a_package_removed_in_the_newest_scan_drops_out(
    client: AsyncClient,
) -> None:
    """Only the current scan counts — a naive all-scans join would keep it."""
    token = _token()
    _, team, user = await _seed_team_with_user(client)
    project_id, old_scan = await _seed_project(client, team_id=team.id)
    await _seed_component(client, scan_id=old_scan, name=f"{token}-removed")
    await _seed_component(client, scan_id=old_scan, name=f"{token}-kept")

    new_scan = await _add_scan(client, project_id=project_id, status="succeeded")
    # The newer scan re-declares only one of the two packages.
    await _seed_component(client, scan_id=new_scan, name=f"{token}-kept-again")

    res = await client.get(
        "/v1/inventory/components", params={"q": token}, headers=_bearer_for(user)
    )
    assert res.status_code == 200
    names = _names(res.json())
    assert names == {f"{token}-kept-again"}
    assert f"{token}-removed" not in names


async def test_archived_projects_are_excluded(client: AsyncClient) -> None:
    token = _token()
    _, team, user = await _seed_team_with_user(client)
    _, live_scan = await _seed_project(client, team_id=team.id)
    _, dead_scan = await _seed_project(client, team_id=team.id, archived=True)
    await _seed_component(client, scan_id=live_scan, name=f"{token}-live")
    await _seed_component(client, scan_id=dead_scan, name=f"{token}-archived")

    res = await client.get(
        "/v1/inventory/components", params={"q": token}, headers=_bearer_for(user)
    )
    assert _names(res.json()) == {f"{token}-live"}


async def test_a_project_with_no_succeeded_scan_contributes_nothing(
    client: AsyncClient,
) -> None:
    token = _token()
    _, team, user = await _seed_team_with_user(client)
    _, running_scan = await _seed_project(client, team_id=team.id, scan_status="running")
    await _seed_component(client, scan_id=running_scan, name=f"{token}-inflight")

    res = await client.get(
        "/v1/inventory/components", params={"q": token}, headers=_bearer_for(user)
    )
    assert res.json()["items"] == []


# ---------------------------------------------------------------------------
# Aggregation — the component grain
# ---------------------------------------------------------------------------


async def test_one_package_across_two_projects_is_one_row_counting_both(
    client: AsyncClient,
) -> None:
    """The whole point of the surface: spread, not repetition."""
    token = _token()
    _, team, user = await _seed_team_with_user(client)
    _, scan_one = await _seed_project(client, team_id=team.id)
    _, scan_two = await _seed_project(client, team_id=team.id)

    component_id = await _seed_component(
        client, scan_id=scan_one, name=f"{token}-shared", version="1.0.0"
    )
    await _seed_component(
        client,
        scan_id=scan_two,
        name=f"{token}-shared",
        version="2.0.0",
        component_id=component_id,
    )

    res = await client.get(
        "/v1/inventory/components", params={"q": token}, headers=_bearer_for(user)
    )
    items = res.json()["items"]
    assert len(items) == 1, items
    row = items[0]
    assert row["project_count"] == 2
    assert row["version_count"] == 2
    assert set(row["versions"]) == {"1.0.0", "2.0.0"}


async def test_one_cve_on_two_versions_counts_once(client: AsyncClient) -> None:
    """De-duplicated by vulnerability, not by finding row.

    A finding-row count would say 2 here. The per-project Components tab makes
    exactly that mistake (its COUNT rides a licence fan-out); this surface spans
    the whole organization, so the error would compound.
    """
    token = _token()
    cve_id = f"CVE-2099-{uuid.uuid4().hex[:8]}"
    _, team, user = await _seed_team_with_user(client)
    _, scan_one = await _seed_project(client, team_id=team.id)
    _, scan_two = await _seed_project(client, team_id=team.id)

    component_id = await _seed_component(
        client, scan_id=scan_one, name=f"{token}-dup", version="1.0.0"
    )
    await _seed_component(
        client,
        scan_id=scan_two,
        name=f"{token}-dup",
        version="2.0.0",
        component_id=component_id,
    )
    await _seed_finding(
        client,
        scan_id=scan_one,
        component_id=component_id,
        version="1.0.0",
        cve_id=cve_id,
        severity="critical",
    )
    await _seed_finding(
        client,
        scan_id=scan_two,
        component_id=component_id,
        version="2.0.0",
        cve_id=cve_id,
        severity="critical",
    )

    res = await client.get(
        "/v1/inventory/components", params={"q": token}, headers=_bearer_for(user)
    )
    row = res.json()["items"][0]
    assert row["vulnerability_count"] == 1, "same CVE on two versions is one CVE"
    assert row["severity_max"] == "critical"


async def test_severity_filter_narrows_by_worst_bucket(client: AsyncClient) -> None:
    token = _token()
    _, team, user = await _seed_team_with_user(client)
    _, scan = await _seed_project(client, team_id=team.id)
    crit = await _seed_component(client, scan_id=scan, name=f"{token}-crit")
    await _seed_component(client, scan_id=scan, name=f"{token}-clean")
    await _seed_finding(
        client,
        scan_id=scan,
        component_id=crit,
        version="1.0.0",
        cve_id=f"CVE-2099-{uuid.uuid4().hex[:8]}",
        severity="critical",
    )

    res = await client.get(
        "/v1/inventory/components",
        params={"q": token, "severity": "critical"},
        headers=_bearer_for(user),
    )
    assert _names(res.json()) == {f"{token}-crit"}


async def test_package_type_filter(client: AsyncClient) -> None:
    token = _token()
    _, team, user = await _seed_team_with_user(client)
    _, scan = await _seed_project(client, team_id=team.id)
    await _seed_component(client, scan_id=scan, name=f"{token}-js", package_type="npm")
    await _seed_component(client, scan_id=scan, name=f"{token}-py", package_type="pypi")

    res = await client.get(
        "/v1/inventory/components",
        params={"q": token, "package_type": "pypi"},
        headers=_bearer_for(user),
    )
    assert _names(res.json()) == {f"{token}-py"}


async def test_pagination_reports_total_beyond_the_page(client: AsyncClient) -> None:
    token = _token()
    _, team, user = await _seed_team_with_user(client)
    _, scan = await _seed_project(client, team_id=team.id)
    for i in range(3):
        await _seed_component(client, scan_id=scan, name=f"{token}-p{i}")

    res = await client.get(
        "/v1/inventory/components",
        params={"q": token, "limit": 2, "offset": 0},
        headers=_bearer_for(user),
    )
    body = res.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2
    assert body["limit"] == 2 and body["offset"] == 0

    page_two = await client.get(
        "/v1/inventory/components",
        params={"q": token, "limit": 2, "offset": 2},
        headers=_bearer_for(user),
    )
    assert len(page_two.json()["items"]) == 1
    # Pages must not overlap — the ordering carries a stable tie-break.
    assert not (_names(body) & _names(page_two.json()))
