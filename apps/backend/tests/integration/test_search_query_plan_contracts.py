# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Search query-plan regression: miniature data, PR-gate (concurrency-scaling
plan, unit 13 / M3).

The plan's §1.4 finding: "there is no basis to judge search performance:
there is no test that asserts EXPLAIN, and the index is only checked by
declaration." ``test_search_index_contracts.py`` closed the declaration half
(migration vs. model metadata agree). This file closes the EXPLAIN half:
for the query the search-results page actually runs
(``services.search_results_service._components``), not a hand-rebuilt copy
(see ``tests/_search_explain.py`` module docstring for why that distinction
matters).

Miniature, not the 2-million-row dataset
------------------------------------------

The plan (§3.1 M3) is explicit: "only the plan assertions of M3 are left in
PR CI, on miniature data." The 200×20×500 dataset itself
(``scripts/seed_load_test.py``) and the EXPLAIN ANALYZE BUFFERS baseline it
enables (``test_search_explain_load_baseline.py``) are nightly/manual only.

That split creates one wrinkle this file works around: the Postgres query
planner reasonably prefers a sequential scan over an index scan on a
handful of rows regardless of which indexes exist: cost-based planning is
supposed to do that, and asserting "the trigram index is CHOSEN" against a
three-row table would be asserting something that is true only by luck. So
``test_trigram_index_available_for_each_query_kind`` forces
``SET LOCAL enable_seqscan = off`` before running ``EXPLAIN``: not to fake a
result, but to ask the honest question this scale-independent contract can
actually answer: "if Postgres refuses every path except an index, does one
exist that serves this exact ILIKE pattern?" That is scale-invariant in a way
"which plan the cost estimator prefers" is not, and it is exactly the
regression this file exists to catch: someone changes the WHERE clause shape
(the column, the wildcard position, the ``ESCAPE`` clause) such that the
GIN trigram index built by migration 0043 can no longer serve it at all.

``test_component_result_count_stays_flat_across_scan_history`` does NOT need
the seqscan trick: it asserts an exact search-result count on a controlled
dataset (1 scan vs. 5 scans of the SAME component), which is deterministic
regardless of which scan strategy the planner picks. This is the
miniature-scale form of the plan §6 question "does the result grow with scan
history": answered exactly instead of approximately, because at this scale
exact is cheap.

Q2 (concurrency-scaling plan unit 22) landed: the query now joins through
``services.scan_resolution.latest_succeeded_scan_select`` instead of every
scan a project has ever run, so growing a project's scan history no longer
grows the result. Only the latest succeeded scan is ever examined. Before Q2
this test asserted STRICT GROWTH (result count 1 -> 5) as the pre-Q2
baseline; it now asserts the opposite (flat at 1), per the note this
docstring carried until Q2 landed: "if this test starts failing because
growth stopped, that is Q2 landing, not a regression." (An earlier revision
of this update asserted an EXPLAIN-captured physical ``scan_components`` row
count instead of the search result; that measurement proved fragile against
the CI integration suite's large shared database, so it was replaced with
the result-based assertion (see the test's own docstring).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# The plan's three fixed query kinds (§6). Reused verbatim from the
# load-test seed script's own constants so both the miniature (this file)
# and heavy (test_search_explain_load_baseline.py) EXPLAIN tests document
# "why this string" in exactly one place.
from scripts.seed_load_test import QUERY_3CHAR, QUERY_COMMON, QUERY_UNCOMMON
from tests._db_required import migrate_to_head
from tests._helpers import (
    make_organization,
    make_project,
    make_scan,
    make_team,
    make_user,
    principal_for,
    unique_suffix,
)
from tests._search_explain import (
    explain_nth_statement,
    index_names_in_plan,
    node_types_in_plan,
)

pytestmark = pytest.mark.integration

# The trigram indexes migration 0043 builds for `components`: see
# tests/unit/test_search_index_contracts.py for the migration-vs-model
# contract these names come from.
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


async def _seed_component_version(
    session: AsyncSession, *, scan_id: uuid.UUID, name: str, dependency_path: str | None = None
) -> uuid.UUID:
    from models import Component, ComponentVersion, ScanComponent

    suffix = unique_suffix()
    purl = f"pkg:npm/{name}-{suffix}"
    component = Component(purl=purl, package_type="npm", name=name)
    session.add(component)
    await session.flush()

    cv = ComponentVersion(
        component_id=component.id, version="1.0.0", purl_with_version=f"{purl}@1.0.0"
    )
    session.add(cv)
    await session.flush()

    session.add(
        ScanComponent(
            scan_id=scan_id,
            component_version_id=cv.id,
            direct=True,
            dependency_path=dependency_path or f"./{name}",
        )
    )
    await session.commit()
    return cv.id


async def _call_components_search(session: AsyncSession, *, actor, q: str):
    from services.search_results_service import search_results

    return await search_results(session, actor=actor, kind="components", q=q, page=1, size=25)


@pytest.mark.parametrize(
    ("label", "query"),
    [
        ("3-char", QUERY_3CHAR),
        ("common-name", QUERY_COMMON),
        ("uncommon-name", QUERY_UNCOMMON),
    ],
)
async def test_trigram_index_serves_the_components_ilike_predicate(
    db_session: AsyncSession, label: str, query: str
) -> None:
    """The exact WHERE-clause SHAPE ``_components`` uses stays index-servable.

    Deliberately does NOT capture-and-EXPLAIN the real join
    (``services.search_results_service._components``'s full page query).
    A first version of this test did that, and it failed even with
    ``enable_seqscan = off`` forced. The query starts from
    ``scan_components`` (``.select_from(ScanComponent)``), and when that
    table has few matching rows, a Nested Loop through the ``component_versions``
    / ``components`` PRIMARY KEYS legitimately costs less than a trigram
    Bitmap Index Scan even with sequential scans penalized, because
    ``enable_seqscan = off`` discourages one access method but does not pin
    a join order. Whether the real join ends up USING the trigram index is
    a genuine data-distribution question (more scan history makes the
    driving-table Nested Loop cheaper; a more selective ILIKE makes the
    trigram Bitmap Scan cheaper). The heavy baseline test
    (``test_search_explain_load_baseline.py``) measures that at real scale
    instead of asserting it here.

    What IS scale-invariant, and what this test asserts, is narrower and
    still catches the regression that matters: given the identical ILIKE +
    ``ESCAPE '\\'`` predicate ``_components`` builds
    (``Component.name.ilike(like, escape="\\")`` / ``Component.purl.ilike(...)``),
    run in isolation against ``components`` with ``enable_seqscan = off``,
    Postgres has no OTHER usable index for it (``ix_components_type_name`` is
    a b-tree on ``(package_type, name)`` and cannot serve a leading
    wildcard). So if the trigram index still exists with the right
    operator class, this is the one query shape where the planner is left
    with no alternative but to pick it. A change to the predicate shape
    itself (dropped ``ESCAPE``, wrapped in a cast, wildcard moved) would
    still fail this even though the index metadata is unchanged, which is
    exactly the drift ``test_search_index_contracts.py``'s declaration-only
    check cannot see.
    """
    from core.sql_safety import escape_like
    from models import Component

    like = f"%{escape_like(query)}%"

    async def _run() -> None:
        stmt = select(Component.id).where(
            or_(
                Component.name.ilike(like, escape="\\"),
                Component.purl.ilike(like, escape="\\"),
            )
        )
        await db_session.execute(stmt)

    await db_session.execute(text("SET LOCAL enable_seqscan = off"))
    _result, plan_root, _sql = await explain_nth_statement(db_session, _run, index=0)
    plan = plan_root["Plan"]
    found = index_names_in_plan(plan)
    assert found & _COMPONENT_TRIGRAM_INDEXES, (
        f"[{label}] query {query!r} produced no plan using "
        f"{sorted(_COMPONENT_TRIGRAM_INDEXES)} even with sequential scan "
        f"disabled and no competing index for this predicate: "
        f"indexes found: {sorted(found)}"
    )


async def test_dedup_step_present_in_plan(db_session: AsyncSession) -> None:
    """The ``DISTINCT`` the components query relies on shows up as a real
    plan node (``HashAggregate`` / ``Unique`` / ``Group``): the structural
    half of plan §6's "does sort/DISTINCT show real work" question. Whether
    it SPILLS TO DISK is a data-volume question the heavy baseline test
    answers; at this row count Postgres will never spill, so this file only
    asserts the dedup step exists at all (a change that silently dropped the
    ``.distinct()` call would still return "correct" rows here by accident,
    since one project has only one scan: this at least catches the plan
    shape disappearing).

    ``index=2``, not 1: Q2 added a scan-id resolution query
    (``latest_succeeded_scan_select``) as statement 0, pushing the COUNT to 1
    and the page ``SELECT`` (the one this test wants) to 2.
    """
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team, name="plan-contract-dedup")
    scan = await make_scan(db_session, project=project, status="succeeded")
    project.latest_scan_id = scan.id
    await db_session.commit()
    await _seed_component_version(db_session, scan_id=scan.id, name="lodash")

    user = await make_user(db_session, is_superuser=True)
    actor = principal_for(user)

    _result, plan_root, _sql = await explain_nth_statement(
        db_session,
        lambda: _call_components_search(db_session, actor=actor, q=QUERY_COMMON),
        index=2,
    )
    plan = plan_root["Plan"]
    types = node_types_in_plan(plan)
    assert types & {"HashAggregate", "Unique", "Group"}, (
        f"expected a dedup plan node for the components DISTINCT query, got: {sorted(types)}"
    )


async def test_component_result_count_stays_flat_across_scan_history(
    db_session: AsyncSession,
) -> None:
    """Post-Q2 property (plan §6, §1.4, §3.4 Q2): more scan history, same result.

    Same project, first with ONE succeeded scan then with FIVE, each of the
    four extra scans carrying its OWN distinct component named the same
    marker (diamond-safe: the unique constraint is
    ``(scan_id, component_version_id, dependency_path)``, and both
    ``component_version_id`` and ``scan_id`` differ every time). The four
    extra scans are backdated (``created_at`` in the past relative to the
    first), so the FIRST scan stays each project's latest succeeded scan
    throughout: the same setup this test used pre-Q2, but now exercising the
    property Q2 introduced instead of the one it removed.

    The query now joins through
    ``services.scan_resolution.latest_succeeded_scan_select`` instead of
    every scan a project has ever run, so a search for this marker should
    keep returning exactly ONE hit as backdated scan history accumulates:
    growing the history no longer grows the result, because only the
    current scan's row is ever in scope. Pre-Q2 this would have gone 1 -> 5
    (a fresh, distinct marker-named component in every one of the five
    scans, all reachable), which is the STRICT GROWTH this test asserted
    before Q2 landed; the docstring at the time said "if this test starts
    failing because growth stopped, that is Q2 landing, not a regression".
    This is that update.

    Asserts on the search RESULT (``SearchResultsPage.total``), not an
    EXPLAIN-captured physical row count: an earlier version of this test
    asserted ``total_actual_rows(..., relation="scan_components")`` before
    and after, which passed against a freshly migrated database but failed
    against the CI integration suite's shared, heavily-populated database
    (1600+ preceding tests): the super-admin actor's unrestricted scope
    feeds ``latest_succeeded_scan_select`` a large accumulated project set,
    and at that scale Postgres can choose a plan for the marker's
    highly-selective ILIKE where ``scan_components`` never appears as a
    named EXPLAIN node even though the join still correctly touches it
    (confirmed: the real service call logged ``total=1`` both times in that
    CI run, i.e. the search itself was already correct; only the plan-node
    introspection was fragile at that scale). The query RESULT is what the
    plan's §4 Q2 regression contract actually names ("components in the
    latest scan get the same result before and after narrowing"), and it is
    not sensitive to which access path the planner happens to choose.
    """
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team, name="plan-contract-growth")
    user = await make_user(db_session, is_superuser=True)
    actor = principal_for(user)

    now = datetime.now(tz=UTC)
    first_scan = await make_scan(db_session, project=project, status="succeeded")
    project.latest_scan_id = first_scan.id
    await db_session.commit()
    marker = f"growth-marker-{unique_suffix()}"
    await _seed_component_version(db_session, scan_id=first_scan.id, name=marker)

    result_at_one_scan = await _call_components_search(db_session, actor=actor, q=marker)
    assert result_at_one_scan.total == 1, (
        f"expected exactly one match for a freshly-seeded marker, got "
        f"{result_at_one_scan.total}"
    )

    # Four more succeeded scans of the SAME project, backdated so the FIRST
    # scan remains "latest" throughout, each carrying its own distinct
    # marker-named component on a scan Q2's join no longer reaches.
    latest_cv_id: uuid.UUID | None = None
    for i in range(4):
        scan = await make_scan(
            db_session,
            project=project,
            status="succeeded",
            created_at=now - timedelta(minutes=(4 - i)),
        )
        # `project.latest_scan_id` tracks the last *attempt*, not what the
        # resolver reads (see `services.scan_resolution`'s module docstring),
        # so updating it here to a chronologically OLDER (backdated) scan
        # deliberately mismatches `created_at` order. A real backfill could
        # do the same, and the resolver must still pick `first_scan`.
        project.latest_scan_id = scan.id
        await db_session.commit()
        latest_cv_id = await _seed_component_version(db_session, scan_id=scan.id, name=marker)
    assert latest_cv_id is not None

    result_at_five_scans = await _call_components_search(db_session, actor=actor, q=marker)
    assert result_at_five_scans.total == result_at_one_scan.total == 1, (
        f"expected the SAME single match at 5 scans of backdated history as "
        f"at 1 (got {result_at_five_scans.total} vs {result_at_one_scan.total}): "
        "the post-Q2 flat-with-history property does not hold"
    )
