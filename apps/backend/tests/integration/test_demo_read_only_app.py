"""
End-to-end (whole-app) test of the DEMO_READ_ONLY guard — v2.1 Track B (B5).

The unit tests (tests/unit/test_demo_read_only.py) drive a synthetic app to pin
the middleware logic. This integration test boots the REAL FastAPI app
(``main.app``) so we prove the guard is wired into the production middleware
stack and cannot be bypassed by any real router:

  * a mutating business route (POST /v1/projects) is rejected with an RFC 7807
    403 BEFORE auth even runs (the guard is outside the router),
  * a GET passes the guard (and gets the normal 401 because we send no token —
    proving the request reached the auth dependency, i.e. was NOT 403'd),
  * an allow-listed auth mutation (POST /auth/login) passes the guard (it gets a
    422/400 for the empty body, NOT a 403 — again proving it reached the route).

The flag is read at request time (CLAUDE.md rule #11), so we toggle it per-test
with monkeypatch on the already-imported app.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

DEMO_TYPE = "urn:trustedoss:problem:demo-read-only"


@pytest.fixture
def app():
    from main import app as fastapi_app

    return fastapi_app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


async def test_post_mutation_blocked_when_read_only(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEMO_READ_ONLY", "true")
    resp = await client.post("/v1/projects", json={"name": "x"})
    assert resp.status_code == 403
    assert resp.headers["content-type"].startswith("application/problem+json")
    body = resp.json()
    assert body["type"] == DEMO_TYPE
    assert body["demo_read_only"] is True
    for field in ("type", "title", "status", "detail", "instance"):
        assert field in body


async def test_get_passes_guard_when_read_only(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A GET is not blocked by the guard — it reaches the auth dependency and
    gets a 401 (no token), proving the guard let it through (not a 403)."""
    monkeypatch.setenv("DEMO_READ_ONLY", "true")
    resp = await client.get("/v1/projects")
    assert resp.status_code != 403


async def test_login_passes_guard_when_read_only(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /auth/login is on the allow-list — the guard must let it reach the
    route (which then 4xx's on the empty/invalid body, NOT 403)."""
    monkeypatch.setenv("DEMO_READ_ONLY", "true")
    resp = await client.post("/auth/login", json={})
    assert resp.status_code != 403


async def test_register_blocked_when_read_only(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Registration is NOT allow-listed — blocked even though it is an auth route."""
    monkeypatch.setenv("DEMO_READ_ONLY", "true")
    resp = await client.post(
        "/auth/register",
        json={"email": "a@b.com", "password": "x" * 12, "full_name": "x"},
    )
    assert resp.status_code == 403
    assert resp.json()["type"] == DEMO_TYPE


async def test_mutation_passes_when_flag_off(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the flag off, the guard is a no-op: the POST reaches the router and
    gets the normal 401 (no token), never a demo 403."""
    monkeypatch.delenv("DEMO_READ_ONLY", raising=False)
    resp = await client.post("/v1/projects", json={"name": "x"})
    assert resp.status_code != 403
    # Defensive: ensure we did not somehow emit the demo problem type.
    if resp.headers.get("content-type", "").startswith("application/problem+json"):
        assert resp.json().get("type") != DEMO_TYPE


# --------------------------------------------------------------------------- #
# feat/demo-sandbox-scan carve-out — end-to-end against the REAL router.
# --------------------------------------------------------------------------- #

_SANDBOX_PROJECT_ID = "00000000-0000-0000-0000-000000000000"


@pytest.mark.parametrize(
    "path",
    [
        f"/v1/projects/{_SANDBOX_PROJECT_ID}/scans",
        f"/v1/projects/{_SANDBOX_PROJECT_ID}/sbom-ingest",
    ],
)
async def test_sandbox_write_blocked_when_carveout_off(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    """DEMO_READ_ONLY on but the carve-out OFF (default): scan/ingest still 403."""
    monkeypatch.setenv("DEMO_READ_ONLY", "true")
    monkeypatch.delenv("DEMO_ALLOW_SANDBOX_SCANS", raising=False)
    resp = await client.post(path, json={})
    assert resp.status_code == 403
    assert resp.json()["type"] == DEMO_TYPE


@pytest.mark.parametrize(
    "path",
    [
        f"/v1/projects/{_SANDBOX_PROJECT_ID}/scans",
        f"/v1/projects/{_SANDBOX_PROJECT_ID}/sbom-ingest",
    ],
)
async def test_sandbox_write_passes_guard_but_not_auth_when_carveout_on(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    """Both flags on: the guard lets the anonymous POST through — but passing the
    middleware is NOT authentication. ``require_role("developer")`` then 401s the
    tokenless caller. So: NOT 403 (guard passed) AND 401 (auth enforced)."""
    monkeypatch.setenv("DEMO_READ_ONLY", "true")
    monkeypatch.setenv("DEMO_ALLOW_SANDBOX_SCANS", "true")
    resp = await client.post(path, json={})
    assert resp.status_code != 403
    assert resp.status_code == 401


async def test_sandbox_carveout_does_not_widen_other_writes(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even with the carve-out on, a non-sandbox mutation (POST /v1/projects)
    stays blocked at the guard — the widening is limited to the two families."""
    monkeypatch.setenv("DEMO_READ_ONLY", "true")
    monkeypatch.setenv("DEMO_ALLOW_SANDBOX_SCANS", "true")
    resp = await client.post("/v1/projects", json={"name": "x"})
    assert resp.status_code == 403
    assert resp.json()["type"] == DEMO_TYPE
