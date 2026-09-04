# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Guessing at one account gets slower; nobody gets shut out of their own.

A counter attached to an address is the standard answer to credential
stuffing and the standard way to hand an attacker a denial of service: knowing
somebody's email is enough to keep them out, and the more useful the account
the more worthwhile that is. The tests here are mostly about the second half.

The first version of this file asserted that a window expires on its own and
stopped there. It did, and the defect was one step further on: every failure
after the threshold opened a new window, so a single wrong password per expired
window held an account shut for good. The property worth testing is not that a
window ends, it is what the next one costs.

Driven against a real Redis, because what is being asserted is when keys
expire and what happens to a counter while a window is open, and a fake would
be asserting the shape of my own assumptions.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    if not os.getenv("DATABASE_URL") or not os.getenv("REDIS_URL"):
        pytest.skip("DATABASE_URL / REDIS_URL not set")
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:
        pytest.skip(f"alembic upgrade head failed: {result.stderr}")


@pytest.fixture(autouse=True)
def _short_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two failures then a two-second window, so waiting one out is testable.

    The enabled flag is set here and then read back. CI turns this control off
    in the jobs that drive ``/auth/login`` for other reasons (fuzzing,
    documented status codes), and the day somebody sets it in the job that runs
    this suite instead, every test below would exercise a disabled code path
    and pass. A guard that is off looks exactly like a guard that works, which
    is the shape this whole file exists to argue about.
    """
    monkeypatch.setenv("LOGIN_THROTTLE_ENABLED", "true")
    monkeypatch.setenv("LOGIN_THROTTLE_FAILURES", "2")
    monkeypatch.setenv("LOGIN_THROTTLE_WINDOWS", "2,4")

    from core.config import (
        login_throttle_enabled,
        login_throttle_failures,
        login_throttle_windows,
    )

    assert login_throttle_enabled(), "the throttle is off, so these tests prove nothing"
    assert login_throttle_failures() == 2, login_throttle_failures()
    assert login_throttle_windows() == (2, 4), login_throttle_windows()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from main import app as fastapi_app

    transport = ASGITransport(app=fastapi_app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest.fixture
async def address() -> AsyncIterator[str]:
    """An address nobody has used, cleaned up so reruns start from zero."""
    from core.login_throttle import clear
    from tests._helpers import unique_suffix

    value = f"throttle-{unique_suffix()}@example.com"
    await clear(value)
    yield value
    await clear(value)


async def _wrong_password(client: AsyncClient, email: str):
    return await client.post(
        "/auth/login", json={"email": email, "password": "definitely not it 1"}
    )


async def test_guessing_is_refused_after_a_few_wrong_passwords(
    client: AsyncClient, address: str
) -> None:
    from core.login_throttle import seconds_until_retry

    assert await seconds_until_retry(address) == 0

    first = await _wrong_password(client, address)
    assert first.status_code == 401, first.text

    second = await _wrong_password(client, address)
    assert second.status_code == 429, second.text
    assert second.headers.get("Retry-After"), second.headers
    assert second.headers["content-type"].startswith("application/problem+json")


async def test_knocking_during_the_window_does_not_extend_it(
    client: AsyncClient, address: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one thing that keeps this from being a way to lock somebody out.

    An attacker who can extend the window by knocking holds the account shut
    for as long as they care to. So attempts arriving inside a window are
    refused without being counted.

    Asserted on the deadline rather than on the time remaining. Remaining time
    falls by itself, so "no larger than before" is satisfied even by a window
    that was thrown away and reopened at full length -- the first version of
    this test said exactly that and a mutant that counted every knock walked
    past it. Where the window *ends* is the thing an attacker moves.
    """
    import asyncio
    import time

    from core.login_throttle import seconds_until_retry

    monkeypatch.setenv("LOGIN_THROTTLE_WINDOWS", "5,20")

    await _wrong_password(client, address)
    await _wrong_password(client, address)

    opened_at = time.monotonic()
    remaining = await seconds_until_retry(address)
    assert remaining > 0, "the window did not open, so this proves nothing"
    deadline = opened_at + remaining

    # Knocked after a pause, not in a burst. A window reopened at full length
    # a fraction of a second after it started ends within a fraction of a
    # second of where it would have, which is inside any sane tolerance -- so
    # a burst cannot tell the two apart. Waiting first makes the difference
    # the length of the wait.
    await asyncio.sleep(2)
    for _ in range(3):
        knock = await _wrong_password(client, address)
        assert knock.status_code == 429, knock.text

    now = time.monotonic()
    deadline_now = now + await seconds_until_retry(address)

    assert deadline_now <= deadline + 1, (
        f"knocking pushed the window's end out by "
        f"{deadline_now - deadline:.1f}s, so an attacker can hold an account "
        "shut for as long as they keep trying"
    )

    await asyncio.sleep(max(deadline - time.monotonic(), 0) + 1)
    assert await seconds_until_retry(address) == 0, "the window did not expire on its own"


async def test_an_address_nobody_owns_is_refused_the_same_way(
    client: AsyncClient, address: str
) -> None:
    """The refusal must not answer "does this account exist".

    A registered address and an unregistered one are both counted and both
    refused, so the reply carries no more information than the sign-in form
    already gives away.
    """
    from tests._helpers import unique_suffix

    registered = f"real-{unique_suffix()}@example.com"
    created = await client.post(
        "/auth/register",
        json={"email": registered, "password": "a long enough password 1", "full_name": "R"},
    )
    assert created.status_code in (200, 201), created.text

    from core.login_throttle import clear

    await clear(registered)
    try:
        for _ in range(2):
            unknown = await _wrong_password(client, address)
            known = await _wrong_password(client, registered)

        assert unknown.status_code == known.status_code == 429
        assert unknown.json()["title"] == known.json()["title"]
        assert unknown.json()["detail"] == known.json()["detail"]
    finally:
        await clear(registered)


async def test_a_correct_password_clears_the_count(client: AsyncClient) -> None:
    """Otherwise a person who mistyped twice is refused after signing in."""
    from core.login_throttle import clear, seconds_until_retry
    from tests._helpers import unique_suffix

    email = f"clears-{unique_suffix()}@example.com"
    password = "a long enough password 2"
    created = await client.post(
        "/auth/register", json={"email": email, "password": password, "full_name": "C"}
    )
    assert created.status_code in (200, 201), created.text
    await clear(email)

    try:
        assert (await _wrong_password(client, email)).status_code == 401

        ok = await client.post("/auth/login", json={"email": email, "password": password})
        assert ok.status_code == 200, ok.text
        assert await seconds_until_retry(email) == 0

        # And the budget really did reset: one more wrong password is a 401,
        # not the 429 it would be if the earlier failure were still counted.
        assert (await _wrong_password(client, email)).status_code == 401
    finally:
        await clear(email)


async def test_case_does_not_buy_a_second_budget(client: AsyncClient, address: str) -> None:
    """The counter has to normalise the way the user lookup does.

    Sign-in resolves Alice@Example.com and alice@example.com to one account.
    A counter that did not would give an attacker a fresh allowance for every
    capitalisation of the address they are working on.
    """
    from core.login_throttle import seconds_until_retry

    await _wrong_password(client, address)
    shouted = await _wrong_password(client, address.upper())

    assert shouted.status_code == 429, (
        "the same address in different case was counted separately, so the "
        "allowance multiplies by however many spellings an attacker tries"
    )
    assert await seconds_until_retry(address.upper()) > 0


async def test_asking_for_a_reset_does_not_refill_the_budget(
    client: AsyncClient, address: str
) -> None:
    """Requesting is not proof of anything; consuming the token is.

    If a request cleared the count, an attacker guessing at an address could
    ask for a reset they will never receive and carry on, and the throttle
    would be decorative.
    """
    from core.login_throttle import seconds_until_retry

    await _wrong_password(client, address)
    await _wrong_password(client, address)
    assert await seconds_until_retry(address) > 0

    asked = await client.post("/auth/forgot-password", json={"email": address})
    assert asked.status_code in (200, 202, 204), asked.text

    assert await seconds_until_retry(address) > 0, (
        "asking for a password reset cleared the failure count, so anyone can "
        "refill their own guessing budget on demand"
    )


async def test_completing_a_reset_clears_the_count(client: AsyncClient, monkeypatch) -> None:
    """And the owner's way back, which the attacker cannot block.

    This is what keeps the throttle from being a usable denial of service: the
    person who owns the inbox always has a path that does not depend on waiting
    out somebody else's guessing.
    """
    from core.login_throttle import clear, seconds_until_retry
    from services import password_reset_service as svc
    from tests._helpers import unique_suffix

    captured: dict[str, str] = {}

    def _capture(*, plaintext_token, user_email, user_id):
        captured["token"] = plaintext_token

    monkeypatch.setattr(svc, "_enqueue_reset_email", _capture)

    email = f"unlocks-{unique_suffix()}@example.com"
    created = await client.post(
        "/auth/register",
        json={"email": email, "password": "a long enough password 3", "full_name": "U"},
    )
    assert created.status_code in (200, 201), created.text
    await clear(email)

    try:
        await _wrong_password(client, email)
        await _wrong_password(client, email)
        assert await seconds_until_retry(email) > 0

        await client.post("/auth/forgot-password", json={"email": email})
        assert captured.get("token"), "the reset flow sent no token"

        done = await client.post(
            "/auth/reset-password",
            json={"token": captured["token"], "new_password": "a replacement password 4"},
        )
        assert done.status_code in (200, 204), done.text

        assert await seconds_until_retry(email) == 0, (
            "finishing a reset left the address refused, so the owner has no way "
            "back except waiting out whoever locked them out"
        )
    finally:
        await clear(email)


async def test_the_counter_refuses_to_extend_its_own_window(address: str) -> None:
    """The rule, asserted on the function that holds it.

    Driving this through the endpoint does not test it: the route refuses
    before it calls anything, so the counter never sees an attempt made during
    a window and the branch never runs. The two guards cover each other, which
    is worth having and means neither is exercised by removing the other. This
    one is called directly.
    """
    import asyncio
    import time

    from core.login_throttle import record_failure

    await record_failure(address)
    opened_at = time.monotonic()
    window = await record_failure(address)
    assert window > 0, "two failures did not open a window, so this proves nothing"
    deadline = opened_at + window

    await asyncio.sleep(1.2)
    again = await record_failure(address)

    assert again > 0, "the counter should still report the address as refused"
    assert time.monotonic() + again <= deadline + 0.5, (
        "a failure arriving inside the window pushed its end out; the window a "
        "person caused has to run out however hard anyone keeps trying"
    )


async def test_a_refused_address_never_reaches_the_password_check(
    client: AsyncClient, address: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The route's own job: refuse before doing any of the work.

    Not only to save a bcrypt. Reaching the credential check means the reply's
    timing depends on whether the address exists, which is the leak the dummy
    hash in ``authenticate`` exists to close, and it means an attacker can
    still spend the server's time while they are supposedly being refused.
    """
    from api.v1 import auth as auth_router

    await _wrong_password(client, address)
    await _wrong_password(client, address)

    async def _must_not_run(*args, **kwargs):
        raise AssertionError("the password was checked for an address being refused")

    monkeypatch.setattr(auth_router, "authenticate", _must_not_run)

    refused = await _wrong_password(client, address)

    assert refused.status_code == 429, refused.text


async def test_one_failure_after_a_window_expires_does_not_re_lock(address: str) -> None:
    """The step past the end of the window, which is where the defect was.

    A window that expires and then re-opens on the next single failure is a
    permanent lockout with extra steps: two requests an hour, from one address,
    nowhere near the per-IP budget. Whoever knows an email can hold its owner
    out for as long as they care to, and a super admin is as easy a target as
    anyone.
    """
    import asyncio

    from core.login_throttle import record_failure, seconds_until_retry

    await record_failure(address)
    window = await record_failure(address)
    assert window > 0, "two failures did not open a window, so this proves nothing"

    await asyncio.sleep(window + 1)
    assert await seconds_until_retry(address) == 0, "the window did not expire"

    assert await record_failure(address) == 0, (
        "one failure after the window expired locked the address again, so an "
        "attacker holds an account shut at one request per window forever"
    )


async def test_the_next_window_costs_a_whole_threshold_again(address: str) -> None:
    """And the other half of it, which the test above does not cover.

    A version that needed two failures rather than one would satisfy the test
    above and still be cheap to sustain. What the counter has to do when a
    window opens is forget, so the next window costs the same as the first.
    """
    import asyncio

    from core.login_throttle import record_failure, seconds_until_retry

    await record_failure(address)
    window = await record_failure(address)
    assert window > 0

    await asyncio.sleep(window + 1)

    # LOGIN_THROTTLE_FAILURES is 2 here, so the first is not enough on its own
    # and the second is exactly enough. Asserted as a pair: only the second
    # opening a window says the counter restarted rather than resumed.
    assert await record_failure(address) == 0
    assert await record_failure(address) > 0
    assert await seconds_until_retry(address) > 0


async def test_a_quiet_spell_forgets_the_failures(address: str, monkeypatch) -> None:
    """Failures decay on a clock nobody can push forward.

    The counter's expiry used to be refreshed on every failure, so it never
    ran out while anyone kept knocking. It is set once now, when the counter is
    created, which is what lets a person who mistyped twice last week start
    from zero today.
    """
    import asyncio

    from core.login_throttle import record_failure, seconds_until_retry

    monkeypatch.setenv("LOGIN_THROTTLE_WINDOWS", "2")

    await record_failure(address)
    await asyncio.sleep(1)
    await record_failure(address)  # opens a window; counter is cleared with it

    await asyncio.sleep(await seconds_until_retry(address) + 2.5)

    # The decay is the longest window (2s here), so by now a lone failure is
    # starting a fresh run rather than joining the old one.
    assert await record_failure(address) == 0


async def test_an_unparseable_setting_does_not_switch_the_throttle_off(
    address: str, monkeypatch
) -> None:
    """A zero-length window used to disable the whole control, silently.

    Zero is a valid integer, so it parsed; applied as an expiry it deletes the
    key it is set on, so the threshold was never reached and no window ever
    opened. Nothing said so. A setting that is ignored has to be noisy, because
    a throttle that is off looks exactly like one that is working.
    """
    from core.config import login_throttle_windows
    from core.login_throttle import record_failure

    monkeypatch.setenv("LOGIN_THROTTLE_WINDOWS", "0")
    assert login_throttle_windows() == (60, 300, 900, 1800), "zero was accepted"

    monkeypatch.setenv("LOGIN_THROTTLE_WINDOWS", "not-a-number")
    assert login_throttle_windows() == (60, 300, 900, 1800)

    monkeypatch.setenv("LOGIN_THROTTLE_FAILURES", "0")
    from core.config import login_throttle_failures

    assert login_throttle_failures() == 10

    # And the throttle still works with the fallbacks rather than raising.
    monkeypatch.setenv("LOGIN_THROTTLE_WINDOWS", "2")
    monkeypatch.setenv("LOGIN_THROTTLE_FAILURES", "2")
    await record_failure(address)
    assert await record_failure(address) > 0


async def test_a_redis_outage_leaves_sign_in_working(client: AsyncClient, monkeypatch) -> None:
    """Redis is not a dependency of being able to sign in.

    The gate is the first statement of the login handler, so an unhandled
    connection error there is a total authentication outage: every sign-in
    becomes a 500, for a control this module calls a slowdown rather than a
    last line. It degrades to the per-IP limit, which is still in force, and
    says so in the log.
    """
    from redis.exceptions import ConnectionError as RedisConnectionError

    from core import login_throttle
    from tests._helpers import unique_suffix

    email = f"outage-{unique_suffix()}@example.com"
    password = "a long enough password 5"
    created = await client.post(
        "/auth/register", json={"email": email, "password": password, "full_name": "O"}
    )
    assert created.status_code in (200, 201), created.text

    def _down() -> None:
        raise RedisConnectionError("redis is down")

    monkeypatch.setattr(login_throttle, "_redis", _down)

    ok = await client.post("/auth/login", json={"email": email, "password": password})
    assert ok.status_code == 200, ok.text


async def test_the_owner_can_still_reset_while_an_attacker_holds_the_address(
    client: AsyncClient, monkeypatch
) -> None:
    """The last door, checked rather than assumed.

    Two of the three ways back need something the attacker cannot supply, and
    one of those needs a signed-in super admin -- no use if every super admin
    is being held shut at once. That leaves the password reset carrying the
    guarantee alone, so whether *it* can be blocked is what the design rests
    on.

    It cannot, and the reason is the other way round from how it first reads:
    the reset mail goes to the address, not to whoever asked. An attacker
    burning the per-address cooldown is posting working tokens into their
    victim's inbox.

    There is a real edge, and it is asserted below rather than hoped past. A
    new request invalidates the previous token, so the attacker can make an
    older link stop working. What they cannot do is leave the victim with no
    working link at all: the token that replaces it went to the same inbox.
    """
    from core.login_throttle import clear, seconds_until_retry
    from services import password_reset_service as svc
    from tests._helpers import unique_suffix

    tokens: list[str] = []

    def _capture(*, plaintext_token, user_email, user_id):
        tokens.append(plaintext_token)

    monkeypatch.setattr(svc, "_enqueue_reset_email", _capture)
    # The cooldown only governs how often a new mail goes out; zero here so the
    # sequence under test takes seconds rather than a quarter of an hour.
    monkeypatch.setenv("PASSWORD_RESET_EMAIL_COOLDOWN_SECONDS", "0")

    email = f"besieged-{unique_suffix()}@example.com"
    created = await client.post(
        "/auth/register",
        json={"email": email, "password": "a long enough password 6", "full_name": "B"},
    )
    assert created.status_code in (200, 201), created.text
    await clear(email)

    try:
        await _wrong_password(client, email)
        await _wrong_password(client, email)
        assert await seconds_until_retry(email) > 0

        for _ in range(3):
            asked = await client.post("/auth/forgot-password", json={"email": email})
            assert asked.status_code in (200, 202, 204), asked.text

        assert len(tokens) >= 2, f"expected several tokens, saw {len(tokens)}"

        # The first one is dead: a later request invalidated it. Asserted so
        # nobody reads the test below as "any emailed link works".
        stale = await client.post(
            "/auth/reset-password",
            json={"token": tokens[0], "new_password": "should not work 12345"},
        )
        assert stale.status_code >= 400, (
            "an older reset link still worked; the single-pending-token policy "
            "is not doing what this test assumes"
        )

        # And the latest one, which reached the same inbox, does.
        done = await client.post(
            "/auth/reset-password",
            json={"token": tokens[-1], "new_password": "back in again 12345"},
        )
        assert done.status_code in (200, 204), done.text

        assert await seconds_until_retry(email) == 0
        signed_in = await client.post(
            "/auth/login", json={"email": email, "password": "back in again 12345"}
        )
        assert signed_in.status_code == 200, signed_in.text
    finally:
        await clear(email)


def test_the_per_ip_limiter_still_gets_to_go_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """The two controls have to stay in the order the docs describe.

    The per-IP limiter allows 5 attempts a minute and answers 429 on the sixth.
    This one refuses an address after a threshold of failures. Set the
    threshold at or below the IP budget and it fires first for anybody signing
    in from one machine: the limiter's documented sixth-attempt 429 becomes
    unreachable, and somebody who mistyped their password meets the control
    with the growing window instead of the one that resets every minute.

    That is not hypothetical. It is what happened: the default was 5, and
    `test_login_rate_limit_returns_429_on_sixth_attempt` failed on the fifth
    attempt. Asserted by comparing the two settings rather than by driving
    sixty requests, so it also fails if either budget is changed on its own.
    """
    from core.config import login_throttle_failures
    from core.ratelimit import LOGIN_RATE_LIMIT

    # The shipped default, not the small one the autouse fixture sets so the
    # other tests here finish in seconds. What ships is what the ordering
    # depends on.
    monkeypatch.delenv("LOGIN_THROTTLE_FAILURES", raising=False)

    per_ip = int(LOGIN_RATE_LIMIT.split("/")[0])

    assert login_throttle_failures() > per_ip, (
        f"the per-address threshold ({login_throttle_failures()}) is not above "
        f"the per-IP budget ({per_ip}), so a single machine meets this control "
        "before the limiter and the limiter's documented behaviour is dead"
    )
