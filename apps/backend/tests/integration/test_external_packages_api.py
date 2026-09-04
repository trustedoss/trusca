# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Integration tests for the pre-adoption catalog lookup HTTP surface.

``GET /v1/external-packages`` and ``GET /v1/external-advisories/{id}``. The
deps.dev calls themselves are stubbed at the ``integrations.depsdev`` call
site (``api.v1.external_packages.lookup_package`` /
``lookup_advisory``, patched in the router's own namespace) -- the real
transport-level behaviour of those functions is covered by
``tests/unit/integrations/test_depsdev.py``. This file's job is the HTTP
contract: auth, validation, the feature flag, rate limiting, and the
response envelope, including that ``internal_projects`` only gets populated
(and only queried) when the deps.dev lookup actually found something.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from core.security import create_access_token
from integrations.depsdev import DepsDevUpstreamError, ExternalAdvisoryLookup, ExternalPackageLookup
from models import User
from tests._db_required import migrate_to_head
from tests._helpers import make_organization, make_team, make_user

PROBLEM_JSON = "application/problem+json"

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


@pytest.fixture
def app():
    from main import app as fastapi_app

    return fastapi_app


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


def _bearer_for(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(subject=str(user.id))}"}


async def _seed_viewer(client: AsyncClient) -> User:
    from core.db import _ensure_state
    from main import app as fastapi_app

    factory = getattr(fastapi_app.state, "session_factory", None) or _ensure_state(fastapi_app)
    async with factory() as session:
        org = await make_organization(session)
        await make_team(session, organization=org)
        return await make_user(session)


# ---------------------------------------------------------------------------
# GET /v1/external-packages
# ---------------------------------------------------------------------------


async def test_requires_auth(client: AsyncClient) -> None:
    r = await client.get("/v1/external-packages", params={"ecosystem": "npm", "name": "lodash"})
    assert r.status_code == 401


async def test_unknown_ecosystem_is_422(client: AsyncClient) -> None:
    user = await _seed_viewer(client)
    r = await client.get(
        "/v1/external-packages",
        params={"ecosystem": "bogus", "name": "lodash"},
        headers=_bearer_for(user),
    )
    assert r.status_code == 422, r.text
    assert r.headers["content-type"].startswith(PROBLEM_JSON)


async def test_empty_name_is_422(client: AsyncClient) -> None:
    user = await _seed_viewer(client)
    r = await client.get(
        "/v1/external-packages",
        params={"ecosystem": "npm", "name": ""},
        headers=_bearer_for(user),
    )
    assert r.status_code == 422, r.text


async def test_found_populates_internal_projects(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await _seed_viewer(client)

    async def _fake_lookup(ecosystem: str, name: str, **_kw) -> ExternalPackageLookup:
        return ExternalPackageLookup(
            ecosystem=ecosystem,
            name=name,
            found=True,
            version="4.18.1",
            purl="pkg:npm/lodash",
            licenses=["MIT"],
            advisory_count=0,
            advisory_ids=[],
            homepage_url="https://lodash.com/",
            source_repo_url=None,
        )

    calls: list[str] = []

    async def _fake_usage(session, *, actor, purl):
        calls.append(purl)
        return []

    monkeypatch.setattr("api.v1.external_packages.lookup_package", _fake_lookup)
    monkeypatch.setattr("api.v1.external_packages.internal_usage_by_purl", _fake_usage)

    r = await client.get(
        "/v1/external-packages",
        params={"ecosystem": "npm", "name": "lodash"},
        headers=_bearer_for(user),
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["found"] is True
    assert body["purl"] == "pkg:npm/lodash"
    assert body["internal_projects"] == []
    # Confirms the internal-usage query only ran because found=True.
    assert calls == ["pkg:npm/lodash"]


async def test_not_found_skips_internal_usage_query(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await _seed_viewer(client)

    async def _fake_lookup(ecosystem: str, name: str, **_kw) -> ExternalPackageLookup:
        return ExternalPackageLookup(ecosystem=ecosystem, name=name, found=False)

    called = False

    async def _fake_usage(session, *, actor, purl):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr("api.v1.external_packages.lookup_package", _fake_lookup)
    monkeypatch.setattr("api.v1.external_packages.internal_usage_by_purl", _fake_usage)

    r = await client.get(
        "/v1/external-packages",
        params={"ecosystem": "npm", "name": "does-not-exist"},
        headers=_bearer_for(user),
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["found"] is False
    assert body["internal_projects"] == []
    assert called is False


async def test_upstream_failure_is_502(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await _seed_viewer(client)

    async def _fake_lookup(ecosystem: str, name: str, **_kw) -> ExternalPackageLookup:
        raise DepsDevUpstreamError("deps.dev returned HTTP 500")

    monkeypatch.setattr("api.v1.external_packages.lookup_package", _fake_lookup)

    r = await client.get(
        "/v1/external-packages",
        params={"ecosystem": "npm", "name": "lodash"},
        headers=_bearer_for(user),
    )

    assert r.status_code == 502, r.text
    assert r.headers["content-type"].startswith(PROBLEM_JSON)


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError("connection refused"),
        httpx.TimeoutException("timed out"),
        httpx.InvalidURL("bad url"),
    ],
    ids=["connect-error", "timeout", "invalid-url"],
)
async def test_transport_level_errors_are_502_not_500(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, exc: Exception
) -> None:
    """The regression `oidc.py` already documents: a narrow except clause
    that misses a network-error subclass leaks a 500 traceback instead of
    the documented 502 (security review finding, 2026-09-02) -- this proves
    it end-to-end through the actual route, not just that lookup_package
    itself raises the exception."""
    user = await _seed_viewer(client)

    async def _fake_lookup(ecosystem: str, name: str, **_kw) -> ExternalPackageLookup:
        raise exc

    monkeypatch.setattr("api.v1.external_packages.lookup_package", _fake_lookup)

    r = await client.get(
        "/v1/external-packages",
        params={"ecosystem": "npm", "name": "lodash"},
        headers=_bearer_for(user),
    )

    assert r.status_code == 502, r.text
    assert r.headers["content-type"].startswith(PROBLEM_JSON)


async def test_disabled_deployment_is_404(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EXTERNAL_PACKAGE_LOOKUP_ENABLED", "false")
    user = await _seed_viewer(client)

    r = await client.get(
        "/v1/external-packages",
        params={"ecosystem": "npm", "name": "lodash"},
        headers=_bearer_for(user),
    )

    assert r.status_code == 404, r.text
    assert r.headers["content-type"].startswith(PROBLEM_JSON)


async def test_package_lookup_is_rate_limited(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EXTERNAL_PACKAGE_LOOKUP_RATE_LIMIT", "2/minute")
    user = await _seed_viewer(client)

    async def _fake_lookup(ecosystem: str, name: str, **_kw) -> ExternalPackageLookup:
        return ExternalPackageLookup(ecosystem=ecosystem, name=name, found=False)

    monkeypatch.setattr("api.v1.external_packages.lookup_package", _fake_lookup)

    headers = _bearer_for(user)
    params = {"ecosystem": "npm", "name": "lodash"}
    r1 = await client.get("/v1/external-packages", params=params, headers=headers)
    r2 = await client.get("/v1/external-packages", params=params, headers=headers)
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text

    r3 = await client.get("/v1/external-packages", params=params, headers=headers)
    assert r3.status_code == 429, r3.text
    assert r3.headers["content-type"].startswith(PROBLEM_JSON)
    assert r3.headers["Retry-After"] == "60"


# ---------------------------------------------------------------------------
# GET /v1/external-advisories/{advisory_id}
# ---------------------------------------------------------------------------


async def test_advisory_requires_auth(client: AsyncClient) -> None:
    r = await client.get("/v1/external-advisories/CVE-2021-23337")
    assert r.status_code == 401


async def test_advisory_found(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    user = await _seed_viewer(client)

    async def _fake_lookup(advisory_id: str, **_kw) -> ExternalAdvisoryLookup:
        return ExternalAdvisoryLookup(
            advisory_id=advisory_id,
            found=True,
            title="Example advisory",
            cvss3_score=6.5,
            cvss3_vector="CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:L/A:L",
            aliases=["CVE-2025-13465"],
        )

    monkeypatch.setattr("api.v1.external_packages.lookup_advisory", _fake_lookup)

    r = await client.get("/v1/external-advisories/GHSA-f23m-r3pf-42rh", headers=_bearer_for(user))

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["found"] is True
    assert body["title"] == "Example advisory"
    assert body["cvss3_score"] == 6.5


async def test_advisory_not_found(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    user = await _seed_viewer(client)

    async def _fake_lookup(advisory_id: str, **_kw) -> ExternalAdvisoryLookup:
        return ExternalAdvisoryLookup(advisory_id=advisory_id, found=False)

    monkeypatch.setattr("api.v1.external_packages.lookup_advisory", _fake_lookup)

    r = await client.get("/v1/external-advisories/CVE-9999-99999", headers=_bearer_for(user))

    assert r.status_code == 200, r.text
    assert r.json()["found"] is False


async def test_advisory_upstream_failure_is_502(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await _seed_viewer(client)

    async def _fake_lookup(advisory_id: str, **_kw) -> ExternalAdvisoryLookup:
        raise DepsDevUpstreamError("deps.dev returned HTTP 500")

    monkeypatch.setattr("api.v1.external_packages.lookup_advisory", _fake_lookup)

    r = await client.get("/v1/external-advisories/CVE-2021-23337", headers=_bearer_for(user))

    assert r.status_code == 502, r.text


async def test_advisory_disabled_deployment_is_404(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EXTERNAL_PACKAGE_LOOKUP_ENABLED", "false")
    user = await _seed_viewer(client)

    r = await client.get("/v1/external-advisories/CVE-2021-23337", headers=_bearer_for(user))

    assert r.status_code == 404, r.text
