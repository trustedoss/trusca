# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Saved-search API — integration tests (S3).

Two concerns.

**Ownership.** Every row is one user's. Another user's row must answer 404, not
403 and not 200 — the two conditions are deliberately indistinguishable so that
probing an id teaches nothing about whether it exists elsewhere.

**Lifecycle sequences.** The testing standards call these out as their own
category, because a suite of single-operation tests passes while a sequence
still breaks (the github-app re-registration that 409'd forever is the case
that put the rule there). The sequence that matters here is
create → delete → create-again-with-the-same-name: the UNIQUE (user_id, name)
constraint makes it a real question rather than a formality.
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
        pytest.skip("DATABASE_URL not set — skip saved-search API tests")
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
            "alembic upgrade head failed; saved-search tests cannot run\n"
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
    return {"Authorization": f"Bearer {create_access_token(subject=str(user.id))}"}


async def _factory(client: AsyncClient):
    app = client._transport.app  # type: ignore[attr-defined]
    factory = getattr(app.state, "session_factory", None)
    if factory is None:
        from core.db import _ensure_state

        factory = _ensure_state(app)
    return factory


async def _seed_user(client: AsyncClient) -> User:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session)
        await make_membership(session, user=user, team=team, role="developer")
    return user


def _name() -> str:
    """Unique per test — the integration DB is not truncated between them."""
    return "saved-" + uuid.uuid4().hex[:10]


async def _create(client: AsyncClient, user: User, *, name: str, kind: str = "vulnerabilities"):
    return await client.post(
        "/v1/saved-searches",
        headers=_bearer_for(user),
        json={"name": name, "kind": kind, "params": {"q": "CVE-2099"}},
    )


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------


async def test_requires_authentication(client: AsyncClient) -> None:
    assert (await client.get("/v1/saved-searches")).status_code == 401


async def test_a_user_only_sees_their_own(client: AsyncClient) -> None:
    alice = await _seed_user(client)
    bob = await _seed_user(client)
    alice_name, bob_name = _name(), _name()
    assert (await _create(client, alice, name=alice_name)).status_code == 201
    assert (await _create(client, bob, name=bob_name)).status_code == 201

    alice_list = await client.get("/v1/saved-searches", headers=_bearer_for(alice))
    names = {item["name"] for item in alice_list.json()["items"]}
    assert alice_name in names
    assert bob_name not in names


async def test_deleting_another_users_row_is_404_not_403(client: AsyncClient) -> None:
    """Permission beats state, and the two failures are indistinguishable."""
    alice = await _seed_user(client)
    bob = await _seed_user(client)
    created = await _create(client, alice, name=_name())
    saved_id = created.json()["id"]

    outsider = await client.delete(
        f"/v1/saved-searches/{saved_id}", headers=_bearer_for(bob)
    )
    assert outsider.status_code == 404
    assert outsider.headers["content-type"].startswith(PROBLEM_JSON)

    # And the row is still there for its owner — a rejected delete must not
    # have taken effect.
    owner_list = await client.get("/v1/saved-searches", headers=_bearer_for(alice))
    assert saved_id in {item["id"] for item in owner_list.json()["items"]}


async def test_unknown_id_is_404(client: AsyncClient) -> None:
    user = await _seed_user(client)
    missing = await client.delete(
        f"/v1/saved-searches/{uuid.uuid4()}", headers=_bearer_for(user)
    )
    assert missing.status_code == 404


# ---------------------------------------------------------------------------
# Lifecycle sequences
# ---------------------------------------------------------------------------


async def test_create_delete_recreate_with_the_same_name(client: AsyncClient) -> None:
    """The sequence the UNIQUE (user_id, name) constraint makes non-obvious.

    A single-operation suite would pass while "delete then save under the same
    name again" 409'd forever, which is exactly the failure mode the testing
    standards added lifecycle tests for.
    """
    user = await _seed_user(client)
    name = _name()

    first = await _create(client, user, name=name)
    assert first.status_code == 201

    duplicate = await _create(client, user, name=name)
    assert duplicate.status_code == 409, "a second save under a live name conflicts"
    assert duplicate.headers["content-type"].startswith(PROBLEM_JSON)

    deleted = await client.delete(
        f"/v1/saved-searches/{first.json()['id']}", headers=_bearer_for(user)
    )
    assert deleted.status_code == 204

    again = await _create(client, user, name=name)
    assert again.status_code == 201, "the name is free once the row is gone"
    assert again.json()["id"] != first.json()["id"]


async def test_the_same_name_is_free_for_a_different_user(client: AsyncClient) -> None:
    """The constraint is per-user, not global."""
    alice = await _seed_user(client)
    bob = await _seed_user(client)
    name = _name()
    assert (await _create(client, alice, name=name)).status_code == 201
    assert (await _create(client, bob, name=name)).status_code == 201


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


async def test_unknown_kind_is_422(client: AsyncClient) -> None:
    user = await _seed_user(client)
    res = await _create(client, user, name=_name(), kind="nope")
    assert res.status_code == 422


async def test_params_round_trip_verbatim(client: AsyncClient) -> None:
    """The blob is opaque — whatever went in comes back unchanged."""
    user = await _seed_user(client)
    params = {"q": "log4j", "severity": ["critical", "high"], "page": 3}
    created = await client.post(
        "/v1/saved-searches",
        headers=_bearer_for(user),
        json={"name": _name(), "kind": "components", "params": params},
    )
    assert created.status_code == 201
    assert created.json()["params"] == params

    listed = await client.get("/v1/saved-searches", headers=_bearer_for(user))
    row = next(
        item for item in listed.json()["items"] if item["id"] == created.json()["id"]
    )
    assert row["params"] == params


async def test_limit_is_enforced_and_reported(client: AsyncClient) -> None:
    """The cap is a 422, and the list echoes it so the UI can pre-empt it."""
    from services.saved_search_service import MAX_PER_USER

    user = await _seed_user(client)
    for index in range(MAX_PER_USER):
        assert (await _create(client, user, name=f"{_name()}-{index}")).status_code == 201

    listed = await client.get("/v1/saved-searches", headers=_bearer_for(user))
    assert listed.json()["total"] == MAX_PER_USER
    assert listed.json()["limit"] == MAX_PER_USER

    over = await _create(client, user, name=_name())
    assert over.status_code == 422
    assert over.headers["content-type"].startswith(PROBLEM_JSON)
