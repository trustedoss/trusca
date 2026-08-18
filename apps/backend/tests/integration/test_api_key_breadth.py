"""
What an API key may do, as distinct from what it may reach.

The test this file exists for is the first one: a key issued before breadth
existed has to keep working. Backfilling those rows as read-only would stop
whatever pipeline is holding them the next time it ran, with nothing in the
portal to explain it, and a suite that only exercised newly issued keys would
pass while it happened.

The rest is the matrix the plan asks for: every surface a key can reach,
refused or allowed according to whether it changes anything.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from core.security import create_access_token
from models import User
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
        pytest.skip("DATABASE_URL not set: skip API key breadth tests")
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
        pytest.skip(f"alembic upgrade head failed\n{result.stdout}\n{result.stderr}")


@pytest.fixture
def app():
    from main import app as fastapi_app

    return fastapi_app


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


async def _factory(client: AsyncClient):
    app = client._transport.app  # type: ignore[attr-defined]
    factory = getattr(app.state, "session_factory", None)
    if factory is None:
        from core.db import _ensure_state

        factory = _ensure_state(app)
    return factory


def _bearer_for(user: User) -> dict[str, str]:
    role = "super_admin" if user.is_superuser else None
    return {"Authorization": f"Bearer {create_access_token(subject=str(user.id), role=role)}"}


async def _seed(client: AsyncClient):
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session)
        await make_membership(session, user=user, team=team, role="developer")
        project = await make_project(session, team=team)
        return team, user, project


async def _issue(
    client: AsyncClient,
    *,
    user: User,
    project_id: uuid.UUID,
    breadth: str | None,
) -> tuple[str, str]:
    """Issue a key through the API. Returns (raw key, id)."""
    body: dict[str, object] = {
        "name": "ci",
        "scope": "project",
        "project_id": str(project_id),
    }
    if breadth is not None:
        body["permission_breadth"] = breadth
    response = await client.post("/v1/api-keys", json=body, headers=_bearer_for(user))
    assert response.status_code == 201, response.text
    return str(response.json()["raw_key"]), str(response.json()["id"])


async def _pretend_key_predates_the_feature(
    client: AsyncClient, api_key_id: str
) -> None:
    """Put a row into the state the migration's backfill left it in.

    The column default was dropped in the same revision that added it, so
    there is no DEFAULT to reach for: what the backfill produced is the
    literal value, which is what this writes.
    """
    factory = await _factory(client)
    async with factory() as session:
        await session.execute(
            text(
                "UPDATE api_keys SET permission_breadth = 'read_write' "
                "WHERE id = :id"
            ),
            {"id": uuid.UUID(api_key_id)},
        )
        await session.commit()


# ---------------------------------------------------------------------------
# The one that must not break
# ---------------------------------------------------------------------------


async def test_a_key_from_before_this_feature_can_still_write(client) -> None:
    """The operational accident this unit had to avoid.

    Every key issued so far can trigger scans, because there has never been a
    way to issue one that cannot. If the migration's default had been
    read-only, the first CI run after the upgrade would have failed on a key
    that had worked for months.
    """
    _team, user, project = await _seed(client)
    raw_key, key_id = await _issue(
        client, user=user, project_id=project.id, breadth="read_write"
    )
    await _pretend_key_predates_the_feature(client, key_id)

    triggered = await client.post(
        f"/v1/projects/{project.id}/scans",
        json={"kind": "source"},
        headers={"Authorization": f"Bearer {raw_key}"},
    )

    assert triggered.status_code == 202, triggered.text


async def test_the_column_carries_no_default_after_the_backfill(client) -> None:
    """The backfill is done, so the permissive value must stop being implicit.

    Left in place, the default would let any later insert that forgets the
    column mint a key that can change things: a data migration, a fixture, an
    operator's manual INSERT during an incident. Dropping it turns that into a
    loud failure instead.
    """
    factory = await _factory(client)
    async with factory() as session:
        default = (
            await session.execute(
                text(
                    "SELECT column_default FROM information_schema.columns "
                    "WHERE table_name = 'api_keys' "
                    "AND column_name = 'permission_breadth'"
                )
            )
        ).scalar_one()
        not_null = (
            await session.execute(
                text(
                    "SELECT is_nullable FROM information_schema.columns "
                    "WHERE table_name = 'api_keys' "
                    "AND column_name = 'permission_breadth'"
                )
            )
        ).scalar_one()

    assert default is None
    # Still NOT NULL, so the insert fails rather than storing a null breadth
    # that the auth path would then have to interpret.
    assert not_null == "NO"


async def test_an_insert_that_forgets_the_breadth_fails(client) -> None:
    """The point of dropping the default: silence becomes a rejection."""
    from sqlalchemy.exc import IntegrityError

    _team, user, project = await _seed(client)
    factory = await _factory(client)
    async with factory() as session:
        with pytest.raises(IntegrityError):
            await session.execute(
                text(
                    "INSERT INTO api_keys "
                    "(key_prefix, key_hash, name, scope, team_id, "
                    " created_by_user_id) "
                    "VALUES ('tos_nodefault', 'x', 'n', 'team', :team, :user)"
                ),
                {"team": project.team_id, "user": user.id},
            )
        await session.rollback()


# ---------------------------------------------------------------------------
# What a new key gets
# ---------------------------------------------------------------------------


async def test_a_new_key_is_read_only_unless_asked_otherwise(client) -> None:
    """The default that makes the feature worth having."""
    _team, user, project = await _seed(client)

    response = await client.post(
        "/v1/api-keys",
        json={"name": "ci", "scope": "project", "project_id": str(project.id)},
        headers=_bearer_for(user),
    )

    assert response.status_code == 201, response.text
    assert response.json()["permission_breadth"] == "read_only"


async def test_a_read_write_key_can_be_asked_for(client) -> None:
    _team, user, project = await _seed(client)

    response = await client.post(
        "/v1/api-keys",
        json={
            "name": "ci",
            "scope": "project",
            "project_id": str(project.id),
            "permission_breadth": "read_write",
        },
        headers=_bearer_for(user),
    )

    assert response.status_code == 201, response.text
    assert response.json()["permission_breadth"] == "read_write"


async def test_the_list_says_what_each_key_may_do(client) -> None:
    """Otherwise nobody can tell which of their keys is the dangerous one."""
    _team, user, project = await _seed(client)
    await _issue(client, user=user, project_id=project.id, breadth="read_only")

    listed = await client.get("/v1/api-keys", headers=_bearer_for(user))

    assert listed.status_code == 200, listed.text
    assert any(
        item["permission_breadth"] == "read_only" for item in listed.json()["items"]
    )


# ---------------------------------------------------------------------------
# The matrix: every surface a key reaches
# ---------------------------------------------------------------------------


async def test_a_read_only_key_cannot_trigger_a_scan(client) -> None:
    _team, user, project = await _seed(client)
    raw_key, _ = await _issue(
        client, user=user, project_id=project.id, breadth="read_only"
    )

    response = await client.post(
        f"/v1/projects/{project.id}/scans",
        json={"kind": "source"},
        headers={"Authorization": f"Bearer {raw_key}"},
    )

    assert response.status_code == 403, response.text
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    # The reason is named. A pipeline owner who gets a bare 403 goes hunting a
    # permissions problem that is not there; the one thing that lets them fix
    # it is being told the key is read-only.
    assert "read-only" in response.text


async def test_a_read_only_key_cannot_push_an_sbom(client) -> None:
    """The second write surface. One guarded endpoint is no guard at all."""
    _team, user, project = await _seed(client)
    raw_key, _ = await _issue(
        client, user=user, project_id=project.id, breadth="read_only"
    )

    response = await client.post(
        f"/v1/projects/{project.id}/sbom-ingest",
        files={"file": ("sbom.json", b"{}", "application/json")},
        headers={"Authorization": f"Bearer {raw_key}"},
    )

    assert response.status_code == 403, response.text
    assert "read-only" in response.text


async def test_a_read_only_key_cannot_post_a_pull_request_comment(client) -> None:
    """The surface that was nearly missed.

    This endpoint resolves the key through its own dependency rather than the
    shared role gate, so an enforcement check placed at that gate left it
    open. The check now sits where the principal is built, which every caller
    has to pass through.
    """
    _team, user, project = await _seed(client)
    write_key, _ = await _issue(
        client, user=user, project_id=project.id, breadth="read_write"
    )
    triggered = await client.post(
        f"/v1/projects/{project.id}/scans",
        json={"kind": "source"},
        headers={"Authorization": f"Bearer {write_key}"},
    )
    scan_id = triggered.json()["id"]
    read_key, _ = await _issue(
        client, user=user, project_id=project.id, breadth="read_only"
    )

    response = await client.post(
        f"/v1/scans/{scan_id}/post-pr-comment",
        json={"repo_full_name": "acme/app", "pr_number": 1, "dry_run": True},
        headers={"Authorization": f"Bearer {read_key}"},
    )

    assert response.status_code == 403, response.text
    assert "read-only" in response.text


async def test_a_read_write_key_can_still_post_a_pull_request_comment(
    client,
) -> None:
    """The allowed half of the surface that was nearly missed.

    Asserted because a later tightening of the breadth check that over-refused
    would break CI commenting with only the deny test green, and this is the
    one write surface where the deny half arrived first.
    """
    _team, user, project = await _seed(client)
    write_key, _ = await _issue(
        client, user=user, project_id=project.id, breadth="read_write"
    )
    key_headers = {"Authorization": f"Bearer {write_key}"}
    triggered = await client.post(
        f"/v1/projects/{project.id}/scans", json={"kind": "source"}, headers=key_headers
    )
    scan_id = triggered.json()["id"]

    response = await client.post(
        f"/v1/scans/{scan_id}/post-pr-comment",
        json={"repo_full_name": "acme/app", "pr_number": 1, "dry_run": True},
        headers=key_headers,
    )

    assert response.status_code == 200, response.text


async def test_a_read_only_key_still_reads_the_build_gate(client) -> None:
    """The same surface's read half stays open, which is what CI mostly does."""
    _team, user, project = await _seed(client)
    read_key, _ = await _issue(
        client, user=user, project_id=project.id, breadth="read_only"
    )

    response = await client.get(
        f"/v1/projects/{project.id}/gate-result",
        headers={"Authorization": f"Bearer {read_key}"},
    )

    assert response.status_code == 200, response.text


async def test_a_read_only_key_still_reads(client) -> None:
    """The whole point: it is a usable key, just not a dangerous one."""
    _team, user, project = await _seed(client)
    write_key, _ = await _issue(
        client, user=user, project_id=project.id, breadth="read_write"
    )
    triggered = await client.post(
        f"/v1/projects/{project.id}/scans",
        json={"kind": "source"},
        headers={"Authorization": f"Bearer {write_key}"},
    )
    scan_id = triggered.json()["id"]

    read_key, _ = await _issue(
        client, user=user, project_id=project.id, breadth="read_only"
    )
    polled = await client.get(
        f"/v1/scans/{scan_id}", headers={"Authorization": f"Bearer {read_key}"}
    )

    assert polled.status_code == 200, polled.text
    assert polled.json()["id"] == scan_id


async def test_breadth_does_not_touch_a_person_session(client) -> None:
    """A JWT is not narrowed by this. Only keys carry the flag."""
    _team, user, project = await _seed(client)
    await _issue(client, user=user, project_id=project.id, breadth="read_only")

    response = await client.post(
        f"/v1/projects/{project.id}/scans",
        json={"kind": "source"},
        headers=_bearer_for(user),
    )

    assert response.status_code == 202, response.text


# ---------------------------------------------------------------------------
# Narrowing: issue, downgrade, use
# ---------------------------------------------------------------------------


async def test_a_key_can_be_narrowed_and_then_cannot_write(client) -> None:
    _team, user, project = await _seed(client)
    raw_key, key_id = await _issue(
        client, user=user, project_id=project.id, breadth="read_write"
    )
    key_headers = {"Authorization": f"Bearer {raw_key}"}
    before = await client.post(
        f"/v1/projects/{project.id}/scans", json={"kind": "source"}, headers=key_headers
    )
    assert before.status_code == 202, before.text

    narrowed = await client.patch(
        f"/v1/api-keys/{key_id}",
        json={"permission_breadth": "read_only"},
        headers=_bearer_for(user),
    )
    after = await client.post(
        f"/v1/projects/{project.id}/scans", json={"kind": "source"}, headers=key_headers
    )

    assert narrowed.status_code == 200, narrowed.text
    assert narrowed.json()["permission_breadth"] == "read_only"
    assert after.status_code == 403, after.text


async def test_narrowing_twice_is_not_an_error(client) -> None:
    """A caller retrying after a timeout asked for a state that already holds."""
    _team, user, project = await _seed(client)
    _raw, key_id = await _issue(
        client, user=user, project_id=project.id, breadth="read_write"
    )
    body = {"permission_breadth": "read_only"}

    first = await client.patch(
        f"/v1/api-keys/{key_id}", json=body, headers=_bearer_for(user)
    )
    second = await client.patch(
        f"/v1/api-keys/{key_id}", json=body, headers=_bearer_for(user)
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text


async def test_a_key_cannot_be_widened(client) -> None:
    """Widening means issuing a new key, with a new secret.

    A key that has been sitting in a CI log for months should not be handed
    more privilege than it was born with just because somebody can reach the
    endpoint.
    """
    _team, user, project = await _seed(client)
    _raw, key_id = await _issue(
        client, user=user, project_id=project.id, breadth="read_only"
    )

    response = await client.patch(
        f"/v1/api-keys/{key_id}",
        json={"permission_breadth": "read_write"},
        headers=_bearer_for(user),
    )

    assert response.status_code == 422, response.text


async def test_another_teams_key_cannot_be_narrowed(client) -> None:
    """404, matching revoke: key ids are not probeable by status code."""
    _team, owner, project = await _seed(client)
    _other_team, outsider, _other_project = await _seed(client)
    _raw, key_id = await _issue(
        client, user=owner, project_id=project.id, breadth="read_write"
    )

    response = await client.patch(
        f"/v1/api-keys/{key_id}",
        json={"permission_breadth": "read_only"},
        headers=_bearer_for(outsider),
    )

    assert response.status_code == 404, response.text
