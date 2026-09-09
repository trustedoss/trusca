# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Concurrency for ``services.group_service.reparent`` — group-hierarchy Phase 5
PR 5-A.

Mirrors ``tests/unit/services/test_admin_team_delete_concurrency.py``'s two
complementary shapes:

  1. Direct lock-behavior test
     (``test_lock_groups_in_id_order_blocks_concurrent_update``): proves
     ``_lock_groups_in_id_order`` actually takes a row lock a concurrent
     UPDATE blocks on, the same way ``_lock_team_for_destructive_op`` does
     for delete.

  2. End-to-end race: two ``reparent`` calls that try to make EACH OTHER's
     group their own parent — A's parent becomes B, B's parent becomes A, at
     the same time. With an inconsistent lock order this pair is the classic
     deadlock shape (each transaction holds the lock the other one wants
     next). ``reparent`` avoids it by locking both rows in ascending-id
     order regardless of which one is "the group" and which is "the new
     parent" in a given call (see ``_lock_groups_in_id_order``'s own
     docstring) — so the two transactions always contend on the SAME first
     lock, never in opposite orders. The task's own framing ("non-deferrable
     FK 락") describes what Postgres's foreign-key check would additionally
     serialise on if the app-level lock did not already; the app-level
     ``FOR UPDATE`` ordering is what this test pins as the actual
     deadlock-avoidance mechanism, verified by round-tripping the DB rather
     than trusting the design note.

  Both tests build A -> B as a chain first (A is B's parent), then race
  "move B under A" (a no-op re-affirmation... no: A is ALREADY B's parent in
  this file's fixture is avoided on purpose — see the fixture comment) against
  its mirror, so the race is genuinely a cycle attempt, not a no-op.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._db_required import migrate_to_head
from tests._helpers import make_organization, make_team, make_user, principal_for, unique_suffix

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Independent session factory — see test_admin_team_delete_concurrency.py
    for rationale (each concurrent task needs its own session/connection,
    not a shared one)."""
    from core.audit import install_audit_listeners
    from core.config import database_url

    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    install_audit_listeners(factory)
    try:
        yield factory
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Direct lock-behavior test
# ---------------------------------------------------------------------------


async def test_lock_groups_in_id_order_blocks_concurrent_update(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """``_lock_groups_in_id_order`` must take a row-level lock a concurrent
    UPDATE blocks on. If it is ever "simplified" back to a plain SELECT
    (dropping ``with_for_update()``), this test fires — same shape as
    ``test_lock_team_for_destructive_op_blocks_concurrent_update``."""
    from services.group_service import _lock_groups_in_id_order

    async with session_factory() as setup:
        org = await make_organization(setup)
        group = await make_team(setup, organization=org, name=f"lockgrp-{unique_suffix()}")

    session_a = session_factory()
    holder = await session_a.__aenter__()
    try:
        locked = await _lock_groups_in_id_order(holder, [group.id])
        assert group.id in locked

        async with session_factory() as competitor:
            await competitor.execute(text("SET LOCAL lock_timeout = '500ms'"))
            with pytest.raises(DBAPIError) as exc_info:
                await competitor.execute(
                    text("UPDATE groups SET name = name WHERE id = :gid"),
                    {"gid": str(group.id)},
                )
            err = str(exc_info.value).lower()
            assert "lock" in err and ("timeout" in err or "55p03" in err), (
                f"expected lock_timeout error, got: {exc_info.value!r}"
            )
            await competitor.rollback()
    finally:
        await holder.rollback()
        await session_a.__aexit__(None, None, None)


async def test_lock_groups_in_id_order_locks_ascending_regardless_of_input_order(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Two calls to ``_lock_groups_in_id_order`` with the SAME two ids but
    OPPOSITE argument order must still lock in the same (ascending) order —
    that is the whole deadlock-avoidance property. Pinned indirectly: the
    second call, racing the first (which holds both locks), must block on
    whichever id is numerically smaller FIRST, not on the order it was
    passed in the argument list."""
    from services.group_service import _lock_groups_in_id_order

    async with session_factory() as setup:
        org = await make_organization(setup)
        g1 = await make_team(setup, organization=org, name=f"g1-{unique_suffix()}")
        g2 = await make_team(setup, organization=org, name=f"g2-{unique_suffix()}")

    lo_id, hi_id = sorted([g1.id, g2.id])

    session_a = session_factory()
    holder = await session_a.__aenter__()
    try:
        # Lock only the LOWER id first, holding it open.
        locked = await _lock_groups_in_id_order(holder, [lo_id])
        assert set(locked) == {lo_id}

        # A competitor asking for BOTH ids, passed in descending argument
        # order, must still contend on lo_id first (proving the function
        # sorts its own input rather than trusting caller order).
        async with session_factory() as competitor:
            await competitor.execute(text("SET LOCAL lock_timeout = '500ms'"))
            with pytest.raises(DBAPIError) as exc_info:
                await _lock_groups_in_id_order(competitor, [hi_id, lo_id])
            err = str(exc_info.value).lower()
            assert "lock" in err and ("timeout" in err or "55p03" in err)
            await competitor.rollback()
    finally:
        await holder.rollback()
        await session_a.__aexit__(None, None, None)


# ---------------------------------------------------------------------------
# End-to-end race: A -> parent B, B -> parent A, at the same time
# ---------------------------------------------------------------------------


async def test_concurrent_mutual_reparent_serialises_without_deadlock(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Two unrelated root groups A and B. Two concurrent ``reparent`` calls:
    one tries to make A a child of B; the other, at the same time, tries to
    make B a child of A. Whichever commits first wins outright (a plain
    "move under a root" — nothing to reject); the SECOND one, once
    unblocked, re-reads the now-current tree and finds a cycle (the winner's
    move made the loser's target a descendant of the group it's trying to
    move) — it must raise GroupCycleDetected, not deadlock and not silently
    corrupt the tree.

    Both must complete (``asyncio.gather`` with no ``return_exceptions``
    swallowing) within the test's own timeout — a real deadlock would hang
    until Postgres's own ``deadlock_timeout`` (1s by default) fired a
    DeadlockDetected error on ONE side while the other still succeeds; that
    is also an acceptable-but-worse outcome this test would still catch
    (asserted as "not both succeeded, and no hang"), but the properly
    ordered lock should mean it never happens: the assertion below is exact,
    not just "eventually finished".
    """
    from services.group_service import GroupCycleDetected, reparent

    async with session_factory() as setup_session:
        org = await make_organization(setup_session)
        a = await make_team(setup_session, organization=org, name=f"race-a-{unique_suffix()}")
        b = await make_team(setup_session, organization=org, name=f"race-b-{unique_suffix()}")
        operator = await make_user(setup_session, is_superuser=True)
        actor = principal_for(operator, role="super_admin")

    a_id: uuid.UUID = a.id
    b_id: uuid.UUID = b.id

    async def _move(group_id: uuid.UUID, new_parent_id: uuid.UUID):
        async with session_factory() as session:
            try:
                return await reparent(
                    session, actor=actor, group_id=group_id, new_parent_id=new_parent_id
                )
            except Exception as exc:  # noqa: BLE001
                return exc

    results = await asyncio.wait_for(
        asyncio.gather(_move(a_id, b_id), _move(b_id, a_id)),
        timeout=15,
    )

    successes = [r for r in results if not isinstance(r, Exception)]
    cycles = [r for r in results if isinstance(r, GroupCycleDetected)]

    assert len(successes) == 1, f"expected exactly one winner, got results={results}"
    assert len(cycles) == 1, (
        f"expected the loser to raise GroupCycleDetected (its target became a "
        f"descendant of the group it tried to move), got results={results}"
    )

    # The tree is consistent: verify_group_paths() reports zero mismatches,
    # and exactly one of the two possible parent-child relationships holds.
    async with session_factory() as verify_session:
        mismatches = (
            await verify_session.execute(text("SELECT * FROM verify_group_paths()"))
        ).fetchall()
        assert mismatches == []

        row = (
            await verify_session.execute(
                text("SELECT id, parent_group_id FROM groups WHERE id IN (:a, :b)"),
                {"a": str(a_id), "b": str(b_id)},
            )
        ).all()
        parents = {r.id: r.parent_group_id for r in row}
        assert (parents[a_id] == b_id) != (parents[b_id] == a_id), (
            f"expected exactly one of A->B or B->A, got {parents}"
        )


# ---------------------------------------------------------------------------
# End-to-end race: moving a subtree while a concurrent reparent touches one
# of that subtree's own descendants (security review, Phase 5 PR 5-A)
# ---------------------------------------------------------------------------


async def test_concurrent_reparent_touching_a_shared_descendant_does_not_deadlock(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Reproduces the shape of a real deadlock a security review found live
    before ``reparent`` locked the full descendant set up front.

    Before the fix, ``reparent`` locked only {group_id, new_parent_id} up
    front and left every descendant of the moved group to be locked
    IMPLICITLY, later, by the bulk descendant-path-propagation UPDATE -- in
    whatever order Postgres's GIN-index scan happened to visit matching
    rows, not id order and not pre-declared anywhere. That broke the one
    property ``_lock_groups_in_id_order`` exists to provide (every
    transaction that might contend for the same rows locks them in the
    SAME order): a concurrent ``reparent`` call touching one of those same
    descendants, in its OWN correctly-ordered {smaller, larger} sequence,
    could hold the row this call's LATER, undeclared lock request needed,
    while this call held a row THAT call's own ordered sequence needed
    next -- a genuine circular wait. Reproduced live with T1 = move A
    (root, has grandchild Z) under an unrelated root B, T2 = concurrently
    promote Z to a direct child of A, when Z.id happened to sort before
    A.id.

    The fix makes both calls declare their FULL touched-row set (moved
    group + every current descendant + new parent) before locking any of
    it, all in one ascending-id pass -- so two calls that share a
    contested row always request it in the same relative order relative
    to everything else either one might also want, regardless of which
    way the ids happen to sort. That property does not depend on Z
    sorting before A specifically (unlike the bug, which only manifested
    in that one ordering), so this test does not need to search for an
    adversarial id pairing to be meaningful: it asserts the invariant
    holds across a handful of independently-seeded attempts, run
    concurrently with no artificial synchronisation, the same style
    ``test_concurrent_mutual_reparent_serialises_without_deadlock`` above
    already uses.
    """
    from services.group_service import reparent

    async def _seed() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
        async with session_factory() as setup:
            org = await make_organization(setup)
            a = await make_team(setup, organization=org, name=f"dl-a-{unique_suffix()}")
            b = await make_team(setup, organization=org, name=f"dl-b-{unique_suffix()}")
            mid = await make_team(
                setup, organization=org, name=f"dl-mid-{unique_suffix()}", parent=a
            )
            z = await make_team(
                setup, organization=org, name=f"dl-z-{unique_suffix()}", parent=mid
            )
        return a.id, b.id, z.id

    async with session_factory() as setup:
        operator = await make_user(setup, is_superuser=True)
        actor = principal_for(operator, role="super_admin")

    from sqlalchemy.exc import DBAPIError as _DBAPIError

    for _ in range(10):
        a_id, b_id, z_id = await _seed()

        async def _move_a_under_b(a_id=a_id, b_id=b_id):
            async with session_factory() as session:
                try:
                    return await reparent(session, actor=actor, group_id=a_id, new_parent_id=b_id)
                except Exception as exc:  # noqa: BLE001
                    return exc

        async def _promote_z_under_a(z_id=z_id, a_id=a_id):
            async with session_factory() as session:
                try:
                    return await reparent(session, actor=actor, group_id=z_id, new_parent_id=a_id)
                except Exception as exc:  # noqa: BLE001
                    return exc

        results = await asyncio.wait_for(
            asyncio.gather(_move_a_under_b(), _promote_z_under_a()), timeout=15
        )

        deadlocks = [r for r in results if isinstance(r, _DBAPIError)]
        assert not deadlocks, f"deadlock instead of a clean serialise: {deadlocks!r}"
        successes = [r for r in results if not isinstance(r, Exception)]
        assert len(successes) == 2, f"both non-conflicting moves should succeed: {results!r}"

        async with session_factory() as verify_session:
            mismatches = (
                await verify_session.execute(text("SELECT * FROM verify_group_paths()"))
            ).fetchall()
            assert mismatches == []


# ---------------------------------------------------------------------------
# create_subgroup: parent deleted between the existence pre-check and commit
# (security review, Low finding, Phase 5 PR 5-A)
# ---------------------------------------------------------------------------


async def test_create_subgroup_parent_deleted_concurrently_raises_not_found(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """``create_subgroup`` pre-checks that *parent_group_id* exists with a
    plain (unlocked) SELECT, so a concurrent deletion of that same parent,
    landing after the check but before this call's own commit, used to be
    reported as :class:`GroupSlugConflict` -- EVERY ``IntegrityError`` at
    commit was assumed to mean a sibling-slug race -- telling the caller to
    retry with a different slug, which cannot fix a parent that no longer
    exists.

    Forces the exact interleaving the TOCTOU gap describes: the parent
    passes ``create_subgroup``'s existence check, then (before this
    session's own flush/commit reaches Postgres) a second, independent
    session deletes and commits that same parent row. ``AsyncSession.commit``
    is patched only to pause the target session at the point it would
    normally call Postgres -- the deletion is real, on a real second
    connection, and the resulting error this test drives through
    ``create_subgroup`` is Postgres's own: NOT a foreign-key violation (see
    ``_parent_group_still_exists``'s docstring for why the vanished-parent
    case does not actually surface as one), but a NOT NULL violation on
    ``path`` from ``groups_derive_path()`` finding no parent row to read a
    path from.
    """
    from services.group_service import GroupHierarchyNotFound, create_subgroup

    async with session_factory() as setup:
        org = await make_organization(setup)
        parent = await make_team(
            setup, organization=org, name=f"vanishing-parent-{unique_suffix()}"
        )
        operator = await make_user(setup, is_superuser=True)
        actor = principal_for(operator, role="super_admin")

    parent_id: uuid.UUID = parent.id

    creator_ready = asyncio.Event()
    parent_deleted = asyncio.Event()
    orig_commit = AsyncSession.commit

    async def _paused_commit(self: AsyncSession, *args: object, **kwargs: object) -> None:
        # Only the creator's own session pauses here -- the patch is
        # process-wide (it replaces the AsyncSession.commit *class*
        # attribute), so without this guard the deleter session's own
        # commit() below would ALSO wait on parent_deleted, which this
        # test itself only sets after that commit returns: a self-inflicted
        # deadlock, not the race under test.
        if self is not creator_session:
            await orig_commit(self, *args, **kwargs)
            return
        creator_ready.set()
        await parent_deleted.wait()
        await orig_commit(self, *args, **kwargs)

    async with session_factory() as creator_session:
        with patch.object(AsyncSession, "commit", _paused_commit):
            create_task = asyncio.create_task(
                create_subgroup(
                    creator_session,
                    actor=actor,
                    parent_group_id=parent_id,
                    name="orphaned-by-race",
                    slug=f"orphaned-{unique_suffix()}",
                )
            )

            await asyncio.wait_for(creator_ready.wait(), timeout=5)

            async with session_factory() as deleter_session:
                await deleter_session.execute(
                    text("DELETE FROM groups WHERE id = :id"), {"id": str(parent_id)}
                )
                await deleter_session.commit()

            parent_deleted.set()

            with pytest.raises(GroupHierarchyNotFound):
                await asyncio.wait_for(create_task, timeout=5)
