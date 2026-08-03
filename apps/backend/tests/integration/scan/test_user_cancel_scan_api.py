"""
HTTP-level tests for POST /v1/scans/{scan_id}/cancel — PR-A1 user cancel.

Drives the FastAPI app via httpx ASGITransport against a real Postgres so the
``require_role("developer")`` gate, the team-access existence-hide, and the
RFC 7807 envelope all run end-to-end.

Coverage:
  - anonymous                          → 401 problem+json.
  - own-team developer, queued scan    → 200 / status='cancelled'.
  - other-team developer               → 404 problem+json (existence hide).
  - already-terminal scan              → 409 problem+json + scan_already_cancelled.

The Celery ``revoke`` call is patched so the test never depends on a live
worker accepting the SIGTERM.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

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
        pytest.skip("DATABASE_URL not set — skip user cancel API tests")
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
            "alembic upgrade head failed; user cancel API tests cannot run\n"
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
# Auth
# ---------------------------------------------------------------------------


async def test_anonymous_cancel_returns_401(client: AsyncClient) -> None:
    import uuid

    response = await client.post(f"/v1/scans/{uuid.uuid4()}/cancel")
    assert response.status_code == 401, response.text
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# Happy path — own team
# ---------------------------------------------------------------------------


async def test_own_team_developer_cancels_scan(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session)
        await make_membership(session, user=user, team=team, role="developer")
        project = await make_project(session, team=team)
        scan = await make_scan(session, project=project, status="queued")

    with patch("tasks.celery_app.celery_app.control.revoke"):
        response = await client.post(
            f"/v1/scans/{scan.id}/cancel", headers=_bearer_for(user)
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "cancelled"
    assert body["error_message"] == "cancelled by user"


# ---------------------------------------------------------------------------
# M1 — the audit row for a user-cancel carries the owning team_id (not NULL)
# ---------------------------------------------------------------------------


async def test_user_cancel_audit_row_has_owning_team_id(client: AsyncClient) -> None:
    """The status='cancelled' update must emit an audit row scoped to the team.

    The audit ``before_flush`` listener reads ``team_id`` from the contextvar;
    the user-cancel path now binds the owning team after the access gate (M1),
    so ``audit_logs.team_id`` is the project's team, never NULL.
    """
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session)
        await make_membership(session, user=user, team=team, role="developer")
        project = await make_project(session, team=team)
        scan = await make_scan(session, project=project, status="queued")
        team_id = team.id
        scan_id = scan.id
        user_id = user.id

    with patch("tasks.celery_app.celery_app.control.revoke"):
        response = await client.post(
            f"/v1/scans/{scan_id}/cancel", headers=_bearer_for(user)
        )
    assert response.status_code == 200, response.text

    async with factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT team_id FROM audit_logs "
                    "WHERE actor_user_id = :a "
                    "  AND target_table = 'scans' "
                    "  AND target_id = :t "
                    "  AND action = 'update' "
                    "ORDER BY created_at DESC LIMIT 1"
                ),
                {"a": str(user_id), "t": str(scan_id)},
            )
        ).first()
    assert row is not None, "expected an audit row for the cancel update"
    assert row[0] is not None, "audit_logs.team_id must not be NULL (M1)"
    assert str(row[0]) == str(team_id)


# ---------------------------------------------------------------------------
# RBAC — other team is existence-hidden as 404
# ---------------------------------------------------------------------------


async def test_other_team_developer_gets_404(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        owning_team = await make_team(session, organization=org)
        other_team = await make_team(session, organization=org)
        other_user = await make_user(session)
        await make_membership(
            session, user=other_user, team=other_team, role="developer"
        )
        project = await make_project(session, team=owning_team)
        scan = await make_scan(session, project=project, status="running")

    with patch("tasks.celery_app.celery_app.control.revoke"):
        response = await client.post(
            f"/v1/scans/{scan.id}/cancel", headers=_bearer_for(other_user)
        )
    assert response.status_code == 404, response.text
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# Idempotency — already terminal → 409 with extension field
# ---------------------------------------------------------------------------


async def test_already_cancelled_returns_409_problem(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session)
        await make_membership(session, user=user, team=team, role="developer")
        project = await make_project(session, team=team)
        scan = await make_scan(session, project=project, status="cancelled")

    response = await client.post(
        f"/v1/scans/{scan.id}/cancel", headers=_bearer_for(user)
    )
    assert response.status_code == 409, response.text
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    body = response.json()
    assert body.get("scan_already_cancelled") is True
    assert body["type"].endswith("/scan-already-cancelled")
