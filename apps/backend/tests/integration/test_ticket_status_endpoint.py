# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""POST /vulnerability_findings/{id}/ticket-status/refresh: route wiring (#385).

The orchestration logic itself is covered end-to-end against a real
Postgres session in ``test_ticket_status_service.py``; this file only
proves the HTTP layer maps it correctly (auth, status codes, response
shape).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from core.security import create_access_token
from models import (
    Component,
    ComponentVersion,
    Scan,
    ScanComponent,
    User,
    Vulnerability,
    VulnerabilityFinding,
)
from tests._db_required import migrate_to_head
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_team,
    make_user,
    unique_suffix,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


@pytest.fixture
async def app():  # noqa: ANN201
    from main import app as fastapi_app

    return fastapi_app


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:  # noqa: ANN001
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


def _bearer(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(subject=str(user.id), role=None)}"}


async def _factory(client: AsyncClient):  # noqa: ANN202
    app = client._transport.app  # type: ignore[attr-defined]
    factory = getattr(app.state, "session_factory", None)
    if factory is None:
        from core.db import _ensure_state

        factory = _ensure_state(app)
    return factory


async def _seed_finding(client: AsyncClient, *, ticket_url: str | None) -> tuple[str, User]:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session)
        await make_membership(session, user=user, team=team, role="developer")
        project = await make_project(session, team=team)
        scan = Scan(
            project_id=project.id,
            kind="source",
            status="succeeded",
            progress_percent=100,
            requested_by_user_id=user.id,
            scan_metadata={},
        )
        session.add(scan)
        await session.commit()
        await session.refresh(scan)

        suffix = unique_suffix()
        purl = f"pkg:npm/ticket-endpoint-{suffix}"
        component = Component(purl=purl, package_type="npm", name=f"ticket-endpoint-{suffix}")
        session.add(component)
        await session.commit()
        await session.refresh(component)

        version = ComponentVersion(
            component_id=component.id, version="1.0.0", purl_with_version=f"{purl}@1.0.0"
        )
        session.add(version)
        await session.commit()
        await session.refresh(version)

        session.add(
            ScanComponent(
                scan_id=scan.id, component_version_id=version.id, direct=True, raw_data={}
            )
        )
        vulnerability = Vulnerability(
            external_id=f"CVE-2024-{suffix}", source="NVD", severity="high", summary="fixture"
        )
        session.add(vulnerability)
        await session.commit()
        await session.refresh(vulnerability)

        finding = VulnerabilityFinding(
            scan_id=scan.id,
            component_version_id=version.id,
            vulnerability_id=vulnerability.id,
            status="new",
            ticket_url=ticket_url,
            ticket_key="PROJ-1" if ticket_url else None,
        )
        session.add(finding)
        await session.commit()
        await session.refresh(finding)
        finding_id = str(finding.id)
    return finding_id, user


def _url(finding_id: str) -> str:
    return f"/v1/vulnerability_findings/{finding_id}/ticket-status/refresh"


async def test_no_ticket_url_is_422(client: AsyncClient) -> None:
    finding_id, user = await _seed_finding(client, ticket_url=None)
    response = await client.post(_url(finding_id), headers=_bearer(user))
    assert response.status_code == 422, response.text


async def test_unauthenticated_call_is_401(client: AsyncClient) -> None:
    finding_id, _user = await _seed_finding(
        client, ticket_url="https://example.atlassian.net/browse/PROJ-1"
    )
    response = await client.post(_url(finding_id))
    assert response.status_code == 401


async def test_a_finding_in_another_team_is_404(client: AsyncClient) -> None:
    finding_id, _owner = await _seed_finding(
        client, ticket_url="https://example.atlassian.net/browse/PROJ-1"
    )
    factory = await _factory(client)
    async with factory() as session:
        outsider = await make_user(session)

    response = await client.post(_url(finding_id), headers=_bearer(outsider))
    assert response.status_code == 404


async def test_ssrf_rejected_url_still_returns_200_with_an_error_field(
    client: AsyncClient,
) -> None:
    """The attempt succeeds (the finding's check state is recorded); the
    OUTBOUND failure is data, not an HTTP error, see the service module's
    docstring for why. The resolved internal IP must never reach the caller
    (security review: it would turn this endpoint into an internal-network
    reconnaissance oracle for a developer-role actor)."""
    finding_id, user = await _seed_finding(
        client, ticket_url="http://169.254.169.254/browse/PROJ-1"
    )
    response = await client.post(_url(finding_id), headers=_bearer(user))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ticket_check_error"] is not None
    assert "169.254.169.254" not in body["ticket_check_error"]
    assert body["ticket_status"] is None
