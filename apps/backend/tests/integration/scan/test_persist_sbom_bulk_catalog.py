# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""#398, batch catalog upsert inside ``persist_sbom_components``.

Before this fix, ``_get_or_create_component`` / ``_get_or_create_component_
version`` / ``_get_or_create_license`` each ran their own SELECT (and, on a
miss, their own SAVEPOINT-guarded INSERT) once per raw SBOM component: up to
tens of thousands of DB round trips for a large Java/Node project, the
bottleneck that set the 60-minute scan soft limit.

``_prefetch_component_catalog`` now warms a per-scan cache the three
functions consult before falling through to that original per-row path, so
the common case resolves in O(few) queries. This file proves three things
the correctness-only tests elsewhere (``test_ingest_sbom_pipeline.py``,
``test_component_license_insert_race.py``) do not:

  1. the batching actually fires: a real 2,544-component SBOM persists in a
     bounded number of statements, not one-per-component (hardening rule 7:
     a passing test that only checked row counts would pass just as happily
     if the fast path silently stopped firing and every call fell through to
     the slow path);
  2. the batch resolvers survive the same concurrent-insert race
     ``test_component_license_insert_race.py`` proves for the per-row
     helpers, at batch scale;
  3. a second scan against the same catalog reuses it rather than
     duplicating it.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar, cast

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker
from structlog.testing import capture_logs

from models import Component, ComponentDependencyEdge, ComponentVersion, ScanComponent
from tests._db_required import migrate_to_head

pytestmark = pytest.mark.integration

T = TypeVar("T")

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "sbom_ingest"
    / "real_cyclonedx_large_js_2544.cdx.json"
)


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


def _large_sbom() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(FIXTURE.read_text(encoding="utf-8")))


def _seed_queued_scan() -> uuid.UUID:
    """Return a fresh ``scans.id``, seeded via the async helpers (same
    pattern as ``test_jsonb_size_guard.py``'s ``_seed_queued_scan``:
    ``persist_sbom_components`` takes a SYNC session, but the org/team/
    project/scan scaffolding helpers are async)."""
    import asyncio

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from core.config import database_url

    async def _build() -> uuid.UUID:
        engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as s:
            from models import Scan
            from tests._helpers import (
                make_membership,
                make_organization,
                make_project,
                make_team,
                make_user,
            )

            org = await make_organization(s)
            team = await make_team(s, organization=org)
            user = await make_user(s)
            await make_membership(s, user=user, team=team, role="developer")
            project = await make_project(s, team=team)
            scan = Scan(
                project_id=project.id,
                kind="source",
                status="queued",
                progress_percent=0,
                requested_by_user_id=user.id,
                scan_metadata={},
            )
            s.add(scan)
            await s.commit()
            await s.refresh(scan)
            scan_id = scan.id
        await engine.dispose()
        return scan_id

    return asyncio.run(_build())


def _record_statements(session: Session, call: Callable[[], T]) -> tuple[T, list[str]]:
    """Run ``call`` and return its result plus every SQL statement it issued."""
    engine = session.get_bind()
    statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _record)
    try:
        result = call()
    finally:
        event.remove(engine, "before_cursor_execute", _record)
    return result, statements


def _touching(statements: list[str], *tables: str) -> list[str]:
    """The subset of ``statements`` whose FROM/INTO/UPDATE names one of ``tables``."""
    return [s for s in statements if any(f" {t} " in f" {s} " for t in tables)]


# ---------------------------------------------------------------------------
# 1. The batching actually fires
# ---------------------------------------------------------------------------


def test_catalog_lookup_stays_flat_regardless_of_component_count(
    sync_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2,544 real components must not cost 2,544 catalog round trips.

    Scoped to statements against ``components`` / ``component_versions`` /
    ``licenses`` specifically, NOT total statement count, so this test stays
    meaningful regardless of what ``scan_components`` / ``license_findings``
    / ``component_dependency_edges`` cost (#461 later batched those too; see
    ``test_scan_component_and_edge_inserts_batch_too`` below for that half).
    A total-statement bound would have conflated the two fixes and stayed
    green even if this one regressed, as long as the total was dwarfed by
    the other tables' row count; this test would not have caught that.

    Disables the registry license fetcher: this fixture has real packages
    cdxgen left unlicensed, and that fallback is a genuine per-purl network
    call, a separate, already-cached subsystem #398 does not touch. Without
    this, the statement/time budget here would measure live HTTP calls to
    public registries, not the catalog batching this test exists to pin.
    """
    monkeypatch.setenv("LICENSE_FETCH_ENABLED", "false")
    from tasks.scan_source import persist_sbom_components

    scan_id = _seed_queued_scan()
    sbom = _large_sbom()

    _, statements = _record_statements(
        sync_session,
        lambda: persist_sbom_components(sync_session, scan_uuid=scan_id, sbom=sbom),
    )
    sync_session.commit()

    component_count = sync_session.execute(
        select(func.count()).select_from(ScanComponent).where(ScanComponent.scan_id == scan_id)
    ).scalar_one()
    assert component_count == 2544

    catalog_statements = _touching(statements, "components", "component_versions", "licenses")
    # Measured on a fresh catalog (this fixture's 1,875 distinct components /
    # 2,544 versions / 23 licenses all new): 1 SELECT + 1 batched INSERT for
    # components, 1 SELECT + 1 batched INSERT + 1 post-insert re-SELECT for
    # component_versions, 1 SELECT + one INSERT per NEW license (small,
    # unbatched, license cardinality per SBOM is tiny) for licenses: low
    # double digits, nowhere near 2,544. The bound below is several times
    # that floor, generous for CI variance and a nonzero fixture-license
    # count, while still an order of magnitude under one-per-component.
    assert len(catalog_statements) < 100, (
        f"{len(catalog_statements)} components/component_versions/licenses "
        f"statements for {component_count} components, looks like the "
        "per-row catalog path fired instead of the #398 prefetch"
    )


def test_scan_component_and_edge_inserts_batch_too(
    sync_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#461: ``scan_components`` / ``license_findings`` / ``component_
    dependency_edges`` batch now too, the gap #398 explicitly left open
    (their ``created_at`` had only a server_default, which blocks
    insertmanyvalues the same way an id-only server_default blocked the
    catalog tables it fixed).

    Unlike the catalog fix, the call shape here is UNCHANGED: still plain
    ``session.add()`` inside the same per-component loop, not a Core
    ``pg_insert``. The three models simply gained a client-side
    ``created_at`` default alongside their existing server_default (see
    ``models/scan.py``), which is enough on its own for SQLAlchemy's
    unit-of-work to coalesce the pending inserts at flush time.

    The commit has to be INSIDE the measured call, not after it like the
    catalog test above: the catalog's own inserts are eager Core statements
    (``session.execute(pg_insert(...))``), so they fire the moment
    ``persist_sbom_components`` runs. ``scan_components`` / ``license_
    findings`` / ``component_dependency_edges`` go through plain
    ``session.add()``, which SQLAlchemy defers until the next flush; measuring
    only up to the point ``persist_sbom_components`` returns (commit called
    after, as the catalog test does) captures zero of these three tables'
    statements regardless of whether they batch. A first draft of this test
    made exactly that mistake and passed unchanged with the fix reverted;
    caught by mutation-testing the assertions, not by review.
    """
    monkeypatch.setenv("LICENSE_FETCH_ENABLED", "false")
    from tasks.scan_source import persist_sbom_components

    scan_id = _seed_queued_scan()
    sbom = _large_sbom()

    def _persist_and_commit() -> None:
        persist_sbom_components(sync_session, scan_uuid=scan_id, sbom=sbom)
        sync_session.commit()

    _, statements = _record_statements(sync_session, _persist_and_commit)

    component_count = sync_session.execute(
        select(func.count()).select_from(ScanComponent).where(ScanComponent.scan_id == scan_id)
    ).scalar_one()
    assert component_count == 2544
    edge_count = sync_session.execute(
        select(func.count())
        .select_from(ComponentDependencyEdge)
        .where(ComponentDependencyEdge.scan_id == scan_id)
    ).scalar_one()
    assert edge_count > 0

    # Each bound is well under its own row count but generous enough for
    # SQLAlchemy's internal insertmanyvalues page size (batches of roughly
    # 1,000 rows per statement by default) to vary across versions without
    # making this test flaky. What it rules out is the one-statement-per-row
    # regression: a fixed floor near the true batch count, not near the
    # unbatched worst case, is what actually catches the fast path silently
    # falling back to the slow one.
    scan_component_statements = _touching(statements, "scan_components")
    assert len(scan_component_statements) < 50, (
        f"{len(scan_component_statements)} scan_components statements for "
        f"{component_count} rows, looks like insertmanyvalues did not batch"
    )
    edge_statements = _touching(statements, "component_dependency_edges")
    assert len(edge_statements) < 50, (
        f"{len(edge_statements)} component_dependency_edges statements for "
        f"{edge_count} rows, looks like insertmanyvalues did not batch"
    )
    license_finding_statements = _touching(statements, "license_findings")
    assert len(license_finding_statements) < 50, (
        f"{len(license_finding_statements)} license_findings statements, "
        "looks like insertmanyvalues did not batch"
    )


def test_scan_component_created_at_is_populated_without_a_round_trip(
    sync_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The client-side default actually lands a real, distinct timestamp
    per row (not e.g. a shared import-time constant, and not left NULL
    because the server_default alone never fires when a client value is
    present)."""
    monkeypatch.setenv("LICENSE_FETCH_ENABLED", "false")
    from tasks.scan_source import persist_sbom_components

    before = sync_session.execute(select(func.now())).scalar_one()
    scan_id = _seed_queued_scan()
    persist_sbom_components(sync_session, scan_uuid=scan_id, sbom=_large_sbom())
    sync_session.commit()
    after = sync_session.execute(select(func.now())).scalar_one()

    rows = sync_session.execute(
        select(ScanComponent.created_at)
        .where(ScanComponent.scan_id == scan_id)
        .limit(10)
    ).scalars().all()
    assert len(rows) == 10
    for created_at in rows:
        assert before <= created_at <= after


# ---------------------------------------------------------------------------
# 2. The batch resolvers survive the same race the per-row helpers do
# ---------------------------------------------------------------------------


@pytest.fixture
def session_factory() -> Any:
    from core.config import database_url_sync

    engine = create_engine(database_url_sync(), pool_pre_ping=True, future=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    try:
        yield factory
    finally:
        engine.dispose()


def _unique_purl() -> str:
    return f"pkg:npm/race-batch-{uuid.uuid4().hex[:12]}"


def _steal_component(
    factory: sessionmaker[Session], purl: str, ready: threading.Event
) -> threading.Thread:
    def _run() -> None:
        session = factory()
        try:
            session.add(Component(purl=purl, name="race-winner", package_type="npm"))
            session.flush()
            ready.set()
            time.sleep(3.0)
            session.commit()
        finally:
            session.close()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread


def test_bulk_resolve_components_survives_concurrent_insert(
    session_factory: sessionmaker[Session],
) -> None:
    """Two workers' BATCH resolves racing on an overlapping purl: the loser's
    batch skips it (``ON CONFLICT DO NOTHING`` never raises) and re-selects
    the winner's row: no exception, and a purl staged earlier in the SAME
    batch survives."""
    from tasks.scan_source import _bulk_resolve_components

    contested = _unique_purl()
    ready = threading.Event()
    thief = _steal_component(session_factory, contested, ready)
    assert ready.wait(10), "second session never opened its insert"

    other = _unique_purl()
    session = session_factory()
    try:
        with capture_logs() as logs:
            resolved = _bulk_resolve_components(
                session,
                {
                    other: ("other-component", "npm"),
                    contested: ("race-loser", "npm"),
                },
            )
        session.commit()
    finally:
        session.close()
    thief.join(10)

    assert set(resolved) == {other, contested}
    matches = [e for e in logs if e.get("event") == "component_insert_race_batch"]
    assert matches, "component_insert_race_batch never logged, the two sessions did not collide"

    verify = session_factory()
    try:
        winner = verify.get(Component, resolved[contested])
        loser_batch_other = verify.get(Component, resolved[other])
    finally:
        verify.close()
    assert winner is not None and winner.name == "race-winner"
    assert loser_batch_other is not None and loser_batch_other.name == "other-component"


def test_bulk_resolve_component_versions_survives_concurrent_insert(
    session_factory: sessionmaker[Session],
) -> None:
    from tasks.scan_source import _bulk_resolve_component_versions

    seed = session_factory()
    try:
        component = Component(purl=_unique_purl(), name="race-component", package_type="npm")
        seed.add(component)
        seed.commit()
        component_id = component.id
        component_purl = component.purl
    finally:
        seed.close()

    contested = f"{component_purl}@1.0.0"
    ready = threading.Event()

    def _run() -> None:
        session = session_factory()
        try:
            session.add(
                ComponentVersion(
                    component_id=component_id, version="1.0.0", purl_with_version=contested
                )
            )
            session.flush()
            ready.set()
            time.sleep(3.0)
            session.commit()
        finally:
            session.close()

    thief = threading.Thread(target=_run, daemon=True)
    thief.start()
    assert ready.wait(10), "second session never opened its insert"

    other = f"{component_purl}@0.9.0"
    session = session_factory()
    try:
        resolved = _bulk_resolve_component_versions(
            session,
            {
                other: (component_id, "0.9.0"),
                contested: (component_id, "1.0.0"),
            },
        )
        session.commit()
    finally:
        session.close()
    thief.join(10)

    # No exception, and no distinct "race" log line: ON CONFLICT DO NOTHING
    # never raises, and the post-insert SELECT this function always runs
    # picks up whichever row actually won: a genuine collision resolves
    # exactly like the no-collision case, from the caller's side.
    assert set(resolved) == {other, contested}

    verify = session_factory()
    try:
        winner = verify.get(ComponentVersion, resolved[contested].id)
        staged = verify.get(ComponentVersion, resolved[other].id)
        count = verify.execute(
            select(func.count())
            .select_from(ComponentVersion)
            .where(ComponentVersion.purl_with_version == contested)
        ).scalar_one()
    finally:
        verify.close()
    assert winner is not None and winner.version == "1.0.0"
    assert staged is not None and staged.version == "0.9.0", (
        "the version staged in the SAME batch as the collision must still "
        "have committed, a whole-batch rollback would have silently dropped it"
    )
    assert count == 1, "exactly one row must survive the race"


# ---------------------------------------------------------------------------
# 3. A second scan reuses the catalog instead of duplicating it
# ---------------------------------------------------------------------------


def test_second_scan_reuses_the_catalog(
    sync_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LICENSE_FETCH_ENABLED", "false")
    from tasks.scan_source import persist_sbom_components

    sbom = _large_sbom()
    scan_a = _seed_queued_scan()
    persist_sbom_components(sync_session, scan_uuid=scan_a, sbom=sbom)
    sync_session.commit()

    component_count_after_first = sync_session.execute(
        select(func.count()).select_from(Component)
    ).scalar_one()

    scan_b = _seed_queued_scan()
    persist_sbom_components(sync_session, scan_uuid=scan_b, sbom=sbom)
    sync_session.commit()

    component_count_after_second = sync_session.execute(
        select(func.count()).select_from(Component)
    ).scalar_one()
    scan_b_components = sync_session.execute(
        select(func.count()).select_from(ScanComponent).where(ScanComponent.scan_id == scan_b)
    ).scalar_one()

    assert component_count_after_second == component_count_after_first, (
        "the second scan's prefetch must resolve to the FIRST scan's catalog "
        "rows, not insert duplicates"
    )
    assert scan_b_components == 2544
