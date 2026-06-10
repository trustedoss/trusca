"""
Team-scoped audit read /v1/audit — M-3 regression.

The guide describes a two-tier audit RBAC (super_admin = all, team_admin =
own teams) but the only read path was the super-admin-only /v1/admin/audit, so
a team_admin could not read audit at all. This endpoint adds the team-scoped
read; the scope MUST come from per-team roles, never the coarse highest role,
so a team_admin in team A who is merely a developer in team B sees only A
(OWASP A01 / CWE-863).

Runs against real Postgres: the scope is a SQL WHERE on audit_logs.team_id and
the auth gate depends on the DB-resolved membership set.
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
    make_team,
    make_user,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent
PROBLEM_JSON = "application/problem+json"

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip team-scoped audit tests")
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
            f"alembic upgrade head failed; team-scoped audit tests cannot run\n"
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


async def _seed_audit_row(factory, *, team_id: uuid.UUID | None, action: str) -> None:
    """Insert one audit_logs row (append-only; INSERT is allowed)."""
    from models import AuditLog

    async with factory() as session:
        session.add(
            AuditLog(
                team_id=team_id,
                action=action,
                target_table="projects",
                target_id=str(uuid.uuid4()),
            )
        )
        await session.commit()


async def test_anonymous_returns_401(client: AsyncClient) -> None:
    response = await client.get("/v1/audit")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_developer_returns_403(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session)
        await make_membership(session, user=user, team=team, role="developer")

    response = await client.get("/v1/audit", headers=_bearer_for(user))
    assert response.status_code == 403
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_team_admin_sees_only_own_team(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team_a = await make_team(session, organization=org)
        team_b = await make_team(session, organization=org)
        admin = await make_user(session)
        await make_membership(session, user=admin, team=team_a, role="team_admin")
        # developer in team_b: must NOT widen the audit scope to team_b.
        await make_membership(session, user=admin, team=team_b, role="developer")
        team_a_id = team_a.id
        team_b_id = team_b.id

    action = f"m3-scope-{uuid.uuid4().hex[:8]}"
    await _seed_audit_row(factory, team_id=team_a_id, action=action)
    await _seed_audit_row(factory, team_id=team_b_id, action=action)

    response = await client.get(f"/v1/audit?action={action}", headers=_bearer_for(admin))
    assert response.status_code == 200
    items = response.json()["items"]
    team_ids = {item["team_id"] for item in items}
    # team A (team_admin) is visible; team B (developer-only) is not.
    assert str(team_a_id) in team_ids
    assert str(team_b_id) not in team_ids
    assert items and all(item["team_id"] == str(team_a_id) for item in items)


async def test_super_admin_sees_all_teams(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team_a = await make_team(session, organization=org)
        team_b = await make_team(session, organization=org)
        admin = await make_user(session, is_superuser=True)
        team_a_id = team_a.id
        team_b_id = team_b.id

    action = f"m3-all-{uuid.uuid4().hex[:8]}"
    await _seed_audit_row(factory, team_id=team_a_id, action=action)
    await _seed_audit_row(factory, team_id=team_b_id, action=action)

    response = await client.get(f"/v1/audit?action={action}", headers=_bearer_for(admin))
    assert response.status_code == 200
    team_ids = {item["team_id"] for item in response.json()["items"]}
    assert {str(team_a_id), str(team_b_id)} <= team_ids
