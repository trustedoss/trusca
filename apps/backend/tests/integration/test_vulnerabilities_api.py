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
    details: str | None = None,
    references: object = None,
    initial_status: str = "new",
    epss_score: float | None = None,
    epss_percentile: float | None = None,
    reachable: bool | None = None,
    reachability_source: str | None = None,
    reachability_analyzed_at: datetime | None = None,
) -> uuid.UUID:
    """Insert one component_version + vulnerability + finding tied to scan_id.

    ``details`` and ``references`` are exposed for W10-D regression tests so a
    test can persist the legacy DT-era shape (``summary == details`` and a
    markdown-scalar references) and assert the API serialiser cleans it up.
    """
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

        vuln_kwargs: dict[str, object] = {
            "external_id": cve_id or f"CVE-2099-API-{suffix}",
            "source": "NVD",
            "severity": severity,
            "summary": summary or f"summary {suffix}",
            "epss_score": epss_score,
            "epss_percentile": epss_percentile,
        }
        if details is not None:
            vuln_kwargs["details"] = details
        if references is not None:
            vuln_kwargs["references"] = references
        vuln = Vulnerability(**vuln_kwargs)  # type: ignore[arg-type]
        session.add(vuln)
        await session.commit()
        await session.refresh(vuln)

        finding = VulnerabilityFinding(
            scan_id=scan_id,
            component_version_id=cv.id,
            vulnerability_id=vuln.id,
            status=initial_status,
            analysis_state=initial_status,
            reachable=reachable,
            reachability_source=reachability_source,
            reachability_analyzed_at=reachability_analyzed_at,
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
# W10-D regression — B2-001 / B2-002
# ---------------------------------------------------------------------------


async def test_detail_legacy_markdown_references_normalised_to_url_objects(client) -> None:
    """B2-001 — a pre-W6 row with a markdown-scalar references column serialises
    as the frontend's ``[{url: str}]`` wire shape, not a placeholder.

    Reproduces the production case found in the W10 audit: CVE-2024-45296 on
    fx-maven-node had ``references`` stored as a single string with markdown
    bullet links. The drawer showed "REF" labels with no clickable URL.
    """
    _, team, user = await _seed_team_with_user(client)
    _, scan_id = await _seed_scanned_project(client, team_id=team.id)
    legacy_markdown = (
        "* [https://github.com/foo/commit/aaa](https://github.com/foo/commit/aaa)\n"
        "* [https://github.com/foo/security/advisories/GHSA-XXXX]"
        "(https://github.com/foo/security/advisories/GHSA-XXXX)\n"
        "* [https://nvd.nist.gov/vuln/detail/CVE-2099-LEG]"
        "(https://nvd.nist.gov/vuln/detail/CVE-2099-LEG)"
    )
    finding_id = await _seed_finding(
        client,
        scan_id=scan_id,
        references=legacy_markdown,
    )
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/vulnerability_findings/{finding_id}", headers=headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    refs = body["references"]
    assert isinstance(refs, list), refs
    # Every entry is a {url: str} object.
    for entry in refs:
        assert isinstance(entry, dict), entry
        assert isinstance(entry["url"], str) and entry["url"].startswith(
            ("http://", "https://")
        )
    urls = [r["url"] for r in refs]
    assert urls == [
        "https://github.com/foo/commit/aaa",
        "https://github.com/foo/security/advisories/GHSA-XXXX",
        "https://nvd.nist.gov/vuln/detail/CVE-2099-LEG",
    ]


async def test_detail_clean_list_references_serialises_as_objects(client) -> None:
    """The W6+ shape — list[str] — wires through as {url} objects too."""
    _, team, user = await _seed_team_with_user(client)
    _, scan_id = await _seed_scanned_project(client, team_id=team.id)
    finding_id = await _seed_finding(
        client,
        scan_id=scan_id,
        references=[
            "https://example.org/1",
            "https://example.org/2",
        ],
    )
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/vulnerability_findings/{finding_id}", headers=headers
    )
    assert response.status_code == 200
    refs = response.json()["references"]
    assert refs == [
        {"url": "https://example.org/1"},
        {"url": "https://example.org/2"},
    ]


async def test_detail_legacy_dangerous_refs_dropped_at_api(client) -> None:
    """A legacy markdown-scalar containing dangerous schemes — javascript:,
    file:, data: — must NEVER round-trip to the drawer. Only http(s) URLs
    survive. Defence in depth: the drawer also runs ``isSafeUrl`` on each
    entry, but we drop them earlier.
    """
    _, team, user = await _seed_team_with_user(client)
    _, scan_id = await _seed_scanned_project(client, team_id=team.id)
    legacy_with_evil = (
        "* [evil](javascript:alert(1))\n"
        "* [safe](https://safe.example/ok)\n"
        "* [also-evil](data:text/html,<script>alert(1)</script>)\n"
        "* [also-safe](https://safe.example/ok-2)"
    )
    finding_id = await _seed_finding(
        client, scan_id=scan_id, references=legacy_with_evil
    )
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/vulnerability_findings/{finding_id}", headers=headers
    )
    assert response.status_code == 200
    refs = response.json()["references"]
    urls = [r["url"] for r in refs]
    assert urls == [
        "https://safe.example/ok",
        "https://safe.example/ok-2",
    ]


async def test_detail_summary_equals_details_collapses(client) -> None:
    """B2-002 — when summary and details hold the same text the API returns
    ``details=null`` so the drawer renders the paragraph exactly once.
    """
    _, team, user = await _seed_team_with_user(client)
    _, scan_id = await _seed_scanned_project(client, team_id=team.id)
    duplicate_text = (
        "path-to-regexp turns path strings into a regular expressions. "
        "In certain cases, the generated regular expression is vulnerable to "
        "ReDoS."
    )
    finding_id = await _seed_finding(
        client,
        scan_id=scan_id,
        summary=duplicate_text,
        details=duplicate_text,
    )
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/vulnerability_findings/{finding_id}", headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["summary"] == duplicate_text
    assert body["details"] is None


async def test_detail_distinct_summary_and_details_both_preserved(client) -> None:
    """A fresh row with distinct summary / details — both pass through."""
    _, team, user = await _seed_team_with_user(client)
    _, scan_id = await _seed_scanned_project(client, team_id=team.id)
    finding_id = await _seed_finding(
        client,
        scan_id=scan_id,
        summary="Short title",
        details="Much longer description text.",
    )
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/vulnerability_findings/{finding_id}", headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["summary"] == "Short title"
    assert body["details"] == "Much longer description text."


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


async def test_patch_idempotent_noop_returns_200_unchanged(client) -> None:
    """M-26: re-PATCHing the current status is an idempotent no-op → 200 with
    the unchanged finding, not a 422."""
    _, team, user = await _seed_team_with_user(client)
    _, scan_id = await _seed_scanned_project(client, team_id=team.id)
    finding_id = await _seed_finding(client, scan_id=scan_id)
    headers = _bearer_for(user)

    response = await client.patch(
        f"/v1/vulnerability_findings/{finding_id}/status",
        headers=headers,
        json={"status": "new"},  # already 'new'
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "new"


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


# ---------------------------------------------------------------------------
# EPSS exposure (v2.1) — response fields, sort=epss, min_epss filter (incl.
# adversarial out-of-range → 422).
# ---------------------------------------------------------------------------


async def test_list_response_carries_epss_fields(client) -> None:
    _, team, user = await _seed_team_with_user(client)
    project_id, scan_id = await _seed_scanned_project(client, team_id=team.id)
    await _seed_finding(
        client, scan_id=scan_id, severity="high", epss_score=0.97123, epss_percentile=0.99412
    )
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project_id}/vulnerabilities", headers=headers
    )
    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["epss_score"] == pytest.approx(0.97123)
    assert item["epss_percentile"] == pytest.approx(0.99412)


async def test_detail_response_carries_epss_fields(client) -> None:
    _, team, user = await _seed_team_with_user(client)
    _, scan_id = await _seed_scanned_project(client, team_id=team.id)
    finding_id = await _seed_finding(
        client, scan_id=scan_id, epss_score=0.12345, epss_percentile=0.54321
    )
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/vulnerability_findings/{finding_id}", headers=headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["epss_score"] == pytest.approx(0.12345)
    assert body["epss_percentile"] == pytest.approx(0.54321)


async def test_list_sort_epss_accepted(client) -> None:
    _, team, user = await _seed_team_with_user(client)
    project_id, _ = await _seed_scanned_project(client, team_id=team.id)
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project_id}/vulnerabilities",
        headers=headers,
        params={"sort": "epss", "order": "desc"},
    )
    assert response.status_code == 200


async def test_list_min_epss_filter_applied(client) -> None:
    _, team, user = await _seed_team_with_user(client)
    project_id, scan_id = await _seed_scanned_project(client, team_id=team.id)
    await _seed_finding(client, scan_id=scan_id, cve_id=None, epss_score=0.90)
    await _seed_finding(client, scan_id=scan_id, cve_id=None, epss_score=0.10)
    await _seed_finding(client, scan_id=scan_id, cve_id=None, epss_score=None)
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project_id}/vulnerabilities",
        headers=headers,
        params={"min_epss": 0.5},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["epss_score"] == pytest.approx(0.90)


async def test_list_min_epss_out_of_range_returns_422(client) -> None:
    """Adversarial: min_epss outside [0, 1] is rejected at the query layer."""
    _, team, user = await _seed_team_with_user(client)
    project_id, _ = await _seed_scanned_project(client, team_id=team.id)
    headers = _bearer_for(user)

    for bad in (1.5, -0.1):
        response = await client.get(
            f"/v1/projects/{project_id}/vulnerabilities",
            headers=headers,
            params={"min_epss": bad},
        )
        assert response.status_code == 422, (bad, response.text)
        assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_list_min_epss_boundary_values_accepted(client) -> None:
    """0 and 1 are valid boundaries (ge=0, le=1)."""
    _, team, user = await _seed_team_with_user(client)
    project_id, _ = await _seed_scanned_project(client, team_id=team.id)
    headers = _bearer_for(user)

    for ok in (0, 1):
        response = await client.get(
            f"/v1/projects/{project_id}/vulnerabilities",
            headers=headers,
            params={"min_epss": ok},
        )
        assert response.status_code == 200, (ok, response.text)


# ---------------------------------------------------------------------------
# Reachability (v2.3 r2) — response fields, ?reachable= filter (true/false/
# unknown + bad token → 422), sort=reachable.
# ---------------------------------------------------------------------------


async def test_list_response_carries_reachability_fields(client) -> None:
    _, team, user = await _seed_team_with_user(client)
    project_id, scan_id = await _seed_scanned_project(client, team_id=team.id)
    analyzed = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
    await _seed_finding(
        client,
        scan_id=scan_id,
        reachable=True,
        reachability_source="govulncheck",
        reachability_analyzed_at=analyzed,
    )
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project_id}/vulnerabilities", headers=headers
    )
    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["reachable"] is True
    assert item["reachability_source"] == "govulncheck"
    assert item["reachability_analyzed_at"] is not None


async def test_detail_response_carries_reachability_fields(client) -> None:
    _, team, user = await _seed_team_with_user(client)
    _, scan_id = await _seed_scanned_project(client, team_id=team.id)
    finding_id = await _seed_finding(
        client, scan_id=scan_id, reachable=False, reachability_source="govulncheck"
    )
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/vulnerability_findings/{finding_id}", headers=headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reachable"] is False
    assert body["reachability_source"] == "govulncheck"


async def test_list_reachable_filter_true(client) -> None:
    _, team, user = await _seed_team_with_user(client)
    project_id, scan_id = await _seed_scanned_project(client, team_id=team.id)
    rid_true = await _seed_finding(client, scan_id=scan_id, reachable=True)
    await _seed_finding(client, scan_id=scan_id, reachable=False)
    await _seed_finding(client, scan_id=scan_id, reachable=None)
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project_id}/vulnerabilities",
        headers=headers,
        params={"reachable": "true"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == str(rid_true)


async def test_list_reachable_filter_unknown_matches_null(client) -> None:
    _, team, user = await _seed_team_with_user(client)
    project_id, scan_id = await _seed_scanned_project(client, team_id=team.id)
    await _seed_finding(client, scan_id=scan_id, reachable=True)
    await _seed_finding(client, scan_id=scan_id, reachable=False)
    rid_null = await _seed_finding(client, scan_id=scan_id, reachable=None)
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project_id}/vulnerabilities",
        headers=headers,
        params={"reachable": "unknown"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == str(rid_null)
    assert body["items"][0]["reachable"] is None


async def test_list_reachable_bad_token_returns_422(client) -> None:
    _, team, user = await _seed_team_with_user(client)
    project_id, _ = await _seed_scanned_project(client, team_id=team.id)
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project_id}/vulnerabilities",
        headers=headers,
        params={"reachable": "maybe"},
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_list_sort_reachable_ranks_reachable_first(client) -> None:
    _, team, user = await _seed_team_with_user(client)
    project_id, scan_id = await _seed_scanned_project(client, team_id=team.id)
    rid_true = await _seed_finding(client, scan_id=scan_id, reachable=True)
    rid_false = await _seed_finding(client, scan_id=scan_id, reachable=False)
    rid_null = await _seed_finding(client, scan_id=scan_id, reachable=None)
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project_id}/vulnerabilities",
        headers=headers,
        params={"sort": "reachable", "order": "desc", "limit": 100},
    )
    assert response.status_code == 200, response.text
    order = [i["id"] for i in response.json()["items"]]
    assert order == [str(rid_true), str(rid_null), str(rid_false)]


# ---------------------------------------------------------------------------
# POST /v1/projects/{id}/vulnerabilities:bulk-transition — W2 #33b
# ---------------------------------------------------------------------------


async def test_bulk_transition_without_auth_returns_401(client) -> None:
    response = await client.post(
        f"/v1/projects/{uuid.uuid4()}/vulnerabilities:bulk-transition",
        json={"finding_ids": [str(uuid.uuid4())], "target_status": "analyzing"},
    )
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_bulk_transition_happy_path_returns_envelope_with_per_row_results(
    client,
) -> None:
    """Happy path: 3 valid ids in one project → 200, succeeded=3, failed=0."""
    _, team, user = await _seed_team_with_user(client)
    project_id, scan_id = await _seed_scanned_project(client, team_id=team.id)
    ids = [
        await _seed_finding(client, scan_id=scan_id) for _ in range(3)
    ]
    headers = _bearer_for(user)

    response = await client.post(
        f"/v1/projects/{project_id}/vulnerabilities:bulk-transition",
        headers=headers,
        json={
            "finding_ids": [str(fid) for fid in ids],
            "target_status": "analyzing",
            "justification": "bulk via API",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["target_status"] == "analyzing"
    assert body["total"] == 3
    assert body["succeeded"] == 3
    assert body["failed"] == 0
    assert {r["finding_id"] for r in body["results"]} == {str(fid) for fid in ids}
    assert all(r["success"] is True and r["status_code"] == 200 for r in body["results"])


async def test_bulk_transition_partial_failure_returns_200_with_mixed_rows(
    client,
) -> None:
    """One transitioned (200), one already-at-target no-op (200 success), one
    missing (404) → envelope 200, succeeded=2 (M-29)."""
    _, team, user = await _seed_team_with_user(client)
    project_id, scan_id = await _seed_scanned_project(client, team_id=team.id)
    ok_id = await _seed_finding(client, scan_id=scan_id, initial_status="new")
    idem_id = await _seed_finding(client, scan_id=scan_id, initial_status="analyzing")
    missing_id = str(uuid.uuid4())
    headers = _bearer_for(user)

    response = await client.post(
        f"/v1/projects/{project_id}/vulnerabilities:bulk-transition",
        headers=headers,
        json={
            "finding_ids": [str(ok_id), str(idem_id), missing_id],
            "target_status": "analyzing",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 3
    assert body["succeeded"] == 2
    assert body["failed"] == 1

    by_id = {r["finding_id"]: r for r in body["results"]}
    assert by_id[str(ok_id)]["success"] is True
    assert by_id[str(ok_id)]["status_code"] == 200
    # M-29: the no-op row is a success coded already_at_target, not a 422.
    assert by_id[str(idem_id)]["success"] is True
    assert by_id[str(idem_id)]["status_code"] == 200
    assert by_id[str(idem_id)]["error"] == "already_at_target"
    assert by_id[missing_id]["status_code"] == 404


async def test_bulk_transition_developer_to_suppressed_is_per_row_403(client) -> None:
    """Developer attempts a bulk → suppressed: row reports 403, envelope is 200."""
    _, team, user = await _seed_team_with_user(client, role="developer")
    project_id, scan_id = await _seed_scanned_project(client, team_id=team.id)
    fid = await _seed_finding(client, scan_id=scan_id)
    headers = _bearer_for(user)

    response = await client.post(
        f"/v1/projects/{project_id}/vulnerabilities:bulk-transition",
        headers=headers,
        json={"finding_ids": [str(fid)], "target_status": "suppressed"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["succeeded"] == 0
    assert body["results"][0]["status_code"] == 403
    assert body["results"][0]["error"] == "forbidden"


async def test_bulk_transition_team_admin_can_suppress_via_bulk(client) -> None:
    _, team, admin = await _seed_team_with_user(client, role="team_admin")
    project_id, scan_id = await _seed_scanned_project(client, team_id=team.id)
    fid = await _seed_finding(client, scan_id=scan_id)
    headers = _bearer_for(admin)

    response = await client.post(
        f"/v1/projects/{project_id}/vulnerabilities:bulk-transition",
        headers=headers,
        json={"finding_ids": [str(fid)], "target_status": "suppressed"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["succeeded"] == 1


async def test_bulk_transition_cross_team_envelope_404(client) -> None:
    """Caller not in the project's team → envelope 404 (existence-hide)."""
    _, my_team, my_user = await _seed_team_with_user(client)
    _, other_team, _ = await _seed_team_with_user(client)
    project_id, scan_id = await _seed_scanned_project(client, team_id=other_team.id)
    fid = await _seed_finding(client, scan_id=scan_id)
    headers = _bearer_for(my_user)

    response = await client.post(
        f"/v1/projects/{project_id}/vulnerabilities:bulk-transition",
        headers=headers,
        json={"finding_ids": [str(fid)], "target_status": "analyzing"},
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_bulk_transition_empty_finding_ids_returns_422(client) -> None:
    """Pydantic min_length=1 → 422 problem envelope."""
    _, team, user = await _seed_team_with_user(client)
    project_id, _ = await _seed_scanned_project(client, team_id=team.id)
    headers = _bearer_for(user)

    response = await client.post(
        f"/v1/projects/{project_id}/vulnerabilities:bulk-transition",
        headers=headers,
        json={"finding_ids": [], "target_status": "analyzing"},
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_bulk_transition_over_cap_returns_422(client) -> None:
    """Pydantic max_length=200 enforces cap at the envelope boundary."""
    _, team, user = await _seed_team_with_user(client)
    project_id, _ = await _seed_scanned_project(client, team_id=team.id)
    headers = _bearer_for(user)

    too_many = [str(uuid.uuid4()) for _ in range(201)]
    response = await client.post(
        f"/v1/projects/{project_id}/vulnerabilities:bulk-transition",
        headers=headers,
        json={"finding_ids": too_many, "target_status": "analyzing"},
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_bulk_transition_unknown_status_returns_422(client) -> None:
    _, team, user = await _seed_team_with_user(client)
    project_id, scan_id = await _seed_scanned_project(client, team_id=team.id)
    fid = await _seed_finding(client, scan_id=scan_id)
    headers = _bearer_for(user)

    response = await client.post(
        f"/v1/projects/{project_id}/vulnerabilities:bulk-transition",
        headers=headers,
        json={"finding_ids": [str(fid)], "target_status": "not-a-status"},
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_bulk_transition_extra_field_rejected(client) -> None:
    """``extra='forbid'`` rejects unknown body keys."""
    _, team, user = await _seed_team_with_user(client)
    project_id, scan_id = await _seed_scanned_project(client, team_id=team.id)
    fid = await _seed_finding(client, scan_id=scan_id)
    headers = _bearer_for(user)

    response = await client.post(
        f"/v1/projects/{project_id}/vulnerabilities:bulk-transition",
        headers=headers,
        json={
            "finding_ids": [str(fid)],
            "target_status": "analyzing",
            "rogue": "field",
        },
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
