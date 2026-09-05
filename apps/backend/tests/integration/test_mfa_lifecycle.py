# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Sequences, because the defects here live between the steps.

Each of these is fine as a single action and wrong as a series. Regenerating
codes works; regenerating and then finding the old ones still accepted does
not. Clearing a factor works; clearing and re-enrolling onto the same secret
does not. A single-action test cannot see either, which is why they are
written as sequences.

Driven through the routes rather than the services. Two of the writes here are
the kind that leave the happy path intact when the commit is missing, and a
test that calls a service and commits on its behalf cannot see a route that
forgot to.
"""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from core import totp
from core.crypto import encrypt_secret
from services.mfa_service import ENCRYPTION_PURPOSE
from tests._db_required import migrate_to_head
from tests._helpers import make_user, unique_suffix

pytestmark = pytest.mark.integration

_PASSWORD = "a long enough password 8"
#: The step-up body these endpoints ask for. Both hand back credentials that
#: outlive a password change, so a session alone does not get them.
_PROOF = {"password": _PASSWORD}
_SEED = base64.b32encode(b"12345678901234567890").decode("ascii").rstrip("=")


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from main import app as fastapi_app

    transport = ASGITransport(app=fastapi_app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest.fixture
async def factory() -> AsyncIterator:
    from core.db import _ensure_state
    from main import app as fastapi_app

    yield getattr(fastapi_app.state, "session_factory", None) or _ensure_state(fastapi_app)


def _counter() -> int:
    import time

    return int(time.time()) // totp.PERIOD_SECONDS


async def _make_user(factory, *, enrolled: bool, superuser: bool = False):
    from core.security import set_password

    async with factory() as session:
        user = await make_user(
            session, email=f"life-{unique_suffix()}@example.com", is_superuser=superuser
        )
        set_password(user, _PASSWORD)
        # Backdated, because #325 refuses an access token minted in the same
        # second as a password change: a fixture that sets a password and signs
        # in immediately gets a token the very next request rejects. That rule
        # is deliberate and this is a test artefact, not a reason to soften it.
        user.password_changed_at = datetime.now(UTC) - timedelta(seconds=5)
        if enrolled:
            user.mfa_enabled = True
            user.mfa_secret_encrypted = encrypt_secret(_SEED, purpose=ENCRYPTION_PURPOSE)
        await session.commit()
        return user.id, user.email


async def _sign_in(client, email: str, *, code: str | None = None) -> str:
    """Return an access token, going through the second factor if there is one."""
    first = await client.post("/auth/login", json={"email": email, "password": _PASSWORD})
    if first.status_code == 200:
        return str(first.json()["access_token"])
    assert first.status_code == 202, first.text
    done = await client.post(
        "/auth/mfa/verify",
        json={
            "mfa_token": first.json()["mfa_token"],
            "code": code or totp.code_at(_SEED, counter=_counter()),
        },
    )
    assert done.status_code == 200, done.text
    return str(done.json()["access_token"])


async def test_regenerating_retires_every_unused_code(client, factory) -> None:
    """Issue, spend one, regenerate, and the survivors must be dead.

    Somebody regenerating usually believes the old set leaked. Leaving even one
    of them live defeats the reason they asked, and the only way to see it is
    to keep a code from before and try it after.
    """
    _id, email = await _make_user(factory, enrolled=True)
    token = await _sign_in(client, email)
    auth = {"Authorization": f"Bearer {token}"}

    first_set = await client.post(
        "/v1/users/me/mfa/recovery-codes", json=_PROOF, headers=auth
    )
    assert first_set.status_code == 200, first_set.text
    old_codes = first_set.json()["codes"]
    assert len(old_codes) >= 2

    second_set = await client.post(
        "/v1/users/me/mfa/recovery-codes", json=_PROOF, headers=auth
    )
    assert second_set.status_code == 200, second_set.text
    new_codes = second_set.json()["codes"]

    assert set(old_codes).isdisjoint(new_codes)

    # An old code, offered as a second factor, must not work.
    started = await client.post("/auth/login", json={"email": email, "password": _PASSWORD})
    refused = await client.post(
        "/auth/mfa/verify",
        json={"mfa_token": started.json()["mfa_token"], "code": old_codes[0]},
    )
    # The specific status, not "did it fail". Each of these performs a whole
    # sign-in, so a 429 from the shared address counter satisfies ">= 400"
    # without the code ever being checked.
    assert refused.status_code == 401, (
        f"a recovery code from before the regeneration: {refused.text}"
    )

    # And a new one does, which is what says the refusal above was about age
    # rather than about recovery codes never working.
    started_again = await client.post(
        "/auth/login", json={"email": email, "password": _PASSWORD}
    )
    accepted = await client.post(
        "/auth/mfa/verify",
        json={"mfa_token": started_again.json()["mfa_token"], "code": new_codes[0]},
    )
    assert accepted.status_code == 200, accepted.text


async def test_a_recovery_code_works_once(client, factory) -> None:
    """Spending one has to reach the database.

    The route verifies and marks the row; if the mark is never committed the
    sign-in still succeeds and the code stays live. Presenting the same one
    twice is the only way that shows.
    """
    _id, email = await _make_user(factory, enrolled=True)
    token = await _sign_in(client, email)
    codes = (
        await client.post(
            "/v1/users/me/mfa/recovery-codes",
            json=_PROOF,
            headers={"Authorization": f"Bearer {token}"},
        )
    ).json()["codes"]

    first_start = await client.post(
        "/auth/login", json={"email": email, "password": _PASSWORD}
    )
    first = await client.post(
        "/auth/mfa/verify",
        json={"mfa_token": first_start.json()["mfa_token"], "code": codes[0]},
    )
    assert first.status_code == 200, first.text

    second_start = await client.post(
        "/auth/login", json={"email": email, "password": _PASSWORD}
    )
    second = await client.post(
        "/auth/mfa/verify",
        json={"mfa_token": second_start.json()["mfa_token"], "code": codes[0]},
    )

    assert second.status_code == 401, f"a spent recovery code: {second.text}"


async def test_clearing_and_re_enrolling_produces_a_different_secret(
    client, factory
) -> None:
    """The reason somebody asks for a clear is that the old secret is gone.

    Lowering the flag and keeping the secret would let the account re-enable
    onto the very one whose device was lost. Only a sequence sees it: enrol,
    clear, enrol again, compare.
    """
    from models import User

    user_id, email = await _make_user(factory, enrolled=True)
    admin_id, admin_email = await _make_user(factory, enrolled=False, superuser=True)

    async with factory() as reader:
        before = (await reader.get(User, user_id)).mfa_secret_encrypted
    assert before

    admin_token = await _sign_in(client, admin_email)
    cleared = await client.post(
        f"/v1/admin/users/{user_id}/clear-mfa",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert cleared.status_code == 204, cleared.text

    async with factory() as reader:
        user = await reader.get(User, user_id)
        assert user.mfa_enabled is False
        assert user.mfa_secret_encrypted is None, (
            "the secret survived the clear, so the account can re-enable onto "
            "the one whose device was lost"
        )
        assert user.mfa_last_counter is None

    # And enrolling again produces a new one rather than reviving the old.
    #
    # Backdated for the same reason the password stamp is backdated in
    # ``_make_user``: the clear refuses tokens minted in its own second, so a
    # test that clears and signs in within one second gets a token the next
    # request rejects. A person told by an administrator does not arrive that
    # fast; the boundary itself is covered by
    # ``test_clearing_ends_the_sessions_that_were_open``.
    async with factory() as session:
        subject = await session.get(User, user_id)
        subject.mfa_changed_at = datetime.now(UTC) - timedelta(seconds=5)
        await session.commit()

    token = await _sign_in(client, email)
    started = await client.post(
        "/v1/users/me/mfa/enrol",
        json=_PROOF,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert started.status_code == 200, started.text

    async with factory() as reader:
        after = (await reader.get(User, user_id)).mfa_secret_encrypted
    assert after and after != before


async def test_clearing_leaves_no_recovery_codes_behind(client, factory) -> None:
    """The codes are a way in too, so a clear that keeps them clears nothing."""
    from sqlalchemy import func, select

    from models import UserRecoveryCode

    user_id, email = await _make_user(factory, enrolled=True)
    _admin_id, admin_email = await _make_user(factory, enrolled=False, superuser=True)

    token = await _sign_in(client, email)
    await client.post(
        "/v1/users/me/mfa/recovery-codes",
        json=_PROOF,
        headers={"Authorization": f"Bearer {token}"},
    )

    async with factory() as reader:
        live = await reader.execute(
            select(func.count())
            .select_from(UserRecoveryCode)
            .where(UserRecoveryCode.user_id == user_id, UserRecoveryCode.used_at.is_(None))
        )
        assert live.scalar_one() > 0

    admin_token = await _sign_in(client, admin_email)
    await client.post(
        f"/v1/admin/users/{user_id}/clear-mfa",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    async with factory() as reader:
        left = await reader.execute(
            select(func.count())
            .select_from(UserRecoveryCode)
            .where(UserRecoveryCode.user_id == user_id, UserRecoveryCode.used_at.is_(None))
        )
        assert left.scalar_one() == 0, (
            "unused recovery codes outlived the clear, so the factor is still "
            "bypassable by whoever holds one"
        )


async def test_finishing_enrolment_does_not_sign_the_person_out(client, factory) -> None:
    """The token that turned the factor on has to keep working.

    ``mfa_changed_at`` refuses every token minted before it, and the request
    that completes enrolment carries one of those. Stamping it here signs the
    person out at the moment they finish setting the factor up, and the way it
    presents is the portal going blank on the recovery-code screen, which is
    the one screen whose content cannot be shown again.

    Written after doing exactly that: the stamp was added to both writes, and
    this is the half that had to come back out.
    """
    user_id, email = await _make_user(factory, enrolled=False)
    token = await _sign_in(client, email)
    headers = {"Authorization": f"Bearer {token}"}

    started = await client.post(
        "/v1/users/me/mfa/enrol", json=_PROOF, headers=headers
    )
    assert started.status_code == 200, started.text
    secret = started.json()["secret"]

    confirmed = await client.post(
        "/v1/users/me/mfa/enrol/confirm",
        json={
            "mfa_token": started.json()["mfa_token"],
            "code": totp.code_at(secret, counter=_counter()),
        },
        headers=headers,
    )
    assert confirmed.status_code == 200, confirmed.text

    # The same token, immediately afterwards. This is the request the browser
    # makes next, and a 401 here is the person being logged out.
    still_in = await client.get("/auth/me", headers=headers)
    assert still_in.status_code == 200, still_in.text
    assert still_in.json()["mfa_enabled"] is True

    from models import User

    async with factory() as session:
        user = await session.get(User, user_id)
        assert user is not None
        assert user.mfa_enabled is True
        assert user.mfa_changed_at is None, (
            "enrolment stamped the boundary that refuses tokens minted before "
            "it, which includes the one that just enrolled"
        )


async def test_clearing_ends_the_sessions_that_were_open(client, factory) -> None:
    """What the admin guide promises an operator during an incident.

    Two halves, and one without the other is not the promise. The access token
    the attacker holds has to stop being accepted, and the refresh token has to
    stop minting new ones -- a seven-day cookie that survives means the operator
    who cleared the factor because they believed the account was compromised
    changed nothing for the person who compromised it.

    Both halves were missing when this was first written: the column was
    stamped and read by nobody, and the refresh rows were left alone.
    """
    from sqlalchemy import select

    from models import RefreshToken

    user_id, email = await _make_user(factory, enrolled=True)
    _admin_id, admin_email = await _make_user(factory, enrolled=False, superuser=True)

    # The session that exists before the clear, both halves of it.
    first = await client.post("/auth/login", json={"email": email, "password": _PASSWORD})
    assert first.status_code == 202, first.text
    done = await client.post(
        "/auth/mfa/verify",
        json={
            "mfa_token": first.json()["mfa_token"],
            "code": totp.code_at(_SEED, counter=_counter()),
        },
    )
    assert done.status_code == 200, done.text
    victim_token = str(done.json()["access_token"])
    refresh_cookie = done.cookies.get("refresh_token")
    assert refresh_cookie, "no refresh cookie was set, so this test proves nothing"

    # It works before the clear. Without this the assertions below could pass
    # against a token that was never accepted in the first place.
    assert (
        await client.get(
            "/auth/me", headers={"Authorization": f"Bearer {victim_token}"}
        )
    ).status_code == 200

    admin_token = await _sign_in(client, admin_email)
    cleared = await client.post(
        f"/v1/admin/users/{user_id}/clear-mfa",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert cleared.status_code == 204, cleared.text

    after = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {victim_token}"}
    )
    assert after.status_code == 401, (
        "the access token minted before the clear is still accepted, so "
        "mfa_changed_at is written and read by nothing"
    )

    refreshed = await client.post("/auth/refresh", cookies={"refresh_token": refresh_cookie})
    assert refreshed.status_code == 401, (
        "the refresh token survived the clear, so the session it belongs to "
        "mints new access tokens for another seven days"
    )

    async with factory() as session:
        live = (
            await session.execute(
                select(RefreshToken).where(
                    RefreshToken.user_id == user_id,
                    RefreshToken.revoked_at.is_(None),
                )
            )
        ).scalars().all()
    assert not live, f"{len(live)} refresh token(s) left live after the clear"


async def test_a_session_alone_does_not_issue_recovery_codes(client, factory) -> None:
    """The finding this endpoint was opened by.

    Ten recovery codes are ten sign-ins that bypass the factor. They outlive a
    password change, revoking sessions does not touch them, and the owner was
    never told. Somebody holding a stolen access token called this once and
    held a bypass for as long as they liked, which is the case a second factor
    is bought to survive.
    """
    _user_id, email = await _make_user(factory, enrolled=True)
    token = await _sign_in(client, email)
    auth = {"Authorization": f"Bearer {token}"}

    refused = await client.post(
        "/v1/users/me/mfa/recovery-codes", json={}, headers=auth
    )
    assert refused.status_code == 401, refused.text
    assert refused.json()["title"] == "Confirm It Is You"

    wrong = await client.post(
        "/v1/users/me/mfa/recovery-codes",
        json={"password": "not the password"},
        headers=auth,
    )
    assert wrong.status_code == 401, wrong.text
    # The same answer as the empty one. Telling them apart tells whoever holds
    # the session which proof is worth attacking.
    assert wrong.json()["title"] == refused.json()["title"]
    assert wrong.json()["detail"] == refused.json()["detail"]

    allowed = await client.post(
        "/v1/users/me/mfa/recovery-codes", json=_PROOF, headers=auth
    )
    assert allowed.status_code == 200, allowed.text
    assert len(allowed.json()["codes"]) == 10


async def test_a_session_alone_does_not_start_an_enrolment(client, factory) -> None:
    """Enrolling on somebody else's account is a takeover.

    The attacker's authenticator becomes the factor and the owner is locked
    out by a control they never set up, so this is gated on the same proof.
    """
    _user_id, email = await _make_user(factory, enrolled=False)
    token = await _sign_in(client, email)
    auth = {"Authorization": f"Bearer {token}"}

    refused = await client.post("/v1/users/me/mfa/enrol", json={}, headers=auth)
    assert refused.status_code == 401, refused.text

    allowed = await client.post("/v1/users/me/mfa/enrol", json=_PROOF, headers=auth)
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["secret"]


async def test_a_code_from_the_app_proves_it_too(client, factory) -> None:
    """Because the ordinary reason to reissue is that the codes ran out.

    Somebody who has the authenticator is the person the factor is about, and
    requiring the password as well would mean a lost password blocks the
    recovery path rather than the factor doing so.
    """
    _user_id, email = await _make_user(factory, enrolled=True)
    token = await _sign_in(client, email)

    issued = await client.post(
        "/v1/users/me/mfa/recovery-codes",
        # A step ahead of the one the sign-in above just spent: the same code
        # is refused by the replay check, which is the point of it.
        json={"code": totp.code_at(_SEED, counter=_counter() + 1)},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert issued.status_code == 200, issued.text
    assert len(issued.json()["codes"]) == 10


async def test_the_person_is_told_when_codes_are_reissued(client, factory) -> None:
    """The notice is what makes this reviewable rather than quiet.

    The administrator's clear already notifies. The self-service path is the
    one an attacker uses, and it did neither the proof nor the notice.
    """
    from sqlalchemy import select

    from models import Notification

    user_id, email = await _make_user(factory, enrolled=True)
    token = await _sign_in(client, email)

    issued = await client.post(
        "/v1/users/me/mfa/recovery-codes",
        json=_PROOF,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert issued.status_code == 200, issued.text

    async with factory() as session:
        rows = (
            (
                await session.execute(
                    select(Notification).where(
                        Notification.user_id == user_id,
                        Notification.kind == "account_security",
                    )
                )
            )
            .scalars()
            .all()
        )

    assert len(rows) == 1, f"{len(rows)} account_security notifications, expected 1"
    assert "recovery codes" in rows[0].title.lower()


async def test_guessing_the_password_at_the_step_up_is_counted(
    client, factory
) -> None:
    """Otherwise the control makes a stolen session worth more, not less.

    The step-up exists because a session alone is not enough. An uncounted
    password check behind it hands whoever holds that session an unlimited
    oracle for the credential above it, and a password is worth more than a
    session: it survives revocation and people reuse it elsewhere.

    Counted into the same per-address counter as the sign-in screen, so this
    also asserts that the two share a budget rather than each having one.
    """
    from core.config import login_throttle_failures
    from core.login_throttle import clear

    _user_id, email = await _make_user(factory, enrolled=True)
    token = await _sign_in(client, email)
    auth = {"Authorization": f"Bearer {token}"}
    await clear(email)

    threshold = login_throttle_failures()
    saw_429 = False
    for attempt in range(threshold + 1):
        refused = await client.post(
            "/v1/users/me/mfa/recovery-codes",
            json={"password": f"wrong guess {attempt}"},
            headers=auth,
        )
        assert refused.status_code in (401, 429), (attempt, refused.text)
        if refused.status_code == 429:
            saw_429 = True
            assert refused.headers.get("Retry-After"), refused.headers
            break

    assert saw_429, (
        f"{threshold + 1} wrong passwords at the step-up and none was refused, "
        "so this endpoint is an unmetered password oracle for anybody holding "
        "a session"
    )

    # And the budget is shared with the sign-in screen rather than separate:
    # the address is now refused there too, without a wrong password ever
    # having been sent to it.
    at_login = await client.post(
        "/auth/login", json={"email": email, "password": _PASSWORD}
    )
    assert at_login.status_code == 429, at_login.text

    await clear(email)


async def test_a_warm_cache_entry_does_not_survive_the_clear(
    client, factory, monkeypatch
) -> None:
    """The layer that is invisible while the cache is off.

    ``_load_current_user`` has two branches and the other tests here reach
    only one, because ``PERMISSION_CACHE_TTL_SECONDS`` defaults to 0. On a
    deployment that turned it on, an entry warmed before the clear carries the
    old ``mfa_changed_at``, so the cached branch passes the very token the
    loaded branch refuses, for up to the TTL per worker.

    Hardening rule 8: two layers can produce the same 401 and a test that
    reaches one of them says nothing about the other.
    """
    from core.config import permission_cache_ttl_seconds
    from core.security import _principal_cache

    monkeypatch.setenv("PERMISSION_CACHE_TTL_SECONDS", "300")
    _principal_cache.clear()
    # Read it back rather than trust setenv: "the cache is off" and "the cache
    # is on and correct" are the same green otherwise.
    assert permission_cache_ttl_seconds() == 300, "the cache is not actually on"

    user_id, email = await _make_user(factory, enrolled=True)
    _admin_id, admin_email = await _make_user(factory, enrolled=False, superuser=True)

    victim_token = await _sign_in(client, email)
    headers = {"Authorization": f"Bearer {victim_token}"}

    warm = await client.get("/auth/me", headers=headers)
    assert warm.status_code == 200, warm.text
    assert _principal_cache, "nothing was cached, so this test proves nothing"

    admin_token = await _sign_in(client, admin_email)
    cleared = await client.post(
        f"/v1/admin/users/{user_id}/clear-mfa",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert cleared.status_code == 204, cleared.text

    after = await client.get("/auth/me", headers=headers)
    assert after.status_code == 401, (
        "the cached principal still carries the pre-clear state, so a "
        "deployment with the cache on keeps serving the token the clear was "
        "supposed to end"
    )
