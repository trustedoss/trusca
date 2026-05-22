"""
Integration tests for the vulnerability PDF report HTTP surface — scan-gap G2.

Endpoint:
  - GET /v1/projects/{project_id}/vulnerability-report.pdf

Pins:
  - Happy path returns 200 + ``content-type: application/pdf`` + a body that
    starts with the ``%PDF`` magic bytes + ``Content-Disposition: attachment``.
  - Anonymous → 401.
  - Outsiders see 404 (existence-hide) — never 403.
  - Unknown project → 404.
  - super_admin bypasses the team check.

IMPORTANT — image rebuild required
----------------------------------
The happy-path assertions exercise weasyprint, which dlopen's native
rendering libs (libpango / cairo / gdk-pixbuf) that are only present after the
backend image is rebuilt with the G2 Dockerfile changes. Tests that need a
real PDF are gated behind :func:`_require_weasyprint`, which *skips* (not
fails) when weasyprint cannot be imported, so this file is green on a stale
image and only proves the PDF path once the image is rebuilt. The auth-gate /
IDOR tests do NOT need weasyprint (they 401 / 404 before rendering) and run
unconditionally.
"""

from __future__ import annotations

import importlib.util
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
        pytest.skip("DATABASE_URL not set — skip reports API tests")
    return url


def _require_weasyprint() -> None:
    """Skip (not fail) when weasyprint is unavailable — i.e. the image has not
    yet been rebuilt with the G2 native deps."""
    if importlib.util.find_spec("weasyprint") is None:
        pytest.skip(
            "weasyprint not installed — rebuild the backend image with the G2 "
            "Dockerfile changes to exercise PDF rendering"
        )


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
            "alembic upgrade head failed; reports API tests cannot run\n"
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


# ---------------------------------------------------------------------------
# Auth gate (no weasyprint needed — rejected before rendering)
# ---------------------------------------------------------------------------


async def test_report_without_auth_returns_401(client: AsyncClient) -> None:
    response = await client.get(
        f"/v1/projects/{uuid.uuid4()}/vulnerability-report.pdf"
    )
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_report_other_team_returns_404_existence_hide(client: AsyncClient) -> None:
    """Outsiders see 404 — same response shape as a missing project, and the
    request is rejected before any PDF rendering happens."""
    _, _, target_project = await _seed(client)
    _, outsider, _ = await _seed(client)
    headers = _bearer_for(outsider)

    response = await client.get(
        f"/v1/projects/{target_project.id}/vulnerability-report.pdf",
        headers=headers,
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    body = response.json()
    assert body["title"] == "Project Not Found"


async def test_report_unknown_project_returns_404(client: AsyncClient) -> None:
    _, admin, _ = await _seed(client, is_superuser=True)
    headers = _bearer_for(admin)

    response = await client.get(
        f"/v1/projects/{uuid.uuid4()}/vulnerability-report.pdf",
        headers=headers,
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# Happy path — requires the rebuilt image (weasyprint)
# ---------------------------------------------------------------------------


async def test_report_happy_path_returns_pdf(client: AsyncClient) -> None:
    _require_weasyprint()
    _, user, project = await _seed(client)
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project.id}/vulnerability-report.pdf",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.content.startswith(b"%PDF")
    cd = response.headers["content-disposition"]
    assert cd.startswith("attachment;")
    assert "vulnerability-report-" in cd
    assert ".pdf" in cd


async def test_report_super_admin_bypasses_team_check(client: AsyncClient) -> None:
    _require_weasyprint()
    _, _, target_project = await _seed(client)
    _, admin, _ = await _seed(client, is_superuser=True)
    headers = _bearer_for(admin)

    response = await client.get(
        f"/v1/projects/{target_project.id}/vulnerability-report.pdf",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.content.startswith(b"%PDF")
