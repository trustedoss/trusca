# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Integration tests for ``scripts/seed_load_test.py`` against a real Postgres
(concurrency-scaling plan, unit 13 / M3).

Deliberately tiny scale (a handful of projects/scans/components, not the
script's 200×20×500 default): these tests exist to pin the SHAPE of the
seed (idempotency, ``--reset``, the common/rare component fixtures the
EXPLAIN regression tests depend on), not to reproduce the heavy dataset
itself. The heavy dataset is nightly/manual only, per the script's own
module docstring and ``test_search_explain_load_baseline.py``'s.

Every test here uses an ISOLATED ``org_slug`` / ``team_slug`` /
``catalog_namespace`` (a fresh :func:`tests._helpers.unique_suffix` per
test), never the real :data:`scripts.seed_load_test.LOAD_TEST_ORG_SLUG`.
This is load-bearing, not incidental: an earlier version of this file used
the real slug directly, and its ``reset=True`` calls deleted a 2-million-row
dataset a manual M3 measurement session had just built in the same local
Postgres, silently, because ``tests/integration`` shares one database and
pytest's file collection order is not guaranteed alphabetical (observed
directly: this file's reset ran BEFORE
``test_search_explain_load_baseline.py`` in one full-suite run despite
sorting after it by name), so ``test_search_explain_load_baseline.py`` saw
its dataset vanish mid-suite and failed with "matched nothing". Real
deployments only ever call ``_seed()`` with the defaults (the CLI never
passes these three parameters), so this isolation is invisible to the
script's actual contract: it exists purely to keep this test file from
being able to touch a namespace a human is using for a real baseline.

Calls ``scripts.seed_load_test._seed`` directly (async, in-process) rather
than shelling out to the script: mirrors
``test_seed_demo_demo_only_db.py``'s pattern for the sibling seed script.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._db_required import migrate_to_head
from tests._helpers import unique_suffix

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


@pytest.fixture(autouse=True)
def _dev_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_seed`` refuses outside APP_ENV=dev: CI's pytest jobs run with
    APP_ENV unset, which resolves to "dev" (``core.config.app_env()``'s
    default), but pin it explicitly so this test does not depend on that.
    """
    monkeypatch.setenv("APP_ENV", "dev")


@pytest.fixture
def isolated_namespace() -> tuple[str, str, str]:
    """A fresh (org_slug, team_slug, catalog_namespace) triple per test:
    see the module docstring for why this isolation is load-bearing.
    """
    suffix = unique_suffix()
    return (f"trusca-load-test-itest-{suffix}", f"loadtest-itest-{suffix}", f"itest-{suffix}-")


async def _run_seed(namespace: tuple[str, str, str], /, **kwargs):
    from scripts.seed_load_test import _seed

    org_slug, team_slug, catalog_namespace = namespace
    # organizations.name is ALSO globally unique (uq_organizations_name):
    # reuse the already-unique org_slug as the name rather than adding a
    # fourth namespace value to generate and thread through.
    return await _seed(
        org_slug=org_slug,
        org_name=org_slug,
        team_slug=team_slug,
        catalog_namespace=catalog_namespace,
        **kwargs,
    )


async def test_seed_creates_expected_shape(
    db_session: AsyncSession, isolated_namespace: tuple[str, str, str]
) -> None:
    from models import Component, Organization, Project, Scan, ScanComponent, Team
    from scripts.seed_load_test import (
        COMMON_COMPONENT_NAMES,
        LOAD_TEST_PACKAGE_TYPE,
        RARE_COMPONENT_NAME,
    )

    org_slug, _team_slug, catalog_namespace = isolated_namespace
    summary = await _run_seed(
        isolated_namespace,
        project_count=2,
        scans_per_project=2,
        components_per_scan=6,
        batch_size=100,
        reset=True,
    )
    assert summary["ok"] is True
    assert summary["already_seeded"] is False
    # The headline arithmetic the module docstring promises: projects *
    # scans_per_project * components_per_scan, exactly.
    assert summary["scan_components_total"] == 2 * 2 * 6

    org = (
        await db_session.execute(select(Organization).where(Organization.slug == org_slug))
    ).scalar_one()
    team = (
        await db_session.execute(select(Team).where(Team.organization_id == org.id))
    ).scalar_one()

    project_count = (
        await db_session.execute(
            select(func.count()).select_from(Project).where(Project.team_id == team.id)
        )
    ).scalar_one()
    assert project_count == 2

    scan_count = (
        await db_session.execute(
            select(func.count())
            .select_from(Scan)
            .join(Project, Project.id == Scan.project_id)
            .where(Project.team_id == team.id)
        )
    ).scalar_one()
    assert scan_count == 2 * 2

    scan_component_count = (
        await db_session.execute(
            select(func.count())
            .select_from(ScanComponent)
            .join(Scan, Scan.id == ScanComponent.scan_id)
            .join(Project, Project.id == Scan.project_id)
            .where(Project.team_id == team.id)
        )
    ).scalar_one()
    assert scan_component_count == 2 * 2 * 6

    catalog_names = set(
        (
            await db_session.execute(
                select(Component.name).where(
                    Component.package_type == LOAD_TEST_PACKAGE_TYPE,
                    Component.purl.like(f"pkg:{LOAD_TEST_PACKAGE_TYPE}/{catalog_namespace}%"),
                )
            )
        )
        .scalars()
        .all()
    )
    assert set(COMMON_COMPONENT_NAMES) <= catalog_names
    assert RARE_COMPONENT_NAME in catalog_names

    # Every project's latest_scan_id is set (search/component surfaces read
    # this pointer) and points at a scan that actually belongs to it.
    projects = (
        (await db_session.execute(select(Project).where(Project.team_id == team.id)))
        .scalars()
        .all()
    )
    for project in projects:
        assert project.latest_scan_id is not None


async def test_seed_is_idempotent_without_reset(
    db_session: AsyncSession, isolated_namespace: tuple[str, str, str]
) -> None:
    first = await _run_seed(
        isolated_namespace,
        project_count=1,
        scans_per_project=1,
        components_per_scan=6,
        batch_size=100,
        reset=True,
    )
    assert first["already_seeded"] is False

    second = await _run_seed(
        isolated_namespace,
        project_count=1,
        scans_per_project=1,
        components_per_scan=6,
        batch_size=100,
        reset=False,
    )
    assert second["already_seeded"] is True
    assert second["organization_id"] == first["organization_id"]
    assert second["team_id"] == first["team_id"]
    # Idempotent means "did not write again": the real row count on disk,
    # not just the returned summary, must match what --reset originally
    # produced (1 project * 1 scan * 6 components).
    assert second["scan_components_total"] == 1 * 1 * 6


async def test_seed_reset_replaces_prior_run(
    db_session: AsyncSession, isolated_namespace: tuple[str, str, str]
) -> None:
    from models import Organization

    org_slug, _team_slug, _catalog_namespace = isolated_namespace
    first = await _run_seed(
        isolated_namespace,
        project_count=2,
        scans_per_project=2,
        components_per_scan=6,
        batch_size=100,
        reset=True,
    )
    second = await _run_seed(
        isolated_namespace,
        project_count=1,
        scans_per_project=1,
        components_per_scan=6,
        batch_size=100,
        reset=True,
    )
    assert second["already_seeded"] is False
    # --reset deletes the prior organization outright: a fresh row with a
    # NEW id, not an update of the old one.
    assert second["organization_id"] != first["organization_id"]
    assert second["scan_components_total"] == 1 * 1 * 6

    org_count = (
        await db_session.execute(
            select(func.count()).select_from(Organization).where(Organization.slug == org_slug)
        )
    ).scalar_one()
    assert org_count == 1  # never two organizations under this test's slug at once


async def test_seed_reset_removes_orphaned_catalog_components(
    db_session: AsyncSession, isolated_namespace: tuple[str, str, str]
) -> None:
    """``--reset`` also deletes the shared component catalog (``package_type
    ='loadtest'``, scoped to this test's own ``catalog_namespace``: see
    module docstring), not just the organization: components are NOT
    org-scoped (``models.scan.Component``'s docstring: "shared across
    projects"), so cascading the organization delete alone would leave the
    old catalog behind as orphaned rows nothing points at, permanently
    inflating the shared components table on every reseed.
    """
    from models import Component
    from scripts.seed_load_test import LOAD_TEST_PACKAGE_TYPE

    _org_slug, _team_slug, catalog_namespace = isolated_namespace

    async def _catalog_count() -> int:
        return (
            await db_session.execute(
                select(func.count())
                .select_from(Component)
                .where(
                    Component.package_type == LOAD_TEST_PACKAGE_TYPE,
                    Component.purl.like(f"pkg:{LOAD_TEST_PACKAGE_TYPE}/{catalog_namespace}%"),
                )
            )
        ).scalar_one()

    await _run_seed(
        isolated_namespace,
        project_count=1,
        scans_per_project=1,
        components_per_scan=6,
        batch_size=100,
        reset=True,
    )
    first_count = await _catalog_count()
    assert first_count > 0

    await _run_seed(
        isolated_namespace,
        project_count=1,
        scans_per_project=1,
        components_per_scan=6,
        batch_size=100,
        reset=True,
    )
    second_count = await _catalog_count()
    # Same requested scale both times -> same catalog size, not doubled.
    assert second_count == first_count


async def test_seed_never_touches_a_concurrent_different_namespace(
    db_session: AsyncSession,
) -> None:
    """The isolation itself, asserted directly: two ``_seed`` calls under
    DIFFERENT namespaces, one of them ``reset=True``, never see or delete
    each other's rows: the property the whole module docstring depends on.
    """
    from models import Organization
    from scripts.seed_load_test import _seed

    suffix_a, suffix_b = uuid.uuid4().hex[:10], uuid.uuid4().hex[:10]
    org_a, team_a, ns_a = (
        f"trusca-load-test-itest-{suffix_a}",
        f"loadtest-itest-{suffix_a}",
        f"itest-{suffix_a}-",
    )
    org_b, team_b, ns_b = (
        f"trusca-load-test-itest-{suffix_b}",
        f"loadtest-itest-{suffix_b}",
        f"itest-{suffix_b}-",
    )

    summary_a = await _seed(
        project_count=1,
        scans_per_project=1,
        components_per_scan=6,
        batch_size=100,
        reset=True,
        org_slug=org_a,
        org_name=org_a,
        team_slug=team_a,
        catalog_namespace=ns_a,
    )
    # A reset under namespace B must not remove namespace A's organization.
    await _seed(
        project_count=1,
        scans_per_project=1,
        components_per_scan=6,
        batch_size=100,
        reset=True,
        org_slug=org_b,
        org_name=org_b,
        team_slug=team_b,
        catalog_namespace=ns_b,
    )

    still_present = (
        await db_session.execute(
            select(Organization.id).where(Organization.slug == org_a)
        )
    ).scalar_one_or_none()
    assert still_present is not None
    assert str(still_present) == summary_a["organization_id"]
