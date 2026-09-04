"""The three ER12 indexes stay servable by the queries that motivated them.

Building an index and not having the query use it is the ordinary way this
kind of work fails, and it fails silently: the migration succeeds, the index
occupies disk and slows every write, and the plan is exactly what it was. So
these assert the plans, not the presence of the index rows.

What "servable" means here, and why it is not "chosen"
-----------------------------------------------------
These run on a handful of rows, and on a handful of rows a sequential scan is
the correct plan no matter which indexes exist. Asserting the index is CHOSEN
at this scale would be asserting something true only by luck, and would go red
the first time somebody adds a row. So, following
``test_search_query_plan_contracts``: force ``enable_seqscan = off`` and ask
the question that IS scale-invariant. Given this exact predicate shape, does
an index exist that can serve it at all, or is Postgres left with a scan even
when scans are penalised?

That catches the regression that matters. Someone reorders the index columns,
narrows the partial predicate, or changes the query's WHERE shape so the index
can no longer apply. It does not catch "the planner stopped preferring it at
scale", which is a data-distribution question the load baseline measures.

When a plan assertion may name ONE index
----------------------------------------
Only when the penalised planner has one index to choose from. Not when one
index covers the whole predicate, which is the test an earlier version of this
file applied and got wrong twice.

The difference is what the planner needs an index to do. It does not need one
that answers the query; it needs one whose leading columns match part of the
predicate, and it will take the rest as a heap filter and the ordering as a
sort. By that measure ``ix_audit_logs_target_table`` competes with the
three-column index on ``(target_table, target_id, created_at)``, and
``ix_audit_logs_actor_user_id`` competes with ``(actor_user_id, created_at)``,
even though neither covers its query. Which one wins is then a cost decision
that moves with the data, and both assertions duly went red when a change
elsewhere added audit rows.

So: count the indexes whose leading column the predicate constrains. More than
one means a definition contract, as the two audit tests and the project test
below use. Asserting the winner is asserting the statistics that happen to be
in the table when the suite reaches this file.

The numbers behind each index are in migration 0078's docstring; they were
measured on 200,000 audit rows and 60,000 projects, not estimated.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from models import AuditLog
from tests._db_required import migrate_to_head
from tests._helpers import make_user
from tests._search_explain import index_names_in_plan

pytestmark = pytest.mark.integration



@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    from core.config import database_url

    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _plan(
    session: AsyncSession, sql: str, params: dict[str, object]
) -> dict[str, Any]:
    """EXPLAIN one statement with sequential scans penalised.

    ``SET LOCAL`` so the setting dies with the transaction and cannot leak
    into another test in the same session.
    """
    await session.execute(text("SET LOCAL enable_seqscan = off"))
    raw = await session.execute(text(f"EXPLAIN (FORMAT JSON) {sql}"), params)
    payload = raw.scalar_one()
    plan = payload if isinstance(payload, list) else json.loads(payload)
    node: dict[str, Any] = plan[0]["Plan"]
    return node


async def _seed_audit_rows(session: AsyncSession, *, target_id: str) -> uuid.UUID:
    """A few audit rows for one finding, plus a handful for other findings.

    Rows matter: asked about a target nothing matches, Postgres picks the
    cheapest index that covers any predicate and the choice says nothing about
    the query this contract is for. A first version of this file passed a
    random UUID and got ``ix_projects_team_id`` back, which is a correct plan
    for zero rows and the wrong thing to assert.
    """
    actor = await make_user(session)
    for i in range(6):
        session.add(
            AuditLog(
                actor_user_id=actor.id,
                action="update" if i % 2 else "create",
                target_table="vulnerability_findings",
                target_id=target_id if i < 3 else str(uuid.uuid4()),
                diff={"status": "new"},
            )
        )
    await session.flush()
    return actor.id


async def test_finding_history_lookup_has_an_index_that_serves_it(
    db_session: AsyncSession,
) -> None:
    """The shape ``services.vulnerability_service`` uses for the history panel.

    Two equality predicates and a time ordering. Before 0078 the only index
    that applied was ``ix_audit_logs_target_table``, which matches the first
    predicate and leaves ``target_id`` to a heap filter over what is by far
    the biggest bucket that column has: the scan pipeline writes one create
    row per finding.
    """
    target_id = str(uuid.uuid4())
    await _seed_audit_rows(db_session, target_id=target_id)

    plan = await _plan(
        db_session,
        """
        SELECT * FROM audit_logs
        WHERE target_table = :tt AND target_id = :tid AND action = 'create'
        ORDER BY created_at ASC, id ASC
        LIMIT 1
        """,
        {"tt": "vulnerability_findings", "tid": target_id},
    )

    indexes = index_names_in_plan(plan)
    # Any index leading with target_table can serve this predicate, and three
    # of them do (`ix_audit_logs_target_table`,
    # `ix_audit_logs_target_actor_created`, and the one 0078 added). Naming one
    # asserts a cost decision; what is scale-invariant is that Postgres is not
    # left with a scan.
    assert indexes & {
        "ix_audit_logs_target_table_target_id_created_at",
        "ix_audit_logs_target_actor_created",
        "ix_audit_logs_target_table",
    }, (
        "no index serves the finding history lookup even with sequential scans "
        f"penalised; either its WHERE shape changed or the indexes did. "
        f"Postgres chose {sorted(indexes)}"
    )

    row = (
        await db_session.execute(
            text(
                "SELECT indexdef FROM pg_indexes WHERE indexname = "
                "'ix_audit_logs_target_table_target_id_created_at'"
            )
        )
    ).scalar_one_or_none()

    assert row is not None, "the finding-history index is missing entirely"
    definition = " ".join(row.split())
    assert "(target_table, target_id, created_at)" in definition, (
        "the finding-history index no longer covers target_table and target_id "
        "before created_at, so target_id goes back to a heap filter over the "
        f"biggest bucket that column has: {definition}"
    )


async def test_actor_scoped_audit_search_has_an_index_that_serves_it(
    db_session: AsyncSession,
) -> None:
    """Actor equality plus the newest-first ordering the admin search uses.

    A definition contract plus a weak plan one, for the reason the
    active-project test gives: this index HAS a competitor.
    ``ix_audit_logs_actor_user_id`` covers the actor predicate on its own and
    leaves the ordering to a sort, so which of the two wins is a cost decision
    that moves with the data rather than a property of the index.

    It was a plan assertion naming ``ix_audit_logs_actor_created_at``, on the
    premise stated in this module that nothing else "gives actor plus time
    ordering". That premise reads the index as having to serve the whole query;
    the planner only needs it to serve the predicate, which the plain actor
    index does. The assertion therefore held by luck, and it went red exactly
    as this module's own docstring warned: a change elsewhere added audit rows,
    the estimate moved, and Postgres chose the sibling index. Nothing about the
    index or the query had changed.

    So this asserts what is scale-invariant. An index serves the predicate at
    all, and the composite one is shaped the way the ORDER BY needs: actor
    first, then created_at, which a btree walks backwards for the newest-first
    order without a sort. Drop it or reorder its columns and this fails, which
    is every way it could stop being the index the query needs at the size that
    matters. The "is it preferred at 200,000 rows" question belongs to
    migration 0078's measurement, not to a test that runs on six rows.
    """
    actor_id = await _seed_audit_rows(db_session, target_id=str(uuid.uuid4()))

    plan = await _plan(
        db_session,
        """
        SELECT * FROM audit_logs
        WHERE actor_user_id = :actor
        ORDER BY created_at DESC
        LIMIT 50
        """,
        {"actor": str(actor_id)},
    )

    indexes = index_names_in_plan(plan)
    # Either actor index is a correct answer here. What must not happen is
    # Postgres being left with a scan when scans are penalised, which is what
    # "no index applies to this predicate any more" looks like.
    assert indexes & {"ix_audit_logs_actor_created_at", "ix_audit_logs_actor_user_id"}, (
        "no index serves the actor-scoped audit search even with sequential "
        f"scans penalised; Postgres chose {sorted(indexes)}"
    )

    row = (
        await db_session.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE indexname = 'ix_audit_logs_actor_created_at'"
            )
        )
    ).scalar_one_or_none()

    assert row is not None, "the actor-scoped audit index is missing entirely"
    definition = " ".join(row.split())
    assert "(actor_user_id, created_at)" in definition, (
        "the actor-scoped audit index no longer leads with actor_user_id "
        "followed by created_at, so it cannot serve the admin search's "
        f"ORDER BY without a sort: {definition}"
    )


async def test_the_active_project_index_is_shaped_for_the_query_it_serves(
    db_session: AsyncSession,
) -> None:
    """A definition contract, not a plan one, and the difference is deliberate.

    Every index this file covers has a competitor by the standard above, so all
    three tests assert that SOME index serves the query plus a definition
    contract on the index the migration added, rather than naming the winner of
    a cost decision. This one competes with ``ix_projects_team_archived``,
    which covers both predicates and differs only in the ordering, so which
    index wins is a genuine cost decision that moves with the data. On the
    handful of rows a PR-gate test can seed, preferring the plain index and
    sorting six rows is the CORRECT plan, and asserting otherwise would be
    asserting luck. Measured at 60,000 projects the partial index wins (802
    buffers to 37, see migration 0078), but that measurement belongs to the
    migration rather than to a test that must pass on an empty database.

    So this asserts what is scale-invariant and still catches the drift that
    matters: the index exists with the column order the ORDER BY needs, the
    descending direction, and the partial predicate that restricts it to the
    half the list reads. Reorder the columns, drop the DESC, or widen the
    predicate and this fails, which is every way the index could stop being
    the one the query needs.
    """
    row = (
        await db_session.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE indexname = 'ix_projects_team_updated_active'"
            )
        )
    ).scalar_one_or_none()

    assert row is not None, "the active-project index is missing entirely"
    definition = " ".join(row.split())
    assert "(team_id, updated_at DESC)" in definition, (
        "the active-project index no longer leads with team_id followed by a "
        f"descending updated_at, so it cannot serve the list's ORDER BY: {definition}"
    )
    assert "WHERE (archived_at IS NULL)" in definition, (
        "the active-project index is no longer partial on the unarchived half, "
        f"so archived rows pay for an index nothing reads them through: {definition}"
    )
