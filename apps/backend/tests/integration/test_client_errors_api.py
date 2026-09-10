# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
POST /v1/client-errors (#423): route wiring, and the service it wraps.

`ErrorBoundary.tsx` used to stop at `console.error`. This is the other half:
proves the report actually reaches a structured log line an operator can
find, unauthenticated calls work (a crash can happen before there is a
token), an authenticated caller's id rides along, the URL's query string is
stripped before logging, and the endpoint is rate-limited.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from core.security import create_access_token
from models import User
from schemas.client_error import ClientErrorReportIn
from services.client_error_service import _strip_query, record_client_error
from tests._db_required import migrate_to_head
from tests._helpers import make_user

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
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


def _bearer(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(subject=str(user.id), role=None)}"}


# ---------------------------------------------------------------------------
# services.client_error_service: unit
# ---------------------------------------------------------------------------


def test_strip_query_removes_query_and_fragment() -> None:
    assert (
        _strip_query("https://portal.example/projects/1?token=abc123&x=1#section")
        == "https://portal.example/projects/1"
    )


def test_strip_query_leaves_a_clean_url_unchanged() -> None:
    assert _strip_query("https://portal.example/projects/1") == "https://portal.example/projects/1"


def test_strip_query_does_not_raise_on_garbage_input() -> None:
    # urlsplit is lenient; this exercises the fallback path explicitly rather
    # than trusting that no input can reach it.
    assert _strip_query("not a url at all") == "not a url at all"


def test_record_client_error_logs_the_report_with_the_query_string_stripped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logged: list[dict[str, Any]] = []

    class _Recorder:
        def warning(self, event: str, **fields: Any) -> None:
            logged.append({"event": event, **fields})

    import services.client_error_service as svc

    monkeypatch.setattr(svc, "log", _Recorder())

    report = ClientErrorReportIn(
        message="Cannot read properties of undefined (reading 'map')",
        stack="TypeError: ...\n    at Component (App.tsx:42:7)",
        component_stack="\n    in Component\n    in ErrorBoundary",
        url="https://portal.example/projects/1?reset_token=super-secret",
    )
    record_client_error(report, actor_user_id=None, user_agent="Mozilla/5.0 test-agent")

    assert len(logged) == 1
    entry = logged[0]
    assert entry["event"] == "client_error_reported"
    assert entry["message"] == report.message
    assert entry["stack"] == report.stack
    assert entry["component_stack"] == report.component_stack
    assert entry["url"] == "https://portal.example/projects/1"
    assert "super-secret" not in entry["url"]
    assert entry["user_agent"] == "Mozilla/5.0 test-agent"
    assert entry["actor_user_id"] is None


# ---------------------------------------------------------------------------
# POST /v1/client-errors: integration
# ---------------------------------------------------------------------------

_VALID_BODY = {
    "message": "Cannot read properties of undefined (reading 'map')",
    "stack": "TypeError: ...\n    at Component (App.tsx:42:7)",
    "component_stack": "\n    in Component\n    in ErrorBoundary",
    "url": "https://portal.example/projects/1",
}


async def test_unauthenticated_report_is_accepted(client: AsyncClient) -> None:
    """A crash can happen on the login screen, before there is a token."""
    response = await client.post("/v1/client-errors", json=_VALID_BODY)
    assert response.status_code == 204, response.text


async def test_authenticated_report_is_accepted_and_actor_id_is_logged(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from main import app as fastapi_app

    factory = getattr(fastapi_app.state, "session_factory", None)
    if factory is None:
        from core.db import _ensure_state

        factory = _ensure_state(fastapi_app)
    async with factory() as session:
        user = await make_user(session)

    logged: list[dict[str, Any]] = []

    class _Recorder:
        def warning(self, event: str, **fields: Any) -> None:
            logged.append({"event": event, **fields})

    import services.client_error_service as svc

    monkeypatch.setattr(svc, "log", _Recorder())

    response = await client.post(
        "/v1/client-errors", json=_VALID_BODY, headers=_bearer(user)
    )
    assert response.status_code == 204, response.text
    assert len(logged) == 1
    assert logged[0]["actor_user_id"] == str(user.id)


async def test_an_invalid_bearer_is_treated_as_anonymous_not_401(client: AsyncClient) -> None:
    """A crash report is not the place to enforce a valid session; the
    endpoint's whole point is to still work when auth is broken/expired."""
    response = await client.post(
        "/v1/client-errors",
        json=_VALID_BODY,
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert response.status_code == 204, response.text


async def test_empty_message_is_422(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/client-errors", json={**_VALID_BODY, "message": ""}
    )
    assert response.status_code == 422


async def test_unknown_field_is_422(client: AsyncClient) -> None:
    """`extra=\"forbid\"`: this is a public, unauthenticated intake point;
    silently accepting arbitrary extra fields is the wrong default here."""
    response = await client.post(
        "/v1/client-errors", json={**_VALID_BODY, "component_props": {"id": 1}}
    )
    assert response.status_code == 422


async def test_missing_url_is_422(client: AsyncClient) -> None:
    body = {k: v for k, v in _VALID_BODY.items() if k != "url"}
    response = await client.post("/v1/client-errors", json=body)
    assert response.status_code == 422


async def test_oversized_stack_is_422(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/client-errors", json={**_VALID_BODY, "stack": "x" * 8001}
    )
    assert response.status_code == 422


async def test_stack_and_component_stack_are_optional(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/client-errors",
        json={"message": "boom", "url": "https://portal.example/"},
    )
    assert response.status_code == 204, response.text


async def test_rate_limit_returns_429_on_the_31st_request_per_ip(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.ratelimit import limiter

    monkeypatch.setattr(limiter, "enabled", True)
    headers = {"X-Forwarded-For": "203.0.113.77"}

    for i in range(30):
        response = await client.post(
            "/v1/client-errors",
            json={**_VALID_BODY, "message": f"boom {i}"},
            headers=headers,
        )
        assert response.status_code != 429, f"request {i + 1} unexpectedly rate-limited"

    thirty_first = await client.post(
        "/v1/client-errors", json=_VALID_BODY, headers=headers
    )
    assert thirty_first.status_code == 429
    assert thirty_first.headers.get("Retry-After"), "missing Retry-After header"
