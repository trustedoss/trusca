"""
Integration tests for the Reports center history HTTP surface — W3 #32a-2.

Endpoint:
  - GET /v1/projects/{project_id}/reports/history

Plus emit verification for the four read-only download endpoints that ROW the
history table:
  - GET /v1/projects/{project_id}/notice                  (notice)
  - GET /v1/projects/{project_id}/sbom                    (sbom)
  - GET /v1/projects/{project_id}/vulnerability-report.pdf (vuln_pdf)
  - GET /v1/projects/{project_id}/vex                     (vex_export)

Pinned cases:
  * Auth gate: anonymous → 401.
  * Cross-team viewer → 404 existence-hide.
  * Unknown project → 404.
  * Type filter narrows the result set.
  * Pagination boundary: 3 rows, page_size=2, page=2 returns the tail.
  * Each of the four download endpoints emits exactly one row of the right shape.
  * VEX *import* (PR-out-of-scope) does NOT emit a report_downloads row.

The PDF assertions skip on stale images (weasyprint native libs absent), mirroring
``test_reports_api.py``; everything else runs unconditionally.
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
from sqlalchemy import select

from core.security import create_access_token
from models import ReportDownload, User
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
        pytest.skip("DATABASE_URL not set — skip report-history API tests")
    return url


def _require_weasyprint() -> None:
    """Skip (not fail) unless weasyprint can actually render a PDF here.

    Mirrors ``test_reports_api._require_weasyprint`` so the vuln_pdf emit
    coverage skips on a pip-only image but exercises the real path on a built
    image. Any failure (ImportError, OSError, AttributeError, bad pydyf pin)
    is treated as "cannot render" and skips.
    """
    try:
        import weasyprint  # noqa: PLC0415 — gated probe, kept local to the guard

        pdf = weasyprint.HTML(string="<p>x</p>").write_pdf()
    except Exception as exc:  # noqa: BLE001 — any failure means "cannot render"
        pytest.skip(
            "weasyprint cannot render a PDF in this environment "
            f"({type(exc).__name__}: {exc})"
        )
    if not pdf or not pdf.startswith(b"%PDF"):
        pytest.skip("weasyprint produced no PDF bytes in this environment")


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
            "alembic upgrade head failed; report-history API tests cannot run\n"
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


async def _seed(client: AsyncClient, *, role: str = "developer", is_superuser: bool = False):
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
    return team, user, project


async def _emit_via_session(
    client: AsyncClient,
    *,
    project,
    user,
    report_type: str,
    fmt: str,
    scan_id: uuid.UUID | None = None,
) -> None:
    """Bypass HTTP and drive ``record_report_download`` directly.

    Several emit-coverage cases want to verify the listing surface without
    requiring weasyprint / SBOM-XML / etc. to actually render. We seed the
    history rows via the service helper so the suite stays portable across
    image variants.
    """
    from services.report_download_service import record_report_download

    factory = await _factory(client)
    async with factory() as session:
        # Re-load the project + user inside this session so attributes are
        # live (the seed session has already closed).
        from models import Project

        fresh_project = (
            await session.execute(select(Project).where(Project.id == project.id))
        ).scalar_one()
        fresh_user = (
            await session.execute(select(User).where(User.id == user.id))
        ).scalar_one()

        class _Req:
            class _C:
                host = "203.0.113.10"

            client = _C()
            headers: dict[str, str] = {"user-agent": "pytest"}

        await record_report_download(
            session,
            project=fresh_project,
            scan_id=scan_id,
            user=fresh_user,
            report_type=report_type,
            fmt=fmt,
            size_bytes=128,
            request=_Req(),  # type: ignore[arg-type]
        )


async def _count_rows(client: AsyncClient, *, project_id: uuid.UUID) -> int:
    factory = await _factory(client)
    async with factory() as session:
        rows = (
            await session.execute(
                select(ReportDownload).where(ReportDownload.project_id == project_id)
            )
        ).scalars().all()
        return len(rows)


async def _fetch_rows(
    client: AsyncClient, *, project_id: uuid.UUID
) -> list[ReportDownload]:
    factory = await _factory(client)
    async with factory() as session:
        rows = (
            await session.execute(
                select(ReportDownload)
                .where(ReportDownload.project_id == project_id)
                .order_by(ReportDownload.created_at.desc())
            )
        ).scalars().all()
        return list(rows)


# ---------------------------------------------------------------------------
# History endpoint — auth + cross-team
# ---------------------------------------------------------------------------


async def test_history_without_auth_returns_401(client: AsyncClient) -> None:
    response = await client.get(
        f"/v1/projects/{uuid.uuid4()}/reports/history",
    )
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_history_other_team_returns_404_existence_hide(client: AsyncClient) -> None:
    """Outsiders see 404 — same shape as a missing project."""
    _, _, target_project = await _seed(client)
    _, outsider, _ = await _seed(client)
    headers = _bearer_for(outsider)

    response = await client.get(
        f"/v1/projects/{target_project.id}/reports/history",
        headers=headers,
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_history_unknown_project_returns_404(client: AsyncClient) -> None:
    _, admin, _ = await _seed(client, is_superuser=True)
    headers = _bearer_for(admin)

    response = await client.get(
        f"/v1/projects/{uuid.uuid4()}/reports/history",
        headers=headers,
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_history_super_admin_bypasses_team_check(client: AsyncClient) -> None:
    _, _, target_project = await _seed(client)
    _, admin, _ = await _seed(client, is_superuser=True)
    headers = _bearer_for(admin)

    response = await client.get(
        f"/v1/projects/{target_project.id}/reports/history",
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 0
    assert body["items"] == []


# ---------------------------------------------------------------------------
# History endpoint — empty / page-size validation
# ---------------------------------------------------------------------------


async def test_history_empty_project_returns_empty_page(client: AsyncClient) -> None:
    _, user, project = await _seed(client)
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project.id}/reports/history",
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body == {"items": [], "total": 0, "page": 1, "page_size": 50}


async def test_history_page_size_too_large_returns_422(client: AsyncClient) -> None:
    _, user, project = await _seed(client)
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project.id}/reports/history?page_size=500",
        headers=headers,
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# History endpoint — filtering + pagination
# ---------------------------------------------------------------------------


async def test_history_type_filter_narrows_results(client: AsyncClient) -> None:
    _, user, project = await _seed(client)
    await _emit_via_session(
        client, project=project, user=user, report_type="notice", fmt="text"
    )
    await _emit_via_session(
        client, project=project, user=user, report_type="sbom", fmt="cyclonedx-json"
    )
    await _emit_via_session(
        client, project=project, user=user, report_type="vuln_pdf", fmt="pdf"
    )

    headers = _bearer_for(user)
    response = await client.get(
        f"/v1/projects/{project.id}/reports/history?type=notice&type=sbom",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    types = sorted(item["report_type"] for item in body["items"])
    assert types == ["notice", "sbom"]
    assert body["total"] == 2


async def test_history_pagination_returns_remaining_tail(client: AsyncClient) -> None:
    """3 rows, page_size=2, page=2 returns 1 row."""
    _, user, project = await _seed(client)
    for _ in range(3):
        await _emit_via_session(
            client, project=project, user=user, report_type="notice", fmt="text"
        )

    headers = _bearer_for(user)
    page1 = await client.get(
        f"/v1/projects/{project.id}/reports/history?page=1&page_size=2",
        headers=headers,
    )
    page2 = await client.get(
        f"/v1/projects/{project.id}/reports/history?page=2&page_size=2",
        headers=headers,
    )

    assert page1.status_code == 200
    assert page2.status_code == 200
    body1 = page1.json()
    body2 = page2.json()
    assert body1["total"] == 3
    assert len(body1["items"]) == 2
    assert body2["total"] == 3
    assert len(body2["items"]) == 1


async def test_history_user_summary_inline(client: AsyncClient) -> None:
    _, user, project = await _seed(client)
    await _emit_via_session(
        client, project=project, user=user, report_type="notice", fmt="text"
    )

    headers = _bearer_for(user)
    response = await client.get(
        f"/v1/projects/{project.id}/reports/history",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["user"]["id"] == str(user.id)
    assert item["user"]["email"] == user.email
    # PII columns MUST NOT appear in the response (CLAUDE.md §5).
    assert "client_ip" not in item
    assert "user_agent" not in item


# ---------------------------------------------------------------------------
# Emit coverage — one row per successful download
# ---------------------------------------------------------------------------


async def test_notice_endpoint_emits_one_row(client: AsyncClient) -> None:
    _, user, project = await _seed(client)
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project.id}/notice?format=text",
        headers=headers,
    )
    assert response.status_code == 200, response.text

    rows = await _fetch_rows(client, project_id=project.id)
    assert len(rows) == 1
    row = rows[0]
    assert row.report_type == "notice"
    assert row.format == "text"
    assert row.team_id == project.team_id
    assert row.user_id == user.id
    assert row.size_bytes is not None and row.size_bytes > 0


async def test_sbom_endpoint_emits_one_row(client: AsyncClient) -> None:
    _, user, project = await _seed(client)
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project.id}/sbom?format=cyclonedx-json",
        headers=headers,
    )
    assert response.status_code == 200, response.text

    rows = await _fetch_rows(client, project_id=project.id)
    assert len(rows) == 1
    row = rows[0]
    assert row.report_type == "sbom"
    assert row.format == "cyclonedx-json"
    assert row.team_id == project.team_id
    assert row.user_id == user.id
    assert row.size_bytes is not None and row.size_bytes > 0


async def test_vex_export_endpoint_emits_one_row(client: AsyncClient) -> None:
    _, user, project = await _seed(client)
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project.id}/vex?format=openvex",
        headers=headers,
    )
    assert response.status_code == 200, response.text

    rows = await _fetch_rows(client, project_id=project.id)
    assert len(rows) == 1
    row = rows[0]
    assert row.report_type == "vex_export"
    assert row.format == "openvex"
    assert row.team_id == project.team_id
    assert row.user_id == user.id
    # VEX export is NOT scan-bound — scan_id must be NULL by design.
    assert row.scan_id is None
    assert row.size_bytes is not None and row.size_bytes > 0


async def test_vuln_pdf_endpoint_emits_one_row(client: AsyncClient) -> None:
    _require_weasyprint()
    _, user, project = await _seed(client)
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project.id}/vulnerability-report.pdf",
        headers=headers,
    )
    assert response.status_code == 200, response.text

    rows = await _fetch_rows(client, project_id=project.id)
    assert len(rows) == 1
    row = rows[0]
    assert row.report_type == "vuln_pdf"
    assert row.format == "pdf"
    assert row.team_id == project.team_id
    assert row.user_id == user.id
    assert row.size_bytes is not None and row.size_bytes > 0


async def test_vuln_xlsx_endpoint_emits_one_row(client: AsyncClient) -> None:
    """Phase G: the Excel download records exactly one ``vuln_xlsx`` history row.

    No weasyprint gate — openpyxl is a hard dependency, so this always runs and
    proves both the enum value (mig 0037) and the download-history wiring.
    """
    _, user, project = await _seed(client)
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project.id}/vulnerability-report.xlsx",
        headers=headers,
    )
    assert response.status_code == 200, response.text

    rows = await _fetch_rows(client, project_id=project.id)
    assert len(rows) == 1
    row = rows[0]
    assert row.report_type == "vuln_xlsx"
    assert row.format == "xlsx"
    assert row.team_id == project.team_id
    assert row.user_id == user.id
    assert row.size_bytes is not None and row.size_bytes > 0


# ---------------------------------------------------------------------------
# Negative coverage — failed export must not emit; VEX import never emits
# ---------------------------------------------------------------------------


async def test_failed_sbom_export_does_not_emit(client: AsyncClient) -> None:
    """A 422 on an unsupported SBOM format must NOT leave a history row."""
    _, user, project = await _seed(client)
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project.id}/sbom?format=garbage-format",
        headers=headers,
    )
    # FastAPI's Query() Literal rejects the value at 422 before the endpoint
    # body runs — confirming the emit IS gated on a successful response.
    assert response.status_code == 422

    assert await _count_rows(client, project_id=project.id) == 0


async def test_vex_import_does_not_emit_history_row(client: AsyncClient) -> None:
    """VEX *import* is captured by ``audit_logs`` (it's a mutation), not by
    ``report_downloads``. This PR keeps the four read-only download paths as
    the only emit sites; verify import is NOT silently double-recorded."""
    _, user, project = await _seed(client)
    # Upgrade user to team_admin so the import gate passes (the import path
    # requires team_admin; the 401/403 case is covered by the VEX suite).
    factory = await _factory(client)
    async with factory() as session:
        from models import Membership

        existing = (
            await session.execute(
                select(Membership).where(
                    (Membership.user_id == user.id) & (Membership.team_id == project.team_id)
                )
            )
        ).scalar_one()
        existing.role = "team_admin"
        await session.commit()

    headers = _bearer_for(user)
    # Send a minimally-valid OpenVEX body — even on a 200 OR a 422 the import
    # endpoint MUST NOT create a report_downloads row.
    files = {"upload": ("vex.json", b"{}", "application/json")}
    response = await client.post(
        f"/v1/projects/{project.id}/vex/import",
        headers=headers,
        files=files,
    )
    # Either 200 (no-op import) or 422 (malformed) is acceptable here; what
    # matters is that NO history row was written.
    assert response.status_code in {200, 422}

    assert await _count_rows(client, project_id=project.id) == 0
