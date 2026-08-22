# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Unit tests for ``core.security.verify_password_async`` — unit A1
(concurrency-scaling-plan-2026-08-22.md §1.3/§1.5/§3.3/§4).

These run without a database — pure ``core/security.py`` primitive tests,
same style as ``tests/unit/test_jwt.py``.

A1's contract (plan §4, A1 row): "인증 성공·실패 판정이 불변이다. 존재하지
않는 키와 틀린 키의 타이밍이 여전히 평탄하다" — success/failure verdicts are
unchanged, and the offload must not introduce a new timing gap. The
higher-level "dummy path still runs after offload" and "success/failure
matrix unchanged" checks live next to the code that has a dummy branch
(``tests/unit/test_security_config.py`` for login,
``tests/unit/services/test_api_key_service.py`` for API keys). This file
covers what is specific to the primitive itself: it returns the same
verdict as the synchronous function it wraps, and — the actual point of
A1 — a call in flight does not block the event loop it runs on.
"""

from __future__ import annotations

import asyncio

from core.security import hash_password, verify_password, verify_password_async


def test_verify_password_async_matches_sync_verdict_on_success():
    hashed = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", hashed) is True
    assert asyncio.run(verify_password_async("correct horse battery staple", hashed)) is True


def test_verify_password_async_matches_sync_verdict_on_failure():
    hashed = hash_password("correct horse battery staple")
    assert verify_password("wrong password", hashed) is False
    assert asyncio.run(verify_password_async("wrong password", hashed)) is False


def test_verify_password_async_matches_sync_verdict_on_malformed_hash():
    # verify_password swallows ValueError/TypeError from passlib and returns
    # False (e.g. a hash that isn't a bcrypt hash at all). The async wrapper
    # must preserve that — it must not let the exception escape from the
    # worker thread as an unhandled failure.
    assert verify_password("anything", "not-a-bcrypt-hash") is False
    assert asyncio.run(verify_password_async("anything", "not-a-bcrypt-hash")) is False


def test_verify_password_async_does_not_block_the_event_loop():
    """The actual point of A1: a bcrypt verification in flight must not
    prevent other coroutines on the same loop from making progress.

    Runs a tight ``asyncio.sleep(0)`` counter concurrently with
    ``verify_password_async`` and asserts the counter keeps advancing while
    the bcrypt call is pending. Before A1, ``verify_password`` ran inline on
    the loop — the counter would not advance at all for the ~213ms the
    verification took (concurrency-scaling-plan-2026-08-22.md §1.5). A free
    event loop doing nothing but ``sleep(0)`` yields thousands of times a
    second, so a floor far below that (but well above "zero, because the
    loop was blocked") is enough to catch a regression without being flaky.
    """
    hashed = hash_password("does not matter for this test")

    async def _run() -> int:
        ticks = 0
        stop = False

        async def ticker() -> None:
            nonlocal ticks
            while not stop:
                ticks += 1
                await asyncio.sleep(0)

        async def verifier() -> None:
            nonlocal stop
            await verify_password_async("wrong-guess-does-not-match", hashed)
            stop = True

        await asyncio.gather(ticker(), verifier())
        return ticks

    ticks = asyncio.run(_run())
    assert ticks > 20, (
        f"event loop only advanced {ticks} times while verify_password_async "
        "was pending — bcrypt may be running inline on the loop again "
        "(A1 regression)"
    )
