"""
Build-gate policy over HTTP.

The cases that matter are the boundaries, not the happy path. Writing what
blocks a build is the ability to stop a build being blocked, so who may write
at which scope is the whole security content of this surface, and the answers
are asserted per grade rather than for one representative caller.

The read side is deliberately wider than the write side: an auditor needs to
see what applies without being able to change it, which is the reason the
lowest grade exists at all.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from core.security import create_access_token
from models import User
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_team,
    make_user,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
PROBLEM_JSON = "application/problem+json"

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set: skip gate policy API tests")
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
        pytest.skip(f"alembic upgrade head failed\n{result.stdout}\n{result.stderr}")


@pytest.fixture
def app():
    from main import app as fastapi_app

    return fastapi_app


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


async def _factory(client: AsyncClient):
    app = client._transport.app  # type: ignore[attr-defined]
    factory = getattr(app.state, "session_factory", None)
    if factory is None:
        from core.db import _ensure_state

        factory = _ensure_state(app)
    return factory


def _bearer_for(user: User) -> dict[str, str]:
    role = "super_admin" if user.is_superuser else None
    return {"Authorization": f"Bearer {create_access_token(subject=str(user.id), role=role)}"}


async def _seed(client: AsyncClient, *, role: str = "team_admin"):
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session)
        await make_membership(session, user=user, team=team, role=role)
        project = await make_project(session, team=team)
        return org.id, team.id, user, project.id


async def _seed_super_admin(client: AsyncClient):
    factory = await _factory(client)
    async with factory() as session:
        return await make_user(session, is_superuser=True)


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


async def test_a_team_admin_writes_and_replaces_their_team_policy(client) -> None:
    """A second PUT replaces rather than adding: the scope is the identity."""
    _, team_id, user, _ = await _seed(client)
    headers = _bearer_for(user)

    first = await client.put(
        f"/v1/gate-policies/teams/{team_id}",
        headers=headers,
        json={"epss_threshold": 0.5},
    )
    second = await client.put(
        f"/v1/gate-policies/teams/{team_id}",
        headers=headers,
        json={"epss_threshold": 0.9, "malicious_blocks": True},
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["epss_threshold"] == 0.9
    assert second.json()["malicious_blocks"] is True


async def test_an_omitted_field_stops_overriding(client) -> None:
    """A PUT replaces the row, so leaving a field out clears it.

    That is the mechanism a team uses to hand a decision back to its
    organization, and it only works if omission stores NULL rather than
    keeping the previous value.
    """
    _, team_id, user, _ = await _seed(client)
    headers = _bearer_for(user)

    await client.put(
        f"/v1/gate-policies/teams/{team_id}",
        headers=headers,
        json={"epss_threshold": 0.5, "malicious_blocks": False},
    )
    response = await client.put(
        f"/v1/gate-policies/teams/{team_id}",
        headers=headers,
        json={"epss_threshold": 0.5},
    )

    assert response.status_code == 200, response.text
    assert response.json()["malicious_blocks"] is None


@pytest.mark.parametrize("role", ["viewer", "developer"])
async def test_a_grade_below_team_admin_may_not_write(client, role: str) -> None:
    """Membership is not authority: the grade decides, and it decides per team."""
    _, team_id, user, _ = await _seed(client, role=role)

    response = await client.put(
        f"/v1/gate-policies/teams/{team_id}",
        headers=_bearer_for(user),
        json={"epss_threshold": 0.5},
    )

    assert response.status_code == 403, response.text
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_a_team_admin_of_another_team_may_not_write(client) -> None:
    """The grade is per team, so administering one is not administering all."""
    _, team_id, _, _ = await _seed(client)
    _, _, outsider, _ = await _seed(client)

    response = await client.put(
        f"/v1/gate-policies/teams/{team_id}",
        headers=_bearer_for(outsider),
        json={"epss_threshold": 0.5},
    )

    assert response.status_code == 403, response.text


async def test_only_a_super_admin_writes_the_organization_default(client) -> None:
    """It is the floor every team inherits, so it answers to the deployment."""
    org_id, _, team_admin, _ = await _seed(client)
    refused = await client.put(
        f"/v1/gate-policies/org/{org_id}",
        headers=_bearer_for(team_admin),
        json={"epss_threshold": 0.4},
    )
    assert refused.status_code == 403, refused.text

    super_admin = await _seed_super_admin(client)
    allowed = await client.put(
        f"/v1/gate-policies/org/{org_id}",
        headers=_bearer_for(super_admin),
        json={"epss_threshold": 0.4},
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["team_id"] is None


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


async def test_a_viewer_reads_the_effective_policy_and_where_it_came_from(client) -> None:
    """The read side is wider than the write side, and says which row to edit."""
    org_id, team_id, viewer, project_id = await _seed(client, role="viewer")
    super_admin = await _seed_super_admin(client)
    await client.put(
        f"/v1/gate-policies/org/{org_id}",
        headers=_bearer_for(super_admin),
        json={"epss_threshold": 0.4, "malicious_blocks": False},
    )

    response = await client.get(
        f"/v1/gate-policies/effective/{project_id}", headers=_bearer_for(viewer)
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["epss_threshold"] == 0.4
    assert body["sources"]["epss_threshold"] == "organization"
    # Nothing decided reachability, so the deployment's answer stands and the
    # source says so rather than implying someone chose it.
    assert body["sources"]["reachable_critical_only"] == "deployment"


async def test_a_team_row_is_reported_as_the_team_source(client) -> None:
    org_id, team_id, user, project_id = await _seed(client)
    super_admin = await _seed_super_admin(client)
    await client.put(
        f"/v1/gate-policies/org/{org_id}",
        headers=_bearer_for(super_admin),
        json={"epss_threshold": 0.4},
    )
    await client.put(
        f"/v1/gate-policies/teams/{team_id}",
        headers=_bearer_for(user),
        json={"epss_threshold": 0.9},
    )

    response = await client.get(
        f"/v1/gate-policies/effective/{project_id}", headers=_bearer_for(user)
    )

    assert response.json()["epss_threshold"] == 0.9
    assert response.json()["sources"]["epss_threshold"] == "team"


async def test_a_stranger_cannot_learn_that_a_team_exists(client) -> None:
    """Existence-hidden: the read gate answers 404, not 403.

    The team must have a policy row for this to test anything. Without one the
    endpoint answers 404 anyway, and the assertion passes whether or not the
    gate is there: removing the guard entirely still produced a 404 until this
    seeded a row, which is the difference between hidden and simply absent.
    """
    _, team_id, owner, _ = await _seed(client)
    await client.put(
        f"/v1/gate-policies/teams/{team_id}",
        headers=_bearer_for(owner),
        json={"epss_threshold": 0.5},
    )
    _, _, stranger, _ = await _seed(client)

    response = await client.get(
        f"/v1/gate-policies/teams/{team_id}", headers=_bearer_for(stranger)
    )

    assert response.status_code == 404, response.text


async def test_a_team_without_a_row_reads_as_absent_not_empty(client) -> None:
    _, team_id, user, _ = await _seed(client)

    response = await client.get(
        f"/v1/gate-policies/teams/{team_id}", headers=_bearer_for(user)
    )

    assert response.status_code == 404, response.text


# ---------------------------------------------------------------------------
# Deleting
# ---------------------------------------------------------------------------


async def test_deleting_a_team_row_returns_it_to_the_organization(client) -> None:
    org_id, team_id, user, project_id = await _seed(client)
    super_admin = await _seed_super_admin(client)
    await client.put(
        f"/v1/gate-policies/org/{org_id}",
        headers=_bearer_for(super_admin),
        json={"epss_threshold": 0.4},
    )
    await client.put(
        f"/v1/gate-policies/teams/{team_id}",
        headers=_bearer_for(user),
        json={"epss_threshold": 0.9},
    )

    removed = await client.delete(
        f"/v1/gate-policies/teams/{team_id}", headers=_bearer_for(user)
    )
    effective = await client.get(
        f"/v1/gate-policies/effective/{project_id}", headers=_bearer_for(user)
    )

    assert removed.status_code == 204, removed.text
    assert effective.json()["epss_threshold"] == 0.4
    assert effective.json()["sources"]["epss_threshold"] == "organization"


async def test_deleting_what_was_never_written_is_a_404(client) -> None:
    """So a caller is never told a delete removed something it did not."""
    _, team_id, user, _ = await _seed(client)

    response = await client.delete(
        f"/v1/gate-policies/teams/{team_id}", headers=_bearer_for(user)
    )

    assert response.status_code == 404, response.text


async def test_an_unknown_team_is_a_404_on_every_verb(client) -> None:
    _, _, user, _ = await _seed(client)
    ghost = uuid.uuid4()
    headers = _bearer_for(user)

    for response in (
        await client.get(f"/v1/gate-policies/teams/{ghost}", headers=headers),
        await client.put(
            f"/v1/gate-policies/teams/{ghost}", headers=headers, json={"epss_threshold": 0.5}
        ),
        await client.delete(f"/v1/gate-policies/teams/{ghost}", headers=headers),
    ):
        assert response.status_code == 404, response.text


async def test_the_row_survives_only_while_its_team_does(client) -> None:
    """The cascade is the reason a deleted team leaves no orphan policy."""
    from models import GatePolicy, Team

    _, team_id, user, _ = await _seed(client)
    await client.put(
        f"/v1/gate-policies/teams/{team_id}",
        headers=_bearer_for(user),
        json={"epss_threshold": 0.5},
    )

    factory = await _factory(client)
    async with factory() as session:
        team = (await session.execute(select(Team).where(Team.id == team_id))).scalar_one()
        await session.delete(team)
        await session.commit()
        rows: list[Any] = list(
            (
                await session.execute(
                    select(GatePolicy).where(GatePolicy.team_id == team_id)
                )
            )
            .scalars()
            .all()
        )

    assert rows == []
