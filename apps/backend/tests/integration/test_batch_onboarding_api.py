# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""``POST /v1/projects:batch`` and ``POST /v1/scans:batch``.

Against a real database, because the two properties worth testing are both
transactional and neither is visible to a mocked session: that a failing row
does not undo the rows before it, and that the batch is actually committed.

The second is the defect this codebase has already shipped twice. Neither
``get_db`` nor ``sync_session_scope`` commits on exit, and releasing a SAVEPOINT
is not committing the enclosing transaction, so a batch can report "300 created"
having written nothing. A test that only reads the response body cannot see it;
these re-read through a separate session.
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
    make_team,
    make_user,
    unique_suffix,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set: skip batch onboarding API tests")
    return url


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    _require_database_url()
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:
        pytest.skip(f"alembic upgrade head failed: {result.stderr}")


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


async def _factory(client: AsyncClient):
    app = client._transport.app  # type: ignore[attr-defined]
    factory = getattr(app.state, "session_factory", None)
    if factory is None:
        from core.db import _ensure_state

        factory = _ensure_state(app)
    return factory


async def _seed_team_and_user(client: AsyncClient, *, role: str = "developer"):
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session)
        await make_membership(session, user=user, team=team, role=role)
    return team, user


def _project_spec(team_id: uuid.UUID, label: str) -> dict:
    suffix = unique_suffix()
    return {
        "team_id": str(team_id),
        "name": f"{label} {suffix}",
        "slug": f"{label}-{suffix}",
        "git_url": f"https://github.com/example/{label}-{suffix}.git",
    }


# ---------------------------------------------------------------------------
# The happy path, and that it reached the database
# ---------------------------------------------------------------------------


async def test_a_batch_creates_every_project_and_commits_them(
    client: AsyncClient,
) -> None:
    """The committed rows are read back through a different session.

    Asserting on the response body alone would pass against a batch that
    reported success and wrote nothing, which is exactly how two maintenance
    tasks in this repository shipped.
    """
    team, user = await _seed_team_and_user(client)
    specs = [_project_spec(team.id, "repo") for _ in range(3)]

    response = await client.post(
        "/v1/projects:batch",
        json={"projects": specs},
        headers=_bearer_for(user),
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["all_succeeded"] is True
    assert body["created"] == 3
    assert body["failed"] == 0
    assert [row["status"] for row in body["rows"]] == ["created"] * 3

    from sqlalchemy import select

    from models import Project

    factory = await _factory(client)
    async with factory() as session:
        slugs = {
            row.slug
            for row in (
                await session.execute(select(Project).where(Project.team_id == team.id))
            ).scalars()
        }
    assert {spec["slug"] for spec in specs} <= slugs


# ---------------------------------------------------------------------------
# Partial failure: the shape, and the transaction boundary
# ---------------------------------------------------------------------------


async def test_a_failing_row_does_not_undo_the_rows_around_it(
    client: AsyncClient,
) -> None:
    """One bad row costs that row, not the batch.

    Rolling the whole batch back on the first failure would mean a single
    repository the caller cannot reach costs them the other 299, which is the
    opposite of why a batch exists.
    """
    team, user = await _seed_team_and_user(client)
    other_team, _ = await _seed_team_and_user(client)

    first = _project_spec(team.id, "before")
    # A team the actor is not a member of: forbidden, and in the middle.
    middle = _project_spec(other_team.id, "denied")
    last = _project_spec(team.id, "after")

    response = await client.post(
        "/v1/projects:batch",
        json={"projects": [first, middle, last]},
        headers=_bearer_for(user),
    )

    assert response.status_code == 207, response.text
    body = response.json()
    assert body["all_succeeded"] is False
    assert body["created"] == 2
    assert body["failed"] == 1
    assert body["failed_by_status"] == {"forbidden": 1}
    assert [row["status"] for row in body["rows"]] == [
        "created",
        "forbidden",
        "created",
    ]

    from sqlalchemy import select

    from models import Project

    factory = await _factory(client)
    async with factory() as session:
        slugs = {
            row.slug
            for row in (
                await session.execute(
                    select(Project).where(
                        Project.slug.in_([first["slug"], middle["slug"], last["slug"]])
                    )
                )
            ).scalars()
        }
    assert first["slug"] in slugs, "the row before the failure was rolled back"
    assert last["slug"] in slugs, "the row after the failure never landed"
    assert middle["slug"] not in slugs, "the failing row was written anyway"


async def test_partial_failure_is_visible_without_reading_the_rows(
    client: AsyncClient,
) -> None:
    """A caller that checks only the status line must not read it as success.

    200 would be treated as success by most client libraries, which is how a
    batch that half-failed gets recorded as having worked.
    """
    team, user = await _seed_team_and_user(client)
    other_team, _ = await _seed_team_and_user(client)

    response = await client.post(
        "/v1/projects:batch",
        json={
            "projects": [
                _project_spec(team.id, "ok"),
                _project_spec(other_team.id, "denied"),
            ]
        },
        headers=_bearer_for(user),
    )

    assert response.status_code == 207
    assert response.json()["all_succeeded"] is False


# ---------------------------------------------------------------------------
# Idempotence
# ---------------------------------------------------------------------------


async def test_re_running_a_batch_reports_success_and_changes_nothing(
    client: AsyncClient,
) -> None:
    """Re-running to finish an interrupted onboarding is the normal path.

    Counting an existing project as a failure would make every re-run report
    failure and make `all_succeeded` useless as a signal.
    """
    team, user = await _seed_team_and_user(client)
    specs = [_project_spec(team.id, "repeat") for _ in range(2)]
    headers = _bearer_for(user)

    first = await client.post("/v1/projects:batch", json={"projects": specs}, headers=headers)
    assert first.status_code == 201, first.text
    assert first.json()["created"] == 2

    second = await client.post("/v1/projects:batch", json={"projects": specs}, headers=headers)

    assert second.status_code == 201, second.text
    body = second.json()
    assert body["all_succeeded"] is True
    assert body["created"] == 0, "a re-run created something it should not have"
    assert body["already_existed"] == 2
    assert body["failed"] == 0
    # The ids are handed back, so a caller that lost them can recover them.
    assert all(row["project_id"] for row in body["rows"])


# ---------------------------------------------------------------------------
# Scan batch
# ---------------------------------------------------------------------------


async def test_scan_batch_starts_a_scan_per_project(client: AsyncClient) -> None:
    team, user = await _seed_team_and_user(client)
    factory = await _factory(client)
    async with factory() as session:
        projects = [await make_project(session, team=team) for _ in range(2)]
        project_ids = [str(p.id) for p in projects]

    response = await client.post(
        "/v1/scans:batch",
        json={"project_ids": project_ids},
        headers=_bearer_for(user),
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["all_succeeded"] is True
    assert body["created"] == 2
    assert all(row["scan_id"] for row in body["rows"])

    from sqlalchemy import select

    from models import Scan

    async with factory() as session:
        scanned = {
            str(row.project_id)
            for row in (
                await session.execute(
                    select(Scan).where(Scan.project_id.in_([p.id for p in projects]))
                )
            ).scalars()
        }
    assert scanned == set(project_ids), "scans were reported but not committed"


async def test_a_project_already_scanning_is_not_a_failure(
    client: AsyncClient,
) -> None:
    """Same reasoning as an existing project: that is the state asked for."""
    team, user = await _seed_team_and_user(client)
    factory = await _factory(client)
    async with factory() as session:
        project = await make_project(session, team=team)
        project_id = str(project.id)
    headers = _bearer_for(user)

    first = await client.post(
        "/v1/scans:batch", json={"project_ids": [project_id]}, headers=headers
    )
    assert first.status_code == 201, first.text

    second = await client.post(
        "/v1/scans:batch", json={"project_ids": [project_id]}, headers=headers
    )

    assert second.status_code == 201, second.text
    body = second.json()
    assert body["all_succeeded"] is True
    assert body["already_existed"] == 1
    assert body["rows"][0]["status"] == "already_exists"
