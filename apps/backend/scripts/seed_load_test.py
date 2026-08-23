# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Large-scale search fixture: concurrency-scaling plan M3 (13).

``seed_demo.py`` seeds a realistic-LOOKING but tiny dataset (5 projects, one
scan each, ten components) so a fresh visitor has something to click on. It
is the wrong fixture for the question M3 exists to answer: the concurrency
plan's §1.4 finding is that the global/project search path has "no basis to
judge performance" against because the only seed in the repo is two orders of
magnitude smaller than the smallest deployment tier this product targets
(``docs/concurrency-scaling-plan-2026-08-22.md`` §0.3 T1 is 50 developers /
30 projects; this script's default is already past T3's 3,000-project scan
volume once scan history is folded in).

This script is a SEPARATE fixture from ``seed_demo.py`` on purpose: mixing
a "look nice for a visitor" dataset with a "generate 2 million rows to stress
an index" dataset would make neither script safe to run where the other is
expected. It seeds its own organization/team, namespaced under
:data:`LOAD_TEST_ORG_SLUG`, and every component it creates carries
``package_type='loadtest'`` so a ``--reset`` run can find and remove exactly
its own rows without touching anything else in the database.

Dataset shape (defaults match the concurrency plan's M3 row, §3.1)
--------------------------------------------------------------------

  * 1 organization, 1 team: namespaced, never collides with seed_demo's
    ``demo-org`` or a real deployment's data.
  * ``--projects`` projects (default 200), all in the one team.
  * ``--scans-per-project`` succeeded scans per project (default 20):
    this is the axis §1.4 says the current search query is NOT bounded by:
    ``scan_components`` grows with scan history, not with catalog size.
  * ``--components-per-scan`` distinct components observed per scan
    (default 500), so the default run seeds
    ``200 * 20 * 500 == 2,000,000`` ``scan_components`` rows: the exact
    figure the concurrency plan names for M3.

Query fixtures
--------------

Every scan of every project carries the same five "common" component names
(:data:`COMMON_COMPONENT_NAMES`) plus one "rare" name
(:data:`RARE_COMPONENT_NAME`) confined to the FIRST project only, so the
plan's §6 "three query kinds" (3-letter, common package name, uncommon
package name) have a stable, known-shape answer to assert against. The
:data:`QUERY_3CHAR` / :data:`QUERY_COMMON` / :data:`QUERY_UNCOMMON` constants
are exported so the EXPLAIN regression tests
(``tests/integration/test_search_explain_load_baseline.py``) import the exact
same strings this script seeded: two copies of one vocabulary would drift.

Allowed environments
---------------------

``dev`` only (stricter than ``seed_demo.py``'s ``{dev, demo}``): this is a
synthetic stress fixture, not something any deployment (including the demo
SaaS) should ever have running against it. Mirrors the guard shape
``core.config.scan_load_test_delay_seconds()`` already uses for the same
plan's M1 load-test knob.

Performance notes
------------------

Row counts at this scale rule out per-row ``session.add()``: the ORM
identity map and flush-per-object overhead would turn a few-minute job into
an hours-long one. Every large table (``components``, ``component_versions``,
``scan_components``) is populated with SQLAlchemy Core batched
``INSERT ... VALUES`` (``Table.insert()`` executed with a list of parameter
dicts, i.e. an executemany) instead of ORM objects. The small tables
(``organizations``, ``teams``, ``projects``, ``scans``: at most a few
thousand rows even at the default scale) use plain ORM objects for
readability; there is no measurable cost at that row count.

Idempotency
-----------

A second run without ``--reset`` finds the existing
:data:`LOAD_TEST_ORG_SLUG` organization and exits 0 without writing anything,
the same "already seeded" shape as ``seed_demo.py``. ``--reset`` deletes
the organization (cascades through team → projects → scans →
scan_components) and every ``package_type='loadtest'`` component (cascades
through component_versions) before reseeding from scratch.

Output
------

A single JSON line on stdout::

    {"organization_id": "...", "team_id": "...", "projects": N,
     "scans_per_project": N, "components_per_scan": N,
     "scan_components_total": N, "elapsed_seconds": N.N,
     "already_seeded": bool, "ok": true}

Exit codes
----------

  0: success (including "already seeded, nothing to do")
  1: refused (APP_ENV not allowed) or runtime failure
  2: argument error
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# Allow running the script from any cwd, matching seed_demo.py's convention.
BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# Allowed APP_ENV values. Stricter than seed_demo.py ({dev, demo}): this
# fixture is never appropriate for a running demo deployment, only a local
# or CI-provisioned dev database. Mirrors
# core.config.scan_load_test_delay_seconds()'s "dev only, no exceptions".
_ALLOWED_ENVS = frozenset({"dev"})

LOAD_TEST_ORG_SLUG = "trusca-load-test"
LOAD_TEST_ORG_NAME = "TRUSCA Load Test"
LOAD_TEST_TEAM_SLUG = "loadtest-team"
LOAD_TEST_TEAM_NAME = "Load Test"

# Every component this script creates carries this package_type so --reset
# can find and delete exactly its own rows (components are a shared,
# org-independent catalog, see models.scan.Component's docstring, so
# deleting the organization does not remove them).
LOAD_TEST_PACKAGE_TYPE = "loadtest"

# Present in EVERY project's EVERY scan: the "common package name" query
# case (concurrency plan §6: "흔한 패키지 이름").
COMMON_COMPONENT_NAMES: tuple[str, ...] = (
    "lodash",
    "requests",
    "axios",
    "spring-core",
    "jackson-databind",
)

# Present ONLY in the first project's scans: the "uncommon package name"
# query case (§6: "흔하지 않은 패키지 이름").
RARE_COMPONENT_NAME = "zzq-rare-widget-9182"

# Query fixtures the EXPLAIN regression tests import directly, so the seeded
# data and the query under test can never drift apart. QUERY_3CHAR is a
# 3-character substring of a common name (the plan's third query kind,
# "3글자": the shortest length the search endpoints accept
# (``services.search_service.MIN_QUERY_LEN`` == 3), which matches the pg_trgm
# floor exactly (wildcards shorter than 3 characters cannot use the index).
QUERY_3CHAR = "das"  # substring of "lodash"
QUERY_COMMON = COMMON_COMPONENT_NAMES[0]  # "lodash": matches every project
QUERY_UNCOMMON = RARE_COMPONENT_NAME  # matches only the first project

DEFAULT_PROJECTS = 200
DEFAULT_SCANS_PER_PROJECT = 20
DEFAULT_COMPONENTS_PER_SCAN = 500
DEFAULT_BATCH_SIZE = 10_000


def _refuse_outside_safe_env() -> None:
    """Refuse to run when ``APP_ENV`` is not ``dev``.

    Reads ``os.getenv`` at call time (CLAUDE.md core rule #11: no
    module-level env caching), matching ``seed_demo.py``'s guard shape.
    """
    current = (os.getenv("APP_ENV") or "").strip().lower()
    if current in _ALLOWED_ENVS:
        return
    allowed = sorted(_ALLOWED_ENVS)
    print(
        "Refusing to run seed_load_test.py: APP_ENV="
        f"{current or '<unset>'} not in {{{', '.join(allowed)}}}. "
        "This is a synthetic stress fixture: dev databases only.",
        file=sys.stderr,
    )
    raise SystemExit(1)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Seed a large-scale search fixture (concurrency-scaling plan M3). "
            "Idempotent unless --reset. Allowed env: dev only."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Validate APP_ENV + parse args but skip all DB work.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        default=False,
        help=(
            "Delete the existing load-test organization and every "
            "package_type='loadtest' component before reseeding."
        ),
    )
    parser.add_argument(
        "--projects",
        type=int,
        default=DEFAULT_PROJECTS,
        help=f"Number of projects to seed (default {DEFAULT_PROJECTS}).",
    )
    parser.add_argument(
        "--scans-per-project",
        type=int,
        default=DEFAULT_SCANS_PER_PROJECT,
        help=f"Succeeded scans per project (default {DEFAULT_SCANS_PER_PROJECT}).",
    )
    parser.add_argument(
        "--components-per-scan",
        type=int,
        default=DEFAULT_COMPONENTS_PER_SCAN,
        help=f"Distinct components per scan (default {DEFAULT_COMPONENTS_PER_SCAN}).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Rows per bulk-insert round trip (default {DEFAULT_BATCH_SIZE}).",
    )
    args = parser.parse_args(argv)
    if args.projects < 1 or args.scans_per_project < 1 or args.components_per_scan < 6:
        # components-per-scan needs room for the 5 common + at least 1
        # project-specific component so every scan has SOME long-tail
        # component beyond the shared catalog.
        parser.error(
            "--projects and --scans-per-project must be >= 1, "
            "--components-per-scan must be >= 6"
        )
    return args


async def _bulk_insert(
    session: Any, table: Any, rows: list[dict[str, Any]], batch_size: int
) -> None:
    """INSERT ``rows`` into ``table`` via chunked SQLAlchemy Core executemany.

    Commits after every chunk: at this row count a single all-or-nothing
    transaction would hold a very long-lived write lock and bloat WAL for no
    benefit (a failed run is recovered via ``--reset``, not a rollback).
    """
    from sqlalchemy import insert

    total = len(rows)
    for start in range(0, total, batch_size):
        chunk = rows[start : start + batch_size]
        await session.execute(insert(table), chunk)
        await session.commit()


async def _delete_existing(session: Any, *, org_slug: str, catalog_namespace: str) -> None:
    """Remove a prior run's rows (``--reset``). Idempotent if nothing exists.

    ``catalog_namespace`` scopes the component-catalog cleanup. Components
    are a SHARED catalog (see ``models.scan.Component``'s docstring: global,
    not org-scoped), so a blanket "delete everything with
    ``package_type='loadtest'``" is only safe when this call owns the WHOLE
    ``loadtest`` namespace, which is true for the real CLI path (empty
    namespace, the default) but would be wrong for a scoped caller: see
    ``_seed``'s docstring for why the integration test suite needs that
    scoping.
    """
    from sqlalchemy import delete

    from models import Component, Organization

    await session.execute(delete(Organization).where(Organization.slug == org_slug))
    if catalog_namespace:
        await session.execute(
            delete(Component).where(
                Component.package_type == LOAD_TEST_PACKAGE_TYPE,
                Component.purl.like(f"pkg:{LOAD_TEST_PACKAGE_TYPE}/{catalog_namespace}%"),
            )
        )
    else:
        await session.execute(
            delete(Component).where(Component.package_type == LOAD_TEST_PACKAGE_TYPE)
        )
    await session.commit()


def _build_catalog(
    *, project_count: int, components_per_scan: int, now: datetime, catalog_namespace: str = ""
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[list[uuid.UUID]]]:
    """Build the shared component catalog in memory (no DB access).

    Returns ``(component_rows, component_version_rows, project_catalog)``
    where ``project_catalog[i]`` is the ordered list of
    ``component_version_id`` project ``i`` uses in EVERY one of its scans
    (the same set every time: see the module docstring's "why the same
    components every scan" note: a new ``ScanComponent`` row per scan is what
    makes the table grow with history even when the underlying dependency
    set is unchanged, which is exactly the pre-Q2 property M3 measures).

    ``catalog_namespace`` is prepended to every ``purl`` this builds (default
    empty, matching the real CLI's namespace-free purls exactly: this
    parameter changes nothing about the script's normal invocation). A
    caller passing a unique namespace (the integration test suite does) gets
    a catalog with no purl overlap against any other run's, including a
    concurrently-running real one, so two seeds can coexist in the same
    database without a unique-constraint collision on ``components.purl``.
    """
    component_rows: list[dict[str, Any]] = []
    version_rows: list[dict[str, Any]] = []

    def _catalog_entry(name: str, purl_suffix: str) -> uuid.UUID:
        cid = uuid.uuid4()
        vid = uuid.uuid4()
        purl = f"pkg:{LOAD_TEST_PACKAGE_TYPE}/{catalog_namespace}{purl_suffix}"
        component_rows.append(
            {
                "id": cid,
                "purl": purl,
                "package_type": LOAD_TEST_PACKAGE_TYPE,
                "name": name,
                "created_at": now,
                "updated_at": now,
                "last_seen_at": now,
            }
        )
        version_rows.append(
            {
                "id": vid,
                "component_id": cid,
                "version": "1.0.0",
                "purl_with_version": f"{purl}@1.0.0",
                "created_at": now,
                "updated_at": now,
                "last_seen_at": now,
            }
        )
        return vid

    common_vids = [_catalog_entry(name, f"{name}-common") for name in COMMON_COMPONENT_NAMES]
    rare_vid = _catalog_entry(RARE_COMPONENT_NAME, RARE_COMPONENT_NAME)

    project_catalog: list[list[uuid.UUID]] = []
    for p_idx in range(project_count):
        catalog = list(common_vids)
        if p_idx == 0:
            catalog.append(rare_vid)
        longtail_needed = components_per_scan - len(catalog)
        for n in range(longtail_needed):
            name = f"proj{p_idx:04d}-pkg{n:04d}"
            catalog.append(_catalog_entry(name, name))
        project_catalog.append(catalog)

    return component_rows, version_rows, project_catalog


async def _seed(
    *,
    project_count: int,
    scans_per_project: int,
    components_per_scan: int,
    batch_size: int,
    reset: bool,
    org_slug: str = LOAD_TEST_ORG_SLUG,
    org_name: str = LOAD_TEST_ORG_NAME,
    team_slug: str = LOAD_TEST_TEAM_SLUG,
    catalog_namespace: str = "",
) -> dict[str, Any]:
    """Run the seed against the live Postgres pointed at by ``DATABASE_URL``.

    ``org_slug`` / ``org_name`` / ``team_slug`` / ``catalog_namespace`` default to the
    well-known constants the real CLI always uses: passing none of them
    reproduces exactly today's behaviour. They exist so
    ``tests/integration/test_seed_load_test_db.py`` can exercise this
    function's full DB-writing path (idempotency, ``--reset``, the catalog
    shape) against ISOLATED rows, without ever touching: let alone
    resetting: a real 200×20×500 dataset a developer may have left running
    in the same local Postgres for a manual M3 measurement. Two orgs
    coexisting under different slugs is the isolation; the namespace on top
    is because the component catalog is a SHARED, non-org-scoped table (see
    ``models.scan.Component``), so org isolation alone would still collide
    on ``components.purl``'s unique constraint the first time a test ran
    next to a real dataset.
    """
    _refuse_outside_safe_env()

    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    from core.config import database_url
    from models import Component, ComponentVersion, Organization, Project, Scan, ScanComponent, Team

    started = time.perf_counter()
    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    try:
        async with factory() as session:
            if reset:
                await _delete_existing(
                    session, org_slug=org_slug, catalog_namespace=catalog_namespace
                )

            existing_org = (
                await session.execute(select(Organization).where(Organization.slug == org_slug))
            ).scalar_one_or_none()
            if existing_org is not None:
                team = (
                    await session.execute(
                        select(Team).where(Team.organization_id == existing_org.id)
                    )
                ).scalar_one()
                scan_component_total = (
                    await session.execute(
                        select(func.count())
                        .select_from(ScanComponent)
                        .join(Scan, Scan.id == ScanComponent.scan_id)
                        .join(Project, Project.id == Scan.project_id)
                        .where(Project.team_id == team.id)
                    )
                ).scalar_one()
                return {
                    "organization_id": str(existing_org.id),
                    "team_id": str(team.id),
                    "projects": project_count,
                    "scans_per_project": scans_per_project,
                    "components_per_scan": components_per_scan,
                    "scan_components_total": int(scan_component_total),
                    "elapsed_seconds": round(time.perf_counter() - started, 2),
                    "already_seeded": True,
                    "ok": True,
                }

            now = datetime.now(tz=UTC)

            org = Organization(id=uuid.uuid4(), name=org_name, slug=org_slug)
            session.add(org)
            await session.flush()

            team = Team(
                id=uuid.uuid4(),
                organization_id=org.id,
                name=LOAD_TEST_TEAM_NAME,
                slug=team_slug,
            )
            session.add(team)
            await session.flush()
            await session.commit()

            print(
                json.dumps({"event": "seed_load_test.catalog_build_started"}),
                file=sys.stderr,
                flush=True,
            )
            component_rows, version_rows, project_catalog = _build_catalog(
                project_count=project_count,
                components_per_scan=components_per_scan,
                now=now,
                catalog_namespace=catalog_namespace,
            )
            await _bulk_insert(session, Component.__table__, component_rows, batch_size)
            await _bulk_insert(session, ComponentVersion.__table__, version_rows, batch_size)
            print(
                json.dumps(
                    {
                        "event": "seed_load_test.catalog_build_done",
                        "components": len(component_rows),
                    }
                ),
                file=sys.stderr,
                flush=True,
            )

            # ── Projects (ORM: a few hundred rows, no perf concern) ───────
            projects: list[Project] = []
            for p_idx in range(project_count):
                slug = f"loadtest-project-{p_idx:04d}"
                projects.append(
                    Project(
                        id=uuid.uuid4(),
                        team_id=team.id,
                        name=slug,
                        slug=slug,
                        visibility="team",
                        git_url=None,
                    )
                )
            session.add_all(projects)
            await session.flush()
            await session.commit()

            # ── Scans (ORM) + ScanComponent (Core bulk insert) ─────────────
            scan_component_total = 0
            for p_idx, project in enumerate(projects):
                catalog = project_catalog[p_idx]
                direct_cutoff = max(1, len(catalog) // 5)
                scans: list[Scan] = []
                for s_idx in range(scans_per_project):
                    # Oldest scan first, newest last, so Project.latest_scan_id
                    # and ORDER BY created_at DESC both resolve to the scan
                    # created LAST in this loop: matching real scan history.
                    scan_created_at = now - timedelta(
                        hours=(scans_per_project - s_idx), minutes=p_idx % 60
                    )
                    scans.append(
                        Scan(
                            id=uuid.uuid4(),
                            project_id=project.id,
                            kind="source",
                            status="succeeded",
                            progress_percent=100,
                            started_at=scan_created_at,
                            completed_at=scan_created_at + timedelta(minutes=8),
                            scan_metadata={"load_test": True, "seed_scan_index": s_idx},
                            created_at=scan_created_at,
                            updated_at=scan_created_at,
                        )
                    )
                session.add_all(scans)
                await session.flush()
                project.latest_scan_id = scans[-1].id

                scan_component_rows: list[dict[str, Any]] = []
                for scan in scans:
                    for c_idx, vid in enumerate(catalog):
                        scan_component_rows.append(
                            {
                                "id": uuid.uuid4(),
                                "scan_id": scan.id,
                                "component_version_id": vid,
                                "direct": c_idx < direct_cutoff,
                                "dependency_path": f"./component-{c_idx:04d}",
                                "raw_data": {},
                                "created_at": scan.created_at,
                            }
                        )
                await _bulk_insert(
                    session, ScanComponent.__table__, scan_component_rows, batch_size
                )
                scan_component_total += len(scan_component_rows)
                await session.commit()

                if (p_idx + 1) % 20 == 0 or p_idx == project_count - 1:
                    print(
                        json.dumps(
                            {
                                "event": "seed_load_test.progress",
                                "projects_done": p_idx + 1,
                                "projects_total": project_count,
                                "scan_components_so_far": scan_component_total,
                                "elapsed_seconds": round(time.perf_counter() - started, 2),
                            }
                        ),
                        file=sys.stderr,
                        flush=True,
                    )

            # A freshly bulk-loaded table has no statistics until autovacuum
            # gets to it, and the EXPLAIN baseline this seed exists to enable
            # (test_search_explain_load_baseline.py) needs the planner to
            # make its real, statistics-informed choice immediately: not
            # whatever default it guesses for a table Postgres still
            # believes is empty. ANALYZE is transaction-safe (unlike
            # VACUUM), so this runs inside the same session.
            print(
                json.dumps({"event": "seed_load_test.analyze_started"}),
                file=sys.stderr,
                flush=True,
            )
            from sqlalchemy import text

            await session.execute(
                text("ANALYZE components, component_versions, scan_components, scans, projects")
            )
            await session.commit()

            return {
                "organization_id": str(org.id),
                "team_id": str(team.id),
                "projects": project_count,
                "scans_per_project": scans_per_project,
                "components_per_scan": components_per_scan,
                "scan_components_total": scan_component_total,
                "elapsed_seconds": round(time.perf_counter() - started, 2),
                "already_seeded": False,
                "ok": True,
            }
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    _refuse_outside_safe_env()

    if args.dry_run:
        print(
            json.dumps(
                {
                    "organization_id": None,
                    "projects": args.projects,
                    "scans_per_project": args.scans_per_project,
                    "components_per_scan": args.components_per_scan,
                    "ok": True,
                    "dry_run": True,
                }
            )
        )
        return 0

    try:
        summary = asyncio.run(
            _seed(
                project_count=args.projects,
                scans_per_project=args.scans_per_project,
                components_per_scan=args.components_per_scan,
                batch_size=args.batch_size,
                reset=args.reset,
            )
        )
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001: top-level CLI handler
        print(f"seed_load_test failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
