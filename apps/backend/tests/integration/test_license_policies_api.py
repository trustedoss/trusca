"""
Integration tests for /v1/license-policies — v2.2 Track C (c1).

The RBAC matrix (anonymous / developer / team_admin / super_admin) is the spine,
mirroring tests/integration/test_api_keys_api.py.

  - Anonymous                 -> 401 + Problem Details
  - Developer (member)        -> 200 read effective / 403 on team upsert+delete
  - Team Admin                -> 200 upsert/read/delete own team / 403 org
  - Super Admin               -> 200 for org default + any team
  - Non-member                -> 403 read / 403 write (cross-team leak guard)

Plus contract assertions:
  - All 4xx responses use application/problem+json (RFC 7807).
  - Org endpoints existence-hide: non-super-admin → 404.
  - Malformed payload → 422.
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

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
PROBLEM_JSON = "application/problem+json"

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip license_policies API tests")
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
            f"alembic upgrade head failed; license_policies API tests cannot run\n"
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


_VALID_BODY = {
    "name": "Engineering",
    "category_overrides": {"MPL-2.0": "forbidden"},
    "unknown_license_category": "conditional",
}


# ---------------------------------------------------------------------------
# Anonymous → 401
# ---------------------------------------------------------------------------


async def test_team_upsert_anonymous_401(client: AsyncClient) -> None:
    r = await client.put(f"/v1/license-policies/teams/{uuid.uuid4()}", json=_VALID_BODY)
    assert r.status_code == 401
    assert r.headers["content-type"].startswith(PROBLEM_JSON)
    body = r.json()
    assert body["status"] == 401
    assert "title" in body and "instance" in body


async def test_team_get_anonymous_401(client: AsyncClient) -> None:
    r = await client.get(f"/v1/license-policies/teams/{uuid.uuid4()}")
    assert r.status_code == 401
    assert r.headers["content-type"].startswith(PROBLEM_JSON)


async def test_org_get_anonymous_401(client: AsyncClient) -> None:
    r = await client.get(f"/v1/license-policies/org/{uuid.uuid4()}")
    assert r.status_code == 401
    assert r.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# Team admin happy path: upsert → read → delete
# ---------------------------------------------------------------------------


async def test_team_admin_upsert_read_delete(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        admin = await make_user(session)
        await make_membership(session, user=admin, team=team, role="team_admin")

    # Upsert.
    r = await client.put(
        f"/v1/license-policies/teams/{team.id}",
        headers=_bearer_for(admin),
        json=_VALID_BODY,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["team_id"] == str(team.id)
    assert body["organization_id"] == str(org.id)
    assert body["category_overrides"] == {"MPL-2.0": "forbidden"}
    assert body["compound_operator_strategy"]["OR"] == "least_restrictive"

    # Read effective.
    r2 = await client.get(f"/v1/license-policies/teams/{team.id}", headers=_bearer_for(admin))
    assert r2.status_code == 200, r2.text
    assert r2.json()["team_id"] == str(team.id)

    # Delete.
    r3 = await client.delete(f"/v1/license-policies/teams/{team.id}", headers=_bearer_for(admin))
    assert r3.status_code == 204

    # Read again → 404 (no policy, falls back to static).
    r4 = await client.get(f"/v1/license-policies/teams/{team.id}", headers=_bearer_for(admin))
    assert r4.status_code == 404
    assert r4.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# Developer (member) RBAC
# ---------------------------------------------------------------------------


async def test_developer_can_read_but_not_write(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        admin = await make_user(session)
        await make_membership(session, user=admin, team=team, role="team_admin")
        dev = await make_user(session)
        await make_membership(session, user=dev, team=team, role="developer")

    # Admin seeds a policy.
    seed = await client.put(
        f"/v1/license-policies/teams/{team.id}",
        headers=_bearer_for(admin),
        json=_VALID_BODY,
    )
    assert seed.status_code == 200, seed.text

    # Developer reads it.
    rr = await client.get(f"/v1/license-policies/teams/{team.id}", headers=_bearer_for(dev))
    assert rr.status_code == 200, rr.text

    # Developer cannot upsert.
    rw = await client.put(
        f"/v1/license-policies/teams/{team.id}",
        headers=_bearer_for(dev),
        json=_VALID_BODY,
    )
    assert rw.status_code == 403
    assert rw.headers["content-type"].startswith(PROBLEM_JSON)

    # Developer cannot delete.
    rd = await client.delete(f"/v1/license-policies/teams/{team.id}", headers=_bearer_for(dev))
    assert rd.status_code == 403


# ---------------------------------------------------------------------------
# Non-member cross-team leak guard
# ---------------------------------------------------------------------------


async def test_non_member_read_forbidden(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        admin = await make_user(session)
        await make_membership(session, user=admin, team=team, role="team_admin")
        outsider = await make_user(session)

    await client.put(
        f"/v1/license-policies/teams/{team.id}",
        headers=_bearer_for(admin),
        json=_VALID_BODY,
    )

    r = await client.get(f"/v1/license-policies/teams/{team.id}", headers=_bearer_for(outsider))
    assert r.status_code == 403
    assert r.headers["content-type"].startswith(PROBLEM_JSON)


async def test_team_admin_other_team_upsert_forbidden(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team_a = await make_team(session, organization=org)
        team_b = await make_team(session, organization=org)
        admin = await make_user(session)
        await make_membership(session, user=admin, team=team_a, role="team_admin")

    r = await client.put(
        f"/v1/license-policies/teams/{team_b.id}",
        headers=_bearer_for(admin),
        json=_VALID_BODY,
    )
    assert r.status_code == 403
    assert r.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# Org default — super_admin only (existence-hide for others)
# ---------------------------------------------------------------------------


async def test_org_default_super_admin_upsert_and_read(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        admin = await make_user(session, is_superuser=True)

    r = await client.put(
        f"/v1/license-policies/org/{org.id}",
        headers=_bearer_for(admin),
        json=_VALID_BODY,
    )
    assert r.status_code == 200, r.text
    assert r.json()["team_id"] is None

    r2 = await client.get(f"/v1/license-policies/org/{org.id}", headers=_bearer_for(admin))
    assert r2.status_code == 200, r2.text


async def test_org_default_non_super_admin_404(client: AsyncClient) -> None:
    """A team_admin hitting the org endpoint sees 404 (admin existence-hide)."""
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        admin = await make_user(session)
        await make_membership(session, user=admin, team=team, role="team_admin")

    r = await client.put(
        f"/v1/license-policies/org/{org.id}",
        headers=_bearer_for(admin),
        json=_VALID_BODY,
    )
    assert r.status_code == 404
    assert r.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# Validation → 422 RFC 7807
# ---------------------------------------------------------------------------


async def test_malformed_payload_422(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        admin = await make_user(session)
        await make_membership(session, user=admin, team=team, role="team_admin")

    r = await client.put(
        f"/v1/license-policies/teams/{team.id}",
        headers=_bearer_for(admin),
        json={"category_overrides": {"MIT": "banana"}},  # invalid category
    )
    assert r.status_code == 422
    assert r.headers["content-type"].startswith(PROBLEM_JSON)


async def test_oversized_override_map_422(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        admin = await make_user(session)
        await make_membership(session, user=admin, team=team, role="team_admin")

    huge = {f"Lic-{i}": "allowed" for i in range(10_000)}
    r = await client.put(
        f"/v1/license-policies/teams/{team.id}",
        headers=_bearer_for(admin),
        json={"category_overrides": huge},
    )
    assert r.status_code == 422
    assert r.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# List endpoint
# ---------------------------------------------------------------------------


async def test_list_returns_visible_policies(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        admin = await make_user(session)
        await make_membership(session, user=admin, team=team, role="team_admin")

    await client.put(
        f"/v1/license-policies/teams/{team.id}",
        headers=_bearer_for(admin),
        json=_VALID_BODY,
    )
    r = await client.get(
        f"/v1/license-policies?organization_id={org.id}", headers=_bearer_for(admin)
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] >= 1
    assert any(item["team_id"] == str(team.id) for item in body["items"])
