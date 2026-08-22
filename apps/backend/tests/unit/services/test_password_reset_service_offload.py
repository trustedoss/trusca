# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Unit tests for services.password_reset_service's bcrypt offload, unit F1
(concurrency-scaling-plan-2026-08-22.md; security-reviewer finding during
A1's review, same-day follow-up).

Before F1, ``consume_reset_token`` ran its verify loop (up to 256
candidates, bcrypt cost 12 each, ~213ms/call) synchronously inside the
request coroutine: an unauthenticated caller could stall every other
request on the worker for up to ~54s. ``request_password_reset``'s two
timing-flattening dummy verifications had the same, smaller-magnitude
defect. Both now go through core.security.verify_password_async or a
single asyncio.to_thread call.

These run without a database: a fake AsyncSession stands in for the one
query each function issues before its bcrypt work, which lets the tests
exercise the offload in isolation and stay fast. End-to-end coverage (real
Postgres, full HTTP round trip, the new rate limit) lives in
tests/integration/test_auth_password_reset.py.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from core.security import hash_password

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeResult:
    """Answers both the ``scalars().all()`` shape (consume_reset_token's
    candidate query) and the ``scalar_one_or_none()`` shape
    (request_password_reset's user lookup) from the same stub."""

    def __init__(self, rows: list[object]):
        self._rows = rows

    def scalars(self) -> _FakeResult:
        return self

    def all(self) -> list[object]:
        return self._rows

    def scalar_one_or_none(self) -> object | None:
        return self._rows[0] if self._rows else None


class _FakeSession:
    """Answers every ``execute()`` call with the same fixed row set.

    Both functions under test issue exactly one query before reaching their
    bcrypt work, so a single canned response is enough.
    """

    def __init__(self, rows: list[object]):
        self._rows = rows

    async def execute(self, _stmt: object) -> _FakeResult:
        return _FakeResult(self._rows)


def _fake_token(token_hash: str) -> SimpleNamespace:
    return SimpleNamespace(token_hash=token_hash)


def _fake_user(*, is_active: bool = True, is_service_account: bool = False) -> SimpleNamespace:
    return SimpleNamespace(is_active=is_active, is_service_account=is_service_account)


# ---------------------------------------------------------------------------
# consume_reset_token: event loop must stay free during the verify loop
# ---------------------------------------------------------------------------


async def test_consume_reset_token_no_match_does_not_block_the_event_loop():
    """The actual point of F1: up to 256 bcrypt verifications in one
    request must not stall other coroutines on the same loop.

    Uses fewer candidates than the real 256-row cap (still bcrypt cost 12
    each) to keep the test's own wall-clock reasonable while remaining long
    enough that "the loop kept ticking throughout" is a meaningful
    assertion rather than a coincidence.
    """
    from services.password_reset_service import InvalidResetToken, consume_reset_token

    candidates = [_fake_token(hash_password(f"unrelated-{i}")) for i in range(20)]
    session = _FakeSession(candidates)

    ticks = 0
    stop = False

    async def ticker() -> None:
        nonlocal ticks
        while not stop:
            ticks += 1
            await asyncio.sleep(0)

    async def consumer() -> None:
        nonlocal stop
        try:
            await consume_reset_token(
                session,
                plaintext_token="none-of-these-match-any-candidate",
                new_password="Sup3rS3cret!123456",
            )
        except InvalidResetToken:
            pass
        stop = True

    await asyncio.gather(ticker(), consumer())

    assert ticks > 20, (
        f"event loop only advanced {ticks} times while the reset-token verify "
        "loop was pending, bcrypt may be running inline on the loop again "
        "(F1 regression)"
    )


# ---------------------------------------------------------------------------
# consume_reset_token: dummy verification still runs after the offload
# ---------------------------------------------------------------------------


async def test_consume_reset_token_still_pays_dummy_verify_after_exhausting_candidates(
    monkeypatch,
):
    """Regression contract (F1, mirrors A1's dummy-path spy): moving the
    loop into a thread must not make the code skip the dummy verification
    that closes the "candidates existed but none matched" vs "no
    candidates at all" timing gap.

    Spies on the module's ``verify_password`` (the sync primitive the
    offloaded helper calls) and asserts it ran once per candidate plus one
    more time for the dummy hash.
    """
    from services import password_reset_service
    from services.password_reset_service import InvalidResetToken, consume_reset_token

    candidates = [_fake_token(hash_password(f"unrelated-{i}")) for i in range(5)]
    session = _FakeSession(candidates)

    calls: list[tuple[str, str]] = []
    real_verify_password = password_reset_service.verify_password

    def spy(plain: str, hashed: str) -> bool:
        calls.append((plain, hashed))
        return bool(real_verify_password(plain, hashed))

    monkeypatch.setattr(password_reset_service, "verify_password", spy)

    try:
        await consume_reset_token(
            session,
            plaintext_token="none-of-these-match-any-candidate",
            new_password="Sup3rS3cret!123456",
        )
    except InvalidResetToken:
        pass

    assert len(calls) == len(candidates) + 1, (
        "expected one verify per candidate plus one dummy verify, got "
        f"{len(calls)} calls for {len(candidates)} candidates"
    )
    assert calls[-1][1] == password_reset_service._DUMMY_BCRYPT_HASH


async def test_consume_reset_token_pays_dummy_verify_with_zero_candidates(monkeypatch):
    """Same contract, empty-candidate-set edge: this is the exact scenario
    the original design comment names (an attacker with no live tokens to
    match against must cost the same as one who has candidates but no
    match)."""
    from services import password_reset_service
    from services.password_reset_service import InvalidResetToken, consume_reset_token

    session = _FakeSession([])

    calls: list[tuple[str, str]] = []
    real_verify_password = password_reset_service.verify_password

    def spy(plain: str, hashed: str) -> bool:
        calls.append((plain, hashed))
        return bool(real_verify_password(plain, hashed))

    monkeypatch.setattr(password_reset_service, "verify_password", spy)

    try:
        await consume_reset_token(
            session,
            plaintext_token="anything",
            new_password="Sup3rS3cret!123456",
        )
    except InvalidResetToken:
        pass

    assert len(calls) == 1, "zero candidates must still pay exactly one dummy verify"
    assert calls[0][1] == password_reset_service._DUMMY_BCRYPT_HASH


# ---------------------------------------------------------------------------
# request_password_reset: dummy verification still runs after the offload
# ---------------------------------------------------------------------------


async def test_request_password_reset_still_runs_dummy_verify_for_unknown_email(monkeypatch):
    """Mirrors A1's ``test_authenticate_verifies_dummy_when_user_not_found``
    for the forgot-password side: the "unknown email" branch must still
    await verify_password_async, now that it's offloaded, not skip it."""
    from services import password_reset_service
    from services.password_reset_service import request_password_reset

    session = _FakeSession([])  # scalar_one_or_none() -> None: user not found

    calls: list[tuple[str, str]] = []
    real_verify_password_async = password_reset_service.verify_password_async

    async def spy(plain: str, hashed: str) -> bool:
        calls.append((plain, hashed))
        return bool(await real_verify_password_async(plain, hashed))

    monkeypatch.setattr(password_reset_service, "verify_password_async", spy)

    outcome = await request_password_reset(session, email="nobody@example.com")

    assert outcome["matched"] is False
    assert len(calls) == 1, "dummy verify_password_async must run on the unknown-email branch"
    assert calls[0][1] == password_reset_service._DUMMY_BCRYPT_HASH


async def test_request_password_reset_still_runs_dummy_verify_for_service_account(monkeypatch):
    """Same offloaded dummy path, service-account branch (treated like an
    unknown address per the existing anti-enumeration design)."""
    from services import password_reset_service
    from services.password_reset_service import request_password_reset

    session = _FakeSession([_fake_user(is_service_account=True)])

    calls: list[tuple[str, str]] = []
    real_verify_password_async = password_reset_service.verify_password_async

    async def spy(plain: str, hashed: str) -> bool:
        calls.append((plain, hashed))
        return bool(await real_verify_password_async(plain, hashed))

    monkeypatch.setattr(password_reset_service, "verify_password_async", spy)

    outcome = await request_password_reset(session, email="bot@example.com")

    assert outcome["matched"] is False
    assert len(calls) == 1
