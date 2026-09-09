# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
A team-scoped API key reaches its group's subtree (group-hierarchy Phase 6).

``core.api_key_auth.get_api_key_principal`` narrows a team-scoped key's
``CurrentUser.team_ids``/``team_roles`` to exactly ``{api_key.team_id: role}``
(see ``tests/integration/test_principal_construction_parity.py``: this is a
DIRECT membership map, built the same way for JWT, API-key, and WebSocket
principals, with no cascade expansion anywhere in construction). Every
downstream gate this project has (``core.authz.can_access_group``,
``assert_team_access``, ``team_scope_filter``) treats that map exactly like
a human's direct memberships and cascade-expands it identically when
``GROUP_CASCADE_ENABLED`` is on -- there is exactly one cascade-aware
primitive in this codebase, and both principal kinds flow through it. So a
team-scoped key issued for a PARENT group should already reach a project
under a CHILD group once cascade is on, with no code change of its own:
this file is the end-to-end proof that architecture claim actually holds,
not just a plausible reading of the two modules' docstrings.

Mirrors ``test_scans_api.py``'s existing API-key-auth shape
(``_bearer_for`` / ``_factory`` / issuing a key through the real
``POST /v1/api-keys`` endpoint, not by constructing an ``APIKey`` row
directly) so this is a genuine end-to-end path, not a unit test of
``get_api_key_principal`` in isolation.
"""

from __future__ import annotations

import uuid
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


async def _seed_parent_child_groups(client: AsyncClient):
    """Org, parent group A (issuer is group_admin there, directly), child
    group B nested under A, and a project owned by B. Returns
    (parent_id, user, project_id)."""
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        parent = await make_team(session, organization=org)
        child = await make_team(session, organization=org, parent=parent)
        user = await make_user(session)
        await make_membership(session, user=user, team=parent, role="group_admin")
        project = await make_project(session, team=child)
    return parent.id, user, project.id


async def _issue_team_api_key(
    client: AsyncClient, *, user: User, team_id: uuid.UUID
) -> str:
    resp = await client.post(
        "/v1/api-keys",
        json={
            "name": "ci-parent-group-key",
            "scope": "team",
            "team_id": str(team_id),
            # Triggering a scan is a POST; a read-only key would 403 on the
            # breadth check alone (core.api_key_auth._assert_breadth_allows)
            # regardless of team access, which would make that 403 ambiguous
            # with the thing this file actually tests. read_write keeps the
            # assertion pinned to team/cascade access specifically.
            "permission_breadth": "read_write",
        },
        headers=_bearer_for(user),
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["raw_key"])


async def test_team_scoped_key_for_the_parent_reaches_a_project_under_the_child(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GROUP_CASCADE_ENABLED", "true")
    parent_id, user, project_id = await _seed_parent_child_groups(client)
    raw_key = await _issue_team_api_key(client, user=user, team_id=parent_id)

    # POST /v1/projects/{project_id}/scans is the one endpoint this suite's
    # sibling file (test_scans_api.py) already proves accepts a real tos_ key
    # end to end (require_role_or_api_key), so this reuses that exact path
    # rather than introducing a second API-key entry point to trust.
    resp = await client.post(
        f"/v1/projects/{project_id}/scans",
        json={"kind": "source"},
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    assert resp.status_code == 202, resp.text


async def test_team_scoped_key_for_the_parent_is_flat_only_with_cascade_off(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same fixture, same key, cascade OFF: the project under the child is
    unreachable -- pins that this is genuinely gated by the flag, not
    something the key's scope narrowing bypasses on its own."""
    monkeypatch.setenv("GROUP_CASCADE_ENABLED", "false")
    parent_id, user, project_id = await _seed_parent_child_groups(client)
    raw_key = await _issue_team_api_key(client, user=user, team_id=parent_id)

    resp = await client.post(
        f"/v1/projects/{project_id}/scans",
        json={"kind": "source"},
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    assert resp.status_code in (403, 404), resp.text
