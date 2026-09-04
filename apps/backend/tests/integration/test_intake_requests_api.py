"""
Asking before using, and the fact that most deployments will not.

The first tests are about the surface not existing. A feature that changes how
an organization works has to be something they turn on, and "off" has to mean
the routes are gone rather than present and refusing: a 403 tells somebody they
lack permission for something their organization never adopted, and they go and
ask for the permission.

The sequence after that is the one the feature exists for: ask, get an answer,
and then have a scan find the package and not ask again.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
from httpx import ASGITransport, AsyncClient

from core.security import create_access_token
from models import User
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
def enabled() -> Iterator[None]:
    """Turn the surface on for one test.

    Set and cleared around each test rather than for the module, because the
    off state is a contract in its own right and the tests that assert it must
    not depend on running before the ones that turn it on.
    """
    previous = os.environ.get("INTAKE_REQUESTS_ENABLED")
    os.environ["INTAKE_REQUESTS_ENABLED"] = "true"
    yield
    if previous is None:
        os.environ.pop("INTAKE_REQUESTS_ENABLED", None)
    else:
        os.environ["INTAKE_REQUESTS_ENABLED"] = previous


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


async def _seed(client: AsyncClient, *, role: str = "team_admin"):
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session)
        await make_membership(session, user=user, team=team, role=role)
        project = await make_project(session, team=team)
        return team, user, project


async def _ask(
    client: AsyncClient, *, user: User, project_id: uuid.UUID, purl: str
) -> dict:
    response = await client.post(
        "/v1/intake-requests",
        headers=_bearer_for(user),
        json={
            "project_id": str(project_id),
            "purl": purl,
            "justification": "we need a date library and this one is maintained",
        },
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


# ---------------------------------------------------------------------------
# Off, which is what almost every deployment gets
# ---------------------------------------------------------------------------


async def test_asking_is_not_possible_when_the_deployment_has_not_asked_for_it(
    client,
) -> None:
    """404, not 403.

    A refusal that reads as a permission problem sends somebody to an
    administrator for a permission that does not exist, because the feature
    their organization never adopted is not a thing anybody can be granted.
    """
    _team, user, project = await _seed(client)

    response = await client.post(
        "/v1/intake-requests",
        headers=_bearer_for(user),
        json={
            "project_id": str(project.id),
            "purl": "pkg:npm/lodash",
            "justification": "we need a date library and this one is maintained",
        },
    )

    assert response.status_code == 404, response.text
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_the_queue_is_not_readable_when_off(client) -> None:
    _team, user, _project = await _seed(client)

    response = await client.get("/v1/intake-requests", headers=_bearer_for(user))

    assert response.status_code == 404, response.text


async def test_turning_it_off_again_closes_the_surface(client, enabled) -> None:
    """Existing rows stay; the surface stops answering.

    A deployment that tries the queue and decides against it should be able to
    stop without the rows becoming unreachable garbage in the meantime.
    """
    _team, user, project = await _seed(client)
    await _ask(client, user=user, project_id=project.id, purl="pkg:npm/lodash")

    os.environ["INTAKE_REQUESTS_ENABLED"] = "false"
    listed = await client.get("/v1/intake-requests", headers=_bearer_for(user))
    os.environ["INTAKE_REQUESTS_ENABLED"] = "true"
    listed_again = await client.get("/v1/intake-requests", headers=_bearer_for(user))

    assert listed.status_code == 404, listed.text
    assert listed_again.status_code == 200, listed_again.text
    assert listed_again.json()["total"] >= 1


# ---------------------------------------------------------------------------
# Asking and answering
# ---------------------------------------------------------------------------


async def test_the_lowest_grade_may_ask(client, enabled) -> None:
    """The person who wants the dependency is usually not the one who decides.

    A queue only an administrator can file into is a queue people route
    around, which leaves the decision where it was before: in a pull request.
    """
    _team, user, project = await _seed(client, role="viewer")

    response = await client.post(
        "/v1/intake-requests",
        headers=_bearer_for(user),
        json={
            "project_id": str(project.id),
            "purl": "pkg:npm/lodash",
            "justification": "we need a date library and this one is maintained",
        },
    )

    assert response.status_code == 201, response.text


async def test_the_lowest_grade_may_not_answer(client, enabled) -> None:
    """Answering is the same judgement an approval takes, so it takes the same grade."""
    _team, admin, project = await _seed(client)
    factory = await _factory(client)
    async with factory() as session:
        from models import Team

        team_row = await session.get(Team, project.team_id)
        assert team_row is not None
        asker = await make_user(session)
        await make_membership(session, user=asker, team=team_row, role="viewer")
    request = await _ask(client, user=admin, project_id=project.id, purl="pkg:npm/lodash")

    response = await client.patch(
        f"/v1/intake-requests/{request['id']}",
        headers=_bearer_for(asker),
        json={"status": "under_review", "note": None},
    )

    assert response.status_code == 403, response.text


async def test_one_open_request_per_package_and_project(client, enabled) -> None:
    """Two people asking the same thing is one question to the reviewer."""
    _team, user, project = await _seed(client)
    await _ask(client, user=user, project_id=project.id, purl="pkg:npm/lodash")

    second = await client.post(
        "/v1/intake-requests",
        headers=_bearer_for(user),
        json={
            "project_id": str(project.id),
            "purl": "pkg:npm/lodash",
            "justification": "asking again because I did not look first",
        },
    )

    assert second.status_code == 409, second.text


async def test_a_name_that_is_not_a_purl_is_refused(client, enabled) -> None:
    """The purl is what a later scan matches the answer against.

    A request carrying "lodash" would be answered and then never found again,
    and the asker would be asked a second time with nothing to explain why.
    """
    _team, user, project = await _seed(client)

    response = await client.post(
        "/v1/intake-requests",
        headers=_bearer_for(user),
        json={
            "project_id": str(project.id),
            "purl": "lodash",
            "justification": "we need a date library and this one is maintained",
        },
    )

    assert response.status_code == 422, response.text


async def test_a_decided_request_is_not_edited_in_place(client, enabled) -> None:
    _team, user, project = await _seed(client)
    request = await _ask(client, user=user, project_id=project.id, purl="pkg:npm/lodash")
    headers = _bearer_for(user)
    reviewing = await client.patch(
        f"/v1/intake-requests/{request['id']}",
        headers=headers,
        json={"status": "under_review", "note": None},
    )
    decided = await client.patch(
        f"/v1/intake-requests/{request['id']}",
        headers={**headers, "If-Match": f'"{reviewing.json()["version"]}"'},
        json={"status": "approved", "note": "fine"},
    )

    again = await client.patch(
        f"/v1/intake-requests/{request['id']}",
        headers=headers,
        json={"status": "rejected", "note": "changed our minds"},
    )

    assert decided.status_code == 200, decided.text
    assert again.status_code == 409, again.text


async def test_another_teams_queue_is_not_visible(client, enabled) -> None:
    _team, owner, project = await _seed(client)
    await _ask(client, user=owner, project_id=project.id, purl="pkg:npm/lodash")
    _other, outsider, _op = await _seed(client)

    listed = await client.get("/v1/intake-requests", headers=_bearer_for(outsider))

    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 0


# ---------------------------------------------------------------------------
# The sequence the feature exists for
# ---------------------------------------------------------------------------


async def test_an_answer_given_early_carries_onto_the_scan_that_finds_it(
    client, enabled
) -> None:
    """Ask, get an answer, then have a scan find the package.

    Without the carry-over the reward for following the process is answering
    the same question twice, and the second answer is the one the build gate
    reads. Somebody who did the right thing would see a Pending row appear for
    a package their team already cleared.
    """
    from sqlalchemy import select as sync_select

    from models import Component, ComponentApproval
    from services.component_approval_service import (
        apply_intake_decisions,
        auto_create_pending_approvals,
    )

    _team, user, project = await _seed(client)
    purl = f"pkg:npm/carried-{unique_suffix()}"
    request = await _ask(client, user=user, project_id=project.id, purl=purl)
    headers = _bearer_for(user)
    reviewing = await client.patch(
        f"/v1/intake-requests/{request['id']}",
        headers=headers,
        json={"status": "under_review", "note": None},
    )
    approved = await client.patch(
        f"/v1/intake-requests/{request['id']}",
        headers={**headers, "If-Match": f'"{reviewing.json()["version"]}"'},
        json={"status": "approved", "note": "cleared before we pulled it in"},
    )
    assert approved.status_code == 200, approved.text

    # Now the package actually arrives, the way the scan pipeline sees it.
    from core.db import sync_session_scope

    with sync_session_scope() as session:
        component = Component(
            purl=purl, package_type="npm", name=purl.split("/")[-1]
        )
        session.add(component)
        session.flush()
        created = auto_create_pending_approvals(
            session,
            project_id=project.id,
            team_id=project.team_id,
            component_ids=[component.id],
        )
        assert created, "the scan should have opened an approval to carry onto"
        carried = apply_intake_decisions(
            session, project_id=project.id, created_component_ids=created
        )
        session.commit()

        status = session.execute(
            sync_select(ComponentApproval.status).where(
                ComponentApproval.component_id == component.id,
                ComponentApproval.project_id == project.id,
            )
        ).scalar_one()

    assert carried == 1
    assert status == "approved"


async def test_an_unanswered_request_carries_nothing(client, enabled) -> None:
    """A question still being argued about is not an answer.

    Carrying it would mark a component approved because somebody had opened a
    review, which is the opposite of what opening a review means.
    """
    from sqlalchemy import select as sync_select

    from models import Component, ComponentApproval
    from services.component_approval_service import (
        apply_intake_decisions,
        auto_create_pending_approvals,
    )

    _team, user, project = await _seed(client)
    purl = f"pkg:npm/pending-{unique_suffix()}"
    await _ask(client, user=user, project_id=project.id, purl=purl)

    from core.db import sync_session_scope

    with sync_session_scope() as session:
        component = Component(
            purl=purl, package_type="npm", name=purl.split("/")[-1]
        )
        session.add(component)
        session.flush()
        created = auto_create_pending_approvals(
            session,
            project_id=project.id,
            team_id=project.team_id,
            component_ids=[component.id],
        )
        carried = apply_intake_decisions(
            session, project_id=project.id, created_component_ids=created
        )
        session.commit()

        status = session.execute(
            sync_select(ComponentApproval.status).where(
                ComponentApproval.component_id == component.id,
                ComponentApproval.project_id == project.id,
            )
        ).scalar_one()

    assert carried == 0
    assert status == "pending"


async def test_a_refusal_carries_too(client, enabled) -> None:
    """Both answers are answers. Carrying only the approvals would mean a
    package the team refused arrives as a fresh question."""
    from sqlalchemy import select as sync_select

    from models import Component, ComponentApproval
    from services.component_approval_service import (
        apply_intake_decisions,
        auto_create_pending_approvals,
    )

    _team, user, project = await _seed(client)
    purl = f"pkg:npm/refused-{unique_suffix()}"
    request = await _ask(client, user=user, project_id=project.id, purl=purl)
    await client.patch(
        f"/v1/intake-requests/{request['id']}",
        headers=_bearer_for(user),
        json={"status": "rejected", "note": "use the one we already have"},
    )

    from core.db import sync_session_scope

    with sync_session_scope() as session:
        component = Component(
            purl=purl, package_type="npm", name=purl.split("/")[-1]
        )
        session.add(component)
        session.flush()
        created = auto_create_pending_approvals(
            session,
            project_id=project.id,
            team_id=project.team_id,
            component_ids=[component.id],
        )
        apply_intake_decisions(session, project_id=project.id, created_component_ids=created)
        session.commit()

        status = session.execute(
            sync_select(ComponentApproval.status).where(
                ComponentApproval.component_id == component.id,
                ComponentApproval.project_id == project.id,
            )
        ).scalar_one()

    assert status == "rejected"
