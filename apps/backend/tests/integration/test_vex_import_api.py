"""
Integration tests for the VEX import HTTP surface + service — v2.1 Track A (A2).

Endpoint:
  - POST /v1/projects/{project_id}/vex/import   (multipart file upload)

These are DB-backed (real Postgres) because the import resolves statements
against ``vulnerability_findings`` rows and applies the transition state
machine — mocking the session would test the mock, not the SQL/transition.
Skipped when ``DATABASE_URL`` is unset.

Pins:
  - happy path: a VEX statement transitions the matching finding;
  - multi-step legality: a `new` finding reaches `not_affected` via the legal
    path (new → analyzing → not_affected), audit trail records both hops;
  - round-trip: export A1 → import that document → status unchanged (no-op);
  - idempotency: importing the same doc twice = second is all-skip;
  - RBAC: developer (member, no team_admin) → 403; outsider → 404; anon → 401;
  - matching skips: unknown vuln, unknown purl, ambiguous;
  - whole-document failures → RFC 7807 422 (broken JSON / unknown format).
"""

from __future__ import annotations

import json
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
    unique_suffix,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
PROBLEM_JSON = "application/problem+json"

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip vex import API tests")
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
            f"alembic upgrade head failed; vex import API tests cannot run\n"
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


async def _seed_project(
    client: AsyncClient,
    *,
    role: str = "team_admin",
    is_superuser: bool = False,
):
    """A team + user(role) + project with a succeeded latest scan."""
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session, is_superuser=is_superuser)
        if not is_superuser:
            await make_membership(session, user=user, team=team, role=role)
        project = await make_project(session, team=team)
        scan = await make_scan(session, project=project, status="succeeded")
        project.latest_scan_id = scan.id
        project.updated_at = datetime.now(tz=UTC)
        await session.commit()
        await session.refresh(project)
    return team, user, project, scan


async def _seed_finding(
    client: AsyncClient,
    *,
    scan_id: uuid.UUID,
    status: str,
    cve_id: str,
    purl_name: str | None = None,
):
    """Insert one (component_version, vulnerability, finding) into the scan."""
    from models import Component, ComponentVersion, Vulnerability, VulnerabilityFinding

    suffix = unique_suffix()
    name = purl_name or f"pkg-{suffix}"
    purl = f"pkg:npm/{name}"
    purl_v = f"{purl}@1.0.0"

    factory = await _factory(client)
    async with factory() as session:
        component = Component(purl=purl, package_type="npm", name=name)
        session.add(component)
        await session.commit()
        await session.refresh(component)

        cv = ComponentVersion(
            component_id=component.id, version="1.0.0", purl_with_version=purl_v
        )
        session.add(cv)
        await session.commit()
        await session.refresh(cv)

        vuln = Vulnerability(
            external_id=cve_id, source="NVD", severity="high", summary="t"
        )
        session.add(vuln)
        await session.commit()
        await session.refresh(vuln)

        vf = VulnerabilityFinding(
            scan_id=scan_id,
            component_version_id=cv.id,
            vulnerability_id=vuln.id,
            status=status,
            analysis_state=status,
        )
        session.add(vf)
        await session.commit()
        await session.refresh(vf)
        return vf.id, purl_v, cve_id


async def _read_finding(client: AsyncClient, finding_id: uuid.UUID):
    from models import VulnerabilityFinding

    factory = await _factory(client)
    async with factory() as session:
        return await session.get(VulnerabilityFinding, finding_id)


def _openvex_bytes(statements: list[dict]) -> bytes:
    doc = {
        "@context": "https://openvex.dev/ns/v0.2.0",
        "@id": "https://trustedoss.io/vex/test/doc",
        "author": "tester",
        "timestamp": "2025-01-02T03:04:05.000Z",
        "version": 1,
        "statements": statements,
    }
    return json.dumps(doc).encode("utf-8")


def _upload(raw: bytes, filename: str = "vex.json"):
    return {"upload": (filename, raw, "application/json")}


# ---------------------------------------------------------------------------
# Auth / RBAC
# ---------------------------------------------------------------------------


async def test_import_without_auth_returns_401(client: AsyncClient) -> None:
    response = await client.post(
        f"/v1/projects/{uuid.uuid4()}/vex/import",
        files=_upload(_openvex_bytes([])),
    )
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_import_developer_member_forbidden_403(client: AsyncClient) -> None:
    """A same-team developer (member but not team_admin) sees 403, not 404 —
    they already know the project exists."""
    _, user, project, _ = await _seed_project(client, role="developer")
    headers = _bearer_for(user)
    response = await client.post(
        f"/v1/projects/{project.id}/vex/import",
        headers=headers,
        files=_upload(_openvex_bytes([])),
    )
    assert response.status_code == 403, response.text
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    assert response.json()["title"] == "Forbidden"


async def test_import_outsider_returns_404_existence_hide(client: AsyncClient) -> None:
    _, _, target_project, _ = await _seed_project(client, role="team_admin")
    _, outsider, _, _ = await _seed_project(client, role="team_admin")
    headers = _bearer_for(outsider)
    response = await client.post(
        f"/v1/projects/{target_project.id}/vex/import",
        headers=headers,
        files=_upload(_openvex_bytes([])),
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_import_unknown_project_returns_404(client: AsyncClient) -> None:
    _, admin, _, _ = await _seed_project(client, is_superuser=True)
    headers = _bearer_for(admin)
    response = await client.post(
        f"/v1/projects/{uuid.uuid4()}/vex/import",
        headers=headers,
        files=_upload(_openvex_bytes([])),
    )
    assert response.status_code == 404


async def test_import_super_admin_allowed(client: AsyncClient) -> None:
    _, _, project, scan = await _seed_project(client, role="team_admin")
    _, admin, _, _ = await _seed_project(client, is_superuser=True)
    headers = _bearer_for(admin)
    response = await client.post(
        f"/v1/projects/{project.id}/vex/import",
        headers=headers,
        files=_upload(_openvex_bytes([])),
    )
    assert response.status_code == 200, response.text


# ---------------------------------------------------------------------------
# Whole-document failures → RFC 7807
# ---------------------------------------------------------------------------


async def test_import_broken_json_returns_422(client: AsyncClient) -> None:
    _, user, project, _ = await _seed_project(client, role="team_admin")
    headers = _bearer_for(user)
    response = await client.post(
        f"/v1/projects/{project.id}/vex/import",
        headers=headers,
        files=_upload(b"{not valid json"),
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    assert response.json()["title"] == "Malformed VEX Document"


async def test_import_unknown_format_returns_422(client: AsyncClient) -> None:
    _, user, project, _ = await _seed_project(client, role="team_admin")
    headers = _bearer_for(user)
    response = await client.post(
        f"/v1/projects/{project.id}/vex/import",
        headers=headers,
        files=_upload(json.dumps({"random": "object"}).encode()),
    )
    assert response.status_code == 422
    assert response.json()["title"] == "Unsupported VEX Format"


# ---------------------------------------------------------------------------
# Happy path — transition + provenance
# ---------------------------------------------------------------------------


async def test_import_transitions_matching_finding(client: AsyncClient) -> None:
    _, user, project, scan = await _seed_project(client, role="team_admin")
    headers = _bearer_for(user)
    cve = f"CVE-2099-{unique_suffix()}"
    finding_id, purl, _ = await _seed_finding(
        client, scan_id=scan.id, status="analyzing", cve_id=cve
    )

    raw = _openvex_bytes(
        [
            {
                "vulnerability": {"name": cve},
                "products": [{"@id": purl}],
                "status": "not_affected",
                "impact_statement": "not reachable in our config",
            }
        ]
    )
    response = await client.post(
        f"/v1/projects/{project.id}/vex/import", headers=headers, files=_upload(raw)
    )
    assert response.status_code == 200, response.text
    summary = response.json()
    assert summary["format"] == "openvex"
    assert summary["matched"] == 1
    assert summary["applied"] == 1
    assert summary["skipped"] == 0

    finding = await _read_finding(client, finding_id)
    assert finding is not None
    assert finding.status == "not_affected"
    assert finding.analysis_source == "vex_import"
    assert finding.analysis_justification == "not reachable in our config"
    assert finding.vex_origin is not None
    assert finding.vex_origin["format"] == "openvex"
    assert finding.vex_origin["vex_status"] == "not_affected"


async def test_import_new_to_not_affected_uses_legal_multistep(
    client: AsyncClient,
) -> None:
    """A `new` finding cannot jump to not_affected directly; the import must
    route through analyzing, leaving the finding at not_affected with both
    audit hops recorded."""
    from models import AuditLog

    _, user, project, scan = await _seed_project(client, role="team_admin")
    headers = _bearer_for(user)
    cve = f"CVE-2099-{unique_suffix()}"
    finding_id, purl, _ = await _seed_finding(
        client, scan_id=scan.id, status="new", cve_id=cve
    )

    raw = _openvex_bytes(
        [
            {
                "vulnerability": {"name": cve},
                "products": [{"@id": purl}],
                "status": "not_affected",
            }
        ]
    )
    response = await client.post(
        f"/v1/projects/{project.id}/vex/import", headers=headers, files=_upload(raw)
    )
    assert response.status_code == 200, response.text
    assert response.json()["applied"] == 1

    finding = await _read_finding(client, finding_id)
    assert finding is not None
    assert finding.status == "not_affected"

    # Two status-change audit rows: new→analyzing and analyzing→not_affected.
    from sqlalchemy import select

    factory = await _factory(client)
    async with factory() as session:
        rows = (
            (
                await session.execute(
                    select(AuditLog)
                    .where(AuditLog.target_table == "vulnerability_findings")
                    .where(AuditLog.target_id == str(finding_id))
                    .where(AuditLog.action == "update")
                )
            )
            .scalars()
            .all()
        )
    status_changes = [r for r in rows if "status" in (r.diff or {})]
    assert len(status_changes) >= 2


# ---------------------------------------------------------------------------
# Idempotency + round-trip
# ---------------------------------------------------------------------------


async def test_import_is_idempotent(client: AsyncClient) -> None:
    _, user, project, scan = await _seed_project(client, role="team_admin")
    headers = _bearer_for(user)
    cve = f"CVE-2099-{unique_suffix()}"
    finding_id, purl, _ = await _seed_finding(
        client, scan_id=scan.id, status="analyzing", cve_id=cve
    )
    raw = _openvex_bytes(
        [{"vulnerability": {"name": cve}, "products": [{"@id": purl}], "status": "fixed"}]
    )

    r1 = await client.post(
        f"/v1/projects/{project.id}/vex/import", headers=headers, files=_upload(raw)
    )
    assert r1.json()["applied"] == 1

    r2 = await client.post(
        f"/v1/projects/{project.id}/vex/import", headers=headers, files=_upload(raw)
    )
    body2 = r2.json()
    assert body2["applied"] == 0
    assert body2["matched"] == 1
    assert body2["skipped"] == 1
    assert body2["errors"][0]["reason"] == "already_at_target"

    finding = await _read_finding(client, finding_id)
    assert finding is not None
    assert finding.status == "fixed"


async def test_export_then_import_is_status_stable(client: AsyncClient) -> None:
    """A1 export → import that exact document → statuses unchanged (no-op).

    The exporter emits each finding's current status; re-importing must be a
    pure no-op (already_at_target) for every statement, proving round-trip
    stability."""
    _, user, project, scan = await _seed_project(client, role="team_admin")
    headers = _bearer_for(user)

    # Seed findings in three already-terminal-ish states that the export maps
    # to a VEX status whose reverse map lands back on the same internal status.
    seeded = {}
    for status_val in ("exploitable", "not_affected", "fixed"):
        cve = f"CVE-2099-{unique_suffix()}"
        fid, purl, _ = await _seed_finding(
            client, scan_id=scan.id, status=status_val, cve_id=cve
        )
        seeded[fid] = status_val

    # Export via A1.
    export = await client.get(
        f"/v1/projects/{project.id}/vex", headers=headers, params={"format": "openvex"}
    )
    assert export.status_code == 200, export.text
    exported_bytes = export.content

    # Import the exported document.
    response = await client.post(
        f"/v1/projects/{project.id}/vex/import",
        headers=headers,
        files=_upload(exported_bytes),
    )
    assert response.status_code == 200, response.text
    summary = response.json()
    # Every statement should be a no-op: matched but not applied.
    assert summary["applied"] == 0
    assert summary["matched"] == len(seeded)

    # Statuses unchanged.
    for fid, original in seeded.items():
        finding = await _read_finding(client, fid)
        assert finding is not None
        assert finding.status == original


# ---------------------------------------------------------------------------
# Matching skips
# ---------------------------------------------------------------------------


async def test_import_unknown_vulnerability_skipped(client: AsyncClient) -> None:
    _, user, project, scan = await _seed_project(client, role="team_admin")
    headers = _bearer_for(user)
    raw = _openvex_bytes(
        [
            {
                "vulnerability": {"name": "CVE-0000-NOPE"},
                "products": [{"@id": "pkg:npm/ghost@1.0.0"}],
                "status": "fixed",
            }
        ]
    )
    response = await client.post(
        f"/v1/projects/{project.id}/vex/import", headers=headers, files=_upload(raw)
    )
    summary = response.json()
    assert summary["matched"] == 0
    assert summary["applied"] == 0
    assert summary["skipped"] == 1
    assert summary["errors"][0]["reason"] == "unknown_vulnerability"


async def test_import_known_cve_wrong_purl_skipped(client: AsyncClient) -> None:
    _, user, project, scan = await _seed_project(client, role="team_admin")
    headers = _bearer_for(user)
    cve = f"CVE-2099-{unique_suffix()}"
    await _seed_finding(client, scan_id=scan.id, status="analyzing", cve_id=cve)

    raw = _openvex_bytes(
        [
            {
                "vulnerability": {"name": cve},
                "products": [{"@id": "pkg:npm/wrong-package@9.9.9"}],
                "status": "fixed",
            }
        ]
    )
    response = await client.post(
        f"/v1/projects/{project.id}/vex/import", headers=headers, files=_upload(raw)
    )
    summary = response.json()
    assert summary["applied"] == 0
    assert summary["errors"][0]["reason"] == "unknown_component"


async def test_import_reopen_terminal_via_legal_path(client: AsyncClient) -> None:
    """fixed → exploitable is reachable only by routing through analyzing
    (fixed → analyzing → exploitable). The importer must apply that legal
    multi-step path rather than rejecting the reopen."""
    _, user, project, scan = await _seed_project(client, role="team_admin")
    headers = _bearer_for(user)
    cve = f"CVE-2099-{unique_suffix()}"
    finding_id, purl, _ = await _seed_finding(
        client, scan_id=scan.id, status="fixed", cve_id=cve
    )
    raw = _openvex_bytes(
        [
            {
                "vulnerability": {"name": cve},
                "products": [{"@id": purl}],
                "status": "affected",  # → exploitable, reopen via analyzing
            }
        ]
    )
    response = await client.post(
        f"/v1/projects/{project.id}/vex/import", headers=headers, files=_upload(raw)
    )
    summary = response.json()
    assert summary["applied"] == 1
    assert summary["skipped"] == 0

    finding = await _read_finding(client, finding_id)
    assert finding is not None
    assert finding.status == "exploitable"


async def test_import_cyclonedx_document(client: AsyncClient) -> None:
    _, user, project, scan = await _seed_project(client, role="team_admin")
    headers = _bearer_for(user)
    cve = f"CVE-2099-{unique_suffix()}"
    finding_id, purl, _ = await _seed_finding(
        client, scan_id=scan.id, status="analyzing", cve_id=cve
    )
    doc = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": "urn:uuid:22222222-2222-2222-2222-222222222222",
        "version": 1,
        "metadata": {"timestamp": "2025-01-02T03:04:05.000Z"},
        "vulnerabilities": [
            {
                "id": cve,
                "source": {"name": "NVD"},
                "analysis": {"state": "false_positive", "detail": "scanner mismatch"},
                "affects": [{"ref": purl}],
            }
        ],
    }
    response = await client.post(
        f"/v1/projects/{project.id}/vex/import",
        headers=headers,
        files=_upload(json.dumps(doc).encode()),
    )
    assert response.status_code == 200, response.text
    summary = response.json()
    assert summary["format"] == "cyclonedx"
    assert summary["applied"] == 1

    finding = await _read_finding(client, finding_id)
    assert finding is not None
    assert finding.status == "false_positive"
    assert finding.analysis_justification == "scanner mismatch"


async def test_import_unmapped_status_skipped(client: AsyncClient) -> None:
    """A VEX status with no internal reverse-mapping is skipped as
    unmapped_status, leaving the finding untouched."""
    _, user, project, scan = await _seed_project(client, role="team_admin")
    headers = _bearer_for(user)
    cve = f"CVE-2099-{unique_suffix()}"
    finding_id, purl, _ = await _seed_finding(
        client, scan_id=scan.id, status="analyzing", cve_id=cve
    )
    raw = _openvex_bytes(
        [
            {
                "vulnerability": {"name": cve},
                "products": [{"@id": purl}],
                "status": "no_such_openvex_status",
            }
        ]
    )
    response = await client.post(
        f"/v1/projects/{project.id}/vex/import", headers=headers, files=_upload(raw)
    )
    summary = response.json()
    assert summary["applied"] == 0
    assert summary["skipped"] == 1
    assert summary["errors"][0]["reason"] == "unmapped_status"

    finding = await _read_finding(client, finding_id)
    assert finding is not None
    assert finding.status == "analyzing"


async def test_import_statement_missing_status_skipped(client: AsyncClient) -> None:
    _, user, project, scan = await _seed_project(client, role="team_admin")
    headers = _bearer_for(user)
    cve = f"CVE-2099-{unique_suffix()}"
    await _seed_finding(client, scan_id=scan.id, status="analyzing", cve_id=cve)
    raw = _openvex_bytes(
        [{"vulnerability": {"name": cve}, "products": [{"@id": "pkg:npm/x@1"}]}]
    )
    response = await client.post(
        f"/v1/projects/{project.id}/vex/import", headers=headers, files=_upload(raw)
    )
    summary = response.json()
    assert summary["applied"] == 0
    assert summary["errors"][0]["reason"] == "malformed_statement"


async def test_import_statement_no_products_skipped(client: AsyncClient) -> None:
    _, user, project, scan = await _seed_project(client, role="team_admin")
    headers = _bearer_for(user)
    cve = f"CVE-2099-{unique_suffix()}"
    await _seed_finding(client, scan_id=scan.id, status="analyzing", cve_id=cve)
    raw = _openvex_bytes(
        [{"vulnerability": {"name": cve}, "products": [], "status": "fixed"}]
    )
    response = await client.post(
        f"/v1/projects/{project.id}/vex/import", headers=headers, files=_upload(raw)
    )
    summary = response.json()
    assert summary["applied"] == 0
    assert summary["errors"][0]["reason"] == "unknown_component"


async def test_import_partial_match_multi_purl(client: AsyncClient) -> None:
    """A statement listing two purls — one matching, one not — applies to the
    match and records a skip for the miss."""
    _, user, project, scan = await _seed_project(client, role="team_admin")
    headers = _bearer_for(user)
    cve = f"CVE-2099-{unique_suffix()}"
    finding_id, purl, _ = await _seed_finding(
        client, scan_id=scan.id, status="analyzing", cve_id=cve
    )
    raw = _openvex_bytes(
        [
            {
                "vulnerability": {"name": cve},
                "products": [{"@id": purl}, {"@id": "pkg:npm/missing@9.9.9"}],
                "status": "fixed",
            }
        ]
    )
    response = await client.post(
        f"/v1/projects/{project.id}/vex/import", headers=headers, files=_upload(raw)
    )
    summary = response.json()
    assert summary["applied"] == 1
    assert summary["skipped"] == 1  # the missing purl
    reasons = {e["reason"] for e in summary["errors"]}
    assert "unknown_component" in reasons

    finding = await _read_finding(client, finding_id)
    assert finding is not None
    assert finding.status == "fixed"


async def test_import_oversized_returns_413(client: AsyncClient) -> None:
    """An over-cap body is rejected 413 via the service's decoded-size guard
    (which the router's declared-content-length fast-fail mirrors)."""
    _, user, project, _ = await _seed_project(client, role="team_admin")
    import os

    os.environ["VEX_IMPORT_MAX_BYTES"] = "100"
    try:
        big = _openvex_bytes(
            [
                {
                    "vulnerability": {"name": f"CVE-{i}"},
                    "products": [{"@id": f"pkg:npm/p{i}@1"}],
                    "status": "fixed",
                }
                for i in range(50)
            ]
        )
        response = await client.post(
            f"/v1/projects/{project.id}/vex/import",
            headers=_bearer_for(user),
            files=_upload(big),
        )
        assert response.status_code == 413
        assert response.headers["content-type"].startswith(PROBLEM_JSON)
        assert response.json()["title"] == "VEX Document Too Large"
    finally:
        del os.environ["VEX_IMPORT_MAX_BYTES"]


async def test_import_empty_project_no_scan_all_skipped(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session)
        await make_membership(session, user=user, team=team, role="team_admin")
        project = await make_project(session, team=team)  # no latest_scan_id
        await session.commit()
        await session.refresh(project)

    headers = _bearer_for(user)
    raw = _openvex_bytes(
        [
            {
                "vulnerability": {"name": "CVE-1"},
                "products": [{"@id": "pkg:npm/x@1"}],
                "status": "fixed",
            }
        ]
    )
    response = await client.post(
        f"/v1/projects/{project.id}/vex/import", headers=headers, files=_upload(raw)
    )
    assert response.status_code == 200, response.text
    summary = response.json()
    assert summary["applied"] == 0
    assert summary["matched"] == 0
    assert summary["skipped"] == 1
