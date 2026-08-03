"""
Integration tests for the OAuth (GitHub + Google) endpoints — Phase 8 PR #23.

Endpoints exercised:
  - GET /auth/oauth/{provider}/authorize  → 302 to provider with signed state
  - GET /auth/oauth/{provider}/callback   → either 302 → success URL with
                                            refresh cookie OR 302 → failure URL

Provider HTTP calls are mocked via ``httpx.MockTransport`` patched onto
``httpx.AsyncClient``. The test stack runs against the real Postgres
configured by ``DATABASE_URL`` so the user / team / oauth_identity row
graph is created end-to-end.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip oauth integration test")
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
            f"alembic upgrade head failed; oauth tests cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture(autouse=True)
def _oauth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure all OAuth env so the providers are 'enabled' by default."""
    monkeypatch.setenv("GITHUB_CLIENT_ID", "github-test-client-id")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "github-test-client-secret")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "google-test-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "google-test-client-secret")
    monkeypatch.setenv(
        "OAUTH_LOGIN_REDIRECT_DEFAULT", "http://localhost:5173/dashboard"
    )
    monkeypatch.setenv(
        "OAUTH_LOGIN_REDIRECT_FAILURE", "http://localhost:5173/login"
    )
    # Stable secret for state-JWT round-trip across the fastapi process.
    monkeypatch.setenv("SECRET_KEY", "oauth-int-test-secret-min-32-chars-XXXXXXXX")


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
async def db_factory() -> AsyncIterator[async_sessionmaker[Any]]:
    from core.config import database_url

    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.fixture
async def seed_organization(db_factory: async_sessionmaker[Any]) -> AsyncIterator[None]:
    """Make sure at least one organization exists (personal-team bootstrap)."""
    from models import Organization

    async with db_factory() as session:
        existing = (
            await session.execute(select(Organization).limit(1))
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                Organization(
                    name=f"OAuthTestOrg-{uuid.uuid4().hex[:6]}",
                    slug=f"oauth-test-org-{uuid.uuid4().hex[:6]}",
                )
            )
            await session.commit()
    yield


@pytest.fixture
def patch_async_client(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[Callable[[httpx.Request], httpx.Response]], None]:
    real_init = httpx.AsyncClient.__init__

    def install(handler: Callable[[httpx.Request], httpx.Response]) -> None:
        def _patched_init(self: httpx.AsyncClient, *args, **kwargs) -> None:
            kwargs["transport"] = httpx.MockTransport(handler)
            real_init(self, *args, **kwargs)

        monkeypatch.setattr(httpx.AsyncClient, "__init__", _patched_init)

    return install


# ---------------------------------------------------------------------------
# /authorize
# ---------------------------------------------------------------------------


async def test_authorize_redirects_to_github(client: AsyncClient) -> None:
    response = await client.get(
        "/auth/oauth/github/authorize",
        params={"redirect_after": "/projects/abc"},
    )
    assert response.status_code == 302
    target = response.headers["location"]
    assert target.startswith("https://github.com/login/oauth/authorize?")
    qs = parse_qs(urlsplit(target).query)
    assert qs["client_id"] == ["github-test-client-id"]
    # state is signed JWT → not directly inspectable, but it MUST be present.
    assert "state" in qs and qs["state"][0]
    # callback redirect_uri is built from the route URL.
    assert qs["redirect_uri"][0].endswith("/auth/oauth/github/callback")


async def test_authorize_unknown_provider_returns_422(client: AsyncClient) -> None:
    """Literal['github', 'google'] gate triggers Pydantic 422 problem+json."""
    response = await client.get("/auth/oauth/facebook/authorize")
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_authorize_disabled_provider_returns_503_problem_details(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GITHUB_CLIENT_ID", raising=False)
    monkeypatch.delenv("GITHUB_CLIENT_SECRET", raising=False)

    response = await client.get("/auth/oauth/github/authorize")
    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["status"] == 503
    assert body.get("oauth_provider_disabled") is True


# ---------------------------------------------------------------------------
# M-1 — demo read-only blocks OAuth (authorize + callback)
# ---------------------------------------------------------------------------


async def test_authorize_blocked_when_demo_read_only(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M-1: even with a fully-configured provider, demo read-only mode 403s the
    authorize endpoint (OAuth sign-in is a write — it can create a user/team)."""
    monkeypatch.setenv("DEMO_READ_ONLY", "true")
    response = await client.get("/auth/oauth/github/authorize")
    assert response.status_code == 403
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["status"] == 403
    assert body.get("demo_read_only") is True
    # It must NOT redirect to the provider.
    assert "location" not in {k.lower() for k in response.headers}


async def test_callback_blocked_when_demo_read_only_creates_no_user(
    client: AsyncClient,
    db_factory: async_sessionmaker[Any],
    seed_organization: None,
    monkeypatch: pytest.MonkeyPatch,
    patch_async_client: Callable[[Callable[[httpx.Request], httpx.Response]], None],
) -> None:
    """M-1: the callback 403s BEFORE any token exchange / DB write under demo
    read-only — no User / OAuthIdentity / Team is created."""
    from models import OAuthIdentity, User

    monkeypatch.setenv("DEMO_READ_ONLY", "true")

    email = f"demo-block-{uuid.uuid4().hex[:8]}@example.com"
    user_id = abs(hash(email)) % (10**9)
    patch_async_client(_github_handler(user_id=user_id, email=email, name="Blocked"))
    state = await _mint_state("github", "/dashboard")

    response = await client.get(
        "/auth/oauth/github/callback",
        params={"code": "abc", "state": state},
    )
    assert response.status_code == 403
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json().get("demo_read_only") is True
    # No write happened.
    async with db_factory() as session:
        user = (
            await session.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()
        assert user is None
        any_identity = (
            await session.execute(
                select(OAuthIdentity).where(
                    OAuthIdentity.provider_user_id == str(user_id)
                )
            )
        ).scalar_one_or_none()
        assert any_identity is None


async def test_authorize_allowed_when_demo_read_only_off(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sanity: with the flag off the authorize endpoint still redirects."""
    monkeypatch.setenv("DEMO_READ_ONLY", "false")
    response = await client.get("/auth/oauth/github/authorize")
    assert response.status_code == 302
    assert response.headers["location"].startswith(
        "https://github.com/login/oauth/authorize?"
    )


# ---------------------------------------------------------------------------
# /callback — happy path: brand new user
# ---------------------------------------------------------------------------


def _github_handler(
    *,
    user_id: int,
    email: str,
    name: str | None,
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "gh-tok-int", "token_type": "bearer"})
        if path == "/user":
            return httpx.Response(
                200,
                json={
                    "id": user_id,
                    "email": email,
                    "name": name,
                    "avatar_url": "https://cdn.test/avatar.png",
                },
            )
        if path == "/user/emails":
            return httpx.Response(
                200,
                json=[{"email": email, "primary": True, "verified": True}],
            )
        raise AssertionError(f"unexpected url {request.url}")

    return handler


async def _mint_state(provider: str, redirect_after: str | None) -> str:
    from services.oauth_service import _signed_state

    return _signed_state(provider=provider, redirect_after=redirect_after)


async def test_callback_creates_new_user_and_personal_team(
    client: AsyncClient,
    db_factory: async_sessionmaker[Any],
    seed_organization: None,
    patch_async_client: Callable[[Callable[[httpx.Request], httpx.Response]], None],
) -> None:
    from models import Membership, OAuthIdentity, Team, User

    email = f"newoauth-{uuid.uuid4().hex[:8]}@example.com"
    user_id = abs(hash(email)) % (10**9)

    patch_async_client(_github_handler(user_id=user_id, email=email, name="OAuth Newbie"))
    state = await _mint_state("github", "/dashboard")

    response = await client.get(
        "/auth/oauth/github/callback",
        params={"code": "abc", "state": state},
    )
    assert response.status_code == 302, response.text
    # Success → success-default URL because we passed redirect_after="/dashboard"
    # which is treated verbatim.
    assert response.headers["location"] == "/dashboard"
    # refresh cookie set, HttpOnly + SameSite=Lax (Secure off in dev).
    raw_cookie = response.headers.get("set-cookie", "")
    assert "refresh_token=" in raw_cookie
    assert "HttpOnly" in raw_cookie
    assert "SameSite=lax".lower() in raw_cookie.lower()

    # DB invariants: User + OAuthIdentity + personal Team + team_admin membership.
    async with db_factory() as session:
        user = (
            await session.execute(select(User).where(User.email == email))
        ).scalar_one()
        identity = (
            await session.execute(
                select(OAuthIdentity).where(OAuthIdentity.user_id == user.id)
            )
        ).scalar_one()
        assert identity.provider == "github"
        assert identity.provider_user_id == str(user_id)
        membership = (
            await session.execute(
                select(Membership).where(Membership.user_id == user.id)
            )
        ).scalar_one()
        assert membership.role == "team_admin"
        team = (
            await session.execute(select(Team).where(Team.id == membership.team_id))
        ).scalar_one()
        assert team.slug.startswith("github-")
        assert "Team" in team.name


# ---------------------------------------------------------------------------
# /callback — existing OAuth identity reused
# ---------------------------------------------------------------------------


async def test_callback_reuses_existing_oauth_identity(
    client: AsyncClient,
    db_factory: async_sessionmaker[Any],
    seed_organization: None,
    patch_async_client: Callable[[Callable[[httpx.Request], httpx.Response]], None],
) -> None:
    """A second sign-in with the same external account must NOT create a new user."""
    from models import OAuthIdentity, User

    email = f"repeatoauth-{uuid.uuid4().hex[:8]}@example.com"
    user_id = abs(hash(email)) % (10**9)

    # First sign-in — creates everything.
    patch_async_client(_github_handler(user_id=user_id, email=email, name="Repeat OAuth"))
    state1 = await _mint_state("github", None)
    r1 = await client.get(
        "/auth/oauth/github/callback",
        params={"code": "code1", "state": state1},
    )
    assert r1.status_code == 302

    async with db_factory() as session:
        users_before = list(
            (await session.execute(select(User).where(User.email == email))).scalars().all()
        )
        assert len(users_before) == 1
        identities_before = list(
            (
                await session.execute(
                    select(OAuthIdentity).where(OAuthIdentity.user_id == users_before[0].id)
                )
            )
            .scalars()
            .all()
        )
        assert len(identities_before) == 1
        first_login_at = identities_before[0].last_login_at

    # Second sign-in — same provider id. Should reuse, NOT create a duplicate.
    state2 = await _mint_state("github", None)
    r2 = await client.get(
        "/auth/oauth/github/callback",
        params={"code": "code2", "state": state2},
    )
    assert r2.status_code == 302

    async with db_factory() as session:
        users_after = list(
            (await session.execute(select(User).where(User.email == email))).scalars().all()
        )
        assert len(users_after) == 1
        identities_after = list(
            (
                await session.execute(
                    select(OAuthIdentity).where(OAuthIdentity.user_id == users_after[0].id)
                )
            )
            .scalars()
            .all()
        )
        assert len(identities_after) == 1
        # last_login_at must have advanced (or at least be set).
        assert identities_after[0].last_login_at is not None
        if first_login_at is not None:
            assert identities_after[0].last_login_at >= first_login_at


# ---------------------------------------------------------------------------
# /callback — link to existing password-only user by email
# ---------------------------------------------------------------------------


async def test_callback_links_existing_password_user_by_email(
    client: AsyncClient,
    db_factory: async_sessionmaker[Any],
    seed_organization: None,
    patch_async_client: Callable[[Callable[[httpx.Request], httpx.Response]], None],
) -> None:
    from core.security import hash_password
    from models import OAuthIdentity, User

    email = f"linkoauth-{uuid.uuid4().hex[:8]}@example.com"
    user_id = abs(hash(email)) % (10**9)

    # Pre-create a password-only User (no OAuth identity yet).
    async with db_factory() as session:
        u = User(
            email=email,
            hashed_password=hash_password("Sup3rSecret!password"),
            full_name="Pre-Existing",
            is_active=True,
            is_superuser=False,
            is_verified=False,
        )
        session.add(u)
        await session.commit()
        await session.refresh(u)
        existing_user_id = u.id

    patch_async_client(_github_handler(user_id=user_id, email=email, name="GitHub Name"))
    state = await _mint_state("github", None)
    response = await client.get(
        "/auth/oauth/github/callback",
        params={"code": "abc", "state": state},
    )
    assert response.status_code == 302

    async with db_factory() as session:
        # Same User, now with one identity attached.
        users = list(
            (await session.execute(select(User).where(User.email == email))).scalars().all()
        )
        assert len(users) == 1
        assert users[0].id == existing_user_id
        identities = list(
            (
                await session.execute(
                    select(OAuthIdentity).where(OAuthIdentity.user_id == existing_user_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(identities) == 1
        assert identities[0].provider == "github"


# ---------------------------------------------------------------------------
# /callback — failure modes
# ---------------------------------------------------------------------------


async def test_callback_provider_denied_redirects_to_failure(
    client: AsyncClient,
) -> None:
    """User clicked Cancel on the consent page — provider sends ?error=access_denied."""
    response = await client.get(
        "/auth/oauth/github/callback",
        params={"error": "access_denied"},
    )
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("http://localhost:5173/login")
    assert "error=oauth_denied" in location


async def test_callback_invalid_state_redirects_to_failure(
    client: AsyncClient,
) -> None:
    """A garbage state JWT must NOT crash; we 302 to the failure URL."""
    response = await client.get(
        "/auth/oauth/github/callback",
        params={"code": "abc", "state": "definitely-not-a-jwt"},
    )
    assert response.status_code == 302
    assert "error=oauth_invalid_state" in response.headers["location"]


async def test_callback_missing_params_redirects_to_failure(
    client: AsyncClient,
) -> None:
    response = await client.get("/auth/oauth/github/callback")
    assert response.status_code == 302
    assert "error=oauth_missing_params" in response.headers["location"]


async def test_callback_provider_5xx_redirects_to_failure(
    client: AsyncClient,
    seed_organization: None,
    patch_async_client: Callable[[Callable[[httpx.Request], httpx.Response]], None],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # Fail the token exchange with a 500 — service should redirect.
        return httpx.Response(500, json={"error": "internal"})

    patch_async_client(handler)
    state = await _mint_state("github", None)
    response = await client.get(
        "/auth/oauth/github/callback",
        params={"code": "abc", "state": state},
    )
    assert response.status_code == 302
    assert "error=oauth_failed" in response.headers["location"]


# ---------------------------------------------------------------------------
# /callback — Google happy path
# ---------------------------------------------------------------------------


async def test_callback_google_happy_path(
    client: AsyncClient,
    db_factory: async_sessionmaker[Any],
    seed_organization: None,
    patch_async_client: Callable[[Callable[[httpx.Request], httpx.Response]], None],
) -> None:
    from models import OAuthIdentity, User

    email = f"goog-{uuid.uuid4().hex[:8]}@example.com"
    sub = f"google-sub-{uuid.uuid4().hex[:12]}"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "ya29.fake", "expires_in": 3600})
        if request.url.host == "openidconnect.googleapis.com":
            return httpx.Response(
                200,
                json={
                    "sub": sub,
                    "email": email,
                    "email_verified": True,
                    "name": "Google User",
                    "picture": "https://lh3.test/pic",
                },
            )
        raise AssertionError(f"unexpected url {request.url}")

    patch_async_client(handler)
    state = await _mint_state("google", None)
    response = await client.get(
        "/auth/oauth/google/callback",
        params={"code": "abc", "state": state},
    )
    assert response.status_code == 302
    # Default redirect when state has no redirect_after.
    assert response.headers["location"] == "http://localhost:5173/dashboard"

    async with db_factory() as session:
        user = (
            await session.execute(select(User).where(User.email == email))
        ).scalar_one()
        identity = (
            await session.execute(
                select(OAuthIdentity).where(OAuthIdentity.user_id == user.id)
            )
        ).scalar_one()
        assert identity.provider == "google"
        assert identity.provider_user_id == sub


# ---------------------------------------------------------------------------
# Defence-in-depth: unique constraint on (provider, provider_user_id)
# ---------------------------------------------------------------------------


async def test_oauth_identity_unique_constraint_present(
    db_factory: async_sessionmaker[Any],
) -> None:
    """The DB layer must reject (provider, provider_user_id) collision."""
    async with db_factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE schemaname = 'public' AND tablename = 'oauth_identities'"
                )
            )
        ).scalars().all()
        names = set(rows)
    # Both the explicit FK index and the (provider, provider_user_id) unique
    # constraint must be present.
    assert "ix_oauth_identities_user_id" in names
    # Unique constraint name from migration 0010.
    assert any("provider_pid" in n for n in names) or "uq_oauth_identities_provider_pid" in names
