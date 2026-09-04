# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Search EXPLAIN baseline: heavy dataset, nightly/manual only (concurrency-
scaling plan, unit 13 / M3).

Requires ``scripts/seed_load_test.py`` to have already populated the
database (default shape: 200 projects × 20 succeeded scans × 500 components
= 2,000,000 ``scan_components`` rows: see that script's module docstring).
This module does NOT run the seed itself: seeding at this scale takes
minutes, and a PR-gate suite that silently ballooned to that runtime the
first time someone touched a search file would be its own regression. Every
test here is guarded by :func:`_require_load_test_seed`, which SKIPS (not
fails) when the load-test organization is absent, which is always true on a
fresh PR-gate Postgres service container, so this file adds zero time to the
normal PR run without needing separate pytest wiring for it.

For a manual baseline capture, run this file on its own
(``pytest tests/integration/test_search_explain_load_baseline.py -s``), not
as part of a full ``tests/integration`` pass. ``test_scan_scheduler.py``
(pre-existing, unrelated to M3) truncates the whole ``scans`` table as part
of its own isolation strategy; that cascades to every ``scan_components``
row in the database, including this dataset's, mid-suite. The guard fixture
below detects that case and skips with an explanatory message instead of
failing confusingly, but a real measurement run should stay isolated.

What this measures (concurrency-scaling plan §6's three questions)
---------------------------------------------------------------------

  1. Does the trigram index built by migration 0043 remain in the plan at
     real scale? (The miniature PR-gate test,
     ``test_search_query_plan_contracts.py``, can only prove the index is
     *servable* in isolation; whether the planner actually *picks* it inside
     the full join is a data-distribution question this file is the only
     place that can honestly answer, per that file's module docstring.)
     Empirically it does, but ONLY once statistics are fresh:
     ``_require_load_test_seed`` re-``ANALYZE``s the five affected tables
     before every test, and that step is load-bearing, not defensive
     boilerplate. Building this file, the SAME unmodified 50k-row dataset
     went from "the trigram index is never chosen: the join always drives
     from ``scan_components`` instead" (measured right after the seed
     script's own post-load ``ANALYZE``) to "chosen every single time" a few
     minutes later with no code or data change in between. Autovacuum's
     background re-sampling had refined the statistics
     ``pg_trgm``/the planner's default LIKE-selectivity heuristic depend on
     enough to flip the join order. That instability window is itself part
     of the M3 baseline: a request that lands in the minutes after a large
     bulk write (a big scan burst, a bulk import) may see a materially
     different, and *more expensive*, plan than one that lands once
     autovacuum has caught up, and nothing in the request path controls
     that timing today.
  2. Does the row count read from ``scan_components`` grow with scan
     history rather than catalog size? Q2 (concurrency-scaling plan unit 22)
     landed: ``search_results_service._components`` now restricts the join to
     each project's latest succeeded scan only
     (``services.scan_resolution.latest_succeeded_scan_select``, the same
     helper ``_vulnerabilities``/``_licenses`` already used). Answered by
     comparing that SHIPPED query against an inline "legacy" variant rebuilt
     to the exact pre-Q2 shape (every scan a project has ever had), so the
     baseline this file prints stays a live "how much did Q2 help" record
     instead of going stale the moment the shipped query changed.
  3. Do sort / DISTINCT spill to disk? Recorded via ``Sort Method`` fields
     in the ``ANALYZE`` output; not hard-asserted either way (a spill here is
     an environment fact, ``work_mem``, not a code regression), but every
     run prints it so the concurrency-scaling tracker has a real number
     instead of an estimate.

Every test prints a JSON summary (``print(json.dumps(..., indent=2))``) so
running this file with ``pytest -s`` produces a paste-able baseline for the
tracker. Assertions are deliberately narrower than "the ideal query plan":
they catch outright breakage (the trigram index disappearing from the one
case it should be nearly certain to win, the shipped latest-only query
reading MORE ``scan_components`` rows than the legacy all-history variant,
which would mean the narrowing regressed) without pinning exact plan shapes
that legitimately vary with ``ANALYZE`` statistics and Postgres version.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from scripts.seed_load_test import (
    LOAD_TEST_ORG_SLUG,
    QUERY_3CHAR,
    QUERY_COMMON,
    QUERY_UNCOMMON,
)
from tests._db_required import migrate_to_head
from tests._helpers import make_user, principal_for
from tests._search_explain import (
    explain_nth_statement,
    index_names_in_plan,
    sort_methods_in_plan,
    total_actual_rows,
    total_buffers,
)

pytestmark = pytest.mark.integration

_COMPONENT_TRIGRAM_INDEXES = frozenset({"ix_components_name_trgm", "ix_components_purl_trgm"})


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


@pytest.fixture(autouse=True)
async def _require_load_test_seed(db_session: AsyncSession) -> None:
    """Skip every test in this module unless ``seed_load_test.py`` has run.

    This is the ONLY gate the heavy baseline needs: a fresh PR-gate Postgres
    service container never has this organization, so this fixture alone
    keeps the whole file out of the normal PR run with no extra pytest
    markers, env vars, or CI wiring.

    Also checks that the org still HAS scan_components, not just that it
    exists. Run this file on its own (as the module docstring says), not as
    part of a full ``tests/integration`` run: ``test_scan_scheduler.py``
    (unrelated to M3, pre-existing) does
    ``TRUNCATE TABLE scan_schedules, scans RESTART IDENTITY CASCADE`` as part
    of its own isolation strategy, which cascades to every row in
    ``scan_components`` project-wide, including this dataset's, while
    leaving the organization/team/project rows untouched. Observed directly
    while building this file: a full-suite run left the load-test
    organization present but with zero scan_components, which without this
    check surfaces as a confusing "matched nothing" failure instead of a
    skip pointing at the actual cause.
    """
    from sqlalchemy import func

    from models import Organization, Project, Scan, ScanComponent, Team

    existing = (
        await db_session.execute(
            select(Organization.id).where(Organization.slug == LOAD_TEST_ORG_SLUG)
        )
    ).scalar_one_or_none()
    if existing is None:
        pytest.skip(
            "load-test dataset not present: run "
            "`APP_ENV=dev python scripts/seed_load_test.py` first "
            "(concurrency-scaling plan M3)"
        )
        return

    team_id = (
        await db_session.execute(select(Team.id).where(Team.organization_id == existing))
    ).scalar_one()
    scan_component_total = (
        await db_session.execute(
            select(func.count())
            .select_from(ScanComponent)
            .join(Scan, Scan.id == ScanComponent.scan_id)
            .join(Project, Project.id == Scan.project_id)
            .where(Project.team_id == team_id)
        )
    ).scalar_one()
    if scan_component_total == 0:
        pytest.skip(
            "load-test organization exists but has 0 scan_components: "
            "something truncated the scans table after seeding (this file's "
            "own module docstring names a known culprit if you ran this "
            "inside a full `tests/integration` pass). Re-run "
            "`APP_ENV=dev python scripts/seed_load_test.py --reset` and run "
            "this file on its own"
        )

    # Re-ANALYZE right before measuring, every run. The seed script already
    # ANALYZEs once after loading, but autovacuum re-sampling in the
    # background between a seed run and a (possibly much later) test run
    # measurably changes the statistics `pg_trgm`'s selectivity estimate
    # depends on (observed directly while building this test): the SAME
    # 50k-row dataset, unmodified, went from "trigram index never chosen"
    # to "trigram index chosen every time" after nothing but the passage of
    # a couple of minutes (autovacuum's doing, not a code change). Baseline
    # numbers taken right after a fresh, deliberate ANALYZE are the
    # reproducible ones; numbers taken at an arbitrary point after seeding
    # are not, and that gap is itself worth recording in the tracker as a
    # caveat on how "stable" this query's plan really is under real
    # deployment conditions (autovacuum timing is not something a request
    # controls).
    from sqlalchemy import text

    await db_session.execute(
        text("ANALYZE components, component_versions, scan_components, scans, projects")
    )
    await db_session.commit()


async def _actor(session: AsyncSession):
    user = await make_user(session, is_superuser=True)
    return principal_for(user)


async def _explain_components_page(
    session: AsyncSession, *, actor, query: str, page: int, size: int = 25
) -> tuple[Any, dict[str, Any], float]:
    """EXPLAIN ANALYZE the components search page query for ``query``.

    Returns ``(result, plan_root, wall_clock_seconds)``: the wall clock is
    measured around the REAL (non-EXPLAIN) call ``explain_nth_statement``
    makes internally, i.e. it includes exactly the work
    ``services.search_results_service.search_results`` itself would do for
    this request, not the extra EXPLAIN re-execution.

    ``index=2``, not 1: Q2 added a scan-id resolution query
    (``latest_succeeded_scan_select``) as statement 0, pushing the COUNT to 1
    and the page ``SELECT`` (the one this measures) to 2.
    """
    from services.search_results_service import search_results

    started = time.perf_counter()

    async def _call():
        return await search_results(
            session, actor=actor, kind="components", q=query, page=page, size=size
        )

    result, plan_root, _sql = await explain_nth_statement(session, _call, index=2, analyze=True)
    elapsed = time.perf_counter() - started
    return result, plan_root, elapsed


async def _explain_all_history_legacy_variant(
    session: AsyncSession, *, actor, query: str
) -> dict[str, Any]:
    """The pre-Q2 query: same predicate, joined through EVERY scan a project
    has ever had instead of just its latest succeeded one.

    Not a call into application code: Q2 (concurrency-scaling plan unit 22)
    landed, so ``search_results_service._components`` no longer runs this
    shape. Rebuilt inline to the exact join it used to run, the same pieces
    the current query still shares (``core.authz.team_scope_filter`` +
    ``core.sql_safety.escape_like``), just without the
    ``services.scan_resolution.latest_succeeded_scan_select`` narrowing. That
    keeps this a real, valid query shape (not a hypothetical one) so the
    comparison below stays meaningful instead of going stale.
    """
    from sqlalchemy import or_

    from core.authz import team_scope_filter
    from core.sql_safety import escape_like
    from models import Component, ComponentVersion, Project, Scan, ScanComponent

    scope = team_scope_filter(actor)
    like = f"%{escape_like(query)}%"

    async def _run() -> None:
        stmt = (
            select(Component.id)
            .select_from(ScanComponent)
            .join(Scan, Scan.id == ScanComponent.scan_id)
            .join(Project, Project.id == Scan.project_id)
            .join(ComponentVersion, ComponentVersion.id == ScanComponent.component_version_id)
            .join(Component, Component.id == ComponentVersion.component_id)
            .where(scope)
            .where(Project.archived_at.is_(None))
            .where(
                or_(
                    Component.name.ilike(like, escape="\\"),
                    Component.purl.ilike(like, escape="\\"),
                )
            )
            .distinct()
        )
        await session.execute(stmt)

    _result, plan_root, _sql = await explain_nth_statement(session, _run, index=0, analyze=True)
    return plan_root


@pytest.mark.parametrize(
    ("label", "query"),
    [
        ("3-char", QUERY_3CHAR),
        ("common-name", QUERY_COMMON),
        ("uncommon-name", QUERY_UNCOMMON),
    ],
)
async def test_explain_baseline_latest_scan_only_vs_all_history_legacy(
    db_session: AsyncSession, label: str, query: str
) -> None:
    """Record + assert the concurrency-scaling plan §6 baseline for one query kind.

    Prints the full comparison so a run with ``pytest -s`` produces a
    paste-able record for the tracker (concurrency-scaling-tracker.md #13, #22).

    The assertion compares ``scan_components`` ROWS EXAMINED
    (:func:`total_actual_rows`), not total buffers. Buffers turned out to be
    the wrong metric here: at load-test scale the planner can independently
    pick a trigram-driven plan for EITHER the shipped query or the
    all-history legacy variant, and that choice (not the row-count property
    being measured) dominates the buffer count, occasionally making the
    legacy query show FEWER buffers than the shipped one even though it does
    strictly more relational work. Row count on the ``scan_components``
    relation is not sensitive to which access path got there; it is the same
    quantity the miniature test asserts exactly at small scale
    (``test_scan_components_rows_stay_flat_across_scan_history``), just read
    from ``ANALYZE`` instead of controlled from the seed.
    """
    actor = await _actor(db_session)

    result, plan_root, elapsed = await _explain_components_page(
        db_session, actor=actor, query=query, page=1
    )
    plan = plan_root["Plan"]
    current_buffers = total_buffers(plan)
    current_scan_component_rows = total_actual_rows(plan, relation="scan_components")
    current_indexes = index_names_in_plan(plan)
    spill_methods = sort_methods_in_plan(plan)

    legacy_plan_root = await _explain_all_history_legacy_variant(
        db_session, actor=actor, query=query
    )
    legacy_plan = legacy_plan_root["Plan"]
    legacy_buffers = total_buffers(legacy_plan)
    legacy_scan_component_rows = total_actual_rows(legacy_plan, relation="scan_components")

    summary = {
        "query_label": label,
        "query": query,
        "result_total": result.total,
        "current_latest_scan_only": {
            "buffers": current_buffers,
            "scan_components_rows_examined": current_scan_component_rows,
            "index_names": sorted(current_indexes),
            "sort_methods": spill_methods,
            "planning_time_ms": plan_root.get("Planning Time"),
            "execution_time_ms": plan_root.get("Execution Time"),
            "wall_clock_seconds": round(elapsed, 4),
        },
        "legacy_all_history_preview": {
            "buffers": legacy_buffers,
            "scan_components_rows_examined": legacy_scan_component_rows,
        },
    }
    print(json.dumps(summary, indent=2))  # noqa: T201 - intentional baseline record for the tracker

    assert result.total > 0, f"[{label}] query {query!r} matched nothing: seed data missing?"
    # Post-Q2 property (plan §6, §1.4, §3.4 Q2): reading only the latest
    # succeeded scan can only examine AT MOST as many scan_components rows as
    # reading every scan a project has ever had. Strict equality would mean
    # the two queries degenerated to the same join, which should not happen
    # when a project has scan history beyond its latest scan (the default
    # seed gives every project 20).
    assert current_scan_component_rows <= legacy_scan_component_rows, (
        f"[{label}] shipped latest-scan-only query examined "
        f"{current_scan_component_rows} scan_components rows, MORE than the "
        f"all-history legacy variant's {legacy_scan_component_rows}: "
        "the Q2 narrowing property does not hold"
    )


async def test_explain_baseline_uncommon_query_uses_trigram_index(
    db_session: AsyncSession,
) -> None:
    """The most selective query kind should be the one Postgres is almost
    certain to serve with the trigram index at 2-million-row scale: the
    real-scale counterpart to the miniature test's isolated-predicate check.
    """
    actor = await _actor(db_session)
    _result, plan_root, _elapsed = await _explain_components_page(
        db_session, actor=actor, query=QUERY_UNCOMMON, page=1
    )
    found = index_names_in_plan(plan_root["Plan"])
    assert found & _COMPONENT_TRIGRAM_INDEXES, (
        f"expected the uncommon-name query to use one of "
        f"{sorted(_COMPONENT_TRIGRAM_INDEXES)} at load-test scale, "
        f"found: {sorted(found)}"
    )


async def test_explain_baseline_page_one_vs_deep_page(db_session: AsyncSession) -> None:
    """Time page 1 against a deep page (page 10) for the common-name query:
    plan §6's "결과 페이지는 1페이지와 깊은 페이지를 따로 잰다".

    No strict SLA assertion (this file is nightly/manual: the numbers are
    for the tracker, not a gate); the bound below only catches a
    catastrophic regression (a query that never returns).
    """
    actor = await _actor(db_session)

    _result_p1, plan_p1, elapsed_p1 = await _explain_components_page(
        db_session, actor=actor, query=QUERY_COMMON, page=1
    )
    _result_p10, plan_p10, elapsed_p10 = await _explain_components_page(
        db_session, actor=actor, query=QUERY_COMMON, page=10
    )

    summary = {
        "query": QUERY_COMMON,
        "page_1": {
            "wall_clock_seconds": round(elapsed_p1, 4),
            "execution_time_ms": plan_p1.get("Execution Time"),
        },
        "page_10": {
            "wall_clock_seconds": round(elapsed_p10, 4),
            "execution_time_ms": plan_p10.get("Execution Time"),
        },
    }
    print(json.dumps(summary, indent=2))  # noqa: T201 - intentional baseline record for the tracker

    assert elapsed_p1 < 60, f"page 1 took {elapsed_p1:.2f}s: catastrophic regression"
    assert elapsed_p10 < 60, f"page 10 took {elapsed_p10:.2f}s: catastrophic regression"
