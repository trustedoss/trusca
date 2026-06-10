"""
Unit tests for ``GET /auth/oauth/providers`` — M-15.

The /login page consumes this endpoint anonymously to decide which OAuth
sign-in buttons to render, so the contract under test is:

  - Unauthenticated call → 200 (the endpoint is an explicit public
    exception to CLAUDE.md core rule #12).
  - Every supported provider is always listed with a bare ``configured``
    boolean — and NOTHING else (no client ids / secrets).
  - ``configured`` is true ONLY when both the client id AND secret are
    set, matching the condition under which ``/{provider}/authorize``
    actually works (``_require_credentials`` in the provider adapters).

No DB / no provider HTTP — the endpoint reads env via the runtime
``core.config`` accessors only.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

_OAUTH_ENV_VARS = (
    "GITHUB_CLIENT_ID",
    "GITHUB_CLIENT_SECRET",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
)


@pytest.fixture(autouse=True)
def _clear_oauth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test from the 'nothing configured' baseline."""
    for var in _OAUTH_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from main import app

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


def _by_provider(body: dict) -> dict[str, bool]:
    return {row["provider"]: row["configured"] for row in body["providers"]}


async def test_unauthenticated_call_returns_200(client: AsyncClient) -> None:
    """(d) Public endpoint — no Authorization header, still 200 JSON."""
    response = await client.get("/auth/oauth/providers")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/json")


async def test_nothing_configured_lists_both_providers_false(
    client: AsyncClient,
) -> None:
    """(a) Both providers unset → both listed, both ``configured=false``."""
    response = await client.get("/auth/oauth/providers")
    assert response.status_code == 200, response.text
    body = response.json()
    # Stable order: the SPA renders buttons in response order.
    assert [row["provider"] for row in body["providers"]] == ["github", "google"]
    assert _by_provider(body) == {"github": False, "google": False}


async def test_github_id_and_secret_set_marks_github_configured(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(b) GitHub id+secret set → github true, google stays false."""
    monkeypatch.setenv("GITHUB_CLIENT_ID", "gh-test-client-id")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "gh-test-client-secret")
    response = await client.get("/auth/oauth/providers")
    assert response.status_code == 200, response.text
    assert _by_provider(response.json()) == {"github": True, "google": False}


async def test_id_without_secret_is_not_configured(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(c) Half-configured (id only) must report false.

    /authorize raises ``OAuthProviderDisabled`` (503) whenever EITHER
    credential is missing, so reporting true here would resurrect the
    M-15 bug (rendered button → 503 on click).
    """
    monkeypatch.setenv("GITHUB_CLIENT_ID", "gh-test-client-id")
    # secret deliberately unset (autouse fixture cleared it)
    response = await client.get("/auth/oauth/providers")
    assert response.status_code == 200, response.text
    assert _by_provider(response.json())["github"] is False


async def test_secret_without_id_is_not_configured(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(c-mirror) Secret only → also not configured."""
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "google-test-client-secret")
    response = await client.get("/auth/oauth/providers")
    assert response.status_code == 200, response.text
    assert _by_provider(response.json())["google"] is False


async def test_response_leaks_no_credentials(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Security: booleans only — no client id/secret in the wire body."""
    monkeypatch.setenv("GITHUB_CLIENT_ID", "gh-secret-marker-id")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "gh-secret-marker-secret")
    response = await client.get("/auth/oauth/providers")
    assert response.status_code == 200, response.text
    assert "secret-marker" not in response.text
    for row in response.json()["providers"]:
        assert set(row.keys()) == {"provider", "configured"}


def test_service_helper_rejects_unknown_provider() -> None:
    """``oauth_provider_configured`` mirrors ``get_provider``'s fail-loud."""
    from services.oauth_service import OAuthProviderUnknown, oauth_provider_configured

    with pytest.raises(OAuthProviderUnknown):
        oauth_provider_configured("gitlab")
