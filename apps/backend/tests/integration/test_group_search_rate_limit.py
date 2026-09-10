# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
``GET /v1/groups`` is rate-limited now -- group-hierarchy Phase 6 security
review.

``tests/integration/test_group_directory_read_api.py`` covers
``services.group_directory_service.list_groups`` directly (no HTTP layer at
all); nothing anywhere hit this endpoint through the real FastAPI app before
this file. That gap mattered the moment
``core.config.group_search_rate_limit`` and its ``@limiter.limit(...)``
decorator landed on the router (``api/v1/groups.py``) alongside the search
floor: slowapi decorator placement/ordering is a real, previously-seen class
of mistake in this codebase (wrong relative order to the route decorator, or
a missing ``key_func``), and a pure service-level test can't see it -- only
a real request through the app can. This file's only job is confirming the
decorated route still answers normally; it does not attempt to actually
trigger a 429 (no other rate-limited endpoint in this codebase's test suite
does either -- see ``test_search_api.py``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from core.security import create_access_token
from models import User
from tests._db_required import migrate_to_head
from tests._helpers import make_membership, make_organization, make_team, make_user

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


async def test_list_groups_still_answers_normally_with_the_rate_limit_decorator(
    client: AsyncClient,
) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session)
        await make_membership(session, user=user, team=team, role="developer")

    resp = await client.get("/v1/groups", headers=_bearer_for(user))
    assert resp.status_code == 200, resp.text
    assert str(team.id) in {item["id"] for item in resp.json()["items"]}


async def test_list_groups_search_mode_still_answers_normally_too(
    client: AsyncClient,
) -> None:
    """The decorator wraps the whole endpoint (both modes, see
    ``group_search_rate_limit``'s own docstring) -- confirm the search
    branch specifically, not just the drill-down default the test above
    exercises."""
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session)
        await make_membership(session, user=user, team=team, role="developer")

    resp = await client.get(
        "/v1/groups", params={"q": team.name}, headers=_bearer_for(user)
    )
    assert resp.status_code == 200, resp.text
    assert str(team.id) in {item["id"] for item in resp.json()["items"]}
