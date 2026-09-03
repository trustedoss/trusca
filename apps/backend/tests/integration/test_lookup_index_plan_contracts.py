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

The numbers behind each index are in migration 0078's docstring; they were
measured on 200,000 audit rows and 60,000 projects, not estimated.
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._search_explain import index_names_in_plan

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set, skipping index plan contracts")
    return url


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    _require_database_url()
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        pytest.skip(
            f"alembic upgrade head failed; index plan contracts cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


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
    plan = await _plan(
        db_session,
        """
        SELECT * FROM audit_logs
        WHERE target_table = :tt AND target_id = :tid AND action = 'create'
        ORDER BY created_at ASC, id ASC
        LIMIT 1
        """,
        {"tt": "vulnerability_findings", "tid": str(uuid.uuid4())},
    )

    assert "ix_audit_logs_target_table_target_id_created_at" in index_names_in_plan(
        plan
    ), (
        "the finding history query no longer resolves through the index built "
        "for it; either its WHERE shape changed or the index did"
    )


async def test_actor_scoped_audit_search_has_an_index_that_serves_it(
    db_session: AsyncSession,
) -> None:
    """Actor equality plus the newest-first ordering the admin search uses.

    ``ix_audit_logs_actor_user_id`` serves the predicate and leaves the order
    to a sort; under a LIMIT the planner would rather walk the created_at
    index backwards and discard every other actor's rows.
    """
    plan = await _plan(
        db_session,
        """
        SELECT * FROM audit_logs
        WHERE actor_user_id = :actor
        ORDER BY created_at DESC
        LIMIT 50
        """,
        {"actor": str(uuid.uuid4())},
    )

    assert "ix_audit_logs_actor_created_at" in index_names_in_plan(plan), (
        "the actor-scoped audit search no longer resolves through the index "
        "built for it"
    )


async def test_active_project_list_has_an_index_that_serves_it(
    db_session: AsyncSession,
) -> None:
    """The project list, which the dashboard reads too.

    The partial predicate is part of the contract: an index without
    ``WHERE archived_at IS NULL`` cannot serve this query, and a query that
    stops filtering on it cannot use this index. Either drift breaks here.
    """
    plan = await _plan(
        db_session,
        """
        SELECT * FROM projects
        WHERE team_id = :team AND archived_at IS NULL
        ORDER BY updated_at DESC, id DESC
        LIMIT 100
        """,
        {"team": str(uuid.uuid4())},
    )

    assert "ix_projects_team_updated_active" in index_names_in_plan(plan), (
        "the active-project list no longer resolves through the partial index "
        "built for it; check both the ORDER BY and the archived_at filter"
    )
