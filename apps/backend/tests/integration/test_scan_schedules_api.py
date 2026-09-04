"""
Scan schedules over HTTP (N18).

Permission split mirrors ``/v1/gate-policies``: a project's own schedule is
its team administrator's call, the organization default is a super admin's,
and reads sit at viewer. The property worth pinning past the CRUD plumbing is
the fall-through itself: the effective endpoint names which scope actually
decided, because that is the first thing an operator asks when a project
scans on a cadence they did not expect.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from core.security import create_access_token
from models import User
from tests._db_required import migrate_to_head
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_team,
    make_user,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


@pytest.fixture
def app():
    from main import app as fastapi_app

    return fastapi_app


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
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


async def _seed(client: AsyncClient, *, role: str = "team_admin"):
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session)
        await make_membership(session, user=user, team=team, role=role)
        project = await make_project(session, team=team)
    return org, team, user, project


DAILY_9AM_UTC = {"is_active": True, "cadence": "daily", "hour": 9, "timezone": "UTC"}


# ---------------------------------------------------------------------------
# No schedule anywhere: the effective view says so
# ---------------------------------------------------------------------------


async def test_a_project_with_no_schedule_anywhere_shows_none(client) -> None:
    _org, _team, user, project = await _seed(client)

    response = await client.get(
        f"/v1/scan-schedules/effective/{project.id}", headers=_bearer_for(user)
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "none"
    assert body["cadence"] is None


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


async def test_a_team_admin_may_write_their_own_projects_schedule(client) -> None:
    _org, _team, user, project = await _seed(client)

    response = await client.put(
        f"/v1/scan-schedules/projects/{project.id}",
        headers=_bearer_for(user),
        json=DAILY_9AM_UTC,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["cadence"] == "daily"
    assert body["hour"] == 9
    assert body["project_id"] == str(project.id)


async def test_a_developer_may_not_write_their_projects_schedule(client) -> None:
    _org, _team, user, project = await _seed(client, role="developer")

    response = await client.put(
        f"/v1/scan-schedules/projects/{project.id}",
        headers=_bearer_for(user),
        json=DAILY_9AM_UTC,
    )

    assert response.status_code == 403, response.text


async def test_a_team_admin_may_not_write_another_teams_project_schedule(client) -> None:
    _org, _team, _user, project = await _seed(client)
    _org2, _team2, other_admin, _project2 = await _seed(client)

    response = await client.put(
        f"/v1/scan-schedules/projects/{project.id}",
        headers=_bearer_for(other_admin),
        json=DAILY_9AM_UTC,
    )

    assert response.status_code == 403, response.text


async def test_a_team_admin_may_not_write_the_organization_default(client) -> None:
    org, _team, user, _project = await _seed(client)

    response = await client.put(
        f"/v1/scan-schedules/org/{org.id}",
        headers=_bearer_for(user),
        json=DAILY_9AM_UTC,
    )

    assert response.status_code == 403, response.text


async def test_a_super_admin_may_write_the_organization_default(client) -> None:
    org, _team, _user, project = await _seed(client)
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)

    response = await client.put(
        f"/v1/scan-schedules/org/{org.id}",
        headers=_bearer_for(admin),
        json=DAILY_9AM_UTC,
    )
    assert response.status_code == 200, response.text

    effective = await client.get(
        f"/v1/scan-schedules/effective/{project.id}", headers=_bearer_for(admin)
    )
    assert effective.json()["source"] == "organization"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


async def test_a_weekly_schedule_without_a_day_is_refused(client) -> None:
    _org, _team, user, project = await _seed(client)

    response = await client.put(
        f"/v1/scan-schedules/projects/{project.id}",
        headers=_bearer_for(user),
        json={"is_active": True, "cadence": "weekly", "hour": 9, "timezone": "UTC"},
    )

    assert response.status_code == 422, response.text


async def test_an_unknown_timezone_is_refused(client) -> None:
    _org, _team, user, project = await _seed(client)

    response = await client.put(
        f"/v1/scan-schedules/projects/{project.id}",
        headers=_bearer_for(user),
        json={"is_active": True, "cadence": "daily", "hour": 9, "timezone": "Nowhere/Imaginary"},
    )

    assert response.status_code == 422, response.text


# ---------------------------------------------------------------------------
# Reading and deleting a project's own row
# ---------------------------------------------------------------------------


async def test_reading_a_project_with_no_row_of_its_own_is_404(client) -> None:
    _org, _team, user, project = await _seed(client)

    response = await client.get(
        f"/v1/scan-schedules/projects/{project.id}", headers=_bearer_for(user)
    )

    assert response.status_code == 404, response.text


async def test_deleting_a_projects_schedule_returns_it_to_the_organization_default(
    client,
) -> None:
    org, _team, user, project = await _seed(client)
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)
    await client.put(
        f"/v1/scan-schedules/org/{org.id}", headers=_bearer_for(admin), json=DAILY_9AM_UTC
    )
    await client.put(
        f"/v1/scan-schedules/projects/{project.id}",
        headers=_bearer_for(user),
        json={"is_active": True, "cadence": "daily", "hour": 14, "timezone": "UTC"},
    )

    delete_response = await client.delete(
        f"/v1/scan-schedules/projects/{project.id}", headers=_bearer_for(user)
    )
    assert delete_response.status_code == 204, delete_response.text

    effective = await client.get(
        f"/v1/scan-schedules/effective/{project.id}", headers=_bearer_for(user)
    )
    body = effective.json()
    assert body["source"] == "organization"
    assert body["hour"] == 9
