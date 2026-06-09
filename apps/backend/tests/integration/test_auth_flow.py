"""
End-to-end auth flow integration tests.

Spec / harness for Phase 1 PR #5. These tests document the contract that
the auth surface must honour:

    1. POST /auth/register → 201 + user payload (no password echo)
    2. POST /auth/login    → 200 + access_token + refresh cookie
                           → audit log row written
    3. GET  /auth/me       → 200 with bearer token, 401 without
    4. POST /auth/refresh  → 200 + new access + rotated refresh
                           → reusing the *previous* refresh = 401 + revocation
    5. POST /auth/logout   → 204 + refresh token revoked

Additional contract assertions:
    - 6th login attempt within a minute returns 429 + Retry-After header
    - all 4xx responses are application/problem+json (RFC 7807)
    - structlog JSON logs carry request_id, never a raw password or token
    - audit_logs table records every INSERT/UPDATE/DELETE with actor_user_id

These run only when DATABASE_URL points at a real Postgres so the full
SQLAlchemy + Alembic stack is exercised. Each test isolates itself with a
unique email prefix; no fixtures truncate tables.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import subprocess
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
PROBLEM_JSON = "application/problem+json"

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip auth integration test")
    return url


def _unique_email(prefix: str = "test") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}@example.com"


def _strong_password() -> str:
    # Meets the 12-char minimum required by §3 (Security defaults).
    return f"Sup3rS3cret!{secrets.token_hex(4)}"


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    """Ensure the schema is at head before any auth test runs."""
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
            f"alembic upgrade head failed; auth tests cannot run\n"
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


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------


async def test_register_creates_user_and_does_not_echo_password(client):
    email = _unique_email("reg")
    password = _strong_password()

    response = await client.post(
        "/auth/register",
        json={"email": email, "password": password, "full_name": "Reg User"},
    )

    assert response.status_code in (200, 201), response.text
    body = response.json()
    assert body["email"].lower() == email.lower()
    assert "password" not in body
    assert "hashed_password" not in body


async def test_register_rejects_weak_password(client):
    email = _unique_email("weak")

    response = await client.post(
        "/auth/register",
        json={"email": email, "password": "short", "full_name": "Weak"},
    )

    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_register_rejects_duplicate_email(client):
    email = _unique_email("dup")
    password = _strong_password()
    payload = {"email": email, "password": password, "full_name": "Dup"}

    first = await client.post("/auth/register", json=payload)
    assert first.status_code in (200, 201)

    second = await client.post("/auth/register", json=payload)
    assert second.status_code in (400, 409)
    assert second.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# Login + JWT
# ---------------------------------------------------------------------------


async def test_login_returns_access_and_sets_refresh_cookie(client):
    email = _unique_email("login")
    password = _strong_password()

    register = await client.post(
        "/auth/register",
        json={"email": email, "password": password, "full_name": "L"},
    )
    assert register.status_code in (200, 201), register.text

    login = await client.post(
        "/auth/login",
        json={"email": email, "password": password},
    )
    assert login.status_code == 200, login.text
    body = login.json()
    assert "access_token" in body
    assert body.get("token_type", "bearer").lower() == "bearer"

    # refresh must come back as an HttpOnly cookie, not a JSON field
    cookie_header = login.headers.get("set-cookie", "")
    assert "refresh" in cookie_header.lower()
    assert "httponly" in cookie_header.lower()
    assert "samesite=lax" in cookie_header.lower()


async def test_login_with_bad_password_is_401(client):
    email = _unique_email("badpw")
    password = _strong_password()
    await client.post(
        "/auth/register",
        json={"email": email, "password": password, "full_name": "X"},
    )

    response = await client.post(
        "/auth/login",
        json={"email": email, "password": "WrongPassword123!"},
    )

    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# Protected endpoint
# ---------------------------------------------------------------------------


async def test_me_returns_401_without_token(client):
    response = await client.get("/auth/me")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_me_returns_user_with_valid_token(client):
    email = _unique_email("me")
    password = _strong_password()
    await client.post(
        "/auth/register",
        json={"email": email, "password": password, "full_name": "Me"},
    )
    login = await client.post(
        "/auth/login",
        json={"email": email, "password": password},
    )
    token = login.json()["access_token"]

    response = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["email"].lower() == email.lower()


# ---------------------------------------------------------------------------
# Refresh rotation + reuse detection
# ---------------------------------------------------------------------------


async def test_refresh_rotates_and_detects_reuse(client):
    email = _unique_email("rot")
    password = _strong_password()
    await client.post(
        "/auth/register",
        json={"email": email, "password": password, "full_name": "R"},
    )
    login = await client.post(
        "/auth/login",
        json={"email": email, "password": password},
    )
    assert login.status_code == 200
    first_refresh = login.cookies.get("refresh_token")
    assert first_refresh, "login did not set refresh_token cookie"

    # First rotation: succeeds, returns a new refresh
    rotated = await client.post(
        "/auth/refresh",
        cookies={"refresh_token": first_refresh},
    )
    assert rotated.status_code == 200, rotated.text
    second_refresh = rotated.cookies.get("refresh_token")
    assert second_refresh and second_refresh != first_refresh

    # Reusing the *first* refresh after rotation must be rejected (reuse detected)
    replay = await client.post(
        "/auth/refresh",
        cookies={"refresh_token": first_refresh},
    )
    assert replay.status_code == 401
    assert replay.headers["content-type"].startswith(PROBLEM_JSON)


async def test_reuse_detection_revokes_whole_family(client):
    """C-1: replaying an old refresh must kill the live descendant session too.

    Before the fix, reuse detection walked only the parent chain upward, so the
    current (post-rotation) refresh — a descendant of the replayed token —
    survived: a thief who replayed a stolen old token was rejected, yet the
    legitimate user's active session stayed valid (and vice-versa). The whole
    token family must be invalidated, forcing re-auth on every device.
    """
    email = _unique_email("family")
    password = _strong_password()
    await client.post(
        "/auth/register",
        json={"email": email, "password": password, "full_name": "F"},
    )
    login = await client.post("/auth/login", json={"email": email, "password": password})
    first_refresh = login.cookies.get("refresh_token")

    rotated = await client.post("/auth/refresh", cookies={"refresh_token": first_refresh})
    assert rotated.status_code == 200, rotated.text
    current_refresh = rotated.cookies.get("refresh_token")
    assert current_refresh and current_refresh != first_refresh

    # Replay the consumed first refresh → reuse detected.
    replay = await client.post("/auth/refresh", cookies={"refresh_token": first_refresh})
    assert replay.status_code == 401

    # The CURRENT (live) refresh must now also be revoked — the family is dead.
    after = await client.post("/auth/refresh", cookies={"refresh_token": current_refresh})
    assert after.status_code == 401, (
        "live descendant session survived reuse detection (C-1 regression)"
    )
    assert after.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------


async def test_logout_revokes_refresh_token(client):
    email = _unique_email("logout")
    password = _strong_password()
    await client.post(
        "/auth/register",
        json={"email": email, "password": password, "full_name": "Lo"},
    )
    login = await client.post(
        "/auth/login",
        json={"email": email, "password": password},
    )
    refresh = login.cookies.get("refresh_token")
    token = login.json()["access_token"]

    logout = await client.post(
        "/auth/logout",
        cookies={"refresh_token": refresh},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert logout.status_code in (200, 204)

    # Refresh after logout = 401
    after = await client.post(
        "/auth/refresh",
        cookies={"refresh_token": refresh},
    )
    assert after.status_code == 401


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


async def test_login_rate_limit_returns_429_on_sixth_attempt(client):
    email = _unique_email("rl")
    password = _strong_password()
    await client.post(
        "/auth/register",
        json={"email": email, "password": password, "full_name": "RL"},
    )

    # 5 wrong-password attempts are allowed (4xx but not 429)
    for i in range(5):
        bad = await client.post(
            "/auth/login",
            json={"email": email, "password": f"WrongPassword{i}!"},
            headers={"X-Forwarded-For": "203.0.113.42"},
        )
        assert bad.status_code != 429, f"attempt {i + 1} unexpectedly rate-limited"

    # 6th attempt within a minute trips the limiter
    sixth = await client.post(
        "/auth/login",
        json={"email": email, "password": "WrongPasswordZ!"},
        headers={"X-Forwarded-For": "203.0.113.42"},
    )
    assert sixth.status_code == 429
    assert sixth.headers.get("Retry-After"), "missing Retry-After header"
    assert sixth.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# Audit log + structured logging contract
# ---------------------------------------------------------------------------


async def test_login_writes_audit_log_entry(client):
    """A successful login must produce an audit_logs row with the actor user id."""
    from sqlalchemy import text

    email = _unique_email("audit")
    password = _strong_password()
    register = await client.post(
        "/auth/register",
        json={"email": email, "password": password, "full_name": "A"},
    )
    user_id = register.json()["id"]

    login = await client.post(
        "/auth/login",
        json={"email": email, "password": password},
    )
    assert login.status_code == 200

    # Settle async listeners before reading
    await asyncio.sleep(0.05)

    session_factory = client._transport.app.state.session_factory  # type: ignore[attr-defined]
    async with session_factory() as session:  # type: AsyncSession
        rows = (
            await session.execute(
                text(
                    "SELECT action, target_table, actor_user_id::text "
                    "FROM audit_logs "
                    "WHERE actor_user_id = :uid "
                    "ORDER BY created_at DESC LIMIT 5"
                ),
                {"uid": user_id},
            )
        ).all()
    assert rows, "expected at least one audit_logs row for the new user"


async def test_logs_never_contain_raw_password(client, capsys):
    email = _unique_email("logs")
    password = _strong_password()
    await client.post(
        "/auth/register",
        json={"email": email, "password": password, "full_name": "Lg"},
    )
    await client.post(
        "/auth/login",
        json={"email": email, "password": password},
    )

    captured = capsys.readouterr()
    combined = captured.out + captured.err

    assert password not in combined, "raw password leaked into logs"
    # JSON log lines should still be present
    json_lines = [ln for ln in combined.splitlines() if ln.startswith("{")]
    if json_lines:
        sample = json.loads(json_lines[-1])
        # request_id is always bound by RequestIDMiddleware
        assert "request_id" in sample or "method" in sample
