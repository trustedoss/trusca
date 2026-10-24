"""
Build-gate policy over HTTP.

The cases that matter are the boundaries, not the happy path. Writing what
blocks a build is the ability to stop a build being blocked, so who may write
at which scope is the whole security content of this surface, and the answers
are asserted per grade rather than for one representative caller.

The read side is deliberately wider than the write side: an auditor needs to
see what applies without being able to change it, which is the reason the
lowest grade exists at all.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from core.security import create_access_token
from models import User
from tests._db_required import migrate_to_head
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_team,
    make_user,
)

PROBLEM_JSON = "application/problem+json"
# Spelled out rather than imported: the wire value is the contract, and an
# import would follow a renamed constant to a new URI without failing.
NOT_SET_TYPE = "https://docs.trustedoss.io/errors/gate-policy-not-set"
CONFLICT_TYPE = "https://docs.trustedoss.io/errors/gate-policy-conflict"

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


async def _seed(client: AsyncClient, *, role: str = "group_admin"):
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session)
        await make_membership(session, user=user, team=team, role=role)
        project = await make_project(session, team=team)
        return org.id, team.id, user, project.id


async def _seed_super_admin(client: AsyncClient):
    factory = await _factory(client)
    async with factory() as session:
        return await make_user(session, is_superuser=True)


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


async def test_a_team_admin_writes_and_replaces_their_team_policy(client) -> None:
    """A second PUT replaces rather than adding: the scope is the identity."""
    _, team_id, user, _ = await _seed(client)
    headers = _bearer_for(user)

    first = await client.put(
        f"/v1/gate-policies/teams/{team_id}",
        headers=headers,
        json={"epss_threshold": 0.5},
    )
    second = await client.put(
        f"/v1/gate-policies/teams/{team_id}",
        headers=headers,
        json={"epss_threshold": 0.9, "malicious_blocks": True},
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["epss_threshold"] == 0.9
    assert second.json()["malicious_blocks"] is True


async def test_an_omitted_field_stops_overriding(client) -> None:
    """A PUT replaces the row, so leaving a field out clears it.

    That is the mechanism a team uses to hand a decision back to its
    organization, and it only works if omission stores NULL rather than
    keeping the previous value.
    """
    _, team_id, user, _ = await _seed(client)
    headers = _bearer_for(user)

    await client.put(
        f"/v1/gate-policies/teams/{team_id}",
        headers=headers,
        json={"epss_threshold": 0.5, "malicious_blocks": False},
    )
    response = await client.put(
        f"/v1/gate-policies/teams/{team_id}",
        headers=headers,
        json={"epss_threshold": 0.5},
    )

    assert response.status_code == 200, response.text
    assert response.json()["malicious_blocks"] is None


@pytest.mark.parametrize("role", ["viewer", "developer"])
async def test_a_grade_below_team_admin_may_not_write(client, role: str) -> None:
    """Membership is not authority: the grade decides, and it decides per team."""
    _, team_id, user, _ = await _seed(client, role=role)

    response = await client.put(
        f"/v1/gate-policies/teams/{team_id}",
        headers=_bearer_for(user),
        json={"epss_threshold": 0.5},
    )

    assert response.status_code == 403, response.text
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_a_team_admin_of_another_team_may_not_write(client) -> None:
    """The grade is per team, so administering one is not administering all."""
    _, team_id, _, _ = await _seed(client)
    _, _, outsider, _ = await _seed(client)

    response = await client.put(
        f"/v1/gate-policies/teams/{team_id}",
        headers=_bearer_for(outsider),
        json={"epss_threshold": 0.5},
    )

    assert response.status_code == 403, response.text


async def test_only_a_super_admin_writes_the_organization_default(client) -> None:
    """It is the floor every team inherits, so it answers to the deployment."""
    org_id, _, team_admin, _ = await _seed(client)
    refused = await client.put(
        f"/v1/gate-policies/org/{org_id}",
        headers=_bearer_for(team_admin),
        json={"epss_threshold": 0.4},
    )
    assert refused.status_code == 403, refused.text

    super_admin = await _seed_super_admin(client)
    allowed = await client.put(
        f"/v1/gate-policies/org/{org_id}",
        headers=_bearer_for(super_admin),
        json={"epss_threshold": 0.4},
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["team_id"] is None


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


async def test_a_viewer_reads_the_effective_policy_and_where_it_came_from(client) -> None:
    """The read side is wider than the write side, and says which row to edit."""
    org_id, team_id, viewer, project_id = await _seed(client, role="viewer")
    super_admin = await _seed_super_admin(client)
    await client.put(
        f"/v1/gate-policies/org/{org_id}",
        headers=_bearer_for(super_admin),
        json={"epss_threshold": 0.4, "malicious_blocks": False},
    )

    response = await client.get(
        f"/v1/gate-policies/effective/{project_id}", headers=_bearer_for(viewer)
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["epss_threshold"] == 0.4
    assert body["sources"]["epss_threshold"]["scope"] == "organization"
    assert body["sources"]["epss_threshold"]["group_ids"] == []
    # The deprecated flat-string alias must agree with the structured field
    # (Phase 3, group-hierarchy generalisation).
    assert body["sources_legacy"]["epss_threshold"] == "organization"
    # Nothing decided reachability, so the deployment's answer stands and the
    # source says so rather than implying someone chose it.
    assert body["sources"]["reachable_critical_only"]["scope"] == "deployment"
    assert body["sources_legacy"]["reachable_critical_only"] == "deployment"


async def test_a_team_row_is_reported_as_the_team_source(client) -> None:
    org_id, team_id, user, project_id = await _seed(client)
    super_admin = await _seed_super_admin(client)
    await client.put(
        f"/v1/gate-policies/org/{org_id}",
        headers=_bearer_for(super_admin),
        json={"epss_threshold": 0.4},
    )
    await client.put(
        f"/v1/gate-policies/teams/{team_id}",
        headers=_bearer_for(user),
        json={"epss_threshold": 0.9},
    )

    response = await client.get(
        f"/v1/gate-policies/effective/{project_id}", headers=_bearer_for(user)
    )

    body = response.json()
    assert body["epss_threshold"] == 0.9
    assert body["sources"]["epss_threshold"]["scope"] == "group"
    assert body["sources"]["epss_threshold"]["group_ids"] == [str(team_id)]
    assert body["sources_legacy"]["epss_threshold"] == "team"


async def test_a_stranger_cannot_learn_that_a_team_exists(client) -> None:
    """Existence-hidden: the read gate answers 404, not 403.

    The team must have a policy row for this to test anything. Without one the
    endpoint answers 404 anyway, and the assertion passes whether or not the
    gate is there: removing the guard entirely still produced a 404 until this
    seeded a row, which is the difference between hidden and simply absent.
    """
    _, team_id, owner, _ = await _seed(client)
    await client.put(
        f"/v1/gate-policies/teams/{team_id}",
        headers=_bearer_for(owner),
        json={"epss_threshold": 0.5},
    )
    _, _, stranger, _ = await _seed(client)

    response = await client.get(
        f"/v1/gate-policies/teams/{team_id}", headers=_bearer_for(stranger)
    )

    assert response.status_code == 404, response.text
    assert response.json()["type"] != NOT_SET_TYPE
    assert "etag" not in response.headers


async def test_a_stranger_cannot_tell_a_rowless_team_from_a_missing_one(client) -> None:
    """The "no row" type is a fact about the team, so it is the owner's to see.

    Answering it to a stranger would confirm the team exists, which is what
    the plain 404 is there to withhold.
    """
    _, team_id, _, _ = await _seed(client)
    _, _, stranger, _ = await _seed(client)

    hidden = await client.get(
        f"/v1/gate-policies/teams/{team_id}", headers=_bearer_for(stranger)
    )
    missing = await client.get(
        f"/v1/gate-policies/teams/{uuid.uuid4()}", headers=_bearer_for(stranger)
    )

    assert hidden.status_code == missing.status_code == 404
    assert hidden.json()["type"] == missing.json()["type"] != NOT_SET_TYPE
    assert hidden.json()["title"] == missing.json()["title"]


async def test_a_stranger_cannot_read_another_orgs_effective_policy(client) -> None:
    """Security review finding (Phase 3): the effective-policy endpoint had no
    project/group membership check at all, only the coarse ``viewer`` role
    floor -- any authenticated user could resolve any project's gate policy,
    and once ``sources`` started naming real groups, that leaked another
    organization's group names and ancestor chain, not just a threshold
    value. Existence-hidden (404), matching ``get_team_policy``'s own gate.
    """
    org_id, team_id, owner, project_id = await _seed(client)
    super_admin = await _seed_super_admin(client)
    await client.put(
        f"/v1/gate-policies/org/{org_id}",
        headers=_bearer_for(super_admin),
        json={"epss_threshold": 0.4},
    )
    await client.put(
        f"/v1/gate-policies/teams/{team_id}",
        headers=_bearer_for(owner),
        json={"epss_threshold": 0.9},
    )
    _, _, stranger, _ = await _seed(client)

    response = await client.get(
        f"/v1/gate-policies/effective/{project_id}", headers=_bearer_for(stranger)
    )

    assert response.status_code == 404, response.text
    # The project's team name/id must not leak into the error body either.
    assert str(team_id) not in response.text


async def test_a_team_without_a_row_reads_as_absent_not_empty(client) -> None:
    """Its own problem type, so a client can tell "no row" from "no team"."""
    _, team_id, user, _ = await _seed(client)

    response = await client.get(
        f"/v1/gate-policies/teams/{team_id}", headers=_bearer_for(user)
    )

    assert response.status_code == 404, response.text
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    assert response.json()["type"] == NOT_SET_TYPE


# ---------------------------------------------------------------------------
# Deleting
# ---------------------------------------------------------------------------


async def test_deleting_a_team_row_returns_it_to_the_organization(client) -> None:
    org_id, team_id, user, project_id = await _seed(client)
    super_admin = await _seed_super_admin(client)
    await client.put(
        f"/v1/gate-policies/org/{org_id}",
        headers=_bearer_for(super_admin),
        json={"epss_threshold": 0.4},
    )
    await client.put(
        f"/v1/gate-policies/teams/{team_id}",
        headers=_bearer_for(user),
        json={"epss_threshold": 0.9},
    )

    removed = await client.delete(
        f"/v1/gate-policies/teams/{team_id}", headers=_bearer_for(user)
    )
    effective = await client.get(
        f"/v1/gate-policies/effective/{project_id}", headers=_bearer_for(user)
    )

    assert removed.status_code == 204, removed.text
    effective_body = effective.json()
    assert effective_body["epss_threshold"] == 0.4
    assert effective_body["sources"]["epss_threshold"]["scope"] == "organization"
    assert effective_body["sources_legacy"]["epss_threshold"] == "organization"


async def test_deleting_what_was_never_written_is_a_404(client) -> None:
    """So a caller is never told a delete removed something it did not."""
    _, team_id, user, _ = await _seed(client)

    response = await client.delete(
        f"/v1/gate-policies/teams/{team_id}", headers=_bearer_for(user)
    )

    assert response.status_code == 404, response.text
    assert response.json()["type"] == NOT_SET_TYPE


async def test_an_unknown_team_is_a_404_on_every_verb(client) -> None:
    _, _, user, _ = await _seed(client)
    ghost = uuid.uuid4()
    headers = _bearer_for(user)

    for response in (
        await client.get(f"/v1/gate-policies/teams/{ghost}", headers=headers),
        await client.put(
            f"/v1/gate-policies/teams/{ghost}", headers=headers, json={"epss_threshold": 0.5}
        ),
        await client.delete(f"/v1/gate-policies/teams/{ghost}", headers=headers),
    ):
        assert response.status_code == 404, response.text
        # Not the "no row" type: a missing team is not a team that follows
        # its organization.
        assert response.json()["type"] != NOT_SET_TYPE


async def test_the_row_survives_only_while_its_team_does(client) -> None:
    """The cascade is the reason a deleted team leaves no orphan policy."""
    from models import GatePolicy, Team

    _, team_id, user, _ = await _seed(client)
    await client.put(
        f"/v1/gate-policies/teams/{team_id}",
        headers=_bearer_for(user),
        json={"epss_threshold": 0.5},
    )

    factory = await _factory(client)
    async with factory() as session:
        team = (await session.execute(select(Team).where(Team.id == team_id))).scalar_one()
        await session.delete(team)
        await session.commit()
        rows: list[Any] = list(
            (
                await session.execute(
                    select(GatePolicy).where(GatePolicy.team_id == team_id)
                )
            )
            .scalars()
            .all()
        )

    assert rows == []


# ---------------------------------------------------------------------------
# Concurrent edits: the entity tag
# ---------------------------------------------------------------------------


async def _row_and_tag(client: AsyncClient, team_id: uuid.UUID, user: User):
    response = await client.get(
        f"/v1/gate-policies/teams/{team_id}", headers=_bearer_for(user)
    )
    assert response.status_code == 200, response.text
    return response.json(), response.headers["etag"]


async def test_the_read_hands_out_a_tag_that_the_next_write_accepts(client) -> None:
    _, team_id, user, _ = await _seed(client)
    created = await client.put(
        f"/v1/gate-policies/teams/{team_id}",
        headers=_bearer_for(user),
        json={"epss_threshold": 0.5},
    )
    _, tag = await _row_and_tag(client, team_id, user)
    _, tag_again = await _row_and_tag(client, team_id, user)

    saved = await client.put(
        f"/v1/gate-policies/teams/{team_id}",
        headers={**_bearer_for(user), "If-Match": tag},
        json={"epss_threshold": 0.7},
    )

    assert tag.startswith('"') and tag.endswith('"')
    assert created.headers["etag"] == tag == tag_again
    assert saved.status_code == 200, saved.text
    assert saved.json()["epss_threshold"] == 0.7
    assert saved.headers["etag"] != tag
    _, tag_after = await _row_and_tag(client, team_id, user)
    assert saved.headers["etag"] == tag_after


async def test_the_second_of_two_editors_is_refused_and_can_recover(client) -> None:
    """The lost update this exists to stop, as the sequence that produces it.

    Both read the same row, the first saves, the second saves from what it
    read. Before the precondition the second save erased the first one's
    edit without either of them knowing. Now it is refused, the first edit
    survives, and reading again lets the second editor save.
    """
    _, team_id, user, _ = await _seed(client)
    await client.put(
        f"/v1/gate-policies/teams/{team_id}",
        headers=_bearer_for(user),
        json={"epss_threshold": 0.5},
    )
    _, first_read = await _row_and_tag(client, team_id, user)
    _, second_read = await _row_and_tag(client, team_id, user)

    first = await client.put(
        f"/v1/gate-policies/teams/{team_id}",
        headers={**_bearer_for(user), "If-Match": first_read},
        json={"epss_threshold": 0.5, "malicious_blocks": False},
    )
    second = await client.put(
        f"/v1/gate-policies/teams/{team_id}",
        headers={**_bearer_for(user), "If-Match": second_read},
        json={"epss_threshold": 0.9},
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 409, second.text
    assert second.headers["content-type"].startswith(PROBLEM_JSON)
    body = second.json()
    assert body["type"] == CONFLICT_TYPE
    assert body["status"] == 409
    assert body["instance"] == f"/v1/gate-policies/teams/{team_id}"
    row, fresh = await _row_and_tag(client, team_id, user)
    assert row["malicious_blocks"] is False
    assert row["epss_threshold"] == 0.5

    retried = await client.put(
        f"/v1/gate-policies/teams/{team_id}",
        headers={**_bearer_for(user), "If-Match": fresh},
        json={"epss_threshold": 0.9, "malicious_blocks": False},
    )
    assert retried.status_code == 200, retried.text
    assert retried.json()["epss_threshold"] == 0.9


async def test_a_write_without_the_header_still_replaces_the_row(client) -> None:
    """Backward compatible: a client that never sends If-Match keeps working."""
    _, team_id, user, _ = await _seed(client)
    await client.put(
        f"/v1/gate-policies/teams/{team_id}",
        headers=_bearer_for(user),
        json={"epss_threshold": 0.5},
    )
    _, stale = await _row_and_tag(client, team_id, user)
    await client.put(
        f"/v1/gate-policies/teams/{team_id}",
        headers={**_bearer_for(user), "If-Match": stale},
        json={"epss_threshold": 0.6},
    )

    blind = await client.put(
        f"/v1/gate-policies/teams/{team_id}",
        headers=_bearer_for(user),
        json={"epss_threshold": 0.8},
    )

    assert blind.status_code == 200, blind.text
    assert blind.json()["epss_threshold"] == 0.8


@pytest.mark.parametrize(
    "header",
    [
        pytest.param('"0"', id="stale-tag"),
        pytest.param("not-a-tag", id="unquoted-garbage"),
        pytest.param('""', id="empty-tag"),
        pytest.param("", id="empty-header"),
    ],
)
async def test_a_tag_that_does_not_match_is_refused(client, header: str) -> None:
    """Anything that is not the current tag is stale, never "no header"."""
    _, team_id, user, _ = await _seed(client)
    await client.put(
        f"/v1/gate-policies/teams/{team_id}",
        headers=_bearer_for(user),
        json={"epss_threshold": 0.5},
    )

    response = await client.put(
        f"/v1/gate-policies/teams/{team_id}",
        headers={**_bearer_for(user), "If-Match": header},
        json={"epss_threshold": 0.9},
    )

    assert response.status_code == 409, response.text
    assert response.json()["type"] == CONFLICT_TYPE
    row, _ = await _row_and_tag(client, team_id, user)
    assert row["epss_threshold"] == 0.5


async def test_a_weakened_tag_and_a_tag_list_are_accepted(client) -> None:
    """A compressing proxy may hand the browser W/"..." for the same row."""
    _, team_id, user, _ = await _seed(client)
    await client.put(
        f"/v1/gate-policies/teams/{team_id}",
        headers=_bearer_for(user),
        json={"epss_threshold": 0.5},
    )
    _, tag = await _row_and_tag(client, team_id, user)

    weak = await client.put(
        f"/v1/gate-policies/teams/{team_id}",
        headers={**_bearer_for(user), "If-Match": f"W/{tag}"},
        json={"epss_threshold": 0.6},
    )
    listed = await client.put(
        f"/v1/gate-policies/teams/{team_id}",
        headers={**_bearer_for(user), "If-Match": f'"0", {weak.headers["etag"]}'},
        json={"epss_threshold": 0.7},
    )

    assert weak.status_code == 200, weak.text
    assert listed.status_code == 200, listed.text


async def test_if_match_on_a_team_with_no_row_is_refused(client) -> None:
    """The row the tag described is gone: deleted after it was read."""
    _, team_id, user, _ = await _seed(client)
    await client.put(
        f"/v1/gate-policies/teams/{team_id}",
        headers=_bearer_for(user),
        json={"epss_threshold": 0.5},
    )
    _, tag = await _row_and_tag(client, team_id, user)
    removed = await client.delete(
        f"/v1/gate-policies/teams/{team_id}", headers=_bearer_for(user)
    )

    response = await client.put(
        f"/v1/gate-policies/teams/{team_id}",
        headers={**_bearer_for(user), "If-Match": tag},
        json={"epss_threshold": 0.9},
    )
    star = await client.put(
        f"/v1/gate-policies/teams/{team_id}",
        headers={**_bearer_for(user), "If-Match": "*"},
        json={"epss_threshold": 0.9},
    )

    assert removed.status_code == 204, removed.text
    assert response.status_code == 409, response.text
    assert star.status_code == 409, star.text
    after = await client.get(
        f"/v1/gate-policies/teams/{team_id}", headers=_bearer_for(user)
    )
    assert after.json()["type"] == NOT_SET_TYPE


async def test_a_recreated_row_does_not_accept_the_old_rows_tag(client) -> None:
    """Delete then create again: the tag of the first row must not fit the second."""
    _, team_id, user, _ = await _seed(client)
    await client.put(
        f"/v1/gate-policies/teams/{team_id}",
        headers=_bearer_for(user),
        json={"epss_threshold": 0.5},
    )
    _, old_tag = await _row_and_tag(client, team_id, user)
    await client.delete(f"/v1/gate-policies/teams/{team_id}", headers=_bearer_for(user))
    await client.put(
        f"/v1/gate-policies/teams/{team_id}",
        headers=_bearer_for(user),
        json={"epss_threshold": 0.6},
    )

    response = await client.put(
        f"/v1/gate-policies/teams/{team_id}",
        headers={**_bearer_for(user), "If-Match": old_tag},
        json={"epss_threshold": 0.9},
    )

    assert response.status_code == 409, response.text


async def test_create_only_refuses_when_somebody_created_the_row_first(client) -> None:
    """If-None-Match: * is how an editor that read "no row" saves safely."""
    _, team_id, user, _ = await _seed(client)
    headers = {**_bearer_for(user), "If-None-Match": "*"}

    created = await client.put(
        f"/v1/gate-policies/teams/{team_id}", headers=headers, json={"epss_threshold": 0.5}
    )
    again = await client.put(
        f"/v1/gate-policies/teams/{team_id}", headers=headers, json={"epss_threshold": 0.9}
    )

    assert created.status_code == 200, created.text
    assert again.status_code == 409, again.text
    assert again.json()["type"] == CONFLICT_TYPE
    row, _ = await _row_and_tag(client, team_id, user)
    assert row["epss_threshold"] == 0.5


async def test_a_save_that_races_another_writer_compares_against_its_result(client) -> None:
    """The comparison alone is not enough: it could pass before the other write lands.

    Another transaction holds the row and has already changed it, but has not
    committed. A save carrying the tag both of them read must wait for that
    transaction and then compare against what it wrote. Without the row lock
    the save reads the old row, passes the comparison, queues behind the other
    writer's lock only at UPDATE time, and then overwrites it: the lost update,
    one level down.
    """
    import asyncio
    from datetime import timedelta

    from models import GatePolicy

    _, team_id, user, _ = await _seed(client)
    await client.put(
        f"/v1/gate-policies/teams/{team_id}",
        headers=_bearer_for(user),
        json={"epss_threshold": 0.5},
    )
    _, tag = await _row_and_tag(client, team_id, user)

    factory = await _factory(client)
    async with factory() as other_writer:
        row = (
            await other_writer.execute(
                select(GatePolicy).where(GatePolicy.team_id == team_id).with_for_update()
            )
        ).scalar_one()
        row.epss_threshold = 0.66
        row.updated_at = row.updated_at + timedelta(seconds=1)
        await other_writer.flush()

        save = asyncio.create_task(
            client.put(
                f"/v1/gate-policies/teams/{team_id}",
                headers={**_bearer_for(user), "If-Match": tag},
                json={"epss_threshold": 0.9},
            )
        )
        # Long enough for the save to reach the row; it cannot finish either
        # way while the lock is held, so this only orders the two.
        await asyncio.sleep(1.0)
        assert not save.done()
        await other_writer.commit()

    response = await save
    assert response.status_code == 409, response.text
    row_after, _ = await _row_and_tag(client, team_id, user)
    assert row_after["epss_threshold"] == 0.66


async def test_the_tag_moves_even_when_the_clock_does_not(client, monkeypatch) -> None:
    """A save stamped no later than the stored time must still change the tag.

    The clock is frozen, which is the worst case of a writer whose clock is
    behind the one that stamped the row: every save would get the same
    ``updated_at`` and so the same tag, and a tag read before somebody else's
    save would still match after it. The service steps past the stored value
    instead, so the stale tag is refused.
    """
    from datetime import UTC
    from datetime import datetime as real_datetime

    import services.gate_policy_service as service

    frozen = real_datetime(2026, 10, 24, 12, 0, 0, 0, tzinfo=UTC)

    class _FrozenDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return frozen

    monkeypatch.setattr(service, "datetime", _FrozenDatetime)

    _, team_id, user, _ = await _seed(client)
    created = await client.put(
        f"/v1/gate-policies/teams/{team_id}",
        headers=_bearer_for(user),
        json={"epss_threshold": 0.5},
    )
    first_tag = created.headers["etag"]
    second = await client.put(
        f"/v1/gate-policies/teams/{team_id}",
        headers={**_bearer_for(user), "If-Match": first_tag},
        json={"epss_threshold": 0.6},
    )
    stale = await client.put(
        f"/v1/gate-policies/teams/{team_id}",
        headers={**_bearer_for(user), "If-Match": first_tag},
        json={"epss_threshold": 0.9},
    )

    assert second.status_code == 200, second.text
    assert second.headers["etag"] != first_tag
    assert stale.status_code == 409, stale.text
    row, _ = await _row_and_tag(client, team_id, user)
    assert row["epss_threshold"] == 0.6


async def test_two_first_saves_at_once_do_not_fail_on_the_unique_constraint(client) -> None:
    """Nothing to lock before the row exists, so one insert loses the constraint.

    The loser is retried as if it had arrived second: with If-None-Match it is
    refused as a conflict, and without a precondition it replaces the row,
    exactly what a sequential caller would get. Neither is a 500.
    """
    import asyncio

    _, team_id, user, _ = await _seed(client)
    guarded = await asyncio.gather(
        *(
            client.put(
                f"/v1/gate-policies/teams/{team_id}",
                headers={**_bearer_for(user), "If-None-Match": "*"},
                json={"epss_threshold": value},
            )
            for value in (0.6, 0.7)
        )
    )

    _, other_team, other_user, _ = await _seed(client)
    blind = await asyncio.gather(
        *(
            client.put(
                f"/v1/gate-policies/teams/{other_team}",
                headers=_bearer_for(other_user),
                json={"epss_threshold": value},
            )
            for value in (0.6, 0.7)
        )
    )

    assert sorted(r.status_code for r in guarded) == [200, 409], [r.text for r in guarded]
    assert [r.status_code for r in blind] == [200, 200], [r.text for r in blind]


async def test_the_organization_default_honours_the_tag_too(client) -> None:
    org_id, _, _, _ = await _seed(client)
    super_admin = await _seed_super_admin(client)
    headers = _bearer_for(super_admin)
    first = await client.put(
        f"/v1/gate-policies/org/{org_id}", headers=headers, json={"epss_threshold": 0.4}
    )
    second = await client.put(
        f"/v1/gate-policies/org/{org_id}",
        headers={**headers, "If-Match": first.headers["etag"]},
        json={"epss_threshold": 0.5},
    )
    stale = await client.put(
        f"/v1/gate-policies/org/{org_id}",
        headers={**headers, "If-Match": first.headers["etag"]},
        json={"epss_threshold": 0.6},
    )

    assert second.status_code == 200, second.text
    assert stale.status_code == 409, stale.text
    assert stale.json()["type"] == CONFLICT_TYPE
