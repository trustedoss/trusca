"""
Integration tests for the public password-reset flow — Phase 6 PR #18.

Endpoints under test:
  - POST /auth/forgot-password
  - POST /auth/reset-password

CWE-204 contract:
  - 204 returned regardless of whether the email exists. The body is empty
    and the response shape is byte-identical between the two branches.
  - The 5-minute per-email cooldown trips Retry-After but still returns 204.

Token contract:
  - secrets.token_urlsafe(32) plaintext bcrypt-hashed in
    ``password_reset_tokens.token_hash``.
  - 1-hour TTL.
  - One-shot — a second reset with the same token returns 422.
  - Tokens older than the TTL return 422 (manually expired in the row).
  - new_password < 8 chars rejected at the schema layer with 422.

These tests run against the real Postgres in docker-compose.dev.yml — the
admin password-reset suite uses the same harness so we follow the pattern.
"""

from __future__ import annotations

import os
import secrets
import subprocess
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
PROBLEM_JSON = "application/problem+json"

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip password-reset integration test")
    return url


def _unique_email(prefix: str = "pwreset") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}@example.com"


def _strong_password() -> str:
    return f"Sup3rS3cret!{secrets.token_hex(4)}"


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
            f"alembic upgrade head failed; password-reset tests cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture(autouse=True)
def _disable_celery_dispatch(monkeypatch):
    """Replace the Celery email enqueue with a no-op so the test does not
    require a worker process."""
    from services import password_reset_service as svc

    def _noop(*, plaintext_token, user_email, user_id):
        # Capture the plaintext on a class attribute so tests can read it.
        _noop.last = {
            "plaintext_token": plaintext_token,
            "user_email": user_email,
            "user_id": user_id,
        }

    _noop.last = None
    monkeypatch.setattr(svc, "_enqueue_reset_email", _noop)
    return _noop


@pytest.fixture(autouse=True)
def _ratelimit_off(monkeypatch):
    """The 5/min slowapi limit will trip mid-suite if we leave it on."""
    monkeypatch.setenv("RATELIMIT_DISABLED", "1")


@pytest.fixture
def app():
    from main import app as fastapi_app

    return fastapi_app


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    from core.audit import install_audit_listeners
    from core.config import database_url

    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    install_audit_listeners(factory)

    async with factory() as session:
        yield session

    await engine.dispose()


async def _register_user(client: AsyncClient, email: str, password: str) -> None:
    response = await client.post(
        "/auth/register",
        json={"email": email, "password": password, "full_name": "PWReset User"},
    )
    assert response.status_code in (200, 201), response.text


# ---------------------------------------------------------------------------
# /auth/forgot-password
# ---------------------------------------------------------------------------


async def test_forgot_password_returns_204_for_unknown_email(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/forgot-password",
        json={"email": _unique_email("nobody")},
    )
    # CWE-204: must look identical to the matched branch.
    assert response.status_code == 204
    assert response.content == b""


async def test_forgot_password_creates_token_for_known_email(
    client: AsyncClient,
    db_session: AsyncSession,
    _disable_celery_dispatch,
) -> None:
    from models import PasswordResetToken, User

    email = _unique_email("known")
    await _register_user(client, email, _strong_password())

    response = await client.post("/auth/forgot-password", json={"email": email})
    assert response.status_code == 204

    user = (
        await db_session.execute(select(User).where(User.email == email))
    ).scalar_one()

    rows = (
        (
            await db_session.execute(
                select(PasswordResetToken).where(PasswordResetToken.user_id == user.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].used_at is None
    assert rows[0].invalidated_at is None
    assert rows[0].expires_at > datetime.now(tz=UTC)
    # Bcrypt hash, not the plaintext.
    assert rows[0].token_hash.startswith("$2")
    assert rows[0].token_hash != _disable_celery_dispatch.last["plaintext_token"]


async def test_forgot_password_uniform_response_for_known_and_unknown(
    client: AsyncClient,
) -> None:
    """CWE-204: response shape is identical (status + headers minus
    Retry-After + body) regardless of whether the email exists."""
    known_email = _unique_email("known")
    unknown_email = _unique_email("unknown")
    await _register_user(client, known_email, _strong_password())

    r_known = await client.post("/auth/forgot-password", json={"email": known_email})
    r_unknown = await client.post(
        "/auth/forgot-password", json={"email": unknown_email}
    )
    assert r_known.status_code == r_unknown.status_code == 204
    assert r_known.content == r_unknown.content == b""


async def test_forgot_password_cooldown_does_not_enqueue_second_email(
    client: AsyncClient,
    db_session: AsyncSession,
    _disable_celery_dispatch,
    monkeypatch,
) -> None:
    """A second forgot-password call within 5 minutes must NOT issue a
    second token. The 204 contract is preserved; Retry-After is set."""
    from models import PasswordResetToken, User

    monkeypatch.setenv("PASSWORD_RESET_EMAIL_COOLDOWN_SECONDS", "300")

    email = _unique_email("cooldown")
    await _register_user(client, email, _strong_password())

    # First call — creates the token.
    r1 = await client.post("/auth/forgot-password", json={"email": email})
    assert r1.status_code == 204
    first_plaintext = _disable_celery_dispatch.last["plaintext_token"]

    # Second call — must be a no-op (cooldown).
    r2 = await client.post("/auth/forgot-password", json={"email": email})
    assert r2.status_code == 204
    # The fake enqueue's last payload is unchanged because cooldown skipped it.
    assert _disable_celery_dispatch.last["plaintext_token"] == first_plaintext
    # Retry-After surfaces the cooldown.
    assert r2.headers.get("retry-after") == "300"

    user = (
        await db_session.execute(select(User).where(User.email == email))
    ).scalar_one()
    rows = (
        (
            await db_session.execute(
                select(PasswordResetToken).where(PasswordResetToken.user_id == user.id)
            )
        )
        .scalars()
        .all()
    )
    # Still exactly one live token — cooldown did not duplicate.
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# /auth/reset-password
# ---------------------------------------------------------------------------


async def test_reset_password_happy_path_rotates_password(
    client: AsyncClient,
    db_session: AsyncSession,
    _disable_celery_dispatch,
) -> None:
    """End-to-end: forgot → reset → login with new password."""
    email = _unique_email("happy")
    old_pw = _strong_password()
    new_pw = _strong_password() + "X"
    await _register_user(client, email, old_pw)

    forgot = await client.post("/auth/forgot-password", json={"email": email})
    assert forgot.status_code == 204
    plaintext = _disable_celery_dispatch.last["plaintext_token"]

    reset = await client.post(
        "/auth/reset-password",
        json={"token": plaintext, "new_password": new_pw},
    )
    assert reset.status_code == 204, reset.text

    # Login with the OLD password fails.
    login_old = await client.post(
        "/auth/login",
        json={"email": email, "password": old_pw},
    )
    assert login_old.status_code == 401

    # Login with the NEW password succeeds.
    login_new = await client.post(
        "/auth/login",
        json={"email": email, "password": new_pw},
    )
    assert login_new.status_code == 200, login_new.text


async def test_reset_password_rejects_invalid_token(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/reset-password",
        json={
            "token": "this-is-not-a-real-token-but-long-enough",
            "new_password": _strong_password(),
        },
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    body = response.json()
    assert body["title"] == "Invalid or Expired Token"


async def test_reset_password_rejects_expired_token(
    client: AsyncClient,
    db_session: AsyncSession,
    _disable_celery_dispatch,
) -> None:
    """Manually backdate ``expires_at`` so the row is past TTL."""
    from models import PasswordResetToken, User

    email = _unique_email("expired")
    await _register_user(client, email, _strong_password())

    forgot = await client.post("/auth/forgot-password", json={"email": email})
    assert forgot.status_code == 204
    plaintext = _disable_celery_dispatch.last["plaintext_token"]

    user = (
        await db_session.execute(select(User).where(User.email == email))
    ).scalar_one()
    row = (
        (
            await db_session.execute(
                select(PasswordResetToken).where(PasswordResetToken.user_id == user.id)
            )
        )
        .scalar_one()
    )
    row.expires_at = datetime.now(tz=UTC) - timedelta(minutes=1)
    await db_session.commit()

    response = await client.post(
        "/auth/reset-password",
        json={"token": plaintext, "new_password": _strong_password()},
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_reset_password_rejects_used_token(
    client: AsyncClient,
    _disable_celery_dispatch,
) -> None:
    """A token consumed once cannot be replayed."""
    email = _unique_email("used")
    await _register_user(client, email, _strong_password())
    new_pw_1 = _strong_password() + "1"
    new_pw_2 = _strong_password() + "2"

    forgot = await client.post("/auth/forgot-password", json={"email": email})
    assert forgot.status_code == 204
    plaintext = _disable_celery_dispatch.last["plaintext_token"]

    first = await client.post(
        "/auth/reset-password",
        json={"token": plaintext, "new_password": new_pw_1},
    )
    assert first.status_code == 204

    second = await client.post(
        "/auth/reset-password",
        json={"token": plaintext, "new_password": new_pw_2},
    )
    assert second.status_code == 422
    assert second.headers["content-type"].startswith(PROBLEM_JSON)


async def test_reset_password_rejects_short_password(
    client: AsyncClient,
    _disable_celery_dispatch,
) -> None:
    email = _unique_email("short")
    await _register_user(client, email, _strong_password())

    forgot = await client.post("/auth/forgot-password", json={"email": email})
    assert forgot.status_code == 204
    plaintext = _disable_celery_dispatch.last["plaintext_token"]

    response = await client.post(
        "/auth/reset-password",
        json={"token": plaintext, "new_password": "short"},
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_reset_password_revokes_all_refresh_tokens(
    client: AsyncClient,
    db_session: AsyncSession,
    _disable_celery_dispatch,
) -> None:
    """After a successful reset every active refresh token for the user is
    marked revoked so a stolen session cannot keep rotating."""
    from models import RefreshToken, User

    email = _unique_email("revoke")
    pw = _strong_password()
    await _register_user(client, email, pw)

    # Login twice to produce two refresh tokens.
    for _ in range(2):
        login = await client.post(
            "/auth/login", json={"email": email, "password": pw}
        )
        assert login.status_code == 200, login.text

    user = (
        await db_session.execute(select(User).where(User.email == email))
    ).scalar_one()

    # Sanity check — at least 2 active refresh rows.
    active_before = (
        (
            await db_session.execute(
                select(RefreshToken).where(
                    RefreshToken.user_id == user.id,
                    RefreshToken.revoked_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(active_before) >= 2

    forgot = await client.post("/auth/forgot-password", json={"email": email})
    assert forgot.status_code == 204
    plaintext = _disable_celery_dispatch.last["plaintext_token"]

    reset = await client.post(
        "/auth/reset-password",
        json={"token": plaintext, "new_password": _strong_password() + "Z"},
    )
    assert reset.status_code == 204, reset.text

    # Force a fresh DB read instead of relying on the existing session cache —
    # AsyncSession.expire_all() needs a greenlet-bound context that this
    # bare integration test does not always have.
    await db_session.commit()
    active_after = (
        (
            await db_session.execute(
                select(RefreshToken).where(
                    RefreshToken.user_id == user.id,
                    RefreshToken.revoked_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    assert active_after == []


async def test_forgot_password_invalidates_prior_pending_token(
    client: AsyncClient,
    db_session: AsyncSession,
    _disable_celery_dispatch,
    monkeypatch,
) -> None:
    """Single-pending-token policy: the 2nd forgot call (with cooldown
    disabled) supersedes the first row."""
    from models import PasswordResetToken, User

    # Disable the cooldown so we can issue twice in quick succession.
    monkeypatch.setenv("PASSWORD_RESET_EMAIL_COOLDOWN_SECONDS", "0")

    email = _unique_email("supersede")
    await _register_user(client, email, _strong_password())

    r1 = await client.post("/auth/forgot-password", json={"email": email})
    assert r1.status_code == 204
    first_plain = _disable_celery_dispatch.last["plaintext_token"]

    r2 = await client.post("/auth/forgot-password", json={"email": email})
    assert r2.status_code == 204
    second_plain = _disable_celery_dispatch.last["plaintext_token"]
    assert first_plain != second_plain

    user = (
        await db_session.execute(select(User).where(User.email == email))
    ).scalar_one()
    rows = (
        (
            await db_session.execute(
                select(PasswordResetToken)
                .where(PasswordResetToken.user_id == user.id)
                .order_by(PasswordResetToken.created_at)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 2
    # Older row is invalidated; newer row is live.
    assert rows[0].invalidated_at is not None
    assert rows[1].invalidated_at is None

    # The OLD plaintext must not work anymore.
    bad = await client.post(
        "/auth/reset-password",
        json={"token": first_plain, "new_password": _strong_password()},
    )
    assert bad.status_code == 422
