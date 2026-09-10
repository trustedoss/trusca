# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Ticket-tracker credential storage (#385).

Mirrors ``test_registry_credentials.py``: the token must never come back out
of the API, and it must be encrypted at rest. Same shape, same reason: a
tracker login is deployment infrastructure with the same blast radius as a
registry login.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from core.security import create_access_token
from models import User
from tests._db_required import migrate_to_head
from tests._helpers import make_organization, make_user

PROBLEM_JSON = "application/problem+json"

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


@pytest.fixture
async def app():  # noqa: ANN201
    from main import app as fastapi_app

    return fastapi_app


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:  # noqa: ANN001
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


def _bearer(user: User) -> dict[str, str]:
    role = "super_admin" if user.is_superuser else None
    return {"Authorization": f"Bearer {create_access_token(subject=str(user.id), role=role)}"}


async def _factory(client: AsyncClient):  # noqa: ANN202
    app = client._transport.app  # type: ignore[attr-defined]
    factory = getattr(app.state, "session_factory", None)
    if factory is None:
        from core.db import _ensure_state

        factory = _ensure_state(app)
    return factory


async def _seed_org_and_admin(client: AsyncClient):  # noqa: ANN202
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        admin = await make_user(session, is_superuser=True)
    return org, admin


def _url(org_id: uuid.UUID) -> str:
    return f"/v1/admin/organizations/{org_id}/ticket-credentials"


async def test_the_token_never_comes_back(client) -> None:
    org, admin = await _seed_org_and_admin(client)

    put = await client.put(
        _url(org.id),
        headers=_bearer(admin),
        json={
            "host": "example.atlassian.net",
            "username": "bot@example.com",
            "api_token": "s3cr3t-token",
        },
    )
    assert put.status_code == 200, put.text
    assert "s3cr3t-token" not in put.text

    listed = await client.get(_url(org.id), headers=_bearer(admin))
    assert listed.status_code == 200
    assert "s3cr3t-token" not in listed.text
    assert "api_token" not in listed.text


async def test_a_pasted_url_is_normalised(client) -> None:
    org, admin = await _seed_org_and_admin(client)

    put = await client.put(
        _url(org.id),
        headers=_bearer(admin),
        json={
            "host": "https://example.atlassian.net/",
            "username": "bot@example.com",
            "api_token": "x",
        },
    )
    assert put.status_code == 200, put.text
    assert put.json()["host"] == "example.atlassian.net"


async def test_an_unsupported_auth_scheme_is_rejected(client) -> None:
    org, admin = await _seed_org_and_admin(client)

    put = await client.put(
        _url(org.id),
        headers=_bearer(admin),
        json={
            "host": "example.atlassian.net",
            "auth_scheme": "some_future_scheme",
            "username": "bot@example.com",
            "api_token": "x",
        },
    )
    assert put.status_code == 422, put.text
    assert put.headers["content-type"].startswith(PROBLEM_JSON)


async def test_jira_basic_requires_a_username(client) -> None:
    org, admin = await _seed_org_and_admin(client)

    put = await client.put(
        _url(org.id),
        headers=_bearer(admin),
        json={"host": "example.atlassian.net", "api_token": "x"},
    )
    assert put.status_code == 422, put.text


async def test_put_replaces_rather_than_duplicating(client) -> None:
    org, admin = await _seed_org_and_admin(client)
    body = {"host": "example.atlassian.net", "username": "bot@example.com", "api_token": "one"}
    await client.put(_url(org.id), headers=_bearer(admin), json=body)
    await client.put(
        _url(org.id),
        headers=_bearer(admin),
        json={**body, "username": "bot2@example.com", "api_token": "two"},
    )
    rows = (await client.get(_url(org.id), headers=_bearer(admin))).json()["items"]
    assert len(rows) == 1
    assert rows[0]["username"] == "bot2@example.com"


async def test_a_non_admin_cannot_read_credentials(client) -> None:
    org, _admin = await _seed_org_and_admin(client)
    factory = await _factory(client)
    async with factory() as session:
        ordinary = await make_user(session)

    response = await client.get(_url(org.id), headers=_bearer(ordinary))
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_deleting_another_orgs_credential_is_hidden(client) -> None:
    """Existence-hiding: a foreign id reads the same as one that never existed."""
    org_a, admin = await _seed_org_and_admin(client)
    org_b, _ = await _seed_org_and_admin(client)
    put = await client.put(
        _url(org_a.id),
        headers=_bearer(admin),
        json={"host": "example.atlassian.net", "username": "bot@example.com", "api_token": "x"},
    )
    cred_id = put.json()["id"]

    response = await client.delete(f"{_url(org_b.id)}/{cred_id}", headers=_bearer(admin))
    assert response.status_code == 404


async def test_the_stored_token_is_not_plaintext(client) -> None:
    from sqlalchemy import select

    from models import TicketCredential

    org, admin = await _seed_org_and_admin(client)
    await client.put(
        _url(org.id),
        headers=_bearer(admin),
        json={
            "host": "example.atlassian.net",
            "username": "bot@example.com",
            "api_token": "plaintext-here",
        },
    )

    factory = await _factory(client)
    async with factory() as session:
        row = (
            await session.execute(
                select(TicketCredential).where(TicketCredential.organization_id == org.id)
            )
        ).scalar_one()
        assert "plaintext-here" not in row.api_token_encrypted

        from core.crypto import decrypt_secret

        assert decrypt_secret(row.api_token_encrypted) == "plaintext-here"
