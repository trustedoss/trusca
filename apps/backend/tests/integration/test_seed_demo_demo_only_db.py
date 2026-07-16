"""
Integration tests for ``scripts/seed_demo.py --demo-only`` (quickstart-gate fix).

The quickstart guide promises exactly 5 seeded projects and the docs-uat gate
replays that claim (``expectVisibleProjectCount(5)``). Since the seed-baseline
agreement (tests/verify-specs/PROVENANCE.md) landed, the default seed also
creates the ``_seed_verify_baseline`` fixture projects (fx-appr + three
"Project …" probe rows), which pushed the visible list past 5 and broke the
gate on every nightly run.

``--demo-only`` restores the documented behaviour for quickstart/demo stacks
WITHOUT touching the default (the verification team's Tier-3 runs and the
verify-specs nightly still get the baseline). These tests run against the real
Postgres (CLAUDE.md core rule #1) and pin:

  * demo-only fresh seed → exactly the 5 documented projects, no baseline rows,
    ``verify_baseline`` reported as ``None``;
  * demo-only re-run on an already-seeded stack does NOT top up the baseline;
  * the default seed on the same stack DOES top it up (agreement intact).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
pytestmark = pytest.mark.integration

_DEMO_PROJECT_NAMES = {
    "portal-web",
    "portal-mobile",
    "portal-api",
    "scan-pipeline",
    "vuln-feed",
}
_BASELINE_PROJECT_NAMES = {
    "fx-appr",
    "Project 2946a3cb02",
    "Project 2f44fc72e0",
    "Project d86682144a",
}


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip seed_demo --demo-only tests")
    return url


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    _require_database_url()
    import subprocess

    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        pytest.skip(
            "alembic upgrade head failed; seed_demo --demo-only tests cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture(autouse=True)
def _demo_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # The seed/reset guard requires APP_ENV ∈ {dev, demo}; dev avoids the
    # non-dev SECRET_KEY requirement so the test runs on a bare local stack.
    monkeypatch.setenv("APP_ENV", "dev")


@pytest.fixture
async def db_factory() -> AsyncIterator[async_sessionmaker[Any]]:
    from core.config import database_url

    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _demo_project_names(
    db_factory: async_sessionmaker[Any],
) -> set[str]:
    from models import Organization, Project, Team
    from scripts import seed_demo

    async with db_factory() as session:
        org = (
            await session.execute(
                select(Organization).where(
                    Organization.slug == seed_demo._DEMO_ORG_SLUG
                )
            )
        ).scalar_one()
        team_ids = [
            t.id
            for t in (
                await session.execute(
                    select(Team).where(Team.organization_id == org.id)
                )
            ).scalars()
        ]
        rows = (
            await session.execute(
                select(Project.name).where(Project.team_id.in_(team_ids))
            )
        ).scalars()
        return set(rows)


async def test_demo_only_seed_creates_exactly_the_documented_projects(
    db_factory: async_sessionmaker[Any],
) -> None:
    import uuid

    from core.security import hash_password
    from models import User
    from scripts import reset_demo, seed_demo

    # A sentinel super admin outside the demo org — the last-active-super_admin
    # DB trigger otherwise refuses _drop_demo on a DB where the demo super
    # admin is the only one (the local-dev case).
    sentinel_email = f"sentinel-{uuid.uuid4().hex[:8]}@example.com"
    async with db_factory() as session:
        session.add(
            User(
                email=sentinel_email,
                hashed_password=hash_password("Sentinel!password12"),
                full_name="Drop Sentinel",
                is_active=True,
                is_superuser=True,
            )
        )
        await session.commit()

    # Fresh slate — a prior full seed in this DB would leave baseline rows
    # behind and mask the assertion.
    await reset_demo._drop_demo()
    try:
        summary = await seed_demo._seed(demo_only=True)
        assert summary["ok"] is True
        assert summary["verify_baseline"] is None

        names = await _demo_project_names(db_factory)
        assert names == _DEMO_PROJECT_NAMES
        assert not (names & _BASELINE_PROJECT_NAMES)

        # Short-circuit path (already seeded): --demo-only must NOT top up
        # the baseline the way the default re-seed does.
        rerun = await seed_demo._seed(demo_only=True)
        assert rerun["verify_baseline"] is None
        assert await _demo_project_names(db_factory) == _DEMO_PROJECT_NAMES

        # Default seed on the same stack DOES top up — the seed-baseline
        # agreement (PROVENANCE.md ground rule 1) stays intact.
        full = await seed_demo._seed()
        assert full["verify_baseline"] is not None
        names_after_full = await _demo_project_names(db_factory)
        assert _BASELINE_PROJECT_NAMES <= names_after_full
    finally:
        # Leave the shared integration DB in the canonical full-seed state
        # other suites assume, and remove the sentinel (the reseeded demo
        # super admin satisfies the trigger again).
        await seed_demo._seed()
        async with db_factory() as session:
            sentinel = (
                await session.execute(
                    select(User).where(User.email == sentinel_email)
                )
            ).scalar_one_or_none()
            if sentinel is not None:
                await session.delete(sentinel)
                await session.commit()
