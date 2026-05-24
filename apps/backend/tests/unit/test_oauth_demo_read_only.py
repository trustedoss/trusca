"""
Unit tests for the OAuth demo read-only short-circuit — security-reviewer M-1.

The OAuth ``authorize`` / ``callback`` endpoints are GETs, so they bypass the
``DemoReadOnlyMiddleware`` (which only gates unsafe methods). But a successful
callback WRITES (creates a User + personal Team, or links/rotates an OAuth
identity). When ``DEMO_READ_ONLY`` is enabled we must disable OAuth sign-in
entirely. These tests exercise the guard at the unit level (no DB / no provider
HTTP) by calling the endpoint functions directly with a fabricated Request and
asserting the RFC 7807 403 short-circuit happens BEFORE any I/O.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from starlette.requests import Request


def _make_request(path: str) -> Request:
    """Build a minimal ASGI GET Request for the given path."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("testclient", 1234),
    }
    return Request(scope)


async def _read_body(response: object) -> dict[Any, Any]:
    """Render a Starlette JSONResponse body to a dict (no live transport)."""
    chunks: list[bytes] = []

    async def _send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))

    async def _receive() -> dict[str, Any]:  # pragma: no cover - unused by JSON
        return {"type": "http.request"}

    scope = {"type": "http"}
    await response(scope, _receive, _send)  # type: ignore[operator]
    parsed: dict[Any, Any] = json.loads(b"".join(chunks).decode())
    return parsed


def test_demo_read_only_helper_returns_problem_json() -> None:
    from api.v1.oauth import _DEMO_READ_ONLY_TYPE, _demo_read_only_blocked

    req = _make_request("/auth/oauth/github/authorize")
    resp = _demo_read_only_blocked(req, provider="github")
    assert resp.status_code == 403
    assert resp.media_type == "application/problem+json"
    # The body carries the RFC 7807 envelope + the snake_case domain extension.
    import asyncio

    body = asyncio.get_event_loop().run_until_complete(_read_body(resp))
    assert body["status"] == 403
    assert body["type"] == _DEMO_READ_ONLY_TYPE
    assert body["demo_read_only"] is True
    assert body["instance"] == "/auth/oauth/github/authorize"


async def test_authorize_short_circuits_when_demo_read_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """authorize() returns the 403 problem BEFORE building any provider URL."""
    monkeypatch.setenv("DEMO_READ_ONLY", "true")
    from api.v1.oauth import authorize

    req = _make_request("/auth/oauth/github/authorize")
    resp = await authorize(req, provider="github", redirect_after="/x")
    assert resp.status_code == 403
    body = await _read_body(resp)
    assert body["demo_read_only"] is True


async def test_callback_short_circuits_before_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """callback() 403s before touching the (None) session — proving no DB I/O.

    Passing ``session=None`` would explode if the guard did not short-circuit
    first, which is exactly the invariant we want to pin.
    """
    monkeypatch.setenv("DEMO_READ_ONLY", "true")
    from api.v1.oauth import callback

    req = _make_request("/auth/oauth/google/callback")
    resp = await callback(
        req,
        provider="google",
        code="abc",
        state="whatever",
        error=None,
        session=None,  # type: ignore[arg-type]
    )
    assert resp.status_code == 403
    body = await _read_body(resp)
    assert body["demo_read_only"] is True


async def test_runtime_env_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard reads DEMO_READ_ONLY at call time (CLAUDE.md core rule #11)."""
    from api.v1.oauth import callback

    req = _make_request("/auth/oauth/github/callback")

    # Flag off → it must NOT short-circuit; it falls through to the missing-params
    # failure redirect (302), proving the env is consulted at call time.
    monkeypatch.setenv("DEMO_READ_ONLY", "false")
    resp_off = await callback(
        req, provider="github", code=None, state=None, error=None,
        session=None,  # type: ignore[arg-type]
    )
    assert resp_off.status_code == 302  # missing-params failure redirect

    monkeypatch.setenv("DEMO_READ_ONLY", "true")
    resp_on = await callback(
        req, provider="github", code=None, state=None, error=None,
        session=None,  # type: ignore[arg-type]
    )
    assert resp_on.status_code == 403
