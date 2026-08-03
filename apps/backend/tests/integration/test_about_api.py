# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""API tests for the About surface — ``GET /v1/about`` and its notice route.

These run without a database. The endpoints touch no tables; the only DB user in
the request path is ``get_current_user``, so the authenticated cases override it
and the unauthenticated case exercises the real dependency with a stub session.
That keeps the suite honest about what it covers — the auth gate and the response
contract — without making a license-notice test depend on alembic.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from core.security import get_current_user
from services.about_service import NOTICE_DOCUMENTS, notice_dir

pytestmark = pytest.mark.anyio


class _StubUser:
    """Minimal stand-in for the authenticated user the routes never read."""

    id = "00000000-0000-0000-0000-000000000001"
    email = "reader@example.com"
    is_active = True
    is_superuser = False


async def _stub_session() -> AsyncIterator[None]:
    """Stands in for get_db so no engine is created."""
    yield None


@pytest.fixture
def app() -> Any:
    from core.db import get_db
    from main import app as fastapi_app

    fastapi_app.dependency_overrides[get_db] = _stub_session
    yield fastapi_app
    fastapi_app.dependency_overrides.clear()


@pytest.fixture
def authed_app(app: Any) -> Any:
    app.dependency_overrides[get_current_user] = lambda: _StubUser()
    return app


@pytest.fixture
async def client(authed_app: Any) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=authed_app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest.fixture
async def anon_client(app: Any) -> AsyncIterator[AsyncClient]:
    """Real auth dependency — for the 401 cases."""
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


# --- auth gate ---------------------------------------------------------------
@pytest.mark.parametrize(
    "path",
    ["/v1/about", "/v1/about/notices/license"],
)
async def test_requires_authentication(anon_client: AsyncClient, path: str) -> None:
    """CLAUDE.md rule #12 — neither route is part of the public surface."""
    response = await anon_client.get(path)

    assert response.status_code == 401


# --- GET /v1/about -----------------------------------------------------------
async def test_about_returns_identity_and_documents(client: AsyncClient) -> None:
    response = await client.get("/v1/about")

    assert response.status_code == 200
    body = response.json()
    assert body["product"] == "TRUSCA"
    assert body["license_spdx_id"] == "Apache-2.0"
    assert body["copyright"] == "Copyright 2026 TRUSCA contributors"
    assert body["source_url"] == "https://github.com/trustedoss/trusca"
    assert body["version"]

    ids = [doc["id"] for doc in body["documents"]]
    assert ids == [doc.id for doc in NOTICE_DOCUMENTS]


async def test_about_document_list_carries_sizes(client: AsyncClient) -> None:
    """Sizes are real in a complete deployment — a null would mean a missing file."""
    response = await client.get("/v1/about")

    for doc in response.json()["documents"]:
        assert doc["size_bytes"] is not None, doc["id"]
        assert doc["size_bytes"] > 0
        assert doc["filename"]
        assert doc["title"]
        assert doc["description"]


async def test_about_is_role_free(client: AsyncClient) -> None:
    """A plain developer reads it — notices gated to admins would be pointless."""
    assert _StubUser.is_superuser is False

    response = await client.get("/v1/about")

    assert response.status_code == 200


# --- GET /v1/about/notices/{id} ----------------------------------------------
@pytest.mark.parametrize("document_id", [doc.id for doc in NOTICE_DOCUMENTS])
async def test_notice_is_served_as_plain_text(
    client: AsyncClient, document_id: str
) -> None:
    response = await client.get(f"/v1/about/notices/{document_id}")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "charset=utf-8" in response.headers["content-type"]
    assert response.text.strip()


async def test_notice_body_is_byte_identical_to_the_shipped_file(
    client: AsyncClient,
) -> None:
    """Verbatim: a reflowed or truncated license text is not the license."""
    base = notice_dir()
    assert base is not None

    response = await client.get("/v1/about/notices/license")

    assert response.text == (base / "LICENSE").read_text(encoding="utf-8")


async def test_third_party_notice_carries_the_upstream_attribution(
    client: AsyncClient,
) -> None:
    """The Apache-2.0 §4(d) credit has to reach the browser, not just the image."""
    response = await client.get("/v1/about/notices/third-party-notices")

    assert response.status_code == 200
    assert "SK Telecom Co., Ltd." in response.text
    assert "github.com/sktelecom/bomlens" in response.text
    assert "GPL-2.0 WITH Classpath-exception-2.0" in response.text


async def test_unknown_notice_is_a_problem_json_404(client: AsyncClient) -> None:
    response = await client.get("/v1/about/notices/not-a-document")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["status"] == 404
    assert body["title"]
    assert body["instance"] == "/v1/about/notices/not-a-document"


async def test_path_traversal_in_the_id_is_a_404_not_a_file_read(
    client: AsyncClient,
) -> None:
    """The id is looked up in a fixed catalogue, never joined onto a path.

    A traversal attempt cannot reach the filesystem: unknown ids stop at the
    dict lookup. Encoded separators are what a router would otherwise decode
    into a path segment.
    """
    for hostile in ("..%2F..%2Fetc%2Fpasswd", "%2Fetc%2Fpasswd", "license%00"):
        response = await client.get(f"/v1/about/notices/{hostile}")

        assert response.status_code == 404, hostile
        assert "root:" not in response.text
