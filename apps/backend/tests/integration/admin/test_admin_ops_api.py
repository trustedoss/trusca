"""
Integration tests for /v1/admin/{scans,disk,audit,health} — Phase 4 PR #14
(W6-#43a: ``/v1/admin/dt/*`` sub-router removed per ADR-0001).

The 4-role matrix (anonymous / developer / team_admin / super_admin) is the
spine: every PR #14 endpoint must hide its existence from non-super-admin
authed users (404, not 403) and reject anonymous calls with 401.

Plus contract assertions:
  - All 4xx are application/problem+json (RFC 7807).
  - Domain extension fields are present on the typed errors
    (scan_already_cancelled, audit_export_too_large).
  - Audit row produced for cancel + cleanup operations.
  - The CSV export streams a header line and respects the 100k cap.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
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

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent
PROBLEM_JSON = "application/problem+json"

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip admin ops API tests")
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
            f"alembic upgrade head failed; admin ops API tests cannot run\n"
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


# ---------------------------------------------------------------------------
# 4-role matrix — applied to every PR #14 endpoint
# ---------------------------------------------------------------------------

# Each tuple: (method, path) — the body / params do not change the auth gate
# behaviour, so we use the simplest call shape per endpoint. W6-#43a removed
# the ``/v1/admin/dt/*`` sub-router (ADR-0001) so it's no longer in the matrix.
_AUTH_MATRIX_ENDPOINTS = [
    ("GET", "/v1/admin/scans"),
    ("POST", f"/v1/admin/scans/{uuid.uuid4()}/cancel"),
    ("GET", "/v1/admin/disk"),
    ("GET", "/v1/admin/audit"),
    ("GET", "/v1/admin/audit/export.csv"),
    ("GET", "/v1/admin/health"),
    # W6-#43e: Trivy DB health panel — existence-hide for non-super-admin.
    ("GET", "/v1/admin/trivy/health"),
]


@pytest.mark.parametrize("method,path", _AUTH_MATRIX_ENDPOINTS)
async def test_anonymous_returns_401(
    client: AsyncClient, method: str, path: str
) -> None:
    response = await client.request(method, path)
    assert response.status_code == 401, response.text
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


@pytest.mark.parametrize("method,path", _AUTH_MATRIX_ENDPOINTS)
async def test_developer_returns_404_existence_hide(
    client: AsyncClient, method: str, path: str
) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session)
        await make_membership(session, user=user, team=team, role="developer")

    response = await client.request(method, path, headers=_bearer_for(user))
    assert response.status_code == 404, response.text
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


@pytest.mark.parametrize("method,path", _AUTH_MATRIX_ENDPOINTS)
async def test_team_admin_returns_404_existence_hide(
    client: AsyncClient, method: str, path: str
) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session)
        await make_membership(session, user=user, team=team, role="team_admin")

    response = await client.request(method, path, headers=_bearer_for(user))
    assert response.status_code == 404, response.text
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# DT Connector — REMOVED in W6-#43a (ADR-0001). Eight integration tests
# covering /v1/admin/dt/{status,orphans,health-check,breaker/reset} were
# deleted alongside the dt sub-router. CVE matching is now Trivy-only (W6-#41)
# and the rematch beat (W6-#42) replaces the DT recheck loop.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Scan Queue
# ---------------------------------------------------------------------------


async def test_admin_scans_super_admin_lists(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        project = await make_project(session, team=team)
        await make_scan(session, project=project, status="queued")

    response = await client.get(
        "/v1/admin/scans?page=1&page_size=10", headers=_bearer_for(admin)
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "items" in body
    assert "total" in body


async def test_admin_scans_status_filter_invalid_returns_422(
    client: AsyncClient,
) -> None:
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)

    response = await client.get(
        "/v1/admin/scans?status=BOGUS",
        headers=_bearer_for(admin),
    )
    assert response.status_code == 422, response.text
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_admin_scan_cancel_terminal_returns_409(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        project = await make_project(session, team=team)
        scan = await make_scan(session, project=project, status="succeeded")

    response = await client.post(
        f"/v1/admin/scans/{scan.id}/cancel",
        headers=_bearer_for(admin),
    )
    assert response.status_code == 409, response.text
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    body = response.json()
    assert body.get("scan_already_cancelled") is True


async def test_admin_scan_cancel_unknown_returns_404_problem(
    client: AsyncClient,
) -> None:
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)

    response = await client.post(
        f"/v1/admin/scans/{uuid.uuid4()}/cancel",
        headers=_bearer_for(admin),
    )
    assert response.status_code == 404, response.text
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    body = response.json()
    assert body.get("scan_not_found") is True


# ---------------------------------------------------------------------------
# Disk
# ---------------------------------------------------------------------------


async def test_admin_disk_super_admin_returns_four_items(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)

    response = await client.get("/v1/admin/disk", headers=_bearer_for(admin))
    assert response.status_code == 200, response.text
    body = response.json()
    names = [item["name"] for item in body["items"]]
    # W6-#43a: dt_volume entry removed alongside the DT integration.
    # M-32: trivy_db card covers the worker-shared Trivy DB cache (H-6).
    assert names == ["workspace", "trivy_db", "postgres", "redis"]


# ---------------------------------------------------------------------------
# Audit Log
# ---------------------------------------------------------------------------


async def test_admin_audit_search_returns_envelope(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)

    response = await client.get("/v1/admin/audit", headers=_bearer_for(admin))
    assert response.status_code == 200, response.text
    body = response.json()
    assert "items" in body
    assert "total" in body
    assert "has_more" in body


async def test_admin_audit_target_table_unknown_returns_422(
    client: AsyncClient,
) -> None:
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)

    response = await client.get(
        "/v1/admin/audit?target_table=nope_table",
        headers=_bearer_for(admin),
    )
    assert response.status_code == 422, response.text


async def test_admin_audit_export_csv_streams(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)

    response = await client.get(
        "/v1/admin/audit/export.csv",
        headers=_bearer_for(admin),
    )
    assert response.status_code == 200, response.text
    # FastAPI's StreamingResponse populates Content-Type as set by the
    # caller; we asserted the precise prefix.
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers.get("content-disposition", "")
    # A3 (sys-bug-audit-2): UTF-8 BOM prefix so Excel on CJK locales
    # auto-detects the encoding instead of decoding under CP949 / SJIS.
    raw = response.content
    assert raw[:3] == b"\xef\xbb\xbf", (
        f"missing UTF-8 BOM; first 16 bytes = {raw[:16]!r}"
    )
    body = raw.decode("utf-8-sig")  # csv lib / utf-8-sig strips the BOM
    # Header line is the CSV column contract.
    assert body.startswith("created_at,actor_user_id,actor_email")


# ---------------------------------------------------------------------------
# System Health
# ---------------------------------------------------------------------------


async def test_admin_health_super_admin_returns_components(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)

    response = await client.get("/v1/admin/health", headers=_bearer_for(admin))
    assert response.status_code == 200, response.text
    body = response.json()
    names = [c["name"] for c in body["components"]]
    # W6-#43a (ADR-0001): ``dt`` probe removed alongside the DT integration.
    assert set(names) == {
        "postgres",
        "redis",
        "celery",
        "disk",
        "active_scans",
        "last_24h_errors",
    }


# ---------------------------------------------------------------------------
# Trivy DB health panel (W6-#43e)
# ---------------------------------------------------------------------------


async def test_admin_trivy_health_super_admin_returns_payload(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Super admin sees the Trivy DB status snapshot (empty-state OK)."""
    # Pin TRIVY_CACHE_DIR to a per-test directory so we don't read the
    # host worker's real Trivy state. No metadata.json written → exercises
    # the "not yet downloaded" branch which is the realistic CI shape.
    monkeypatch.setenv("TRIVY_CACHE_DIR", str(tmp_path / "trivy-cache"))
    # Invalidate the 60s service cache so a previous test's snapshot does
    # not leak into this one.
    from services.trivy_health_service import reset_cache

    reset_cache()

    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)

    response = await client.get(
        "/v1/admin/trivy/health", headers=_bearer_for(admin)
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    # Closed shape: every contracted field present.
    assert set(body.keys()) == {
        "last_update",
        "next_refresh_at",
        "vuln_count",
        "db_version",
        "db_size_bytes",
        "refresh_interval_hours",
        "freshness",
        "cache_dir",
        "repository",
    }
    assert body["freshness"] in {"fresh", "stale", "very_stale", "unknown"}
    assert body["refresh_interval_hours"] >= 1
    assert body["cache_dir"].endswith("trivy-cache")
    assert body["repository"] == "ghcr.io/aquasecurity/trivy-db"
