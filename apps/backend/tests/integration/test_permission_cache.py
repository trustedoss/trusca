"""
Reusing a resolved principal, and the price of doing so (N5).

Every authenticated request rebuilds the same answer with two queries. Caching
it is worth something on a busy deployment and costs something that matters
more than latency: the answer is somebody's permissions, and a stale one keeps
a demoted person at their old grade.

So the lifetime is the contract. Whatever an operator writes is the longest a
revocation can go unfelt, and the tests below pin exactly that rather than
"the cache works". Off is the default, and the first two cases are about the
default doing nothing at all: a suite that only ever runs with the cache off
would leave every defect in the on state undiscovered, which is the named risk
for this unit.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from core.security import create_access_token, reset_principal_cache
from models import Membership, User
from tests._helpers import (
    make_membership,
    make_organization,
    make_team,
    make_user,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set, skipping permission cache tests")
    return url


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    _require_database_url()
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"alembic upgrade failed: {result.stderr[-400:]}")


@pytest.fixture(autouse=True)
def _clean_cache() -> Iterator[None]:
    """No entry survives into the next test.

    Process-wide state in a test suite is how a case starts passing because of
    the one before it.
    """
    reset_principal_cache()
    yield
    reset_principal_cache()


@pytest.fixture
def app():
    from main import app as fastapi_app

    return fastapi_app


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
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
    token = create_access_token(subject=str(user.id), role=role)
    return {"Authorization": f"Bearer {token}"}


async def _seed_member(client: AsyncClient, *, role: str = "developer"):
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session)
        await make_membership(session, user=user, team=team, role=role)
    return team, user


def _new_project(team) -> dict[str, str]:
    suffix = uuid.uuid4().hex[:10]
    return {
        "name": f"cache-probe-{suffix}",
        "slug": f"cache-probe-{suffix}",
        "team_id": str(team.id),
    }


async def _demote_in_the_database(client: AsyncClient, user: User, role: str) -> None:
    """Change the grade behind the cache's back.

    Deliberately not through the admin API, which drops the entry as it
    writes. What is under test here is the lifetime, and the lifetime is the
    only thing standing between a change made anywhere (another worker, a
    migration, somebody with psql) and the answer this process gives.
    """
    factory = await _factory(client)
    async with factory() as session:
        membership = (
            await session.execute(
                select(Membership).where(Membership.user_id == user.id)
            )
        ).scalar_one()
        membership.role = role
        await session.commit()


# ---------------------------------------------------------------------------
# Off, which is the default
# ---------------------------------------------------------------------------


async def test_nothing_is_kept_when_no_lifetime_is_set(client, monkeypatch) -> None:
    """The default path reads the database every time.

    Asserted on the cache itself rather than by counting queries: a store that
    is empty after a request cannot serve a stale answer to the next one, and
    that is the property the default promises.
    """
    monkeypatch.delenv("PERMISSION_CACHE_TTL_SECONDS", raising=False)
    from core import security

    _team, user = await _seed_member(client)

    response = await client.get("/v1/projects", headers=_bearer_for(user))

    assert response.status_code == 200, response.text
    assert security._principal_cache == {}


async def test_a_demotion_is_immediate_with_no_lifetime(client, monkeypatch) -> None:
    monkeypatch.delenv("PERMISSION_CACHE_TTL_SECONDS", raising=False)
    team, user = await _seed_member(client, role="developer")
    headers = _bearer_for(user)
    await client.get("/v1/projects", headers=headers)

    await _demote_in_the_database(client, user, "viewer")
    refused = await client.post(
        "/v1/projects", headers=headers, json=_new_project(team)
    )

    assert refused.status_code == 403, refused.text


# ---------------------------------------------------------------------------
# On, which is where the defects would otherwise hide
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ttl", ["1", "5", "60"])
async def test_the_lifetime_is_an_upper_bound_on_a_demotion(
    client, monkeypatch, ttl: str
) -> None:
    """Inside the window the old answer may be used; past it, never.

    Parametrised over three lifetimes because the guarantee is about the
    number an operator wrote, not about one value that happens to work. The
    expiry is exercised by moving the clock rather than by sleeping: a test
    that sleeps for 60 seconds is a test somebody deletes.
    """
    monkeypatch.setenv("PERMISSION_CACHE_TTL_SECONDS", ttl)
    from core import security

    team, user = await _seed_member(client, role="developer")
    headers = _bearer_for(user)
    await client.get("/v1/projects", headers=headers)
    await _demote_in_the_database(client, user, "viewer")

    inside = await client.post(
        "/v1/projects", headers=headers, json=_new_project(team)
    )

    # Now stand just past the deadline the entry was written with. The reading
    # is taken before the clock is replaced and then held constant: computing
    # it inside the lambda would look it up again after the expiry has already
    # removed the entry it reads.
    just_past = _past(security, user)
    monkeypatch.setattr(security, "time", _FrozenClock(just_past))
    outside = await client.post(
        "/v1/projects", headers=headers, json=_new_project(team)
    )

    assert inside.status_code == 201, inside.text
    assert outside.status_code == 403, outside.text


class _FrozenClock:
    """Stands in for the ``time`` module at a chosen reading.

    Only ``monotonic`` is used by the cache, and freezing it is what lets a
    sixty-second lifetime be tested in a suite nobody waits on.
    """

    def __init__(self, reading: float) -> None:
        self._reading = reading

    def monotonic(self) -> float:
        return self._reading


def _past(security_module: Any, user: User | uuid.UUID) -> float:
    """A clock reading one second past this user's cached deadline."""
    key = user if isinstance(user, uuid.UUID) else user.id
    _principal, deadline = security_module._principal_cache[key]
    return float(deadline) + 1.0


async def test_a_deactivation_is_felt_within_the_lifetime(
    client, monkeypatch
) -> None:
    """The case that matters most.

    A demoted person can still read; a deactivated one is supposed to be gone.
    The cache must not be the reason they are still holding a session.
    """
    monkeypatch.setenv("PERMISSION_CACHE_TTL_SECONDS", "60")
    from core import security

    _team, user = await _seed_member(client)
    headers = _bearer_for(user)
    await client.get("/v1/projects", headers=headers)

    factory = await _factory(client)
    async with factory() as session:
        row = (await session.execute(select(User).where(User.id == user.id))).scalar_one()
        row.is_active = False
        await session.commit()

    just_past = _past(security, user)
    monkeypatch.setattr(security, "time", _FrozenClock(just_past))
    response = await client.get("/v1/projects", headers=headers)

    assert response.status_code == 401, response.text


async def test_an_inactive_principal_is_never_stored(client, monkeypatch) -> None:
    """Otherwise reactivating somebody makes them wait out a timer.

    The refusal costs the two queries it always did; what this saves is the
    ordinary path, and an entry that every caller rejects saves nothing.
    """
    monkeypatch.setenv("PERMISSION_CACHE_TTL_SECONDS", "60")
    from core import security

    factory = await _factory(client)
    async with factory() as session:
        user = await make_user(session, is_active=False)

    await client.get("/v1/projects", headers=_bearer_for(user))

    assert user.id not in security._principal_cache


async def test_demoting_through_the_admin_api_takes_effect_at_once(
    client, monkeypatch
) -> None:
    """The lifetime is the guarantee; this is the ordinary experience.

    An administrator who demotes somebody and looks at the screen should see
    the new grade, not wait out a timer they did not set for this.
    """
    monkeypatch.setenv("PERMISSION_CACHE_TTL_SECONDS", "60")
    team, user = await _seed_member(client, role="developer")
    headers = _bearer_for(user)
    await client.get("/v1/projects", headers=headers)

    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)
    demoted = await client.patch(
        f"/v1/admin/users/{user.id}/role",
        headers=_bearer_for(admin),
        json={"role": "viewer", "team_id": str(team.id)},
    )
    refused = await client.post(
        "/v1/projects", headers=headers, json=_new_project(team)
    )

    assert demoted.status_code == 200, demoted.text
    assert refused.status_code == 403, refused.text


async def test_deactivating_through_the_admin_api_takes_effect_at_once(
    client, monkeypatch
) -> None:
    monkeypatch.setenv("PERMISSION_CACHE_TTL_SECONDS", "60")
    _team, user = await _seed_member(client)
    headers = _bearer_for(user)
    await client.get("/v1/projects", headers=headers)

    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)
    await client.patch(
        f"/v1/admin/users/{user.id}/deactivate", headers=_bearer_for(admin)
    )

    response = await client.get("/v1/projects", headers=headers)

    assert response.status_code == 401, response.text


async def test_removing_somebody_from_a_team_takes_effect_at_once(
    client, monkeypatch
) -> None:
    """Membership is where the grade comes from, so it is the third write that
    changes what somebody may do."""
    monkeypatch.setenv("PERMISSION_CACHE_TTL_SECONDS", "60")
    team, user = await _seed_member(client, role="developer")
    headers = _bearer_for(user)
    listed_before = await client.get("/v1/projects", headers=headers)

    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)
    removed = await client.delete(
        f"/v1/admin/teams/{team.id}/members/{user.id}", headers=_bearer_for(admin)
    )
    refused = await client.post(
        "/v1/projects", headers=headers, json=_new_project(team)
    )

    assert listed_before.status_code == 200, listed_before.text
    assert removed.status_code == 200, removed.text
    assert refused.status_code in (403, 404), refused.text


async def test_the_lifetime_is_capped(monkeypatch) -> None:
    """A cache measured in hours is a second copy of the permission model that
    nobody updates, and the operator who wrote it will not remember it when
    they deactivate somebody."""
    from core.config import _PERMISSION_CACHE_TTL_MAX, permission_cache_ttl_seconds

    monkeypatch.setenv("PERMISSION_CACHE_TTL_SECONDS", "86400")

    assert permission_cache_ttl_seconds() == _PERMISSION_CACHE_TTL_MAX


@pytest.mark.parametrize("value", ["0", "-5", "", "   ", "soon", "5.5"])
def test_anything_that_is_not_a_positive_count_of_seconds_means_off(
    monkeypatch, value: str
) -> None:
    """Fail closed, in the direction of reading the database more often."""
    from core.config import permission_cache_ttl_seconds

    monkeypatch.setenv("PERMISSION_CACHE_TTL_SECONDS", value)

    assert permission_cache_ttl_seconds() == 0


def test_the_store_is_bounded(monkeypatch) -> None:
    """An expired entry is only noticed when somebody looks it up.

    A deployment where ten thousand people sign in once would otherwise keep
    ten thousand dead entries that nothing ever reads again, which is a map
    that fills rather than a cache.
    """
    from core import security
    from core.security import CurrentUser

    monkeypatch.setattr(security, "_PRINCIPAL_CACHE_MAX_ENTRIES", 4)
    reset_principal_cache()

    for _ in range(20):
        principal = CurrentUser(id=uuid.uuid4(), email="x@example.com", role="viewer")
        security._remember_principal(principal, 60)

    assert len(security._principal_cache) <= 4


def test_a_full_store_drops_what_expires_soonest(monkeypatch) -> None:
    """The entry about to be re-read anyway is the cheapest to lose.

    Refusing to store instead would mean the busiest moment is the one where
    caching stops happening.
    """
    from core import security
    from core.security import CurrentUser

    monkeypatch.setattr(security, "_PRINCIPAL_CACHE_MAX_ENTRIES", 2)
    reset_principal_cache()

    first = CurrentUser(id=uuid.uuid4(), email="a@example.com", role="viewer")
    second = CurrentUser(id=uuid.uuid4(), email="b@example.com", role="viewer")
    third = CurrentUser(id=uuid.uuid4(), email="c@example.com", role="viewer")
    security._remember_principal(first, 10)
    security._remember_principal(second, 300)
    security._remember_principal(third, 300)

    assert first.id not in security._principal_cache
    assert second.id in security._principal_cache
    assert third.id in security._principal_cache


# ---------------------------------------------------------------------------
# What must never be kept, and what must never be missed
# ---------------------------------------------------------------------------


async def test_deleting_a_team_forgets_the_people_who_were_in_it(
    client, monkeypatch
) -> None:
    """The memberships go with the team, and a membership is a grade.

    Somebody whose only team_admin row was in the deleted team drops to the
    floor. This was the one write that changed what a person may do without
    saying so.
    """
    monkeypatch.setenv("PERMISSION_CACHE_TTL_SECONDS", "60")
    from core import security

    team, user = await _seed_member(client, role="team_admin")
    await client.get("/v1/projects", headers=_bearer_for(user))
    assert user.id in security._principal_cache

    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)
    deleted = await client.delete(
        f"/v1/admin/teams/{team.id}", headers=_bearer_for(admin)
    )

    assert deleted.status_code in (200, 204), deleted.text
    assert user.id not in security._principal_cache


async def test_a_key_principal_is_never_stored(client, monkeypatch) -> None:
    """A key carries its issuer's user id, so it would share their slot.

    Serving one for the other hands a scoped CI key the issuer's whole
    membership set, or hands their browser session a read-only flag. Only the
    token path reaches the store today; this is what keeps that true.
    """
    monkeypatch.setenv("PERMISSION_CACHE_TTL_SECONDS", "60")
    from core import security
    from core.security import CurrentUser

    reset_principal_cache()
    scoped = CurrentUser(
        id=uuid.uuid4(),
        email="ci@example.com",
        role="developer",
        api_key_project_id=uuid.uuid4(),
    )
    read_only = CurrentUser(
        id=uuid.uuid4(),
        email="ci2@example.com",
        role="developer",
        api_key_read_only=True,
    )

    security._remember_principal(scoped, 60)
    security._remember_principal(read_only, 60)

    assert security._principal_cache == {}


async def test_a_cached_request_still_writes_an_audit_row_naming_the_actor(
    client, monkeypatch
) -> None:
    """Skipping the user lookup must not skip the actor binding.

    An audit trail that loses its actor on the cached path is worse than no
    cache: the rows are still written, so nothing looks wrong until somebody
    reads them.
    """
    monkeypatch.setenv("PERMISSION_CACHE_TTL_SECONDS", "60")
    from models import AuditLog

    team, user = await _seed_member(client, role="developer")
    headers = _bearer_for(user)
    await client.get("/v1/projects", headers=headers)

    created = await client.post("/v1/projects", headers=headers, json=_new_project(team))

    factory = await _factory(client)
    async with factory() as session:
        rows = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.target_table == "projects",
                        AuditLog.target_id == created.json()["id"],
                    )
                )
            )
            .scalars()
            .all()
        )

    assert created.status_code == 201, created.text
    assert rows, "the write should have produced an audit row"
    assert all(row.actor_user_id == user.id for row in rows)


async def test_a_super_admin_demotion_is_bounded_like_any_other(
    client, monkeypatch
) -> None:
    """The grade that bypasses every team check is the one worth pinning.

    Twenty-seven call sites read `role == "super_admin"` and stop asking
    questions, so a stale copy of it is the most valuable thing this store
    could hold.
    """
    monkeypatch.setenv("PERMISSION_CACHE_TTL_SECONDS", "60")
    from core import security

    factory = await _factory(client)
    async with factory() as session:
        target = await make_user(session, is_superuser=True)
        # A second one so the deployment still has an administrator after the
        # demotion below, which the last-super-admin guard would otherwise
        # refuse.
        await make_user(session, is_superuser=True)
    headers = _bearer_for(target)
    await client.get("/v1/admin/users?page=1&page_size=1", headers=headers)

    async with factory() as session:
        row = (
            await session.execute(select(User).where(User.id == target.id))
        ).scalar_one()
        row.is_superuser = False
        await session.commit()

    just_past = _past(security, target.id)
    monkeypatch.setattr(security, "time", _FrozenClock(just_past))
    response = await client.get("/v1/admin/users?page=1&page_size=1", headers=headers)

    assert response.status_code == 404, response.text


async def test_re_reading_the_same_person_does_not_evict_anybody(monkeypatch) -> None:
    """A refresh of an entry already held is not a new slot.

    Treating it as one made the busiest user evict somebody on every request
    once the store was full.
    """
    from core import security
    from core.security import CurrentUser

    monkeypatch.setattr(security, "_PRINCIPAL_CACHE_MAX_ENTRIES", 2)
    reset_principal_cache()
    first = CurrentUser(id=uuid.uuid4(), email="a@example.com", role="viewer")
    second = CurrentUser(id=uuid.uuid4(), email="b@example.com", role="viewer")
    security._remember_principal(first, 60)
    security._remember_principal(second, 60)

    security._remember_principal(second, 60)

    assert first.id in security._principal_cache
    assert second.id in security._principal_cache


async def test_a_stored_principal_cannot_be_altered_through_the_copy_handed_out(
    monkeypatch,
) -> None:
    """The dataclass holds a list and a dict, and the stored one outlives the
    request that built it. One append downstream would otherwise rewrite what
    everybody holding that session may do."""
    from core import security
    from core.security import CurrentUser

    reset_principal_cache()
    team_id = uuid.uuid4()
    principal = CurrentUser(
        id=uuid.uuid4(),
        email="a@example.com",
        role="viewer",
        team_ids=[team_id],
        team_roles={team_id: "viewer"},
    )
    security._remember_principal(principal, 60)

    handed_out = security._cached_principal(principal.id, 60)
    assert handed_out is not None
    handed_out.team_ids.append(uuid.uuid4())
    handed_out.team_roles[team_id] = "team_admin"

    again = security._cached_principal(principal.id, 60)
    assert again is not None
    assert again.team_ids == [team_id]
    assert again.team_roles == {team_id: "viewer"}
