"""
Integration tests for the Scan HTTP surface — Phase 2 PR #7 / PR #8.

Endpoints:
  - POST /v1/projects/{project_id}/scans   Trigger a scan (PR #8: enqueues
                                            via tasks.enqueue_scan)
  - GET  /v1/scans/{scan_id}                Read one scan (IDOR-safe)
  - GET  /v1/projects/{project_id}/scans    List scans for a project

PR #7 contract: the trigger persists status='queued'.
PR #8 contract: the trigger also enqueues a Celery task and stores the
returned task id on `scan.celery_task_id`. We assert the wire shape
(ScanPublic with the `metadata` field, not `scan_metadata`) and the
partial-unique-index 409 contract.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from core.security import create_access_token
from models import User
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_scan,
    make_team,
    make_user,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
PROBLEM_JSON = "application/problem+json"

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip scans API tests")
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
            f"alembic upgrade head failed; scans API tests cannot run\n"
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
    role = "super_admin" if user.is_superuser else None
    token = create_access_token(subject=str(user.id), role=role)
    return {"Authorization": f"Bearer {token}"}


async def _factory(client: AsyncClient):
    """
    Return the AsyncSession factory backing the FastAPI app.

    httpx's ASGITransport does not run lifespan events by default, so
    `app.state.session_factory` may be unset. `core.db._ensure_state` builds
    it lazily and is idempotent.
    """
    app = client._transport.app  # type: ignore[attr-defined]
    factory = getattr(app.state, "session_factory", None)
    if factory is None:
        from core.db import _ensure_state

        factory = _ensure_state(app)
    return factory


async def _seed(
    client: AsyncClient,
    *,
    role: str = "developer",
    is_superuser: bool = False,
):
    """Seed organization + team + user (+ membership) + project. Returns ids."""
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session, is_superuser=is_superuser)
        if not is_superuser:
            await make_membership(session, user=user, team=team, role=role)
        project = await make_project(session, team=team)
    return team, user, project


async def _seed_scan(client: AsyncClient, *, project_id: uuid.UUID, status: str = "succeeded"):
    factory = await _factory(client)
    async with factory() as session:
        from sqlalchemy import select

        from models import Project

        project = (
            await session.execute(select(Project).where(Project.id == project_id))
        ).scalar_one()
        scan = await make_scan(session, project=project, status=status)
        return scan.id


# ---------------------------------------------------------------------------
# API-key auth on the CI surface (regression for the dogfood 401)
#
# scan-action authenticates with a tos_ API key, not a JWT. The trigger +
# status-poll endpoints were JWT-only (require_role) and 401'd the action;
# require_role_or_api_key now accepts either.
# ---------------------------------------------------------------------------


async def _issue_project_api_key(
    client: AsyncClient, *, user: User, project_id: uuid.UUID
) -> str:
    resp = await client.post(
        "/v1/api-keys",
        json={"name": "ci-dogfood", "scope": "project", "project_id": str(project_id)},
        headers=_bearer_for(user),
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["raw_key"])


async def test_scan_trigger_and_poll_accept_api_key(client) -> None:
    team, user, project = await _seed(client, role="developer")
    raw_key = await _issue_project_api_key(client, user=user, project_id=project.id)
    key_headers = {"Authorization": f"Bearer {raw_key}"}

    trigger = await client.post(
        f"/v1/projects/{project.id}/scans",
        json={"kind": "source"},
        headers=key_headers,
    )
    assert trigger.status_code == 202, trigger.text
    scan_id = trigger.json()["id"]

    # The action polls GET /v1/scans/{id} next — also API-key authed.
    poll = await client.get(f"/v1/scans/{scan_id}", headers=key_headers)
    assert poll.status_code == 200, poll.text
    assert poll.json()["id"] == scan_id


async def test_scan_trigger_still_rejects_anonymous(client) -> None:
    _team, _user, project = await _seed(client, role="developer")
    resp = await client.post(
        f"/v1/projects/{project.id}/scans", json={"kind": "source"}
    )
    assert resp.status_code == 401, resp.text


async def test_scan_trigger_rejects_bogus_api_key(client) -> None:
    _team, _user, project = await _seed(client, role="developer")
    resp = await client.post(
        f"/v1/projects/{project.id}/scans",
        json={"kind": "source"},
        headers={"Authorization": "Bearer tos_deadbeef_notarealsecret00000000000000000"},
    )
    assert resp.status_code == 401, resp.text


async def test_api_key_cannot_trigger_other_teams_project(client) -> None:
    _teamA, userA, projectA = await _seed(client, role="developer")
    _teamB, _userB, projectB = await _seed(client, role="developer")
    raw_key = await _issue_project_api_key(client, user=userA, project_id=projectA.id)
    key_headers = {"Authorization": f"Bearer {raw_key}"}

    # A key issued against team A's project must not reach team B's project.
    resp = await client.post(
        f"/v1/projects/{projectB.id}/scans",
        json={"kind": "source"},
        headers=key_headers,
    )
    assert resp.status_code in (403, 404), resp.text


async def test_project_scoped_key_cannot_reach_issuers_other_team(client) -> None:
    """Scope is an authorization boundary: a project-scoped key is bounded to
    the project's team even when the issuer ALSO belongs to other teams
    (security-reviewer Medium — scope was previously cosmetic)."""
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team_a = await make_team(session, organization=org)
        team_b = await make_team(session, organization=org)
        user = await make_user(session)
        await make_membership(session, user=user, team=team_a, role="developer")
        await make_membership(session, user=user, team=team_b, role="developer")
        project_a = await make_project(session, team=team_a)
        project_b = await make_project(session, team=team_b)

    # Key scoped to project_a (team_a); the issuer is also a member of team_b.
    raw_key = await _issue_project_api_key(client, user=user, project_id=project_a.id)
    key_headers = {"Authorization": f"Bearer {raw_key}"}

    # Must NOT reach team_b's project despite the issuer's team_b membership.
    cross = await client.post(
        f"/v1/projects/{project_b.id}/scans",
        json={"kind": "source"},
        headers=key_headers,
    )
    assert cross.status_code in (403, 404), cross.text

    # Sanity: it CAN trigger its own scoped project.
    own = await client.post(
        f"/v1/projects/{project_a.id}/scans",
        json={"kind": "source"},
        headers=key_headers,
    )
    assert own.status_code == 202, own.text


# ---------------------------------------------------------------------------
# POST /v1/projects/{id}/scans — trigger
# ---------------------------------------------------------------------------


async def test_developer_can_trigger_scan_in_own_team(client) -> None:
    team, user, project = await _seed(client, role="developer")
    headers = _bearer_for(user)

    response = await client.post(
        f"/v1/projects/{project.id}/scans",
        headers=headers,
        json={"kind": "source", "metadata": {"git_ref": "main"}},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["project_id"] == str(project.id)
    assert body["status"] == "queued"
    assert body["progress_percent"] == 0
    # PR #8: trigger_scan now enqueues via tasks.enqueue_scan and stores the
    # returned task id (UUID string from Celery).
    assert isinstance(body["celery_task_id"], str)
    assert len(body["celery_task_id"]) > 0
    # The schema must surface `metadata` (the API field) — not the ORM
    # attribute name `scan_metadata`. This is the smoke test for the
    # serialization_alias contract in schemas/scan.py::ScanPublic.
    assert body["metadata"] == {"git_ref": "main"}
    assert "scan_metadata" not in body


async def test_trigger_scan_default_kind_is_source(client) -> None:
    team, user, project = await _seed(client, role="developer")
    headers = _bearer_for(user)

    response = await client.post(
        f"/v1/projects/{project.id}/scans",
        headers=headers,
        json={},
    )
    assert response.status_code == 202, response.text
    assert response.json()["kind"] == "source"


async def test_concurrent_trigger_returns_409_problem(client) -> None:
    """Partial unique index gate: second trigger while one is queued = 409."""
    team, user, project = await _seed(client, role="developer")
    headers = _bearer_for(user)

    first = await client.post(
        f"/v1/projects/{project.id}/scans",
        headers=headers,
        json={"kind": "source"},
    )
    assert first.status_code == 202

    second = await client.post(
        f"/v1/projects/{project.id}/scans",
        headers=headers,
        json={"kind": "source"},
    )
    assert second.status_code == 409
    assert second.headers["content-type"].startswith(PROBLEM_JSON)
    body = second.json()
    assert body["title"] == "Scan Already In Progress"
    assert body["status"] == 409
    # P1 #10 — machine-checkable extension so the SPA can render a targeted
    # notice and link to the in-progress drawer without parsing the detail.
    assert body.get("scan_already_in_progress") is True


async def test_trigger_scan_other_team_returns_403(client) -> None:
    _, target_user, target_project = await _seed(client, role="developer")
    _, outsider, _ = await _seed(client, role="developer")
    headers = _bearer_for(outsider)

    response = await client.post(
        f"/v1/projects/{target_project.id}/scans",
        headers=headers,
        json={"kind": "source"},
    )
    assert response.status_code == 403
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_trigger_scan_unknown_project_returns_404(client) -> None:
    _, admin, _ = await _seed(client, role="developer", is_superuser=True)
    headers = _bearer_for(admin)

    response = await client.post(
        f"/v1/projects/{uuid.uuid4()}/scans",
        headers=headers,
        json={"kind": "source"},
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_trigger_scan_super_admin_bypasses_team_check(client) -> None:
    _, _, target_project = await _seed(client, role="developer")
    _, admin, _ = await _seed(client, role="developer", is_superuser=True)
    headers = _bearer_for(admin)

    response = await client.post(
        f"/v1/projects/{target_project.id}/scans",
        headers=headers,
        json={"kind": "container"},
    )
    assert response.status_code == 202, response.text
    assert response.json()["kind"] == "container"


async def test_trigger_scan_without_auth_returns_401(client) -> None:
    response = await client.post(
        f"/v1/projects/{uuid.uuid4()}/scans",
        json={"kind": "source"},
    )
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_inactive_user_cannot_trigger_scan(client) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session, is_active=False)
        await make_membership(session, user=user, team=team, role="developer")
        project = await make_project(session, team=team)
    headers = _bearer_for(user)

    response = await client.post(
        f"/v1/projects/{project.id}/scans",
        headers=headers,
        json={"kind": "source"},
    )
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# POST /v1/projects/{id}/scans — B1 abuse controls (rate limit + team cap)
# ---------------------------------------------------------------------------


async def _seed_extra_project(client: AsyncClient, *, team_id: uuid.UUID):
    """Create another project in an existing team and return it."""
    factory = await _factory(client)
    async with factory() as session:
        from sqlalchemy import select

        from models import Team

        team = (
            await session.execute(select(Team).where(Team.id == team_id))
        ).scalar_one()
        project = await make_project(session, team=team)
        return project


async def test_team_concurrency_cap_returns_429_problem_with_extensions(
    client, monkeypatch
) -> None:
    """B1: team at its concurrent-scan cap → 429 + RFC 7807 + Retry-After.

    The cap counts queued+running across the team's projects. We set the cap
    to 2 and fire triggers on three distinct projects in the same team; the
    third is blocked.
    """
    monkeypatch.setenv("SCAN_CONCURRENCY_CAP_PER_TEAM", "2")
    # Keep the per-user rate limit out of the way for this test.
    monkeypatch.setenv("SCAN_TRIGGER_RATE_LIMIT", "100/minute")

    team, user, project1 = await _seed(client, role="developer")
    project2 = await _seed_extra_project(client, team_id=team.id)
    project3 = await _seed_extra_project(client, team_id=team.id)
    headers = _bearer_for(user)

    r1 = await client.post(
        f"/v1/projects/{project1.id}/scans", headers=headers, json={"kind": "source"}
    )
    r2 = await client.post(
        f"/v1/projects/{project2.id}/scans", headers=headers, json={"kind": "source"}
    )
    assert r1.status_code == 202, r1.text
    assert r2.status_code == 202, r2.text

    r3 = await client.post(
        f"/v1/projects/{project3.id}/scans", headers=headers, json={"kind": "source"}
    )
    assert r3.status_code == 429, r3.text
    assert r3.headers["content-type"].startswith(PROBLEM_JSON)
    assert r3.headers["Retry-After"] == "30"
    body = r3.json()
    assert body["type"] == "urn:trustedoss:problem:concurrent_scan_limit"
    assert body["status"] == 429
    assert body["title"] == "Concurrent Scan Limit Exceeded"
    assert body["limit"] == 2
    assert body["instance"] == f"/v1/projects/{project3.id}/scans"
    # M1: the live per-team active-scan count must not leak into the body.
    assert "running_scans" not in body


async def test_team_concurrency_cap_disabled_when_zero(client, monkeypatch) -> None:
    """Cap 0 = unlimited: many concurrent triggers all succeed."""
    monkeypatch.setenv("SCAN_CONCURRENCY_CAP_PER_TEAM", "0")
    monkeypatch.setenv("SCAN_TRIGGER_RATE_LIMIT", "100/minute")

    team, user, project1 = await _seed(client, role="developer")
    project2 = await _seed_extra_project(client, team_id=team.id)
    project3 = await _seed_extra_project(client, team_id=team.id)
    headers = _bearer_for(user)

    for project in (project1, project2, project3):
        r = await client.post(
            f"/v1/projects/{project.id}/scans",
            headers=headers,
            json={"kind": "source"},
        )
        assert r.status_code == 202, r.text


async def test_scan_trigger_per_user_rate_limit_returns_429(
    client, monkeypatch
) -> None:
    """B1: more triggers than the per-user budget → 429 + Retry-After.

    We set a tiny budget (2/minute) and a high concurrency cap so the rate
    limiter is the control under test. Each trigger is on a distinct project
    in the same team so the per-project unique index (409) does not fire
    first. The limit string is read per request (callable), so the env set
    here takes effect immediately.
    """
    monkeypatch.setenv("SCAN_TRIGGER_RATE_LIMIT", "2/minute")
    monkeypatch.setenv("SCAN_CONCURRENCY_CAP_PER_TEAM", "0")  # disable cap

    team, user, project1 = await _seed(client, role="developer")
    project2 = await _seed_extra_project(client, team_id=team.id)
    project3 = await _seed_extra_project(client, team_id=team.id)
    headers = _bearer_for(user)

    r1 = await client.post(
        f"/v1/projects/{project1.id}/scans", headers=headers, json={"kind": "source"}
    )
    r2 = await client.post(
        f"/v1/projects/{project2.id}/scans", headers=headers, json={"kind": "source"}
    )
    assert r1.status_code == 202, r1.text
    assert r2.status_code == 202, r2.text

    # Third trigger within the same minute exceeds the 2/minute user budget.
    r3 = await client.post(
        f"/v1/projects/{project3.id}/scans", headers=headers, json={"kind": "source"}
    )
    assert r3.status_code == 429, r3.text
    assert r3.headers["content-type"].startswith(PROBLEM_JSON)
    assert "Retry-After" in r3.headers
    assert r3.json()["status"] == 429


async def test_scan_trigger_rate_limit_is_per_user_not_shared(
    client, monkeypatch
) -> None:
    """A second user in the same team gets a fresh budget (per-user keying).

    User A exhausts a 1/minute budget; user B (same team, same egress IP in
    the test transport) must still be able to trigger — proving the limiter
    keys by token sub, not by IP.
    """
    monkeypatch.setenv("SCAN_TRIGGER_RATE_LIMIT", "1/minute")
    monkeypatch.setenv("SCAN_CONCURRENCY_CAP_PER_TEAM", "0")

    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user_a = await make_user(session)
        user_b = await make_user(session)
        await make_membership(session, user=user_a, team=team, role="developer")
        await make_membership(session, user=user_b, team=team, role="developer")
        project_a = await make_project(session, team=team)
        project_b = await make_project(session, team=team)
        a_id, b_id = user_a.id, user_b.id
        pa_id, pb_id = project_a.id, project_b.id

    headers_a = {"Authorization": f"Bearer {create_access_token(subject=str(a_id))}"}
    headers_b = {"Authorization": f"Bearer {create_access_token(subject=str(b_id))}"}

    # User A: first allowed, second blocked (1/minute budget).
    ra1 = await client.post(
        f"/v1/projects/{pa_id}/scans", headers=headers_a, json={"kind": "source"}
    )
    assert ra1.status_code == 202, ra1.text
    ra2 = await client.post(
        f"/v1/projects/{pa_id}/scans", headers=headers_a, json={"kind": "source"}
    )
    # 429 (rate) — would otherwise be 409 (active scan); rate check runs first.
    assert ra2.status_code == 429, ra2.text

    # User B has a separate bucket → still allowed.
    rb1 = await client.post(
        f"/v1/projects/{pb_id}/scans", headers=headers_b, json={"kind": "source"}
    )
    assert rb1.status_code == 202, rb1.text


# ---------------------------------------------------------------------------
# GET /v1/scans/{scan_id}
# ---------------------------------------------------------------------------


async def test_get_scan_same_team_returns_200(client) -> None:
    team, user, project = await _seed(client, role="developer")
    scan_id = await _seed_scan(client, project_id=project.id)
    headers = _bearer_for(user)

    response = await client.get(f"/v1/scans/{scan_id}", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == str(scan_id)
    assert body["project_id"] == str(project.id)
    # Wire field uses `metadata` not `scan_metadata`
    assert "metadata" in body
    assert "scan_metadata" not in body


async def test_get_scan_other_team_returns_403(client) -> None:
    _, _, target_project = await _seed(client, role="developer")
    scan_id = await _seed_scan(client, project_id=target_project.id)
    _, outsider, _ = await _seed(client, role="developer")
    headers = _bearer_for(outsider)

    response = await client.get(f"/v1/scans/{scan_id}", headers=headers)
    assert response.status_code == 403
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_get_scan_super_admin_bypasses_team_check(client) -> None:
    _, _, target_project = await _seed(client, role="developer")
    scan_id = await _seed_scan(client, project_id=target_project.id)
    _, admin, _ = await _seed(client, role="developer", is_superuser=True)
    headers = _bearer_for(admin)

    response = await client.get(f"/v1/scans/{scan_id}", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["id"] == str(scan_id)


async def test_get_scan_unknown_id_returns_404(client) -> None:
    _, admin, _ = await _seed(client, role="developer", is_superuser=True)
    headers = _bearer_for(admin)

    response = await client.get(f"/v1/scans/{uuid.uuid4()}", headers=headers)
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_get_scan_without_auth_returns_401(client) -> None:
    response = await client.get(f"/v1/scans/{uuid.uuid4()}")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# GET /v1/projects/{id}/scans
# ---------------------------------------------------------------------------


async def test_list_scans_for_project_returns_paginated_list(client) -> None:
    team, user, project = await _seed(client, role="developer")
    # Seed three scans (terminal status so the partial unique index doesn't
    # block the inserts).
    for _ in range(3):
        await _seed_scan(client, project_id=project.id, status="succeeded")

    headers = _bearer_for(user)
    response = await client.get(
        f"/v1/projects/{project.id}/scans",
        headers=headers,
        params={"page": 1, "size": 2},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["page"] == 1
    assert body["size"] == 2
    assert body["total"] >= 3
    assert len(body["items"]) == 2
    for item in body["items"]:
        assert "metadata" in item
        assert "scan_metadata" not in item


async def test_list_scans_for_project_other_team_returns_403(client) -> None:
    _, _, target_project = await _seed(client, role="developer")
    _, outsider, _ = await _seed(client, role="developer")
    headers = _bearer_for(outsider)

    response = await client.get(f"/v1/projects/{target_project.id}/scans", headers=headers)
    assert response.status_code == 403
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
