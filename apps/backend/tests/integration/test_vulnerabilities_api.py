"""
Integration tests for vulnerability HTTP surface — Phase 3 PR #11.

Endpoints:
  - GET   /v1/projects/{project_id}/vulnerabilities
  - GET   /v1/vulnerability_findings/{finding_id}
  - PATCH /v1/vulnerability_findings/{finding_id}/status

Pins the wire format (RFC 7807 envelope on errors with `allowed_to` extension
for 422), the auth gate, and IDOR / role policy. Heavier behavioural coverage
(filter combinations, sort, audit derivation) lives in
`tests/unit/test_vulnerability_service.py`.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
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
        pytest.skip("DATABASE_URL not set — skip vulnerabilities API tests")
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
            "alembic upgrade head failed; vulnerabilities API tests cannot run\n"
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


async def _seed_scanned_project(client: AsyncClient, *, team_id: uuid.UUID):
    factory = await _factory(client)
    async with factory() as session:
        from sqlalchemy import select

        from models import Team

        team = (
            await session.execute(select(Team).where(Team.id == team_id))
        ).scalar_one()
        project = await make_project(session, team=team)
        scan = await make_scan(session, project=project, status="succeeded")
        project.latest_scan_id = scan.id
        project.updated_at = datetime.now(tz=UTC)
        await session.commit()
        await session.refresh(project)
        return project.id, scan.id


async def _seed_finding(
    client: AsyncClient,
    *,
    scan_id: uuid.UUID,
    severity: str = "high",
    cve_id: str | None = None,
    summary: str | None = None,
    initial_status: str = "new",
) -> uuid.UUID:
    """Insert one component_version + vulnerability + finding tied to scan_id."""
    factory = await _factory(client)
    async with factory() as session:
        from models import (
            Component,
            ComponentVersion,
            Vulnerability,
            VulnerabilityFinding,
        )

        suffix = uuid.uuid4().hex[:10]
        cname = f"pkg-{suffix}"
        purl = f"pkg:npm/{cname}"
        component = Component(purl=purl, package_type="npm", name=cname)
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
            external_id=cve_id or f"CVE-2099-API-{suffix}",
            source="NVD",
            severity=severity,
            summary=summary or f"summary {suffix}",
        )
        session.add(vuln)
        await session.commit()
        await session.refresh(vuln)

        finding = VulnerabilityFinding(
            scan_id=scan_id,
            component_version_id=cv.id,
            vulnerability_id=vuln.id,
            status=initial_status,
            analysis_state=initial_status,
        )
        session.add(finding)
        await session.commit()
        await session.refresh(finding)
        return finding.id


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------


async def test_list_without_auth_returns_401(client) -> None:
    response = await client.get(f"/v1/projects/{uuid.uuid4()}/vulnerabilities")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_detail_without_auth_returns_401(client) -> None:
    response = await client.get(f"/v1/vulnerability_findings/{uuid.uuid4()}")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_patch_without_auth_returns_401(client) -> None:
    response = await client.patch(
        f"/v1/vulnerability_findings/{uuid.uuid4()}/status",
        json={"status": "analyzing"},
    )
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# GET /v1/projects/{id}/vulnerabilities
# ---------------------------------------------------------------------------


async def test_list_happy_path_empty(client) -> None:
    _, team, user = await _seed_team_with_user(client)
    project_id, _ = await _seed_scanned_project(client, team_id=team.id)
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project_id}/vulnerabilities",
        headers=headers,
        params={"limit": 20, "offset": 0},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["limit"] == 20
    assert body["offset"] == 0
    assert body["total"] == 0
    assert body["items"] == []


async def test_list_returns_seeded_finding(client) -> None:
    _, team, user = await _seed_team_with_user(client)
    project_id, scan_id = await _seed_scanned_project(client, team_id=team.id)
    finding_id = await _seed_finding(client, scan_id=scan_id, severity="critical")
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project_id}/vulnerabilities",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == str(finding_id)
    assert body["items"][0]["severity"] == "critical"


async def test_list_multivalue_severity_query_param(client) -> None:
    _, team, user = await _seed_team_with_user(client)
    project_id, scan_id = await _seed_scanned_project(client, team_id=team.id)
    await _seed_finding(client, scan_id=scan_id, severity="critical")
    await _seed_finding(client, scan_id=scan_id, severity="high")
    await _seed_finding(client, scan_id=scan_id, severity="medium")
    headers = _bearer_for(user)

    # Repeat-key style: ?severity=critical&severity=high
    response = await client.get(
        f"/v1/projects/{project_id}/vulnerabilities",
        headers=headers,
        params=[("severity", "critical"), ("severity", "high")],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    severities = {item["severity"] for item in body["items"]}
    assert severities == {"critical", "high"}


async def test_list_sort_and_order_query_params_accepted(client) -> None:
    _, team, user = await _seed_team_with_user(client)
    project_id, _ = await _seed_scanned_project(client, team_id=team.id)
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project_id}/vulnerabilities",
        headers=headers,
        params={"sort": "cvss", "order": "asc"},
    )
    assert response.status_code == 200


async def test_list_invalid_sort_returns_422_problem(client) -> None:
    _, team, user = await _seed_team_with_user(client)
    project_id, _ = await _seed_scanned_project(client, team_id=team.id)
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project_id}/vulnerabilities",
        headers=headers,
        params={"sort": "BOGUS"},
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_list_other_team_returns_403_problem(client) -> None:
    _, my_team, my_user = await _seed_team_with_user(client)
    _, other_team, _ = await _seed_team_with_user(client)
    other_project_id, _ = await _seed_scanned_project(client, team_id=other_team.id)
    headers = _bearer_for(my_user)

    response = await client.get(
        f"/v1/projects/{other_project_id}/vulnerabilities", headers=headers
    )
    assert response.status_code == 403
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_list_unknown_project_returns_404_problem(client) -> None:
    _, _, admin = await _seed_team_with_user(client, is_superuser=True)
    headers = _bearer_for(admin)
    response = await client.get(
        f"/v1/projects/{uuid.uuid4()}/vulnerabilities", headers=headers
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_list_limit_over_cap_returns_422(client) -> None:
    _, team, user = await _seed_team_with_user(client)
    project_id, _ = await _seed_scanned_project(client, team_id=team.id)
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project_id}/vulnerabilities",
        headers=headers,
        params={"limit": 5000},
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_list_problem_envelope_has_required_fields(client) -> None:
    """Pin RFC 7807 fields on an error response."""
    _, my_team, my_user = await _seed_team_with_user(client)
    _, other_team, _ = await _seed_team_with_user(client)
    other_project_id, _ = await _seed_scanned_project(client, team_id=other_team.id)
    headers = _bearer_for(my_user)

    response = await client.get(
        f"/v1/projects/{other_project_id}/vulnerabilities", headers=headers
    )
    assert response.status_code == 403
    body = response.json()
    for key in ("type", "title", "status", "detail", "instance"):
        assert key in body, f"missing key {key} in problem body: {body}"
    assert body["status"] == 403


# ---------------------------------------------------------------------------
# GET /v1/vulnerability_findings/{id}
# ---------------------------------------------------------------------------


async def test_detail_happy_path(client) -> None:
    _, team, user = await _seed_team_with_user(client)
    project_id, scan_id = await _seed_scanned_project(client, team_id=team.id)
    finding_id = await _seed_finding(client, scan_id=scan_id)
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/vulnerability_findings/{finding_id}", headers=headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == str(finding_id)
    assert body["project_id"] == str(project_id)
    assert body["status"] == "new"
    # Synthesized initial entry must be present.
    assert len(body["status_history"]) >= 1
    assert body["status_history"][0]["previous_status"] is None
    assert body["status_history"][0]["new_status"] == "new"


async def test_detail_unknown_id_returns_404_problem(client) -> None:
    _, _, admin = await _seed_team_with_user(client, is_superuser=True)
    headers = _bearer_for(admin)

    response = await client.get(
        f"/v1/vulnerability_findings/{uuid.uuid4()}", headers=headers
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_detail_cross_team_returns_404_not_403(client) -> None:
    """IDOR: cross-team detail surfaces 404 to hide existence."""
    _, my_team, my_user = await _seed_team_with_user(client)
    _, other_team, _ = await _seed_team_with_user(client)
    _, scan_id = await _seed_scanned_project(client, team_id=other_team.id)
    finding_id = await _seed_finding(client, scan_id=scan_id)
    headers = _bearer_for(my_user)

    response = await client.get(
        f"/v1/vulnerability_findings/{finding_id}", headers=headers
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# PATCH /v1/vulnerability_findings/{id}/status
# ---------------------------------------------------------------------------


async def test_patch_happy_path_returns_full_detail(client) -> None:
    _, team, user = await _seed_team_with_user(client)
    _, scan_id = await _seed_scanned_project(client, team_id=team.id)
    finding_id = await _seed_finding(client, scan_id=scan_id)
    headers = _bearer_for(user)

    response = await client.patch(
        f"/v1/vulnerability_findings/{finding_id}/status",
        headers=headers,
        json={"status": "analyzing", "justification": "starting triage"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == str(finding_id)
    assert body["status"] == "analyzing"
    assert body["analysis_justification"] == "starting triage"


async def test_patch_idempotent_rejected_with_allowed_to_extension(client) -> None:
    """422 problem must carry `allowed_to` listing legal next states."""
    _, team, user = await _seed_team_with_user(client)
    _, scan_id = await _seed_scanned_project(client, team_id=team.id)
    finding_id = await _seed_finding(client, scan_id=scan_id)
    headers = _bearer_for(user)

    response = await client.patch(
        f"/v1/vulnerability_findings/{finding_id}/status",
        headers=headers,
        json={"status": "new"},  # already 'new'
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    body = response.json()
    assert "allowed_to" in body
    # `new` outgoing edges are analyzing + suppressed.
    assert sorted(body["allowed_to"]) == sorted(["analyzing", "suppressed"])


async def test_patch_developer_to_suppressed_returns_403(client) -> None:
    _, team, user = await _seed_team_with_user(client, role="developer")
    _, scan_id = await _seed_scanned_project(client, team_id=team.id)
    finding_id = await _seed_finding(client, scan_id=scan_id)
    headers = _bearer_for(user)

    response = await client.patch(
        f"/v1/vulnerability_findings/{finding_id}/status",
        headers=headers,
        json={"status": "suppressed"},
    )
    assert response.status_code == 403
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_patch_team_admin_can_suppress(client) -> None:
    _, team, admin_user = await _seed_team_with_user(client, role="team_admin")
    _, scan_id = await _seed_scanned_project(client, team_id=team.id)
    finding_id = await _seed_finding(client, scan_id=scan_id)
    headers = _bearer_for(admin_user)

    response = await client.patch(
        f"/v1/vulnerability_findings/{finding_id}/status",
        headers=headers,
        json={"status": "suppressed"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "suppressed"


async def test_patch_cross_team_returns_404(client) -> None:
    """Hide existence on cross-team PATCH."""
    _, my_team, my_user = await _seed_team_with_user(client)
    _, other_team, _ = await _seed_team_with_user(client)
    _, scan_id = await _seed_scanned_project(client, team_id=other_team.id)
    finding_id = await _seed_finding(client, scan_id=scan_id)
    headers = _bearer_for(my_user)

    response = await client.patch(
        f"/v1/vulnerability_findings/{finding_id}/status",
        headers=headers,
        json={"status": "analyzing"},
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_patch_missing_status_field_returns_422(client) -> None:
    _, team, user = await _seed_team_with_user(client)
    _, scan_id = await _seed_scanned_project(client, team_id=team.id)
    finding_id = await _seed_finding(client, scan_id=scan_id)
    headers = _bearer_for(user)

    response = await client.patch(
        f"/v1/vulnerability_findings/{finding_id}/status",
        headers=headers,
        json={"justification": "missing status"},
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_patch_justification_over_4000_chars_returns_422(client) -> None:
    _, team, user = await _seed_team_with_user(client)
    _, scan_id = await _seed_scanned_project(client, team_id=team.id)
    finding_id = await _seed_finding(client, scan_id=scan_id)
    headers = _bearer_for(user)

    response = await client.patch(
        f"/v1/vulnerability_findings/{finding_id}/status",
        headers=headers,
        json={"status": "analyzing", "justification": "x" * 4001},
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_patch_unknown_status_value_returns_422(client) -> None:
    _, team, user = await _seed_team_with_user(client)
    _, scan_id = await _seed_scanned_project(client, team_id=team.id)
    finding_id = await _seed_finding(client, scan_id=scan_id)
    headers = _bearer_for(user)

    response = await client.patch(
        f"/v1/vulnerability_findings/{finding_id}/status",
        headers=headers,
        json={"status": "not-a-real-status"},
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_patch_extra_field_rejected(client) -> None:
    """Pydantic config has extra='forbid'; unknown keys → 422."""
    _, team, user = await _seed_team_with_user(client)
    _, scan_id = await _seed_scanned_project(client, team_id=team.id)
    finding_id = await _seed_finding(client, scan_id=scan_id)
    headers = _bearer_for(user)

    response = await client.patch(
        f"/v1/vulnerability_findings/{finding_id}/status",
        headers=headers,
        json={"status": "analyzing", "rogue": "field"},
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_patch_optimistic_concurrency_mismatch_returns_409(client) -> None:
    _, team, user = await _seed_team_with_user(client)
    _, scan_id = await _seed_scanned_project(client, team_id=team.id)
    finding_id = await _seed_finding(client, scan_id=scan_id)
    headers = _bearer_for(user)

    # Pass an obviously stale token. The server compares ISO8601 round-tripped.
    stale = "2000-01-01T00:00:00+00:00"
    response = await client.patch(
        f"/v1/vulnerability_findings/{finding_id}/status",
        headers=headers,
        json={"status": "analyzing", "if_match": stale},
    )
    assert response.status_code == 409
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
