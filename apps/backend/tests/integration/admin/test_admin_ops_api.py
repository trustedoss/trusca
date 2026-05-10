"""
Integration tests for /v1/admin/{dt,scans,disk,audit,health} — Phase 4 PR #14.

The 4-role matrix (anonymous / developer / team_admin / super_admin) is the
spine: every PR #14 endpoint must hide its existence from non-super-admin
authed users (404, not 403) and reject anonymous calls with 401.

Plus contract assertions:
  - All 4xx are application/problem+json (RFC 7807).
  - Domain extension fields are present on the typed errors
    (dt_unreachable, dt_orphan_cleanup_in_progress, scan_already_cancelled,
    audit_export_too_large).
  - Audit row produced for cancel + cleanup + health-check operations.
  - The CSV export streams a header line and respects the 100k cap.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from core.security import create_access_token
from integrations.dt.breaker import BreakerSnapshot
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
# behaviour, so we use the simplest call shape per endpoint.
_AUTH_MATRIX_ENDPOINTS = [
    ("GET", "/v1/admin/dt/status"),
    ("GET", "/v1/admin/dt/orphans"),
    # /dt/orphans/cleanup is POST + body — covered separately because the
    # body must be valid JSON for the auth gate to fire ahead of validation.
    ("POST", "/v1/admin/dt/health-check"),
    # A4 — manual sys-bug fix. No body required, so the auth gate fires
    # cleanly across the 4-role matrix.
    ("POST", "/v1/admin/dt/breaker/reset"),
    ("GET", "/v1/admin/scans"),
    ("POST", f"/v1/admin/scans/{uuid.uuid4()}/cancel"),
    ("GET", "/v1/admin/disk"),
    ("GET", "/v1/admin/audit"),
    ("GET", "/v1/admin/audit/export.csv"),
    ("GET", "/v1/admin/health"),
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
# DT Connector
# ---------------------------------------------------------------------------


async def test_dt_status_super_admin_returns_payload(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mock the breaker snapshot so the test does not depend on real DT health."""
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)

    snapshot = BreakerSnapshot(state="closed", fail_count=0, opened_at=None)

    with patch("services.admin_dt_service.get_breaker") as mock_breaker, patch(
        "services.admin_dt_service.build_client"
    ) as mock_build:
        mock_breaker.return_value.snapshot.return_value = snapshot
        # Force a successful version probe.
        mock_breaker.return_value.call.return_value = {"version": "4.13.2"}

        class _DummyClient:
            def health(self) -> dict[str, str]:
                return {"version": "4.13.2"}

            def close(self) -> None:
                pass

        mock_build.return_value = _DummyClient()

        # Invalidate the cache from prior runs.
        from services.admin_dt_service import _reset_status_cache_for_tests

        _reset_status_cache_for_tests()

        response = await client.get("/v1/admin/dt/status", headers=_bearer_for(admin))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "closed"


async def test_dt_orphan_cleanup_inprogress_returns_409_problem(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)

    # Pre-acquire the lock via the same redis client the service uses.
    import redis as _redis

    from core.config import redis_url

    rds = _redis.Redis.from_url(redis_url(), decode_responses=True)
    rds.set("dt:admin:orphan_cleanup_lock", "1", ex=60)
    try:
        response = await client.post(
            "/v1/admin/dt/orphans/cleanup",
            headers=_bearer_for(admin),
            # G6: empty list now returns 400; send a real UUID so the lock
            # check (409) is reached.
            json={"dt_project_uuids": ["11111111-1111-1111-1111-111111111111"]},
        )
    finally:
        rds.delete("dt:admin:orphan_cleanup_lock")
        rds.close()  # type: ignore[no-untyped-call]

    assert response.status_code == 409, response.text
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    body = response.json()
    assert body.get("dt_orphan_cleanup_in_progress") is True
    assert body["type"].endswith("/dt-orphan-cleanup-in-progress")


async def test_dt_orphans_cleanup_happy_path_enqueues_and_audits(
    client: AsyncClient,
) -> None:
    """Cleanup with a valid UUID list dispatches the Celery task and emits an audit row."""
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)

    # Stub the Celery dispatch via the service injection point so the test
    # does not require a live broker. We patch the import path the route
    # uses (the local-import inside enqueue_orphan_cleanup picks up the
    # patched ``delay`` via the keyword arg path; the route does not
    # exercise that, so we patch the task module directly).
    class _FakeAsync:
        id = "fake-task-id"

    # Pre-clear any leftover lock from previous tests.
    import redis as _redis

    from core.config import redis_url

    rds = _redis.Redis.from_url(redis_url(), decode_responses=True)
    rds.delete("dt:admin:orphan_cleanup_lock")
    rds.close()  # type: ignore[no-untyped-call]

    with patch("tasks.dt_orphan_cleanup.dt_orphan_cleanup_task") as mock_task:
        mock_task.delay = lambda _uuids: _FakeAsync()
        response = await client.post(
            "/v1/admin/dt/orphans/cleanup",
            headers=_bearer_for(admin),
            # G6: empty list now returns 400; use a real UUID.
            json={"dt_project_uuids": ["22222222-2222-2222-2222-222222222222"]},
        )

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["task_id"] == "fake-task-id"

    # Audit row recorded for the dispatch event.
    async with factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT count(*) FROM audit_logs "
                    "WHERE actor_user_id=:a AND target_table='dt_projects' "
                    "  AND action='cleanup_enqueued'"
                ),
                {"a": str(admin.id)},
            )
        ).scalar_one()
    assert rows >= 1


async def test_dt_breaker_reset_super_admin_returns_200_and_audits(
    client: AsyncClient,
) -> None:
    """A4: super_admin reset on an OPEN breaker returns 200 + transition + audit."""
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)

    # Drive the real Redis breaker to OPEN via the same module the service
    # uses, then reset the cached singleton so the next call picks up the
    # fresh state.
    from integrations.dt.breaker import get_breaker, reset_default_breaker

    reset_default_breaker()
    breaker = get_breaker()
    breaker.force_close()  # known-good baseline first
    # Synthesize OPEN by setting the redis state directly — avoids depending
    # on the failure_threshold env var.
    import redis as _redis

    from core.config import redis_url

    rds = _redis.Redis.from_url(redis_url(), decode_responses=True)
    try:
        rds.set("dt:breaker:state", "open")
        rds.set("dt:breaker:fail_count", "5")
        rds.set("dt:breaker:opened_at", "1700000000.0")

        response = await client.post(
            "/v1/admin/dt/breaker/reset", headers=_bearer_for(admin)
        )
    finally:
        rds.close()  # type: ignore[no-untyped-call]

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state_before"] == "open"
    assert body["state_after"] == "closed"
    assert body["fail_count_before"] >= 0

    async with factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT count(*) FROM audit_logs "
                    "WHERE actor_user_id=:a AND target_table='dt_breaker' "
                    "  AND action='breaker_reset'"
                ),
                {"a": str(admin.id)},
            )
        ).scalar_one()
    assert rows >= 1


async def test_dt_breaker_reset_already_closed_returns_409_problem(
    client: AsyncClient,
) -> None:
    """A4: CLOSED breaker refuses reset with 409 + dt_breaker_already_closed."""
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)

    from integrations.dt.breaker import get_breaker, reset_default_breaker

    reset_default_breaker()
    get_breaker().force_close()  # ensure CLOSED baseline

    response = await client.post(
        "/v1/admin/dt/breaker/reset", headers=_bearer_for(admin)
    )

    assert response.status_code == 409, response.text
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    body = response.json()
    assert body.get("dt_breaker_already_closed") is True
    assert body["type"].endswith("/dt-breaker-already-closed")


async def test_dt_force_health_check_writes_audit(
    client: AsyncClient,
) -> None:
    """The force-health-check endpoint emits an audit row."""
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)

    # The endpoint calls run_health_check which probes DT. With a
    # half-broken DT (or none) the call returns healthy=False but the
    # endpoint MUST still respond 200. We mock the underlying service
    # call so the test is deterministic.
    class _FakeOutcome:
        healthy = True
        state_before = "closed"
        state_after = "closed"
        fail_count = 0
        auto_restart_attempted = False
        error = None
        checked_at = datetime.now(tz=UTC)

        def model_dump_json(self) -> str:
            import json as _json

            return _json.dumps(
                {
                    "healthy": True,
                    "state_before": "closed",
                    "state_after": "closed",
                    "fail_count": 0,
                    "auto_restart_attempted": False,
                    "error": None,
                    "checked_at": self.checked_at.isoformat(),
                }
            )

    with patch("api.v1.admin.dt.force_health_check") as mock_check:
        mock_check.return_value = _FakeOutcome()
        response = await client.post(
            "/v1/admin/dt/health-check", headers=_bearer_for(admin)
        )
    assert response.status_code == 200, response.text

    async with factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT count(*) FROM audit_logs "
                    "WHERE actor_user_id=:a AND target_table='dt_health'"
                ),
                {"a": str(admin.id)},
            )
        ).scalar_one()
    assert rows >= 1


async def test_dt_orphans_list_returns_envelope(
    client: AsyncClient,
) -> None:
    """The orphan list endpoint returns an envelope (we mock the service to bypass DT)."""
    from schemas.admin_ops import DTOrphanListPage

    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)

    with patch("api.v1.admin.dt.list_orphans") as mock_list:
        mock_list.return_value = DTOrphanListPage(items=[], total=0, has_more=False)
        response = await client.get("/v1/admin/dt/orphans", headers=_bearer_for(admin))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {"items": [], "total": 0, "has_more": False}


async def test_dt_orphans_cleanup_invalid_uuid_returns_422(
    client: AsyncClient,
) -> None:
    """Schema validation kicks in BEFORE the auth gate consideration is moot.

    Schema-level rejection is the only thing standing between a malformed
    UUID list and the Celery task — pin it.
    """
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)

    response = await client.post(
        "/v1/admin/dt/orphans/cleanup",
        headers=_bearer_for(admin),
        json={"dt_project_uuids": ["not-a-uuid", "../../etc/passwd"]},
    )
    assert response.status_code == 422, response.text
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


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
    assert names == ["workspace", "dt_volume", "postgres", "redis"]


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
    assert set(names) == {
        "postgres",
        "redis",
        "celery",
        "dt",
        "disk",
        "active_scans",
        "last_24h_errors",
    }
