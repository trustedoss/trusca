"""
Transition approvals over HTTP.

Two things are worth testing here rather than in the service tests. First, that
naming a status in the policy actually closes both doors: the single-row PATCH
and the bulk transition, because a control that only covers one of them is not
a control. Second, that an approval genuinely moves the finding, since a
workflow that records agreement without applying it would look like it worked
from the queue and leave the finding untouched.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from core.security import CurrentUser, create_access_token
from models import User
from tests._db_required import migrate_to_head
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_scan,
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


async def _seed(client: AsyncClient, *, approval_statuses: list[str] | None = None):
    """One team, two administrators, one finding sitting in ``analyzing``.

    Two administrators because a single one cannot exercise the rule: with one
    person there is nobody to approve, which is the operational consequence of
    turning this on and is asserted separately.
    """
    from models import (
        Component,
        ComponentVersion,
        GatePolicy,
        ScanComponent,
        Vulnerability,
        VulnerabilityFinding,
    )

    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        requester = await make_user(session)
        approver = await make_user(session)
        await make_membership(session, user=requester, team=team, role="team_admin")
        await make_membership(session, user=approver, team=team, role="team_admin")
        project = await make_project(session, team=team)
        scan = await make_scan(session, project=project, status="succeeded")

        if approval_statuses is not None:
            session.add(
                GatePolicy(
                    organization_id=org.id,
                    team_id=None,
                    approval_required_statuses=approval_statuses,
                )
            )

        suffix = unique_suffix()
        purl = f"pkg:npm/appr-api-{suffix}"
        component = Component(purl=purl, package_type="npm", name=f"appr-api-{suffix}")
        session.add(component)
        await session.commit()
        await session.refresh(component)

        version = ComponentVersion(
            component_id=component.id,
            version="1.0.0",
            purl_with_version=f"{purl}@1.0.0",
        )
        session.add(version)
        await session.commit()
        await session.refresh(version)

        session.add(
            ScanComponent(
                scan_id=scan.id,
                component_version_id=version.id,
                direct=True,
                raw_data={},
            )
        )
        vuln = Vulnerability(
            external_id=f"CVE-2024-{suffix}",
            source="NVD",
            severity="high",
            summary="fixture",
        )
        session.add(vuln)
        await session.commit()
        await session.refresh(vuln)

        finding = VulnerabilityFinding(
            scan_id=scan.id,
            component_version_id=version.id,
            vulnerability_id=vuln.id,
            status="analyzing",
        )
        session.add(finding)
        await session.commit()
        await session.refresh(finding)
        return {
            "team_id": team.id,
            "project_id": project.id,
            "finding_id": finding.id,
            "requester": requester,
            "approver": approver,
        }


async def _approval_state(client: AsyncClient, approval_id) -> str:
    """Read the row directly.

    Asserted alongside the response code because a refusal that still records
    agreement is the failure mode worth catching: the caller sees 403 while the
    queue shows the request decided.
    """
    import uuid as _uuid

    from models import TransitionApproval

    factory = await _factory(client)
    async with factory() as session:
        row = await session.get(TransitionApproval, _uuid.UUID(str(approval_id)))
        assert row is not None
        return str(row.state)


async def _status_of(client: AsyncClient, finding_id) -> str:
    from models import VulnerabilityFinding

    factory = await _factory(client)
    async with factory() as session:
        row = await session.get(VulnerabilityFinding, finding_id)
        assert row is not None
        return str(row.status)


# ---------------------------------------------------------------------------
# Both doors, or neither
# ---------------------------------------------------------------------------


async def test_a_named_status_cannot_be_reached_by_the_patch(client) -> None:
    seed = await _seed(client, approval_statuses=["suppressed"])

    response = await client.patch(
        f"/v1/vulnerability_findings/{seed['finding_id']}/status",
        headers=_bearer_for(seed["requester"]),
        json={"status": "suppressed", "justification": "accepted for this release"},
    )

    assert response.status_code == 409, response.text
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    # The marker, not the title: a stale if_match is also a 409 here, and the
    # UI has to tell "reload and retry" apart from "ask somebody".
    assert response.json()["approval_required"] is True
    assert await _status_of(client, seed["finding_id"]) == "analyzing"


async def test_the_bulk_route_is_not_a_way_around_it(client) -> None:
    """The reason this is asserted: one endpoint guarded is zero endpoints guarded."""
    seed = await _seed(client, approval_statuses=["suppressed"])

    response = await client.post(
        f"/v1/projects/{seed['project_id']}/vulnerabilities:bulk-transition",
        headers=_bearer_for(seed["requester"]),
        json={
            "finding_ids": [str(seed["finding_id"])],
            "target_status": "suppressed",
            "justification": "accepted for this release",
        },
    )

    assert response.status_code == 409, response.text
    assert await _status_of(client, seed["finding_id"]) == "analyzing"


async def test_an_unnamed_status_still_moves_in_one_call(client) -> None:
    """The default path. Turning nothing on changes nothing."""
    seed = await _seed(client, approval_statuses=None)

    response = await client.patch(
        f"/v1/vulnerability_findings/{seed['finding_id']}/status",
        headers=_bearer_for(seed["requester"]),
        json={"status": "suppressed", "justification": "accepted for this release"},
    )

    assert response.status_code == 200, response.text
    assert await _status_of(client, seed["finding_id"]) == "suppressed"


# ---------------------------------------------------------------------------
# The round trip
# ---------------------------------------------------------------------------


async def test_a_request_then_somebody_elses_approval_moves_the_finding(client) -> None:
    seed = await _seed(client, approval_statuses=["suppressed"])

    opened = await client.post(
        "/v1/transition-approvals",
        headers=_bearer_for(seed["requester"]),
        json={
            "finding_id": str(seed["finding_id"]),
            "target_status": "suppressed",
            "justification": "accepted for this release",
        },
    )
    assert opened.status_code == 201, opened.text
    assert await _status_of(client, seed["finding_id"]) == "analyzing"

    decided = await client.post(
        f"/v1/transition-approvals/{opened.json()['id']}/decision",
        headers=_bearer_for(seed["approver"]),
        json={"approve": True, "note": "agreed"},
    )

    assert decided.status_code == 200, decided.text
    assert decided.json()["state"] == "approved"
    assert await _status_of(client, seed["finding_id"]) == "suppressed"


async def test_the_requester_is_refused_at_the_endpoint_too(client) -> None:
    seed = await _seed(client, approval_statuses=["suppressed"])
    opened = await client.post(
        "/v1/transition-approvals",
        headers=_bearer_for(seed["requester"]),
        json={
            "finding_id": str(seed["finding_id"]),
            "target_status": "suppressed",
            "justification": "accepted for this release",
        },
    )

    decided = await client.post(
        f"/v1/transition-approvals/{opened.json()['id']}/decision",
        headers=_bearer_for(seed["requester"]),
        json={"approve": True, "note": None},
    )

    assert decided.status_code == 403, decided.text
    # The token, not the sentence: the same 403 covers lacking the grade, and
    # the two ask the reader to do completely different things.
    assert decided.json()["reason"] == "self_decision"
    assert await _approval_state(client, opened.json()["id"]) == "pending"
    assert await _status_of(client, seed["finding_id"]) == "analyzing"


async def test_a_refusal_leaves_the_finding_where_it_was(client) -> None:
    seed = await _seed(client, approval_statuses=["suppressed"])
    opened = await client.post(
        "/v1/transition-approvals",
        headers=_bearer_for(seed["requester"]),
        json={
            "finding_id": str(seed["finding_id"]),
            "target_status": "suppressed",
            "justification": "accepted for this release",
        },
    )

    decided = await client.post(
        f"/v1/transition-approvals/{opened.json()['id']}/decision",
        headers=_bearer_for(seed["approver"]),
        json={"approve": False, "note": "fix it instead"},
    )

    assert decided.status_code == 200, decided.text
    assert decided.json()["state"] == "rejected"
    assert await _status_of(client, seed["finding_id"]) == "analyzing"


async def test_a_status_nobody_asked_to_gate_is_refused_as_a_request(client) -> None:
    """No queue entries for changes the caller can simply make."""
    seed = await _seed(client, approval_statuses=["not_affected"])

    response = await client.post(
        "/v1/transition-approvals",
        headers=_bearer_for(seed["requester"]),
        json={
            "finding_id": str(seed["finding_id"]),
            "target_status": "suppressed",
            "justification": "accepted for this release",
        },
    )

    assert response.status_code == 409, response.text


async def test_an_approval_is_spent_once_and_not_reusable(client) -> None:
    """An agreement authorises one change, not that change for ever.

    A finding reopened after a suppression could otherwise be suppressed again
    on the strength of the older agreement, with nobody asked a second time.
    """
    from services.vulnerability_service import (
        VulnerabilityApprovalRequired,
        update_vulnerability_status,
    )

    seed = await _seed(client, approval_statuses=["suppressed"])
    opened = await client.post(
        "/v1/transition-approvals",
        headers=_bearer_for(seed["requester"]),
        json={
            "finding_id": str(seed["finding_id"]),
            "target_status": "suppressed",
            "justification": "accepted for this release",
        },
    )
    decided = await client.post(
        f"/v1/transition-approvals/{opened.json()['id']}/decision",
        headers=_bearer_for(seed["approver"]),
        json={"approve": True, "note": None},
    )
    assert decided.status_code == 200, decided.text
    assert await _status_of(client, seed["finding_id"]) == "suppressed"

    # Re-triage: the finding is open again, and the old agreement is spent.
    reopened = await client.patch(
        f"/v1/vulnerability_findings/{seed['finding_id']}/status",
        headers=_bearer_for(seed["requester"]),
        json={"status": "analyzing", "justification": "looking at this again"},
    )
    assert reopened.status_code == 200, reopened.text

    factory = await _factory(client)
    async with factory() as session:
        actor = CurrentUser(
            id=seed["approver"].id,
            email=seed["approver"].email,
            role="team_admin",
            team_ids=[seed["team_id"]],
            team_roles={seed["team_id"]: "team_admin"},
            is_active=True,
            is_superuser=False,
        )
        with pytest.raises(VulnerabilityApprovalRequired):
            await update_vulnerability_status(
                session,
                finding_id=seed["finding_id"],
                actor=actor,
                target_status="suppressed",
                justification="reusing the agreement from before",
                approved_request_id=uuid.UUID(opened.json()["id"]),
            )

    assert await _status_of(client, seed["finding_id"]) == "analyzing"


async def test_an_approval_is_spent_on_the_finding_it_was_granted_for(
    client,
) -> None:
    """An agreement is not a token that opens any door.

    The apply path takes an approval id, and the row behind it is checked
    against this finding and this status. Without that check an agreement to
    suppress one finding would let a second one through, which is the whole
    control failing quietly.
    """
    from services.vulnerability_service import update_vulnerability_status

    granted = await _seed(client, approval_statuses=["suppressed"])
    other = await _seed(client, approval_statuses=["suppressed"])
    opened = await client.post(
        "/v1/transition-approvals",
        headers=_bearer_for(granted["requester"]),
        json={
            "finding_id": str(granted["finding_id"]),
            "target_status": "suppressed",
            "justification": "accepted for this release",
        },
    )
    decided = await client.post(
        f"/v1/transition-approvals/{opened.json()['id']}/decision",
        headers=_bearer_for(granted["approver"]),
        json={"approve": True, "note": None},
    )
    assert decided.status_code == 200, decided.text

    # The service is called directly: no route offers this parameter, which is
    # itself part of the design. The test guards the function against a future
    # caller that does.
    from core.security import CurrentUser
    from services.vulnerability_service import VulnerabilityApprovalRequired

    factory = await _factory(client)
    async with factory() as session:
        actor = CurrentUser(
            id=other["approver"].id,
            email=other["approver"].email,
            role="team_admin",
            team_ids=[other["team_id"]],
            team_roles={other["team_id"]: "team_admin"},
            is_active=True,
            is_superuser=False,
        )
        with pytest.raises(VulnerabilityApprovalRequired):
            await update_vulnerability_status(
                session,
                finding_id=other["finding_id"],
                actor=actor,
                target_status="suppressed",
                justification="reusing somebody else's approval",
                approved_request_id=uuid.UUID(opened.json()["id"]),
            )

    assert await _status_of(client, other["finding_id"]) == "analyzing"


# ---------------------------------------------------------------------------
# Who sees and who decides
# ---------------------------------------------------------------------------


async def test_another_teams_request_is_not_in_the_queue(client) -> None:
    mine = await _seed(client, approval_statuses=["suppressed"])
    theirs = await _seed(client, approval_statuses=["suppressed"])
    await client.post(
        "/v1/transition-approvals",
        headers=_bearer_for(theirs["requester"]),
        json={
            "finding_id": str(theirs["finding_id"]),
            "target_status": "suppressed",
            "justification": "accepted for this release",
        },
    )

    queue = await client.get(
        "/v1/transition-approvals", headers=_bearer_for(mine["requester"])
    )

    assert queue.status_code == 200, queue.text
    assert all(
        item["finding_id"] != str(theirs["finding_id"]) for item in queue.json()["items"]
    )


async def test_a_developer_cannot_decide_even_where_they_could_transition(
    client,
) -> None:
    """The narrow case that shows deciding is its own rule.

    ``not_affected`` needs no particular grade to set directly, so a developer
    who could make this change alone still may not be the one who agrees to it
    once the policy has asked for a second person. Testing this with a status
    that already requires an administrator would prove nothing: the transition
    matrix would refuse it either way.
    """
    from models import Team

    seed = await _seed(client, approval_statuses=["not_affected"])
    factory = await _factory(client)
    async with factory() as session:
        requesting_dev = await make_user(session)
        deciding_dev = await make_user(session)
        team = await session.get(Team, seed["team_id"])
        assert team is not None
        await make_membership(session, user=requesting_dev, team=team, role="developer")
        await make_membership(session, user=deciding_dev, team=team, role="developer")

    opened = await client.post(
        "/v1/transition-approvals",
        headers=_bearer_for(requesting_dev),
        json={
            "finding_id": str(seed["finding_id"]),
            "target_status": "not_affected",
            "justification": "the vulnerable path is never reached",
        },
    )
    assert opened.status_code == 201, opened.text

    decided = await client.post(
        f"/v1/transition-approvals/{opened.json()['id']}/decision",
        headers=_bearer_for(deciding_dev),
        json={"approve": True, "note": None},
    )

    assert decided.status_code == 403, decided.text
    assert decided.json()["reason"] == "not_team_admin"
    assert await _approval_state(client, opened.json()["id"]) == "pending"
    assert await _status_of(client, seed["finding_id"]) == "analyzing"


async def test_a_finding_that_moved_meanwhile_leaves_the_request_pending(
    client,
) -> None:
    """Approving is not a promise the change will still be legal.

    Requests sit in a queue, and findings keep being worked on while they sit.
    If the transition is refused at apply time the request must stay pending
    rather than reading as approved, or the queue records an agreement to
    something that never happened.
    """
    seed = await _seed(client, approval_statuses=["suppressed"])
    opened = await client.post(
        "/v1/transition-approvals",
        headers=_bearer_for(seed["requester"]),
        json={
            "finding_id": str(seed["finding_id"]),
            "target_status": "suppressed",
            "justification": "accepted for this release",
        },
    )
    moved = await client.patch(
        f"/v1/vulnerability_findings/{seed['finding_id']}/status",
        headers=_bearer_for(seed["requester"]),
        json={"status": "exploitable", "justification": "reachable after all"},
    )
    assert moved.status_code == 200, moved.text

    decided = await client.post(
        f"/v1/transition-approvals/{opened.json()['id']}/decision",
        headers=_bearer_for(seed["approver"]),
        json={"approve": True, "note": "agreed"},
    )

    assert decided.status_code == 422, decided.text
    assert await _approval_state(client, opened.json()["id"]) == "pending"
    assert await _status_of(client, seed["finding_id"]) == "exploitable"


async def test_another_teams_finding_cannot_be_requested(client) -> None:
    """404, and no row: an outsider may not seed another team's queue either."""
    theirs = await _seed(client, approval_statuses=["suppressed"])
    outsider = await _seed(client, approval_statuses=["suppressed"])

    response = await client.post(
        "/v1/transition-approvals",
        headers=_bearer_for(outsider["requester"]),
        json={
            "finding_id": str(theirs["finding_id"]),
            "target_status": "suppressed",
            "justification": "accepted for this release",
        },
    )

    assert response.status_code == 404, response.text
    queue = await client.get(
        "/v1/transition-approvals", headers=_bearer_for(theirs["approver"])
    )
    assert queue.json()["items"] == []


async def test_another_teams_request_cannot_be_decided(client) -> None:
    """404 rather than 403: an outsider learns nothing about what exists."""
    theirs = await _seed(client, approval_statuses=["suppressed"])
    outsider_seed = await _seed(client, approval_statuses=["suppressed"])
    opened = await client.post(
        "/v1/transition-approvals",
        headers=_bearer_for(theirs["requester"]),
        json={
            "finding_id": str(theirs["finding_id"]),
            "target_status": "suppressed",
            "justification": "accepted for this release",
        },
    )

    decided = await client.post(
        f"/v1/transition-approvals/{opened.json()['id']}/decision",
        headers=_bearer_for(outsider_seed["approver"]),
        json={"approve": True, "note": None},
    )

    assert decided.status_code == 404, decided.text
    assert await _status_of(client, theirs["finding_id"]) == "analyzing"


async def test_a_developer_cannot_decide(client) -> None:
    """Agreeing to accept a risk stays an administrator's act."""
    seed = await _seed(client, approval_statuses=["suppressed"])
    from models import Team

    factory = await _factory(client)
    async with factory() as session:
        developer = await make_user(session)
        team = await session.get(Team, seed["team_id"])
        assert team is not None
        await make_membership(session, user=developer, team=team, role="developer")

    opened = await client.post(
        "/v1/transition-approvals",
        headers=_bearer_for(seed["requester"]),
        json={
            "finding_id": str(seed["finding_id"]),
            "target_status": "suppressed",
            "justification": "accepted for this release",
        },
    )

    decided = await client.post(
        f"/v1/transition-approvals/{opened.json()['id']}/decision",
        headers=_bearer_for(developer),
        json={"approve": True, "note": None},
    )

    assert decided.status_code == 403, decided.text
    assert decided.json()["reason"] == "not_team_admin"
    assert await _approval_state(client, opened.json()["id"]) == "pending"
    assert await _status_of(client, seed["finding_id"]) == "analyzing"
