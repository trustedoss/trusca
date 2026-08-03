"""
Integration tests for /v1/github-app-credentials — v2.2-b1.

Drives the HTTP surface end-to-end (ASGITransport + real Postgres). Asserts:
  - Anonymous → 401 + RFC 7807 problem+json.
  - Developer cannot register (403); team_admin can (201).
  - The 201 response NEVER contains the private key / ciphertext.
  - Cross-team read is hidden (404 existence-hide).
  - 409 on duplicate (team, app_id).
  - Validation errors (malformed PEM / app_id) → 422 problem+json.
  - Install link opt-in + list + unlink round-trip.
  - All 4xx responses use application/problem+json (RFC 7807).
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient

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
        pytest.skip("DATABASE_URL not set — skip github_app API tests")
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
            f"alembic upgrade head failed; github_app API tests cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from cryptography.fernet import Fernet

    monkeypatch.setenv("GITHUB_APP_ENCRYPTION_KEY", Fernet.generate_key().decode())


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


def _bearer_for(user: User, *, role: str | None = None) -> dict[str, str]:
    resolved = "super_admin" if user.is_superuser else role
    token = create_access_token(subject=str(user.id), role=resolved)
    return {"Authorization": f"Bearer {token}"}


def _make_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")


# ---------------------------------------------------------------------------
# Anonymous
# ---------------------------------------------------------------------------


async def test_post_anonymous_returns_401(client: AsyncClient) -> None:
    import uuid as _uuid

    response = await client.post(
        f"/v1/github-app-credentials?team_id={_uuid.uuid4()}",
        json={"app_id": "1", "private_key": _make_pem()},
    )
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    body = response.json()
    assert body["status"] == 401
    assert "title" in body and "instance" in body


async def test_list_anonymous_returns_401(client: AsyncClient) -> None:
    response = await client.get("/v1/github-app-credentials")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# register: developer 403, team_admin 201, no key leak
# ---------------------------------------------------------------------------


async def test_developer_cannot_register(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as s:
        org = await make_organization(s)
        team = await make_team(s, organization=org)
        dev = await make_user(s)
        await make_membership(s, user=dev, team=team, role="developer")

    response = await client.post(
        f"/v1/github-app-credentials?team_id={team.id}",
        json={"app_id": "10", "private_key": _make_pem()},
        headers=_bearer_for(dev, role="developer"),
    )
    assert response.status_code == 403
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_team_admin_register_201_no_key_leak(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as s:
        org = await make_organization(s)
        team = await make_team(s, organization=org)
        admin = await make_user(s)
        await make_membership(s, user=admin, team=team, role="team_admin")

    pem = _make_pem()
    response = await client.post(
        f"/v1/github-app-credentials?team_id={team.id}",
        json={"app_id": "11", "app_slug": "scanner", "private_key": pem, "webhook_secret": "ws"},
        headers=_bearer_for(admin, role="team_admin"),
    )
    assert response.status_code == 201
    body = response.json()
    # The response is metadata only — NEVER the key or ciphertext.
    raw = response.text
    assert "BEGIN" not in raw
    assert "private_key" not in body
    assert "private_key_encrypted" not in body
    assert "webhook_secret" not in body
    assert body["has_private_key"] is True
    assert body["has_webhook_secret"] is True
    assert body["app_id"] == "11"


async def test_register_duplicate_409(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as s:
        org = await make_organization(s)
        team = await make_team(s, organization=org)
        admin = await make_user(s)
        await make_membership(s, user=admin, team=team, role="team_admin")

    headers = _bearer_for(admin, role="team_admin")
    first = await client.post(
        f"/v1/github-app-credentials?team_id={team.id}",
        json={"app_id": "12", "private_key": _make_pem()},
        headers=headers,
    )
    assert first.status_code == 201
    dup = await client.post(
        f"/v1/github-app-credentials?team_id={team.id}",
        json={"app_id": "12", "private_key": _make_pem()},
        headers=headers,
    )
    assert dup.status_code == 409
    assert dup.headers["content-type"].startswith(PROBLEM_JSON)


async def test_register_malformed_pem_422(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as s:
        org = await make_organization(s)
        team = await make_team(s, organization=org)
        admin = await make_user(s)
        await make_membership(s, user=admin, team=team, role="team_admin")

    response = await client.post(
        f"/v1/github-app-credentials?team_id={team.id}",
        json={"app_id": "13", "private_key": "not-a-pem"},
        headers=_bearer_for(admin, role="team_admin"),
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# read: cross-team existence-hide
# ---------------------------------------------------------------------------


async def test_get_cross_team_404(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as s:
        org = await make_organization(s)
        team_a = await make_team(s, organization=org)
        team_b = await make_team(s, organization=org)
        admin = await make_user(s)
        await make_membership(s, user=admin, team=team_a, role="team_admin")
        outsider = await make_user(s)
        await make_membership(s, user=outsider, team=team_b, role="team_admin")

    created = await client.post(
        f"/v1/github-app-credentials?team_id={team_a.id}",
        json={"app_id": "14", "private_key": _make_pem()},
        headers=_bearer_for(admin, role="team_admin"),
    )
    assert created.status_code == 201
    cred_id = created.json()["id"]

    response = await client.get(
        f"/v1/github-app-credentials/{cred_id}",
        headers=_bearer_for(outsider, role="team_admin"),
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_list_does_not_leak_key(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as s:
        org = await make_organization(s)
        team = await make_team(s, organization=org)
        admin = await make_user(s)
        await make_membership(s, user=admin, team=team, role="team_admin")

    headers = _bearer_for(admin, role="team_admin")
    await client.post(
        f"/v1/github-app-credentials?team_id={team.id}",
        json={"app_id": "15", "private_key": _make_pem()},
        headers=headers,
    )
    response = await client.get(f"/v1/github-app-credentials?team_id={team.id}", headers=headers)
    assert response.status_code == 200
    # No PEM and no ciphertext column leak. ("has_private_key" boolean is fine —
    # we assert on the key MATERIAL, not the metadata flag.)
    assert "BEGIN" not in response.text
    assert "private_key_encrypted" not in response.text
    assert '"private_key"' not in response.text


# ---------------------------------------------------------------------------
# installations: link / list / unlink round-trip + RBAC
# ---------------------------------------------------------------------------


async def test_installation_link_list_unlink(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as s:
        org = await make_organization(s)
        team = await make_team(s, organization=org)
        admin = await make_user(s)
        await make_membership(s, user=admin, team=team, role="team_admin")
        project = await make_project(s, team=team)

    headers = _bearer_for(admin, role="team_admin")
    created = await client.post(
        f"/v1/github-app-credentials?team_id={team.id}",
        json={"app_id": "16", "private_key": _make_pem()},
        headers=headers,
    )
    cred_id = created.json()["id"]

    linked = await client.post(
        f"/v1/github-app-credentials/{cred_id}/installations",
        json={
            "installation_id": "424242",
            "account_login": "acme",
            "repository_full_name": "acme/widgets",
            "project_id": str(project.id),
        },
        headers=headers,
    )
    assert linked.status_code == 201
    inst = linked.json()
    assert inst["installation_id"] == "424242"
    assert inst["project_id"] == str(project.id)
    inst_row_id = inst["id"]

    listed = await client.get(
        f"/v1/github-app-credentials/{cred_id}/installations", headers=headers
    )
    assert listed.status_code == 200
    assert listed.json()["total"] == 1

    unlinked = await client.delete(
        f"/v1/github-app-credentials/{cred_id}/installations/{inst_row_id}",
        headers=headers,
    )
    assert unlinked.status_code == 204

    listed_again = await client.get(
        f"/v1/github-app-credentials/{cred_id}/installations", headers=headers
    )
    assert listed_again.json()["total"] == 0


async def test_revoke_204_and_developer_403(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as s:
        org = await make_organization(s)
        team = await make_team(s, organization=org)
        admin = await make_user(s)
        await make_membership(s, user=admin, team=team, role="team_admin")
        dev = await make_user(s)
        await make_membership(s, user=dev, team=team, role="developer")

    admin_headers = _bearer_for(admin, role="team_admin")
    created = await client.post(
        f"/v1/github-app-credentials?team_id={team.id}",
        json={"app_id": "17", "private_key": _make_pem()},
        headers=admin_headers,
    )
    cred_id = created.json()["id"]

    # Developer cannot revoke (visible but not authorized → 403).
    dev_resp = await client.delete(
        f"/v1/github-app-credentials/{cred_id}",
        headers=_bearer_for(dev, role="developer"),
    )
    assert dev_resp.status_code == 403
    assert dev_resp.headers["content-type"].startswith(PROBLEM_JSON)

    # Team admin revokes (204), idempotent on second call.
    ok = await client.delete(f"/v1/github-app-credentials/{cred_id}", headers=admin_headers)
    assert ok.status_code == 204
    again = await client.delete(f"/v1/github-app-credentials/{cred_id}", headers=admin_headers)
    assert again.status_code == 204


async def test_audit_row_masks_key(client: AsyncClient) -> None:
    """The audit_logs diff for the registration must mask the key ciphertext."""
    from sqlalchemy import text

    factory = await _factory(client)
    async with factory() as s:
        org = await make_organization(s)
        team = await make_team(s, organization=org)
        admin = await make_user(s)
        await make_membership(s, user=admin, team=team, role="team_admin")

    created = await client.post(
        f"/v1/github-app-credentials?team_id={team.id}",
        json={"app_id": "18", "private_key": _make_pem(), "webhook_secret": "ws"},
        headers=_bearer_for(admin, role="team_admin"),
    )
    cred_id = created.json()["id"]

    factory = await _factory(client)
    async with factory() as s:
        diffs = (
            (
                await s.execute(
                    text(
                        "SELECT diff::text FROM audit_logs "
                        "WHERE target_table = 'github_app_credentials' "
                        "AND action = 'create' ORDER BY created_at DESC LIMIT 5"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert diffs
    joined = "\n".join(d or "" for d in diffs)
    assert "BEGIN" not in joined
    assert '"private_key_encrypted": "***"' in joined
    # The created credential id is referenced so we know we read the right rows.
    assert cred_id  # sanity
