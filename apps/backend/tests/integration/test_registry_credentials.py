# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Registry credential storage and the allow-list interaction (ER3).

Two properties carry most of the weight here: the password must never come back
out of the API, and a credential that can never be used must not be stored (or,
if the list changed afterwards, must be visibly marked dead). Silence in either
direction is the failure this is designed against.
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
from tests._helpers import make_organization, make_user

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
PROBLEM_JSON = "application/problem+json"

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    if not os.getenv("DATABASE_URL"):
        pytest.skip("DATABASE_URL not set")
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:
        pytest.skip(f"alembic upgrade head failed:\n{result.stdout}\n{result.stderr}")


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
    return f"/v1/admin/organizations/{org_id}/registry-credentials"


async def test_the_password_never_comes_back(client, monkeypatch) -> None:
    """Write-only by design: an operator never needs to read it back, and a
    response body travels through logs, proxies and browser history."""
    monkeypatch.delenv("CONTAINER_SCAN_ALLOWED_REGISTRIES", raising=False)
    org, admin = await _seed_org_and_admin(client)

    put = await client.put(
        _url(org.id),
        headers=_bearer(admin),
        json={"registry_host": "ghcr.io", "username": "bot", "password": "s3cr3t-token"},
    )
    assert put.status_code == 200, put.text
    assert "s3cr3t-token" not in put.text

    listed = await client.get(_url(org.id), headers=_bearer(admin))
    assert listed.status_code == 200
    assert "s3cr3t-token" not in listed.text
    assert "password" not in listed.text


async def test_a_pasted_url_is_normalised(client, monkeypatch) -> None:
    """`https://ghcr.io/` must become `ghcr.io`, or the scan-time lookup (which
    uses the parsed host of the image reference) silently never matches."""
    monkeypatch.delenv("CONTAINER_SCAN_ALLOWED_REGISTRIES", raising=False)
    org, admin = await _seed_org_and_admin(client)

    put = await client.put(
        _url(org.id),
        headers=_bearer(admin),
        json={"registry_host": "https://ghcr.io/", "username": "bot", "password": "x"},
    )
    assert put.status_code == 200, put.text
    assert put.json()["registry_host"] == "ghcr.io"


async def test_saving_a_credential_for_an_excluded_registry_is_rejected(
    client, monkeypatch
) -> None:
    """It could never be used, so storing it quietly would be a no-op nobody sees."""
    monkeypatch.setenv("CONTAINER_SCAN_ALLOWED_REGISTRIES", "ghcr.io")
    org, admin = await _seed_org_and_admin(client)

    put = await client.put(
        _url(org.id),
        headers=_bearer(admin),
        json={"registry_host": "other.example.com", "username": "bot", "password": "x"},
    )
    assert put.status_code == 422, put.text
    assert put.headers["content-type"].startswith(PROBLEM_JSON)
    # The operator's fix is naming the registry, so the error must say which
    # setting to change.
    assert "CONTAINER_SCAN_ALLOWED_REGISTRIES" in put.text


async def test_a_credential_orphaned_by_a_later_tightening_is_flagged(
    client, monkeypatch
) -> None:
    """The other ordering.

    Saving is rejected when the list already excludes the registry, but a list
    tightened afterwards leaves rows that can no longer be used. Deleting them
    behind the operator's back would be worse (the list may be the mistake), so
    they are shown as not allowed instead of silently doing nothing.
    """
    monkeypatch.delenv("CONTAINER_SCAN_ALLOWED_REGISTRIES", raising=False)
    org, admin = await _seed_org_and_admin(client)
    put = await client.put(
        _url(org.id),
        headers=_bearer(admin),
        json={"registry_host": "other.example.com", "username": "bot", "password": "x"},
    )
    assert put.status_code == 200
    assert put.json()["allowed"] is True

    # Operator tightens the list afterwards.
    monkeypatch.setenv("CONTAINER_SCAN_ALLOWED_REGISTRIES", "ghcr.io")
    listed = await client.get(_url(org.id), headers=_bearer(admin))
    rows = listed.json()["items"]
    assert [r["registry_host"] for r in rows] == ["other.example.com"]
    assert rows[0]["allowed"] is False


async def test_put_replaces_rather_than_duplicating(client, monkeypatch) -> None:
    monkeypatch.delenv("CONTAINER_SCAN_ALLOWED_REGISTRIES", raising=False)
    org, admin = await _seed_org_and_admin(client)
    body = {"registry_host": "ghcr.io", "username": "bot", "password": "one"}
    await client.put(_url(org.id), headers=_bearer(admin), json=body)
    await client.put(
        _url(org.id),
        headers=_bearer(admin),
        json={**body, "username": "bot2", "password": "two"},
    )
    rows = (await client.get(_url(org.id), headers=_bearer(admin))).json()["items"]
    assert len(rows) == 1
    assert rows[0]["username"] == "bot2"


async def test_a_non_admin_cannot_read_credentials(client, monkeypatch) -> None:
    monkeypatch.delenv("CONTAINER_SCAN_ALLOWED_REGISTRIES", raising=False)
    org, _admin = await _seed_org_and_admin(client)
    factory = await _factory(client)
    async with factory() as session:
        ordinary = await make_user(session)

    response = await client.get(_url(org.id), headers=_bearer(ordinary))
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_deleting_another_orgs_credential_is_hidden(client, monkeypatch) -> None:
    """Existence-hiding: a foreign id reads the same as one that never existed."""
    monkeypatch.delenv("CONTAINER_SCAN_ALLOWED_REGISTRIES", raising=False)
    org_a, admin = await _seed_org_and_admin(client)
    org_b, _ = await _seed_org_and_admin(client)
    put = await client.put(
        _url(org_a.id),
        headers=_bearer(admin),
        json={"registry_host": "ghcr.io", "username": "bot", "password": "x"},
    )
    cred_id = put.json()["id"]

    response = await client.delete(
        f"{_url(org_b.id)}/{cred_id}", headers=_bearer(admin)
    )
    assert response.status_code == 404


async def test_the_stored_password_is_not_plaintext(client, monkeypatch) -> None:
    """Encrypted at rest via core.crypto, like every other credential column."""
    from sqlalchemy import select

    from models import RegistryCredential

    monkeypatch.delenv("CONTAINER_SCAN_ALLOWED_REGISTRIES", raising=False)
    org, admin = await _seed_org_and_admin(client)
    await client.put(
        _url(org.id),
        headers=_bearer(admin),
        json={"registry_host": "ghcr.io", "username": "bot", "password": "plaintext-here"},
    )

    factory = await _factory(client)
    async with factory() as session:
        row = (
            await session.execute(
                select(RegistryCredential).where(
                    RegistryCredential.organization_id == org.id
                )
            )
        ).scalar_one()
        assert "plaintext-here" not in row.password_encrypted

        from core.crypto import decrypt_secret

        assert decrypt_secret(row.password_encrypted) == "plaintext-here"
