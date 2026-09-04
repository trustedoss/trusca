# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Who a project's work may be assigned to (ER65).

This is a new permission surface, and the thing it must never do is let
somebody enumerate another team's people. Everything else it returns is a
colleague's name, which is not secret inside a team and is not the caller's
business outside one.

The other half is an equality nobody would notice breaking. The list exists to
feed the assignment PATCH, so the set it returns and the set that PATCH accepts
have to be the same set. They are computed from one predicate for that reason,
and the tests check both directions: everyone offered can be saved, and
everyone savable is offered. Checking one direction only catches half of it,
and the two halves fail differently. Offering somebody the write refuses is a
form the user meets immediately; refusing to offer somebody the write would
accept is invisible, and the work goes to whoever is on the list instead.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

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
pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


@pytest.fixture
async def app():  # noqa: ANN201
    import main as m

    return m.app


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:  # noqa: ANN001
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


def _bearer(user: User, role: str = "developer") -> dict[str, str]:
    actual = "super_admin" if user.is_superuser else role
    return {
        "Authorization": f"Bearer {create_access_token(subject=str(user.id), role=actual)}"
    }


async def _factory(client: AsyncClient):  # noqa: ANN202
    app = client._transport.app  # type: ignore[attr-defined]
    factory = getattr(app.state, "session_factory", None)
    if factory is None:
        from core.db import _ensure_state

        factory = _ensure_state(app)
    return factory


async def _seed_team_with_people(client: AsyncClient):  # noqa: ANN202
    """A project whose team holds one of everything the predicate rules on.

    Five people, and each one is a case: an ordinary member, a member with no
    name, a deactivated member, a service account, and somebody on another
    team entirely. A fixture with only assignable people would let a query
    that forgot every condition pass.
    """
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        other_team = await make_team(session, organization=org)
        project = await make_project(session, team=team)

        caller = await make_user(session, full_name="Ada Caller")
        await make_membership(session, user=caller, team=team, role="developer")

        # The effective role comes from membership, not from the token claim,
        # so a viewer needs a viewer membership. Asserting on the claim alone
        # would have passed a developer off as a viewer.
        viewer = await make_user(session, full_name="Vic Viewer")
        await make_membership(session, user=viewer, team=team, role="viewer")

        # A viewer here who is a developer somewhere else. The effective grade
        # is one number across every membership, so this person clears the
        # role floor with a grade earned in a different team. Its result is
        # recorded below rather than assumed.
        dual = await make_user(session, full_name="Dee Dual")
        await make_membership(session, user=dual, team=team, role="viewer")
        await make_membership(session, user=dual, team=other_team, role="developer")

        named = await make_user(session, full_name="Grace Member")
        await make_membership(session, user=named, team=team, role="developer")

        nameless = await make_user(session)
        # ``make_user`` substitutes a generated name for None, so the case this
        # fixture is for has to be made after the fact. Passing None and
        # trusting it would have tested the helper.
        nameless.full_name = None
        await make_membership(session, user=nameless, team=team, role="developer")

        deactivated = await make_user(session, full_name="Gone Away")
        deactivated.is_active = False
        await make_membership(session, user=deactivated, team=team, role="developer")

        robot = await make_user(session, full_name="CI Robot")
        robot.is_service_account = True
        await make_membership(session, user=robot, team=team, role="developer")

        outsider = await make_user(session, full_name="Other Team")
        await make_membership(session, user=outsider, team=other_team, role="developer")

        await session.commit()
        return {
            "project_id": project.id,
            "team_id": team.id,
            "caller": caller,
            "named": named.id,
            "nameless": nameless.id,
            "deactivated": deactivated.id,
            "robot": robot.id,
            "outsider": outsider.id,
            "outsider_user": outsider,
            "viewer_user": viewer,
            "viewer": viewer.id,
            "dual_user": dual,
            "dual": dual.id,
        }


async def _get(client: AsyncClient, seed, user=None, role: str = "developer"):  # noqa: ANN001, ANN202
    return await client.get(
        f"/v1/projects/{seed['project_id']}/assignable-members",
        headers=_bearer(user or seed["caller"], role),
    )


async def test_the_list_is_exactly_the_people_who_may_be_named(
    client: AsyncClient,
) -> None:
    seed = await _seed_team_with_people(client)

    response = await _get(client, seed)

    assert response.status_code == 200, response.text
    body = response.json()
    ids = {m["user_id"] for m in body["members"]}

    assert str(seed["named"]) in ids
    assert str(seed["nameless"]) in ids, (
        "somebody with no display name is still assignable, and a list that "
        "hides them leaves the write reachable only by whoever knows the id"
    )
    assert str(seed["deactivated"]) not in ids
    assert str(seed["robot"]) not in ids
    assert str(seed["outsider"]) not in ids
    assert body["total"] == len(body["members"])


async def test_everyone_offered_can_actually_be_saved(client: AsyncClient) -> None:
    """The first direction of the equality, driven through the real predicate.

    Reads the list, then asks the assignment rule about each person. A list
    that offered somebody the write refuses would fail here.
    """
    from services.assignee import is_assignable_to_team

    seed = await _seed_team_with_people(client)
    response = await _get(client, seed)
    assert response.status_code == 200
    offered = [uuid.UUID(m["user_id"]) for m in response.json()["members"]]
    assert offered, "an empty list would make this assert nothing"

    factory = await _factory(client)
    async with factory() as session:
        for user_id in offered:
            assert await is_assignable_to_team(session, user_id, seed["team_id"]), (
                f"{user_id} was offered by the list and the assignment rule "
                "refuses them, so the picker shows a choice that cannot be saved"
            )


async def test_everyone_savable_is_offered(client: AsyncClient) -> None:
    """The other direction, which is the one nobody would notice breaking.

    A person the write accepts but the list omits cannot be chosen, so the work
    goes to somebody else and nothing anywhere reports a problem.
    """
    from services.assignee import is_assignable_to_team

    seed = await _seed_team_with_people(client)
    response = await _get(client, seed)
    offered = {uuid.UUID(m["user_id"]) for m in response.json()["members"]}

    candidates = [
        seed["viewer"],
        seed["named"],
        seed["nameless"],
        seed["deactivated"],
        seed["robot"],
        seed["outsider"],
        seed["caller"].id,
    ]
    factory = await _factory(client)
    async with factory() as session:
        savable = {
            user_id
            for user_id in candidates
            if await is_assignable_to_team(session, user_id, seed["team_id"])
        }

    assert savable, "no candidate is savable, so this compares two empty sets"
    missing = savable - offered
    assert not missing, (
        f"{sorted(map(str, missing))} may be assigned and are not on the list, "
        "so nobody can choose them and the work quietly goes elsewhere"
    )


async def test_another_team_cannot_enumerate_these_people(
    client: AsyncClient,
) -> None:
    """The reason this needed a security review.

    A member of another team asking about this project must learn nothing about
    who is on it.
    """
    seed = await _seed_team_with_people(client)

    response = await _get(client, seed, user=seed["outsider_user"])

    assert response.status_code == 403, response.text
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    body = response.text
    for name in ("Grace Member", "Ada Caller", str(seed["named"])):
        assert name not in body, (
            f"the refusal names {name!r}, so a caller outside the team learns "
            "who is inside it from the error"
        )


async def test_the_refusal_does_carry_the_owning_team_id(
    client: AsyncClient,
) -> None:
    """Recorded, not fixed here, because it is not this route's to fix.

    ``ProjectForbidden`` says "actor is not a member of team <uuid>" and the
    problem handler passes the message straight through, so a caller outside
    the team learns the owning team's id along with the refusal. That is
    pre-existing on every route built on ``get_project``, fifteen of them,
    including ``GET /v1/projects/{project_id}`` at a lower grade, so this route
    discloses nothing a caller could not already obtain.

    Asserted as present rather than left unsaid. The test above certifies that
    no member data is in the refusal, and reading it alone would suggest the
    refusal is information-free, which it is not. Whoever removes the id from
    that message will see this fail and know it was a decision rather than an
    oversight; the id is already in the ``authz.cross_team_attempt`` log line,
    which is where it belongs.
    """
    seed = await _seed_team_with_people(client)

    response = await _get(client, seed, user=seed["outsider_user"])

    assert response.status_code == 403
    assert str(seed["team_id"]) in response.text, (
        "the owning team id is no longer in the refusal. If that was "
        "deliberate, delete this test; the property it records is gone and "
        "the route is better for it."
    )


async def test_a_viewer_is_refused(client: AsyncClient) -> None:
    """Classified as denied for the viewer grade, and asserted here.

    A viewer cannot assign, so a directory of colleagues serves them nothing.
    The caller is a viewer by membership rather than by the role in the token:
    the effective grade is derived from memberships, so a developer with
    ``role="viewer"`` in the header is still a developer, and the first version
    of this test proved only that.
    """
    seed = await _seed_team_with_people(client)

    response = await _get(client, seed, user=seed["viewer_user"], role="viewer")

    assert response.status_code == 403, response.text


async def test_the_response_carries_no_email(client: AsyncClient) -> None:
    """Names, not addresses.

    The admin team view returns an email because an administrator auditing who
    can reach a team needs one. A picker does not, and shipping it would make
    every project page a place addresses can be collected from.
    """
    seed = await _seed_team_with_people(client)
    factory = await _factory(client)
    async with factory() as session:
        from sqlalchemy import select

        emails = (
            await session.execute(select(User.email).where(User.id.in_([seed["named"]])))
        ).scalars().all()
    assert emails, "the fixture user has no email, so the check below is vacuous"

    response = await _get(client, seed)

    assert response.status_code == 200
    for email in emails:
        assert email not in response.text
    member = next(
        m for m in response.json()["members"] if m["user_id"] == str(seed["named"])
    )
    assert set(member) == {"user_id", "full_name"}, (
        f"the member row carries {sorted(member)}; anything beyond the two is "
        "a field somebody has to justify"
    )


async def test_a_nameless_member_comes_back_as_null_not_as_an_address(
    client: AsyncClient,
) -> None:
    """The fallback is the client's to render, and never a piece of the email."""
    seed = await _seed_team_with_people(client)

    response = await _get(client, seed)

    member = next(
        m for m in response.json()["members"] if m["user_id"] == str(seed["nameless"])
    )
    assert member["full_name"] is None, (
        f"expected null, got {member['full_name']!r}. A server-side fallback "
        "built from the email address leaks the part of it that identifies a "
        "person, which is why the address is not in this response at all."
    )


def test_the_query_carries_its_own_join() -> None:
    """A tenancy filter that only works when the caller remembers something.

    The first version of this exported the three conditions as a bare
    ``and_(...)``. A caller who selected from ``User`` and filtered on it
    without joining ``Membership`` got no error and no warning, just
    ``FROM users, memberships``: a cross join returning every active person in
    the deployment rather than the team's. The security review found it by
    compiling the statement, which is what this does now on every run.

    One final FROM is the whole assertion. Two means the join came off and the
    team condition is filtering a product instead of a relationship.
    """
    from sqlalchemy import select

    from models import User
    from services.assignee import assignable_members_select

    statement = assignable_members_select(uuid.uuid4())
    froms = statement.get_final_froms()
    assert len(froms) == 1, (
        f"the query has {len(froms)} FROM entries, so it is a cross join and "
        "the team filter no longer restricts anything: "
        f"{str(statement)}"
    )

    # The mistake's actual shape, built here so the assertion above is known to
    # be capable of failing rather than trivially true of every statement.
    from models import Membership

    cross_joined = select(User.id).where(
        User.is_active.is_(True), Membership.team_id == uuid.uuid4()
    )
    assert len(cross_joined.get_final_froms()) == 2, (
        "the form this guard exists to reject now compiles to one FROM, so "
        "the check above no longer distinguishes anything"
    )


def test_narrowing_the_query_keeps_the_join() -> None:
    """The single-person check is the same query with a filter on top.

    Written this way so the two questions cannot diverge. If somebody rebuilds
    it by hand later, this fails.
    """
    from models import User
    from services.assignee import assignable_members_select

    narrowed = assignable_members_select(uuid.uuid4()).where(
        User.id == uuid.uuid4()
    ).limit(1)

    assert len(narrowed.get_final_froms()) == 1
    compiled = str(narrowed)
    assert "JOIN memberships" in compiled, compiled


async def test_a_viewer_here_who_is_a_developer_elsewhere_is_not_refused(
    client: AsyncClient,
) -> None:
    """The limit of the viewer denial, recorded because the matrix cannot say it.

    ``CurrentUser.role`` is one grade across every membership, so somebody who
    is a viewer on this team and a developer on another clears the role floor,
    and ``get_project`` then asks only for membership, not for a grade within
    the team. The viewer entry in the target matrix reads as absolute and is
    conditional on the caller having no higher grade anywhere.

    Not changed here, and the reason matters. The assignment PATCH this list
    feeds reaches the same person by the same mechanism, so the list is exactly
    as reachable as the write, which is the property the whole design rests on.
    Tightening one without the other would open the gap the single predicate
    exists to close. Per-team grading is a change to both and to the role model
    behind them.

    So this asserts what happens, and fails if somebody changes it without
    changing the write.
    """
    seed = await _seed_team_with_people(client)

    response = await _get(client, seed, user=seed["dual_user"], role="viewer")

    assert response.status_code == 200, (
        "a viewer here who is a developer elsewhere is now refused. If the "
        "role model became per-team, check that the assignment PATCH moved "
        "with it: a list narrower than the write it feeds is the drift the "
        "shared predicate exists to prevent."
    )


async def test_organization_visibility_does_not_widen_project_access_yet(
    client: AsyncClient,
) -> None:
    """The premise this route's safety rests on, asserted instead of described.

    Deriving the team from the project is only safe while reaching a project
    means being on its team. Today it does: ``visibility='organization'``
    exists in the schema and nothing reads it for access, so an outsider is
    refused either way.

    The day somebody wires it up, this fails rather than the route's docstring
    going quietly stale, and the failure lands next to the route it changes the
    meaning of. Without it the only thing standing between organization-wide
    visibility and cross-team member enumeration is a comment.
    """
    seed = await _seed_team_with_people(client)

    factory = await _factory(client)
    async with factory() as session:
        from sqlalchemy import update

        from models import Project

        await session.execute(
            update(Project)
            .where(Project.id == seed["project_id"])
            .values(visibility="organization")
        )
        await session.commit()

        current = (
            await session.execute(
                select(Project.visibility).where(Project.id == seed["project_id"])
            )
        ).scalar_one()
    assert current == "organization", (
        "the project was not switched, so the refusal below proves nothing"
    )

    response = await _get(client, seed, user=seed["outsider_user"])

    assert response.status_code == 403, (
        "an organization-visible project now admits a caller from another "
        "team, so this route hands them that team's member list. Decide what "
        "it should do before shipping that: reading a project's findings and "
        "listing the people on its team are different permissions."
    )
