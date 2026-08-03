"""
Integration test for the /health endpoint and core middleware/error contracts.

Covered:
- /health returns {"status": "ok"} with 200
- response carries an X-Request-ID header (auto-generated when client omits it)
- when the client sends X-Request-ID, the server echoes the same value back
- unhandled errors are returned as RFC 7807 problem+json with required fields
"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def app():
    from main import app as fastapi_app

    return fastapi_app


@pytest.fixture
async def client(app):
    # raise_app_exceptions=False so we can assert the RFC 7807 problem response
    # for unhandled exceptions: Starlette's ServerErrorMiddleware always re-raises
    # the original exception after sending the error response (a hook for test
    # clients), and ASGITransport propagates it back unless we opt out.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


async def test_health_returns_ok(client):
    response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    # v2.1 Track B (B5): the liveness probe surfaces the read-only demo flag so
    # the SPA can render its banner. Defaults to False outside the demo deploy.
    assert body["demo_read_only"] is False


async def test_health_emits_request_id_header(client):
    response = await client.get("/health")

    request_id = response.headers.get("X-Request-ID")
    assert request_id, "missing X-Request-ID response header"
    # auto-generated id should look like a UUID
    uuid.UUID(request_id)


async def test_health_propagates_inbound_request_id(client):
    inbound_id = "test-fixed-request-id-001"
    response = await client.get("/health", headers={"X-Request-ID": inbound_id})

    assert response.headers.get("X-Request-ID") == inbound_id


async def test_unhandled_exception_returns_problem_json(app, client):
    """An unexpected error must surface as application/problem+json (RFC 7807)."""

    @app.get("/__test/boom")
    async def _boom() -> None:  # pragma: no cover - exercised below
        raise RuntimeError("boom")

    response = await client.get("/__test/boom")

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    for key in ("type", "title", "status", "detail", "instance"):
        assert key in body, f"problem response missing required field: {key}"
    assert body["status"] == 500
    assert body["instance"] == "/__test/boom"


async def test_404_returns_problem_json(client):
    response = await client.get("/__definitely_missing__")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["status"] == 404
    assert body["instance"] == "/__definitely_missing__"
