"""
Integration tests for /v1/admin/users — Phase 4 PR #13.

The 4-role matrix (anonymous / developer / team_admin / super_admin) is the
spine of these tests:

  - Anonymous            -> 401 + Problem Details
  - Developer            -> 404 (existence-hide)
  - Team Admin           -> 404 (existence-hide)
  - Super Admin          -> 200/201/204 (or 422 for safety-blocked ops)

Plus contract assertions:
  - All 4xx are application/problem+json (RFC 7807).
  - Audit log row produced for every mutation.
  - Password reset issues bcrypt-hashed token; second call invalidates first.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from core.security import create_access_token
from models import RefreshToken, User
from tests._helpers import (
    make_membership,
    make_organization,
    make_team,
    make_user,
    unique_suffix,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent
PROBLEM_JSON = "application/problem+json"

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip admin users API tests")
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
            f"alembic upgrade head failed; admin users API tests cannot run\n"
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


# ---------------------------------------------------------------------------
# Auth gate (existence-hide for non-super-admin)
# ---------------------------------------------------------------------------


async def test_list_users_anonymous_returns_401(client: AsyncClient) -> None:
    response = await client.get("/v1/admin/users")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    body = response.json()
    assert body["status"] == 401
    assert "title" in body
    assert "instance" in body


async def test_list_users_developer_returns_404_existence_hide(
    client: AsyncClient,
) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session)
        await make_membership(session, user=user, team=team, role="developer")

    response = await client.get("/v1/admin/users", headers=_bearer_for(user))
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_list_users_team_admin_returns_404_existence_hide(
    client: AsyncClient,
) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session)
        await make_membership(session, user=user, team=team, role="team_admin")

    response = await client.get("/v1/admin/users", headers=_bearer_for(user))
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# F3 — strict role query enum (fail-closed 422)
# ---------------------------------------------------------------------------
# The ``role`` query parameter was a free-form ``str`` until security-reviewer
# F3 — values outside the canonical 3-role set were silently dropped (fail
# open), so a typo like ``role=admin`` returned the entire user list. The fix
# pins the parameter to ``Literal["super_admin", "team_admin", "developer"]``
# so anything else fails with a 422 + Problem Details (fail closed) BEFORE
# the service runs.
#
# Adversarial input parametrize (memory feedback_adversarial_input_parametrize):
# untrusted-input parsing must be exercised with separator-only / scheme /
# oversized / CRLF / null byte / RTL / SQL-keyword / repeated-key payloads.


@pytest.mark.parametrize(
    "role",
    ["super_admin", "team_admin", "developer"],
)
async def test_list_users_role_query_accepts_valid_enum(
    client: AsyncClient, role: str
) -> None:
    """All three canonical role values must remain accepted (200)."""
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)

    response = await client.get(
        f"/v1/admin/users?role={role}",
        headers=_bearer_for(admin),
    )
    assert response.status_code == 200, response.text


@pytest.mark.parametrize(
    "label,raw",
    [
        # Out-of-set values (fail closed).
        ("rejects_unknown_value", "invalid"),
        ("rejects_typo_admin", "admin"),
        ("rejects_uppercase", "SUPER_ADMIN"),
        ("rejects_mixed_case", "Super_Admin"),
        ("rejects_trailing_whitespace", "developer "),
        ("rejects_leading_whitespace", " developer"),
        # Empty string is rejected: Literal[..] does not include "".
        ("rejects_empty_string", ""),
        # Adversarial inputs (memory feedback_adversarial_input_parametrize).
        ("rejects_javascript_scheme", "javascript:alert(1)"),
        ("rejects_oversized", "a" * 1000),
        ("rejects_crlf", "developer\r\nSet-Cookie: x=y"),
        ("rejects_null_byte", "developer\x00"),
        ("rejects_rtl_override", "‮developer"),
        ("rejects_sql_keyword", "developer' OR 1=1 --"),
        ("rejects_integer_stringified", "1"),
    ],
)
async def test_list_users_role_query_rejects_adversarial(
    client: AsyncClient, label: str, raw: str
) -> None:
    """Every out-of-enum / adversarial value MUST 422 + problem+json envelope."""
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)

    # Send via query-params dict so httpx URL-encodes control bytes / CRLF
    # safely instead of throwing at the client level.
    response = await client.get(
        "/v1/admin/users",
        params={"role": raw},
        headers=_bearer_for(admin),
    )
    assert response.status_code == 422, (
        f"{label!r} payload {raw!r} produced {response.status_code}; "
        f"body={response.text!r}"
    )
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    body = response.json()
    assert body["status"] == 422
    assert body["title"] == "Validation Error"
    # F2 redaction pin: the per-error ``input`` field MUST be the sentinel,
    # never the raw value. We check the error rows directly rather than the
    # whole body — ``detail`` legitimately contains common English words
    # ("invalid") that would collide with adversarial payloads as a
    # substring match.
    role_rows = [
        e
        for e in body["errors"]
        if isinstance(e, dict) and "role" in tuple(e.get("loc", ()))
    ]
    assert role_rows, f"missing role validation row in {body['errors']!r}"
    for row in role_rows:
        assert row.get("input") == "<redacted>", (
            f"raw value leaked under redaction sentinel: {row!r}"
        )


async def test_list_users_role_query_repeated_key_fails_closed(
    client: AsyncClient,
) -> None:
    """
    Repeated ?role=a&role=b: FastAPI binds the LAST value when the parameter
    is scalar (vs. ``list[str]``). Our pin is scalar Literal, so the second
    value still drives validation. If it's out-of-enum we get 422 — neither
    silent acceptance nor a 500.
    """
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)

    response = await client.get(
        "/v1/admin/users?role=developer&role=admin",
        headers=_bearer_for(admin),
    )
    assert response.status_code == 422, response.text
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# Super admin happy paths
# ---------------------------------------------------------------------------


async def test_list_users_super_admin_returns_200(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)

    response = await client.get(
        "/v1/admin/users?page=1&page_size=10",
        headers=_bearer_for(admin),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "items" in body
    assert "total" in body
    assert body["page"] == 1
    assert body["page_size"] == 10


async def test_get_user_detail_super_admin_returns_200(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        target = await make_user(session)
        await make_membership(session, user=target, team=team, role="developer")
        admin = await make_user(session, is_superuser=True)

    response = await client.get(
        f"/v1/admin/users/{target.id}",
        headers=_bearer_for(admin),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == str(target.id)
    assert body["scan_count"] == 0
    assert any(m["team_id"] == str(team.id) for m in body["memberships"])


async def test_get_user_detail_unknown_returns_404_problem(
    client: AsyncClient,
) -> None:
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)

    response = await client.get(
        f"/v1/admin/users/{uuid.uuid4()}",
        headers=_bearer_for(admin),
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_update_user_role_writes_audit(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        target = await make_user(session)
        admin = await make_user(session, is_superuser=True)

    response = await client.patch(
        f"/v1/admin/users/{target.id}/role",
        headers=_bearer_for(admin),
        json={"role": "team_admin", "team_id": str(team.id)},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert any(
        m["role"] == "team_admin" and m["team_id"] == str(team.id) for m in body["memberships"]
    )

    factory = await _factory(client)
    async with factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT count(*) FROM audit_logs "
                    "WHERE actor_user_id = :a AND target_table = 'memberships' "
                    "  AND action = 'create'"
                ),
                {"a": str(admin.id)},
            )
        ).scalar_one()
    assert rows >= 1


async def test_update_user_role_self_modify_returns_422(
    client: AsyncClient,
) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        admin = await make_user(session, is_superuser=True)

    response = await client.patch(
        f"/v1/admin/users/{admin.id}/role",
        headers=_bearer_for(admin),
        json={"role": "developer", "team_id": str(team.id)},
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    body = response.json()
    assert body.get("cannot_modify_self") is True


async def test_update_user_role_invalid_payload_returns_422_problem(
    client: AsyncClient,
) -> None:
    factory = await _factory(client)
    async with factory() as session:
        target = await make_user(session)
        admin = await make_user(session, is_superuser=True)

    response = await client.patch(
        f"/v1/admin/users/{target.id}/role",
        headers=_bearer_for(admin),
        json={"role": "GOD"},
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# Deactivate / activate
# ---------------------------------------------------------------------------


async def test_deactivate_user_revokes_refresh_tokens(client: AsyncClient) -> None:
    from datetime import UTC, datetime, timedelta

    factory = await _factory(client)
    async with factory() as session:
        target = await make_user(session)
        admin = await make_user(session, is_superuser=True)
        rt = RefreshToken(
            user_id=target.id,
            jti=f"j-{unique_suffix()}",
            token_hash=f"h-{unique_suffix()}",
            expires_at=datetime.now(tz=UTC) + timedelta(days=7),
        )
        session.add(rt)
        await session.commit()
        rt_id = rt.id

    response = await client.patch(
        f"/v1/admin/users/{target.id}/deactivate",
        headers=_bearer_for(admin),
    )
    assert response.status_code == 200, response.text
    assert response.json()["is_active"] is False

    factory = await _factory(client)
    async with factory() as session:
        fresh = (
            await session.execute(select(RefreshToken).where(RefreshToken.id == rt_id))
        ).scalar_one()
    assert fresh.revoked_at is not None
    assert fresh.revoked_reason == "logout"


async def test_deactivate_user_self_returns_422(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)

    response = await client.patch(
        f"/v1/admin/users/{admin.id}/deactivate",
        headers=_bearer_for(admin),
    )
    assert response.status_code == 422
    body = response.json()
    assert body.get("cannot_modify_self") is True


async def test_activate_user_returns_200(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        target = await make_user(session, is_active=False)
        admin = await make_user(session, is_superuser=True)

    response = await client.patch(
        f"/v1/admin/users/{target.id}/activate",
        headers=_bearer_for(admin),
    )
    assert response.status_code == 200, response.text
    assert response.json()["is_active"] is True


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------


async def test_password_reset_returns_204_and_persists_hash(
    client: AsyncClient,
) -> None:
    from models import PasswordResetToken

    factory = await _factory(client)
    async with factory() as session:
        target = await make_user(session)
        admin = await make_user(session, is_superuser=True)

    response = await client.post(
        f"/v1/admin/users/{target.id}/password-reset",
        headers=_bearer_for(admin),
    )
    assert response.status_code == 204

    # Plaintext is never present in any response body. Even at 204 there is
    # no body, but we double-check.
    assert response.content == b""

    factory = await _factory(client)
    async with factory() as session:
        rows = (
            (
                await session.execute(
                    select(PasswordResetToken).where(
                        PasswordResetToken.user_id == target.id,
                    )
                )
            )
            .scalars()
            .all()
        )
    assert rows
    for row in rows:
        # bcrypt hash format
        assert row.token_hash.startswith("$2")


async def test_password_reset_second_call_invalidates_first(
    client: AsyncClient,
) -> None:
    from models import PasswordResetToken

    factory = await _factory(client)
    async with factory() as session:
        target = await make_user(session)
        admin = await make_user(session, is_superuser=True)

    headers = _bearer_for(admin)
    first = await client.post(f"/v1/admin/users/{target.id}/password-reset", headers=headers)
    assert first.status_code == 204
    second = await client.post(f"/v1/admin/users/{target.id}/password-reset", headers=headers)
    assert second.status_code == 204

    factory = await _factory(client)
    async with factory() as session:
        rows = (
            (
                await session.execute(
                    select(PasswordResetToken)
                    .where(PasswordResetToken.user_id == target.id)
                    .order_by(PasswordResetToken.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) >= 2
    # The earliest must be invalidated; the latest must be live.
    assert rows[0].invalidated_at is not None
    assert rows[-1].invalidated_at is None
    assert rows[-1].used_at is None
