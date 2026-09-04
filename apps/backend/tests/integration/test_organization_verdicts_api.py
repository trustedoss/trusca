"""
Organization rulings over HTTP.

The boundaries are the point. Writing reaches every team in the organization,
so it belongs to whoever administers the deployment; reading is deliberately
wider, because a component shows as approved in somebody's project because of
a row here and they should be able to see why. What neither may do is reach
across an organization, and both endpoints take an id straight from the URL,
which is exactly the shape that leaks when nobody checks.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from core.security import create_access_token
from models import User
from models.component_approval import ApprovalStatus
from tests._db_required import migrate_to_head
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_team,
    make_user,
    unique_suffix,
)

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


async def _scene(client: AsyncClient):
    """One organization, one project in it, one component, and three callers."""
    from models import Component

    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        project = await make_project(session, team=team)
        admin = await make_user(session, is_superuser=True)
        member = await make_user(session)
        await make_membership(session, user=member, team=team, role="developer")

        suffix = unique_suffix()
        component = Component(
            purl=f"pkg:npm/orgapi-{suffix}",
            package_type="npm",
            name=f"orgapi-{suffix}",
        )
        session.add(component)
        await session.commit()
        await session.refresh(component)
        return {
            "org_id": org.id,
            "team_id": team.id,
            "project_id": project.id,
            "component_id": component.id,
            "admin": admin,
            "member": member,
        }


async def _rule(client: AsyncClient, scene, outcome: str) -> str:
    """Open a ruling and carry it to a decided state. Returns its id."""
    headers = _bearer_for(scene["admin"])
    opened = await client.post(
        f"/v1/organization-verdicts/org/{scene['org_id']}",
        headers=headers,
        json={
            "component_id": str(scene["component_id"]),
            "justification": "reviewed centrally for the whole organization",
        },
    )
    assert opened.status_code == 201, opened.text
    verdict_id = opened.json()["id"]
    version = opened.json()["version"]

    moved = await client.patch(
        f"/v1/organization-verdicts/{verdict_id}",
        headers={**headers, "If-Match": f'"{version}"'},
        json={"status": ApprovalStatus.under_review, "note": None},
    )
    assert moved.status_code == 200, moved.text

    decided = await client.patch(
        f"/v1/organization-verdicts/{verdict_id}",
        headers={**headers, "If-Match": f'"{moved.json()["version"]}"'},
        json={"status": outcome, "note": "decided"},
    )
    assert decided.status_code == 200, decided.text
    return str(verdict_id)


# ---------------------------------------------------------------------------
# Who may rule
# ---------------------------------------------------------------------------


async def test_a_deployment_administrator_rules_and_a_project_inherits(
    client,
) -> None:
    scene = await _scene(client)
    await _rule(client, scene, ApprovalStatus.approved)

    effective = await client.get(
        f"/v1/organization-verdicts/effective/{scene['project_id']}/{scene['component_id']}",
        headers=_bearer_for(scene["member"]),
    )

    assert effective.status_code == 200, effective.text
    assert effective.json()["status"] == ApprovalStatus.approved
    assert effective.json()["scope"] == "organization"


async def test_a_team_member_may_not_rule_for_the_organization(client) -> None:
    """The answer reaches every team, so it is not one team's to give.

    404, matching the other administrator surfaces: whether the route is there
    at all is not something to confirm by status code to somebody who may not
    use it.
    """
    scene = await _scene(client)

    response = await client.post(
        f"/v1/organization-verdicts/org/{scene['org_id']}",
        headers=_bearer_for(scene["member"]),
        json={
            "component_id": str(scene["component_id"]),
            "justification": "we should approve this everywhere",
        },
    )

    assert response.status_code == 404, response.text
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_a_reason_is_required(client) -> None:
    scene = await _scene(client)

    response = await client.post(
        f"/v1/organization-verdicts/org/{scene['org_id']}",
        headers=_bearer_for(scene["admin"]),
        json={"component_id": str(scene["component_id"]), "justification": "ok"},
    )

    assert response.status_code == 422, response.text


# ---------------------------------------------------------------------------
# Organization boundaries
# ---------------------------------------------------------------------------


async def test_an_outsider_cannot_list_another_organizations_rulings(
    client,
) -> None:
    """404, not 403: whether an organization exists is not theirs to probe."""
    theirs = await _scene(client)
    outsider = await _scene(client)
    await _rule(client, theirs, ApprovalStatus.approved)

    response = await client.get(
        f"/v1/organization-verdicts/org/{theirs['org_id']}",
        headers=_bearer_for(outsider["member"]),
    )

    assert response.status_code == 404, response.text


async def test_a_member_reads_their_own_organizations_rulings(client) -> None:
    """Wider than writing on purpose: they have to be able to explain their screen."""
    scene = await _scene(client)
    verdict_id = await _rule(client, scene, ApprovalStatus.approved)

    response = await client.get(
        f"/v1/organization-verdicts/org/{scene['org_id']}",
        headers=_bearer_for(scene["member"]),
    )

    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()["items"]] == [verdict_id]


async def test_an_outsider_cannot_read_another_projects_effective_verdict(
    client,
) -> None:
    theirs = await _scene(client)
    outsider = await _scene(client)
    await _rule(client, theirs, ApprovalStatus.approved)

    response = await client.get(
        f"/v1/organization-verdicts/effective/{theirs['project_id']}/{theirs['component_id']}",
        headers=_bearer_for(outsider["member"]),
    )

    assert response.status_code == 404, response.text


async def test_a_member_reads_the_reason_but_not_the_deliberation(client) -> None:
    """Two different questions, and only one of them is everybody's.

    The published reason explains why a component shows as approved in
    somebody's project, so it goes to the whole organization. The note an
    administrator wrote while deciding, and the names of the people involved,
    explain nothing about the outcome and are held back. The effective
    endpoint had this split from the start and the list did not, which is the
    inconsistency this pins.
    """
    scene = await _scene(client)
    await _rule(client, scene, ApprovalStatus.approved)

    as_member = await client.get(
        f"/v1/organization-verdicts/org/{scene['org_id']}",
        headers=_bearer_for(scene["member"]),
    )
    as_admin = await client.get(
        f"/v1/organization-verdicts/org/{scene['org_id']}",
        headers=_bearer_for(scene["admin"]),
    )

    member_row = as_member.json()["items"][0]
    admin_row = as_admin.json()["items"][0]
    assert member_row["justification"]
    assert "decision_note" not in member_row
    assert "decided_by_user_id" not in member_row
    assert admin_row["decision_note"] == "decided"
    assert admin_row["decided_by_user_id"] is not None


async def test_the_list_is_paged(client) -> None:
    """It is designed to hold a row per package, so it is paged from the start."""
    from models import Component

    scene = await _scene(client)
    factory = await _factory(client)
    async with factory() as session:
        for _ in range(3):
            suffix = unique_suffix()
            session.add(
                Component(
                    purl=f"pkg:npm/paged-{suffix}",
                    package_type="npm",
                    name=f"paged-{suffix}",
                )
            )
        await session.commit()

    headers = _bearer_for(scene["admin"])
    from sqlalchemy import select as _select

    async with factory() as session:
        ids = (
            await session.execute(_select(Component.id).where(Component.name.like("paged-%")))
        ).scalars().all()
    for component_id in ids[:3]:
        await client.post(
            f"/v1/organization-verdicts/org/{scene['org_id']}",
            headers=headers,
            json={
                "component_id": str(component_id),
                "justification": "reviewed centrally for the whole organization",
            },
        )

    first = await client.get(
        f"/v1/organization-verdicts/org/{scene['org_id']}?page=1&page_size=2",
        headers=headers,
    )

    assert first.status_code == 200, first.text
    assert len(first.json()["items"]) <= 2
    assert first.json()["total"] >= 3
    assert first.json()["page_size"] == 2


async def test_a_ruling_on_a_component_that_is_not_there_is_404(client) -> None:
    """Not a 500, and not "a ruling is already open" either."""
    import uuid as _uuid

    scene = await _scene(client)

    response = await client.post(
        f"/v1/organization-verdicts/org/{scene['org_id']}",
        headers=_bearer_for(scene["admin"]),
        json={
            "component_id": str(_uuid.uuid4()),
            "justification": "ruling on something that does not exist",
        },
    )

    assert response.status_code == 404, response.text


# ---------------------------------------------------------------------------
# The workflow
# ---------------------------------------------------------------------------


async def test_a_stale_etag_is_refused(client) -> None:
    scene = await _scene(client)
    headers = _bearer_for(scene["admin"])
    opened = await client.post(
        f"/v1/organization-verdicts/org/{scene['org_id']}",
        headers=headers,
        json={
            "component_id": str(scene["component_id"]),
            "justification": "reviewed centrally for the whole organization",
        },
    )

    response = await client.patch(
        f"/v1/organization-verdicts/{opened.json()['id']}",
        headers={**headers, "If-Match": '"999"'},
        json={"status": ApprovalStatus.under_review, "note": None},
    )

    assert response.status_code == 412, response.text


async def test_a_decided_ruling_is_not_edited_in_place(client) -> None:
    scene = await _scene(client)
    verdict_id = await _rule(client, scene, ApprovalStatus.approved)

    response = await client.patch(
        f"/v1/organization-verdicts/{verdict_id}",
        headers=_bearer_for(scene["admin"]),
        json={"status": ApprovalStatus.rejected, "note": "changed our minds"},
    )

    assert response.status_code == 409, response.text


async def test_a_second_open_ruling_is_refused(client) -> None:
    scene = await _scene(client)
    headers = _bearer_for(scene["admin"])
    body = {
        "component_id": str(scene["component_id"]),
        "justification": "reviewed centrally for the whole organization",
    }
    first = await client.post(
        f"/v1/organization-verdicts/org/{scene['org_id']}", headers=headers, json=body
    )
    assert first.status_code == 201, first.text

    second = await client.post(
        f"/v1/organization-verdicts/org/{scene['org_id']}", headers=headers, json=body
    )

    assert second.status_code == 409, second.text
