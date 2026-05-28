"""
Unit tests for the v2.1 Track B (B5) DEMO_READ_ONLY guard.

This is a SECURITY boundary: when ``DEMO_READ_ONLY`` is on, the public live demo
must reject EVERY mutation that is not an explicitly allow-listed auth flow. The
tests below pin both the happy path (reads pass, allow-listed auth writes pass)
AND adversarial bypass attempts (case-variant verbs/paths, traversal, trailing
slashes, registration/password mutation, exotic methods), plus the no-regression
case (flag OFF → everything works).

Driven through a real ``TestClient`` over a tiny app that mounts the actual
``DemoReadOnlyMiddleware`` with representative routes, so we exercise the same
ASGI scope handling production uses (not just the pure helper functions).
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

from core.config import demo_read_only
from core.middleware import (
    _DEMO_WRITE_ALLOWLIST,
    DemoReadOnlyMiddleware,
    _is_demo_write_allowed,
    _normalize_path,
)

DEMO_RESPONSE_TYPE = "urn:trustedoss:problem:demo-read-only"


def _build_app() -> FastAPI:
    """A minimal app whose routes mirror the real surface the guard protects.

    We register the auth allow-list paths *and* a representative mutating
    business route (`/v1/projects`) under every mutating verb so we can assert
    the middleware — not the route — is what blocks (or allows) the request.
    """
    app = FastAPI()
    app.add_middleware(DemoReadOnlyMiddleware)

    # Read surface.
    @app.get("/v1/projects")
    async def _list_projects() -> dict[str, str]:
        return {"items": "ok"}

    @app.get("/health")
    async def _health() -> dict[str, str]:
        return {"status": "ok"}

    # Allow-listed auth flows (must pass even under read-only).
    @app.post("/auth/login")
    async def _login() -> dict[str, str]:
        return {"access_token": "x"}

    @app.post("/auth/refresh")
    async def _refresh() -> dict[str, str]:
        return {"access_token": "y"}

    @app.post("/auth/logout")
    async def _logout() -> Response:
        return Response(status_code=204)

    # Deliberately NOT allow-listed auth mutations.
    @app.post("/auth/register")
    async def _register() -> dict[str, str]:
        return {"id": "z"}

    @app.post("/auth/reset-password")
    async def _reset_password() -> Response:
        return Response(status_code=204)

    # Representative business mutations across every verb.
    @app.post("/v1/projects")
    async def _create_project() -> dict[str, str]:
        return {"id": "1"}

    @app.put("/v1/projects/1")
    async def _replace_project() -> dict[str, str]:
        return {"id": "1"}

    @app.patch("/v1/projects/1")
    async def _update_project() -> dict[str, str]:
        return {"id": "1"}

    @app.delete("/v1/projects/1")
    async def _delete_project() -> Response:
        return Response(status_code=204)

    # A path that would let traversal *target* an allow-listed prefix.
    @app.post("/v1/projects/{rest:path}")
    async def _catch_all(rest: str) -> dict[str, str]:
        return {"rest": rest}

    return app


@pytest.fixture
def ro_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DEMO_READ_ONLY", "true")
    return TestClient(_build_app())


@pytest.fixture
def open_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("DEMO_READ_ONLY", raising=False)
    return TestClient(_build_app())


# --------------------------------------------------------------------------- #
# Happy path: reads pass, allow-listed auth writes pass.
# --------------------------------------------------------------------------- #


def test_get_passes_under_read_only(ro_client: TestClient) -> None:
    resp = ro_client.get("/v1/projects")
    assert resp.status_code == 200


def test_health_get_passes_under_read_only(ro_client: TestClient) -> None:
    assert ro_client.get("/health").status_code == 200


@pytest.mark.parametrize("path", ["/auth/login", "/auth/refresh", "/auth/logout"])
def test_allowlisted_auth_writes_pass(ro_client: TestClient, path: str) -> None:
    resp = ro_client.post(path)
    assert resp.status_code in (200, 204)


def test_options_preflight_passes(ro_client: TestClient) -> None:
    # OPTIONS must pass so CORS preflight keeps working.
    resp = ro_client.options("/v1/projects")
    # 405 from the route is fine — the point is the middleware did NOT 403 it.
    assert resp.status_code != 403


# --------------------------------------------------------------------------- #
# Core block: every mutating verb on a non-allow-listed path → RFC 7807 403.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/v1/projects"),
        ("put", "/v1/projects/1"),
        ("patch", "/v1/projects/1"),
        ("delete", "/v1/projects/1"),
    ],
)
def test_mutations_blocked_under_read_only(
    ro_client: TestClient, method: str, path: str
) -> None:
    resp = getattr(ro_client, method)(path)
    assert resp.status_code == 403
    assert resp.headers["content-type"].startswith("application/problem+json")
    body = resp.json()
    assert body["type"] == DEMO_RESPONSE_TYPE
    assert body["status"] == 403
    assert body["demo_read_only"] is True
    # RFC 7807 required fields present.
    for field in ("type", "title", "status", "detail", "instance"):
        assert field in body


def test_non_allowlisted_auth_mutations_blocked(ro_client: TestClient) -> None:
    """register / reset-password are intentionally NOT on the allow-list."""
    assert ro_client.post("/auth/register").status_code == 403
    assert ro_client.post("/auth/reset-password").status_code == 403


# --------------------------------------------------------------------------- #
# Adversarial bypass attempts — must all be BLOCKED.
# --------------------------------------------------------------------------- #


def test_traversal_into_allowlist_is_blocked(ro_client: TestClient) -> None:
    """A write path dressed up to *contain* an allow-listed segment must not
    smuggle through. After normalization '/v1/projects/../auth/login' collapses
    to '/v1/auth/login' (NOT on the allow-list), so it stays blocked."""
    resp = ro_client.post("/v1/projects/..%2Fauth%2Flogin")
    assert resp.status_code == 403


def test_traversal_out_of_allowlist_is_blocked(ro_client: TestClient) -> None:
    """'/auth/login/../../v1/projects' normalizes to '/v1/projects' → blocked."""
    # Build the raw path explicitly; httpx will percent-encode, the ASGI server
    # decodes, and our normalizer collapses the '..' segments.
    resp = ro_client.request("POST", "/auth/login/../../v1/projects")
    assert resp.status_code == 403


@pytest.mark.parametrize(
    "raw",
    [
        "/v1/projects/../auth/login",  # collapses to /v1/auth/login
        "/auth/login/../../v1/projects",  # collapses to /v1/projects
        "/auth/./login/../register",  # collapses to /auth/register
        "//auth/login",  # double slash -> /auth/login (still must be checked)
    ],
)
def test_normalize_path_resolves_traversal(raw: str) -> None:
    normalized = _normalize_path(raw)
    assert ".." not in normalized
    assert normalized.startswith("/")


def test_backslash_separator_does_not_bypass() -> None:
    # A back-slash separated 'path' must fold to '/' before comparison so it
    # cannot dodge the allow-list.
    assert _normalize_path("\\auth\\login") == "/auth/login"


def test_normalize_empty_path_is_root() -> None:
    assert _normalize_path("") == "/"


def test_normalize_relative_path_gets_leading_slash() -> None:
    # A path lacking a leading slash (defensive — ASGI always supplies one)
    # must be coerced to an absolute path so the comparison is unambiguous.
    assert _normalize_path("auth/login").startswith("/")
    # '..' that would escape above root collapses but stays absolute.
    assert _normalize_path("../../etc/passwd").startswith("/")


def test_trailing_slash_matches_allowlist() -> None:
    # '/auth/login/' must be treated identically to '/auth/login'.
    assert _is_demo_write_allowed("POST", "/auth/login/") is True


def test_lowercase_verb_does_not_bypass() -> None:
    # Defensive: a lower-cased verb is still treated as a mutation.
    assert _is_demo_write_allowed("post", "/v1/projects") is False
    assert _is_demo_write_allowed("delete", "/v1/projects/1") is False


def test_exotic_verb_is_blocked() -> None:
    # Any non-safe, non-allow-listed method is blocked (deny-by-default). The
    # allow-list is keyed on (method, path), so an exotic verb cannot ride an
    # allow-listed *path* — CONNECT /auth/login is still blocked.
    assert _is_demo_write_allowed("PROPFIND", "/v1/projects") is False
    assert _is_demo_write_allowed("CONNECT", "/auth/login") is False
    assert _is_demo_write_allowed("PUT", "/auth/login") is False


def test_safe_methods_always_allowed() -> None:
    for method in ("GET", "HEAD", "OPTIONS", "get", "head", "options"):
        assert _is_demo_write_allowed(method, "/v1/projects") is True


def test_allowlist_does_not_include_register_or_reset() -> None:
    # Lock the allow-list contents so a future edit that widens it is caught.
    assert _DEMO_WRITE_ALLOWLIST == frozenset(
        {
            ("POST", "/auth/login"),
            ("POST", "/auth/refresh"),
            ("POST", "/auth/logout"),
        }
    )


# --------------------------------------------------------------------------- #
# No regression: flag OFF → everything works.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/v1/projects"),
        ("put", "/v1/projects/1"),
        ("patch", "/v1/projects/1"),
        ("delete", "/v1/projects/1"),
        ("post", "/auth/register"),
    ],
)
def test_mutations_pass_when_flag_off(
    open_client: TestClient, method: str, path: str
) -> None:
    resp = getattr(open_client, method)(path)
    assert resp.status_code != 403


def test_demo_read_only_config_accessor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEMO_READ_ONLY", raising=False)
    assert demo_read_only() is False
    for truthy in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("DEMO_READ_ONLY", truthy)
        assert demo_read_only() is True
    for falsy in ("0", "false", "no", "off", ""):
        monkeypatch.setenv("DEMO_READ_ONLY", falsy)
        assert demo_read_only() is False
