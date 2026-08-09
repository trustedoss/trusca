"""
End-to-end (real app + real Postgres) tests for the feat/demo-sandbox-scan
service-layer write guards (security review finding / H-2).

These prove the guards are WIRED into the live router, not just the pure helpers:

  * H-1 — with the carve-out on, a container scan against the sandbox is 422'd
    (its ``image_ref`` has no SSRF guard); a source scan passes.
  * H-2 — with the carve-out on, a scan / SBOM-ingest against a NON-sandbox
    project is 403'd even though the demo account can reach the owning team.
  * No regression — with the carve-out off, a non-sandbox source scan and a
    container scan both behave exactly as before (202).

Security assertions are parametrized over (flag state × project × kind) per
CLAUDE.md §2 hardening rule 1. The demo account authenticates with a directly
minted JWT (the middleware carve-out lets the scan/ingest POST through; the
guards run inside the service).
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from core.security import create_access_token
from models import User
from services.scan_service import DEMO_SANDBOX_PROJECT_NAME
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_team,
    make_user,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
PROBLEM_JSON = "application/problem+json"
pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip demo sandbox guard tests")
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
            "alembic upgrade head failed; demo sandbox guard tests cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


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
    token = create_access_token(subject=str(user.id), role=None)
    return {"Authorization": f"Bearer {token}"}


def _scan_body(kind: str) -> dict[str, object]:
    """A trigger body that passes schema validation for *kind*.

    These tests are about the demo-sandbox guards, which live in the service
    layer. A container scan needs ``metadata.image_ref`` to get past the schema
    at all, and a body rejected there would never reach the guard under test —
    the 422 would look the same while proving nothing.
    """
    if kind == "container":
        return {"kind": kind, "metadata": {"image_ref": "ghcr.io/acme/api:1.4.0"}}
    return {"kind": kind}


async def _factory(client: AsyncClient):
    app = client._transport.app  # type: ignore[attr-defined]
    factory = getattr(app.state, "session_factory", None)
    if factory is None:
        from core.db import _ensure_state

        factory = _ensure_state(app)
    return factory


async def _seed_sandbox_and_other(client: AsyncClient):
    """One developer in a team owning BOTH a "Demo Sandbox" and another project."""
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session)
        await make_membership(session, user=user, team=team, role="developer")
        sandbox = await make_project(
            session, team=team, name=DEMO_SANDBOX_PROJECT_NAME
        )
        other = await make_project(session, team=team, name="portal-api")
    return user, sandbox, other


@pytest.fixture
def _demo_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEMO_READ_ONLY", "true")
    monkeypatch.setenv("DEMO_ALLOW_SANDBOX_SCANS", "true")


@pytest.fixture
def _demo_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEMO_READ_ONLY", raising=False)
    monkeypatch.delenv("DEMO_ALLOW_SANDBOX_SCANS", raising=False)


# --------------------------------------------------------------------------- #
# Carve-out ON — the narrow guards fire.
# --------------------------------------------------------------------------- #


async def test_sandbox_source_scan_allowed(client, _demo_on) -> None:
    user, sandbox, _other = await _seed_sandbox_and_other(client)
    resp = await client.post(
        f"/v1/projects/{sandbox.id}/scans",
        json={"kind": "source"},
        headers=_bearer_for(user),
    )
    assert resp.status_code == 202, resp.text


async def test_sandbox_container_scan_blocked_h1(client, _demo_on) -> None:
    """H-1: container kind is 422'd (SSRF-prone image_ref) even on the sandbox.

    The body carries a valid ``image_ref`` so the request clears schema
    validation — otherwise the 422 under test would be indistinguishable from
    the schema's own "a container scan needs an image" rejection, and this
    would stop covering the sandbox guard it is named for.
    """
    user, sandbox, _other = await _seed_sandbox_and_other(client)
    resp = await client.post(
        f"/v1/projects/{sandbox.id}/scans",
        json=_scan_body("container"),
        headers=_bearer_for(user),
    )
    assert resp.status_code == 422, resp.text
    assert resp.headers["content-type"].startswith(PROBLEM_JSON)


@pytest.mark.parametrize("kind", ["source", "container"])
async def test_non_sandbox_scan_blocked_h2(client, _demo_on, kind: str) -> None:
    """H-2: a non-sandbox project is 403'd first — for BOTH kinds — because the
    project guard (403) precedes the kind guard (422) per hardening rule 1."""
    user, _sandbox, other = await _seed_sandbox_and_other(client)
    resp = await client.post(
        f"/v1/projects/{other.id}/scans",
        json=_scan_body(kind),
        headers=_bearer_for(user),
    )
    assert resp.status_code == 403, resp.text
    assert resp.headers["content-type"].startswith(PROBLEM_JSON)


async def test_non_sandbox_sbom_ingest_blocked_h2(client, _demo_on) -> None:
    """H-2 covers the SBOM-ingest path too (shared prepare_scan_target guard).

    The 403 fires inside ``prepare_scan_target`` — which the ingest service runs
    BEFORE body validation — so a valid multipart SBOM against a non-sandbox
    project is rejected on the authz axis (hardening rule 1)."""
    user, _sandbox, other = await _seed_sandbox_and_other(client)
    valid_sbom = (
        b'{"bomFormat": "CycloneDX", "specVersion": "1.5", "version": 1, '
        b'"components": [{"type": "library", "name": "demo", "version": "1.0.0"}]}'
    )
    resp = await client.post(
        f"/v1/projects/{other.id}/sbom-ingest",
        files={"sbom": ("bom.cdx.json", valid_sbom, "application/json")},
        headers=_bearer_for(user),
    )
    assert resp.status_code == 403, resp.text


# --------------------------------------------------------------------------- #
# Carve-out OFF — no regression.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("kind", ["source", "container"])
async def test_non_sandbox_scan_allowed_when_carveout_off(
    client, _demo_off, kind: str
) -> None:
    """Flag off: a non-sandbox project accepts source AND container scans as
    before — the demo guards are inert."""
    user, _sandbox, other = await _seed_sandbox_and_other(client)
    resp = await client.post(
        f"/v1/projects/{other.id}/scans",
        json=_scan_body(kind),
        headers=_bearer_for(user),
    )
    assert resp.status_code == 202, resp.text
