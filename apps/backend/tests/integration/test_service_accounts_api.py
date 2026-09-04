"""
Credentials that outlive the person who created them, and the rule they leave
alone.

Two tests carry this file. One is the sequence the whole feature exists for:
create an identity, issue it a key, deactivate the person who created it, and
watch the key keep working. The other is its mirror: a personal key still stops
when its issuer is deactivated. Breaking the second while fixing the first is
the failure that would pass a suite testing only the new path, and it would
hand every departed employee's key back to whoever holds it.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from core.security import create_access_token
from models import User
from tests._db_required import migrate_to_head
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_team,
    make_user,
    strong_password,
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


async def _seed(client: AsyncClient, *, role: str = "team_admin"):
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session)
        await make_membership(session, user=user, team=team, role=role)
        project = await make_project(session, team=team)
        return team, user, project


async def _create_account(client: AsyncClient, *, user: User, team_id: uuid.UUID):
    response = await client.post(
        "/v1/service-accounts",
        headers=_bearer_for(user),
        json={
            "team_id": str(team_id),
            "slug": f"ci-{unique_suffix()}",
            "display_name": "Nightly build",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _issue_key(
    client: AsyncClient,
    *,
    user: User,
    project_id: uuid.UUID,
    service_account_id: str | None = None,
) -> str:
    body: dict[str, object] = {
        "name": "ci",
        "scope": "project",
        "project_id": str(project_id),
        "permission_breadth": "read_write",
    }
    if service_account_id is not None:
        body["service_account_id"] = service_account_id
    response = await client.post("/v1/api-keys", json=body, headers=_bearer_for(user))
    assert response.status_code == 201, response.text
    return str(response.json()["raw_key"])


async def _deactivate(client: AsyncClient, user_id: uuid.UUID) -> None:
    factory = await _factory(client)
    async with factory() as session:
        await session.execute(
            text("UPDATE users SET is_active = false WHERE id = :id"),
            {"id": user_id},
        )
        await session.commit()


async def _trigger(client: AsyncClient, raw_key: str, project_id: uuid.UUID) -> str:
    response = await client.post(
        f"/v1/projects/{project_id}/scans",
        json={"kind": "source"},
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    assert response.status_code == 202, response.text
    return str(response.json()["id"])


async def _key_authenticates(client: AsyncClient, raw_key: str, scan_id: str) -> bool:
    """Whether the key still gets through the auth path.

    A read rather than a second scan trigger: triggering twice on one project
    hits the scan-in-progress conflict, and a test that read that 409 as "the
    key is dead" would report the feature broken for a reason that has nothing
    to do with it.
    """
    response = await client.get(
        f"/v1/scans/{scan_id}", headers={"Authorization": f"Bearer {raw_key}"}
    )
    if response.status_code == 200:
        return True
    assert response.status_code == 401, response.text
    return False


# ---------------------------------------------------------------------------
# The sequence the feature exists for
# ---------------------------------------------------------------------------


async def test_a_service_account_key_survives_its_creator_leaving(client) -> None:
    """The nightly build does not stop because its author left.

    Create, issue, deactivate the person, use the key. Every step matters: a
    version that only checked the key at issuance time would pass while the
    thing the feature promises never happened.
    """
    team, creator, project = await _seed(client)
    account = await _create_account(client, user=creator, team_id=team.id)
    raw_key = await _issue_key(
        client, user=creator, project_id=project.id, service_account_id=account["id"]
    )
    scan_id = await _trigger(client, raw_key, project.id)

    await _deactivate(client, creator.id)

    assert await _key_authenticates(client, raw_key, scan_id)


async def test_a_personal_key_still_stops_when_its_issuer_leaves(client) -> None:
    """The rule this feature did not change, and must not have.

    A branch that loosened the lifetime rule for everyone rather than for
    service accounts would hand every departed employee's key back to whoever
    holds it, and a suite testing only the new path would not notice.
    """
    _team, user, project = await _seed(client)
    raw_key = await _issue_key(client, user=user, project_id=project.id)
    scan_id = await _trigger(client, raw_key, project.id)

    await _deactivate(client, user.id)

    assert not await _key_authenticates(client, raw_key, scan_id)


async def test_deactivating_the_account_stops_its_keys(client) -> None:
    """The counterpart to loosening the rule.

    Keys no longer stop when a person leaves, so there has to be a deliberate
    way to stop them, and it has to be one action rather than a hunt through
    the key list.
    """
    team, creator, project = await _seed(client)
    account = await _create_account(client, user=creator, team_id=team.id)
    raw_key = await _issue_key(
        client, user=creator, project_id=project.id, service_account_id=account["id"]
    )
    scan_id = await _trigger(client, raw_key, project.id)

    response = await client.delete(
        f"/v1/service-accounts/{account['id']}", headers=_bearer_for(creator)
    )

    assert response.status_code == 200, response.text
    assert not await _key_authenticates(client, raw_key, scan_id)


# ---------------------------------------------------------------------------
# Not a person
# ---------------------------------------------------------------------------


async def test_a_service_account_cannot_log_in(client) -> None:
    """The one real risk of sharing a table with people.

    Tried with the address it carries and a password nobody set, which is the
    shape an attacker would use. The refusal is the same 401 a wrong password
    gets, so the response cannot be used to find which addresses are automation.
    """
    team, creator, _project = await _seed(client)
    account = await _create_account(client, user=creator, team_id=team.id)

    response = await client.post(
        "/auth/login",
        json={"email": account["email"], "password": strong_password()},
    )

    assert response.status_code == 401, response.text


async def test_the_login_refusal_does_not_rest_on_the_unusable_password(
    client,
) -> None:
    """The guard, tested where it is the only thing acting.

    A service account's password hash is unusable, so the login form refuses
    it whether or not the explicit check exists, and a test through HTTP would
    pass with the check deleted. This gives one a password that genuinely
    verifies and asserts it is still refused, so the guard is what is holding
    rather than the hash.
    """
    from core.security import hash_password
    from services.auth_service import authenticate

    team, creator, _project = await _seed(client)
    account = await _create_account(client, user=creator, team_id=team.id)
    password = strong_password()
    factory = await _factory(client)
    async with factory() as session:
        await session.execute(
            text("UPDATE users SET hashed_password = :h WHERE id = :id"),
            {"h": hash_password(password), "id": uuid.UUID(account["id"])},
        )
        await session.commit()

        resolved = await authenticate(
            session, email=account["email"], password=password
        )

    assert resolved is None


async def test_a_service_account_cannot_be_sent_a_password_reset(client) -> None:
    """There is nobody to send it to, and the answer must not say so."""
    team, creator, _project = await _seed(client)
    account = await _create_account(client, user=creator, team_id=team.id)

    response = await client.post(
        "/auth/forgot-password", json={"email": account["email"]}
    )

    # The endpoint answers the same way for every address on purpose, so this
    # asserts the outcome rather than the wording: no reset row was created.
    assert response.status_code in (200, 202, 204), response.text
    factory = await _factory(client)
    async with factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT count(*) FROM password_reset_tokens WHERE user_id = :id"
                ),
                {"id": uuid.UUID(account["id"])},
            )
        ).scalar_one()
    assert rows == 0


async def test_a_service_account_is_not_in_the_admin_user_list(client) -> None:
    """The list offers actions that are wrong for it.

    There is nobody to email a reset to, and deactivating one from a leavers
    screen is a pipeline outage that reads on screen as tidying up.
    """
    team, creator, _project = await _seed(client)
    account = await _create_account(client, user=creator, team_id=team.id)
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)

    listed = await client.get("/v1/admin/users?page_size=200", headers=_bearer_for(admin))

    assert listed.status_code == 200, listed.text
    assert all(item["id"] != account["id"] for item in listed.json()["items"])


async def test_a_service_account_cannot_be_added_from_the_team_members_surface(
    client,
) -> None:
    """Its reach is set where it was created, not from a staffing screen.

    That screen is built for who works here, and widening what a credential
    can touch from it would happen with nothing on the page saying so.
    """
    team, creator, _project = await _seed(client)
    account = await _create_account(client, user=creator, team_id=team.id)
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)
        other_org = await make_organization(session)
        other_team = await make_team(session, organization=other_org)
        other_team_id = other_team.id

    response = await client.post(
        f"/v1/admin/teams/{other_team_id}/members",
        headers=_bearer_for(admin),
        json={"user_id": account["id"], "role": "team_admin"},
    )

    assert response.status_code == 404, response.text


async def test_the_team_page_shows_an_automation_identity_as_one(client) -> None:
    """Shown, because its role is real reach; labelled, so nobody writes to it."""
    team, creator, _project = await _seed(client)
    account = await _create_account(client, user=creator, team_id=team.id)
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)

    detail = await client.get(f"/v1/admin/teams/{team.id}", headers=_bearer_for(admin))

    assert detail.status_code == 200, detail.text
    member = next(
        m for m in detail.json()["members"] if m["user_id"] == account["id"]
    )
    assert member["is_service_account"] is True
    assert all(
        m["is_service_account"] is False
        for m in detail.json()["members"]
        if m["user_id"] != account["id"]
    )


async def test_removing_its_membership_would_strand_it_so_it_is_refused(
    client,
) -> None:
    """The service-accounts surface finds an account through its team.

    Remove the membership and the account answers 404 to everybody while its
    keys go on authenticating: live credentials nobody can reach the stop
    button for. The removal is refused on the staffing screen, where nothing
    would have said that is what happened.
    """
    team, creator, _project = await _seed(client)
    account = await _create_account(client, user=creator, team_id=team.id)
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)

    removed = await client.delete(
        f"/v1/admin/teams/{team.id}/members/{account['id']}",
        headers=_bearer_for(admin),
    )

    assert removed.status_code == 404, removed.text
    still_there = await client.get(
        f"/v1/service-accounts?team_id={team.id}", headers=_bearer_for(creator)
    )
    assert any(
        item["id"] == account["id"] for item in still_there.json()["items"]
    )


# ---------------------------------------------------------------------------
# Stewardship
# ---------------------------------------------------------------------------


async def test_an_unowned_account_may_not_be_given_more_keys(client) -> None:
    """Existing keys keep working. Handing it more credentials does not.

    The refusal is what prompts somebody to take the account over, which is
    the only thing that brings a name back against it.
    """
    team, creator, project = await _seed(client)
    account = await _create_account(client, user=creator, team_id=team.id)
    factory = await _factory(client)
    async with factory() as session:
        other_admin = await make_user(session)
        from models import Team

        team_row = await session.get(Team, team.id)
        assert team_row is not None
        await make_membership(
            session, user=other_admin, team=team_row, role="team_admin"
        )
    raw_key = await _issue_key(
        client, user=creator, project_id=project.id, service_account_id=account["id"]
    )
    scan_id = await _trigger(client, raw_key, project.id)

    await _deactivate(client, creator.id)

    refused = await client.post(
        "/v1/api-keys",
        headers=_bearer_for(other_admin),
        json={
            "name": "another",
            "scope": "project",
            "project_id": str(project.id),
            "service_account_id": account["id"],
        },
    )

    assert refused.status_code == 409, refused.text
    # And the existing one is untouched, which is the half that matters.
    assert await _key_authenticates(client, raw_key, scan_id)


async def test_assigning_a_steward_lets_it_issue_again(client) -> None:
    """Succession, and the keys do not change hands with it."""
    team, creator, project = await _seed(client)
    account = await _create_account(client, user=creator, team_id=team.id)
    factory = await _factory(client)
    async with factory() as session:
        successor = await make_user(session)
        from models import Team

        team_row = await session.get(Team, team.id)
        assert team_row is not None
        await make_membership(session, user=successor, team=team_row, role="team_admin")
    await _deactivate(client, creator.id)

    assigned = await client.put(
        f"/v1/service-accounts/{account['id']}/steward",
        headers=_bearer_for(successor),
        json={"steward_user_id": str(successor.id)},
    )
    issued = await client.post(
        "/v1/api-keys",
        headers=_bearer_for(successor),
        json={
            "name": "another",
            "scope": "project",
            "project_id": str(project.id),
            "service_account_id": account["id"],
        },
    )

    assert assigned.status_code == 200, assigned.text
    assert issued.status_code == 201, issued.text


async def test_a_steward_must_belong_to_the_accounts_team(client) -> None:
    """Otherwise the gate is satisfied by a name rather than by a person.

    An administrator facing "no new keys until somebody owns it" would clear
    it by pointing at any active account in the deployment, and the person
    named would never learn they had been made answerable for a credential.
    """
    team, creator, _project = await _seed(client)
    account = await _create_account(client, user=creator, team_id=team.id)
    _other_team, outsider, _op = await _seed(client)

    response = await client.put(
        f"/v1/service-accounts/{account['id']}/steward",
        headers=_bearer_for(creator),
        json={"steward_user_id": str(outsider.id)},
    )

    assert response.status_code == 422, response.text


async def test_a_steward_who_leaves_the_team_stops_counting(client) -> None:
    """Re-checked at issuance, not only when the steward was set.

    A gate that held at the moment it was configured and never again would let
    an account go on minting credentials with nobody who could be asked about
    them.
    """
    team, creator, project = await _seed(client)
    account = await _create_account(client, user=creator, team_id=team.id)
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)
        # A second administrator, because the team refuses to lose its last
        # one while other members remain. Without this the removal below is
        # rejected and the test would pass on the steward never having left.
        from models import Team

        team_row = await session.get(Team, team.id)
        assert team_row is not None
        successor = await make_user(session)
        await make_membership(
            session, user=successor, team=team_row, role="team_admin"
        )

    removed = await client.delete(
        f"/v1/admin/teams/{team.id}/members/{creator.id}",
        headers=_bearer_for(admin),
    )
    assert removed.status_code in (200, 204), removed.text

    refused = await client.post(
        "/v1/api-keys",
        headers=_bearer_for(admin),
        json={
            "name": "another",
            "scope": "project",
            "project_id": str(project.id),
            "service_account_id": account["id"],
        },
    )

    assert refused.status_code == 409, refused.text


async def test_a_service_account_cannot_be_a_steward(client) -> None:
    """Otherwise a chain of them vouches for each other with nobody at the end."""
    team, creator, _project = await _seed(client)
    account = await _create_account(client, user=creator, team_id=team.id)
    other = await _create_account(client, user=creator, team_id=team.id)

    response = await client.put(
        f"/v1/service-accounts/{account['id']}/steward",
        headers=_bearer_for(creator),
        json={"steward_user_id": other["id"]},
    )

    assert response.status_code == 422, response.text


async def test_the_admin_user_surface_will_not_touch_one(client) -> None:
    """The role endpoint could otherwise make an automation identity a super
    admin, and the list filter would then hide the result from the one screen
    anybody sweeps afterwards.

    Every operation on that surface shares one loader, so this checks the
    loader rather than each endpoint: detail, role, deactivate and activate all
    answer the same way.
    """
    team, creator, _project = await _seed(client)
    account = await _create_account(client, user=creator, team_id=team.id)
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)
    headers = _bearer_for(admin)

    detail = await client.get(f"/v1/admin/users/{account['id']}", headers=headers)
    escalate = await client.patch(
        f"/v1/admin/users/{account['id']}/role",
        headers=headers,
        json={"role": "super_admin"},
    )

    assert detail.status_code == 404, detail.text
    assert escalate.status_code == 404, escalate.text


async def test_the_database_refuses_a_superuser_automation_identity(
    client,
) -> None:
    """The backstop under the loader.

    The escalation it prevents produces a non-expiring org-wide key whose
    issuer outlives every session involved in making it, so it is worth
    stating where no future code path can talk its way past.
    """
    from sqlalchemy.exc import IntegrityError

    team, creator, _project = await _seed(client)
    account = await _create_account(client, user=creator, team_id=team.id)
    factory = await _factory(client)
    async with factory() as session:
        with pytest.raises(IntegrityError):
            await session.execute(
                text("UPDATE users SET is_superuser = true WHERE id = :id"),
                {"id": uuid.UUID(account["id"])},
            )
            await session.commit()
        await session.rollback()


async def test_an_oauth_callback_cannot_sign_in_as_one(client) -> None:
    """The path that defeated the first version of this guard.

    Excluding service accounts from the address lookup sent a matching
    callback into the create branch, which collided on the unique address and
    recovered by re-finding the row the filter had just skipped. The check now
    sits where every branch has to pass.
    """
    from integrations.oauth.base import OAuthUserInfo
    from services.oauth_service import OAuthCallbackFailed, _resolve_or_create_user

    team, creator, _project = await _seed(client)
    account = await _create_account(client, user=creator, team_id=team.id)

    factory = await _factory(client)
    async with factory() as session:
        info = OAuthUserInfo(
            provider="google",
            provider_user_id=f"ext-{unique_suffix()}",
            email=account["email"],
            full_name="Not a person",
            avatar_url=None,
            email_can_link_existing_account=True,
        )

        # The ordinary lookup skips the service account, so the create branch
        # runs and collides on the address. The recovery re-query carries the
        # same exclusion, so it finds nothing and refuses rather than handing
        # back the row the filter had just skipped.
        with pytest.raises(OAuthCallbackFailed):
            await _resolve_or_create_user(session, info=info)
        await session.rollback()

        # And no identity was linked on the way past.
        linked = (
            await session.execute(
                text(
                    "SELECT count(*) FROM oauth_identities WHERE user_id = :id"
                ),
                {"id": uuid.UUID(account["id"])},
            )
        ).scalar_one()
        assert linked == 0


# ---------------------------------------------------------------------------
# Who may manage them
# ---------------------------------------------------------------------------


async def test_a_developer_may_not_create_one(client) -> None:
    """Creating a credential holder is the same act as issuing credentials."""
    team, user, _project = await _seed(client, role="developer")

    response = await client.post(
        "/v1/service-accounts",
        headers=_bearer_for(user),
        json={
            "team_id": str(team.id),
            "slug": f"ci-{unique_suffix()}",
            "display_name": "Nightly build",
        },
    )

    assert response.status_code == 403, response.text
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_another_teams_accounts_are_not_listed(client) -> None:
    """404 rather than an empty list: team ids are not probeable."""
    theirs_team, theirs_admin, _p = await _seed(client)
    await _create_account(client, user=theirs_admin, team_id=theirs_team.id)
    _other_team, outsider, _op = await _seed(client)

    response = await client.get(
        f"/v1/service-accounts?team_id={theirs_team.id}", headers=_bearer_for(outsider)
    )

    assert response.status_code == 404, response.text


async def test_a_bad_name_is_refused_rather_than_reshaped(client) -> None:
    """The name becomes an identifier with a unique index behind it."""
    team, user, _project = await _seed(client)

    response = await client.post(
        "/v1/service-accounts",
        headers=_bearer_for(user),
        json={
            "team_id": str(team.id),
            "slug": "Nightly Build",
            "display_name": "Nightly build",
        },
    )

    assert response.status_code == 422, response.text
