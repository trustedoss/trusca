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

from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests._db_required import migrate_to_head

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


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


@pytest.fixture(autouse=True)
def _demo_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # The seed/reset guard requires APP_ENV ∈ {dev, demo}; dev avoids the
    # non-dev SECRET_KEY requirement so the test runs on a bare local stack.
    monkeypatch.setenv("APP_ENV", "dev")
    # feat/demo-sandbox-scan: default the sandbox carve-out OFF so the
    # documented-5-projects assertions are not perturbed by a leaked env var.
    # The flag-on path has its own dedicated test below.
    monkeypatch.delenv("DEMO_ALLOW_SANDBOX_SCANS", raising=False)


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
        # Carve-out OFF (default): the sandbox project is NOT materialised, so
        # the documented 5-project demo list is unchanged.
        assert "Demo Sandbox" not in names
        assert summary["demo_sandbox"] is None

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


@pytest.mark.parametrize(
    ("read_only", "allow", "expect_created"),
    [
        # L-1: the sandbox is materialised ONLY when BOTH flags are on, matching
        # the /health demo_sandbox_scans signal (read_only AND allow). Any other
        # combination must leave the demo project list unchanged.
        ("true", "true", True),
        ("true", None, False),
        (None, "true", False),
        (None, None, False),
    ],
)
async def test_sandbox_seed_requires_both_flags(
    db_factory: async_sessionmaker[Any],
    monkeypatch: pytest.MonkeyPatch,
    read_only: str | None,
    allow: str | None,
    expect_created: bool,
) -> None:
    """L-1 permission×state matrix: sandbox seed gated on read_only AND allow."""
    from models import Project
    from scripts import seed_demo

    if read_only is None:
        monkeypatch.delenv("DEMO_READ_ONLY", raising=False)
    else:
        monkeypatch.setenv("DEMO_READ_ONLY", read_only)
    if allow is None:
        monkeypatch.delenv("DEMO_ALLOW_SANDBOX_SCANS", raising=False)
    else:
        monkeypatch.setenv("DEMO_ALLOW_SANDBOX_SCANS", allow)

    try:
        summary = await seed_demo._seed()
        if expect_created:
            assert summary["demo_sandbox"] is not None
            assert "Demo Sandbox" in await _demo_project_names(db_factory)
        else:
            assert summary["demo_sandbox"] is None
            assert "Demo Sandbox" not in await _demo_project_names(db_factory)
    finally:
        async with db_factory() as session:
            leftover = (
                await session.execute(
                    select(Project).where(
                        Project.slug == seed_demo._DEMO_SANDBOX_SLUG
                    )
                )
            ).scalars().all()
            for row in leftover:
                await session.delete(row)
            await session.commit()


async def test_sandbox_project_seeded_when_carveout_enabled(
    db_factory: async_sessionmaker[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """feat/demo-sandbox-scan: with both demo flags on, the idempotent seed
    materialises exactly one "Demo Sandbox" project in the Backend team.

    Runs on the already-seeded (short-circuit) path — proving an existing demo
    stack gains the sandbox on upgrade — and re-runs to pin idempotency.
    """
    from models import Membership, Project, Team, User
    from scripts import seed_demo

    monkeypatch.setenv("DEMO_READ_ONLY", "true")
    monkeypatch.setenv("DEMO_ALLOW_SANDBOX_SCANS", "true")

    try:
        summary = await seed_demo._seed()
        sandbox_id = summary["demo_sandbox"]
        assert sandbox_id is not None

        names = await _demo_project_names(db_factory)
        assert "Demo Sandbox" in names

        # Re-run is idempotent: same id, still exactly one row.
        rerun = await seed_demo._seed()
        assert rerun["demo_sandbox"] == sandbox_id

        async with db_factory() as session:
            rows = (
                await session.execute(
                    select(Project).where(
                        Project.slug == seed_demo._DEMO_SANDBOX_SLUG
                    )
                )
            ).scalars().all()
            assert len(rows) == 1
            sandbox = rows[0]
            # Lives in the Backend team so the seeded developer can trigger scans.
            team = (
                await session.execute(select(Team).where(Team.id == sandbox.team_id))
            ).scalar_one()
            assert team.slug == "backend"
            developer = (
                await session.execute(
                    select(User).where(User.email == "dev@demo.trustedoss.dev")
                )
            ).scalar_one()
            membership = (
                await session.execute(
                    select(Membership).where(
                        Membership.user_id == developer.id,
                        Membership.team_id == sandbox.team_id,
                        Membership.role == "developer",
                    )
                )
            ).scalar_one_or_none()
            assert membership is not None
    finally:
        # Restore the canonical demo state other suites assume: remove the
        # sandbox project this test introduced.
        async with db_factory() as session:
            leftover = (
                await session.execute(
                    select(Project).where(
                        Project.slug == seed_demo._DEMO_SANDBOX_SLUG
                    )
                )
            ).scalars().all()
            for row in leftover:
                await session.delete(row)
            await session.commit()


async def test_queued_scan_fixture_is_seeded_for_dev_but_not_for_demo(
    db_factory: async_sessionmaker[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The parked `queued` scan exists on a dev stack and not on the demo host.

    It is a fixture: nothing ever moves it, because no Celery task is enqueued
    for it. On a dev stack that is the point — scan-retention captures it via
    `GET /v1/scans?status=queued` to prove an active scan resists deletion, and
    the dashboard specs count it. On the public demo the same row is a scan
    sitting at 0% that never finishes, which reads as a broken portal.

    Both directions are asserted here. A guard that only pins the demo case
    would pass just as well if the fixture stopped being seeded anywhere, and
    the specs that need it fail far from this file.
    """
    from models import Project, Scan
    from scripts import seed_demo

    async def _queued_count() -> int:
        async with db_factory() as session:
            host = (
                await session.execute(
                    select(Project).where(Project.name == "portal-mobile")
                )
            ).scalars().first()
            if host is None:
                return 0
            rows = (
                await session.execute(
                    select(Scan).where(
                        Scan.project_id == host.id, Scan.status == "queued"
                    )
                )
            ).scalars().all()
            return len(rows)

    async def _drop_queued() -> None:
        async with db_factory() as session:
            host = (
                await session.execute(
                    select(Project).where(Project.name == "portal-mobile")
                )
            ).scalars().first()
            if host is None:
                return
            rows = (
                await session.execute(
                    select(Scan).where(
                        Scan.project_id == host.id, Scan.status == "queued"
                    )
                )
            ).scalars().all()
            for row in rows:
                await session.delete(row)
            await session.commit()

    try:
        # demo — the row must not come back after the seed runs.
        await _drop_queued()
        monkeypatch.setenv("APP_ENV", "demo")
        summary = await seed_demo._seed()
        assert summary["verify_baseline"]["queued_scan"] is False
        assert await _queued_count() == 0

        # dev — the same seed puts it back.
        monkeypatch.setenv("APP_ENV", "dev")
        summary = await seed_demo._seed()
        assert summary["verify_baseline"]["queued_scan"] is True
        assert await _queued_count() == 1
    finally:
        # Leave the shared integration DB the way the other suites expect it:
        # APP_ENV=dev, fixture present, exactly one row.
        monkeypatch.setenv("APP_ENV", "dev")
        await seed_demo._seed()
