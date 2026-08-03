"""
Integration tests for the L-3 reset blast-radius fix (scripts/reset_demo.py).

Before the fix, ``_drop_demo`` deleted users by ``email LIKE
'%@demo.trustedoss.dev'`` and let the membership CASCADE strip them from any
team — including NON-demo teams. A real co-tenant who happened to share that
email suffix (or whose membership graph touched another org) could be wiped.

The fix scopes deletion by **demo-org membership**: a user is deleted iff they
have at least one membership in a demo-org team AND zero memberships outside it.
These tests run against the real Postgres (CLAUDE.md core rule #1 — no SQLite)
and prove:

  * a user who belongs ONLY to the demo org IS deleted;
  * a user who ALSO belongs to another org is PRESERVED, even with a
    ``@demo.trustedoss.dev`` email and a membership inside the demo org;
  * the demo-org row itself is gone after the drop.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip reset_demo scope tests")
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
            "alembic upgrade head failed; reset_demo scope tests cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture(autouse=True)
def _demo_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # The seed/reset guard requires APP_ENV ∈ {dev, demo}.
    monkeypatch.setenv("APP_ENV", "demo")


@pytest.fixture
async def db_factory() -> AsyncIterator[async_sessionmaker[Any]]:
    from core.config import database_url

    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def test_reset_preserves_cross_tenant_user_and_deletes_demo_only(
    db_factory: async_sessionmaker[Any],
) -> None:
    from models import Membership, Organization, Team, User
    from scripts import reset_demo, seed_demo

    # ── 1. Seed the demo dataset (idempotent). ──────────────────────────────
    await seed_demo._seed()

    # ── 2. Create a non-demo org + team, and a "cross-tenant" user that:
    #        - has a @demo.trustedoss.dev email (would match the OLD suffix
    #          filter), AND
    #        - is a member of BOTH the demo org's Frontend team and a separate
    #          non-demo team.
    #      Also create a control user that is a member ONLY of the demo org.
    suffix = uuid.uuid4().hex[:8]
    cross_email = f"cross-tenant-{suffix}@demo.trustedoss.dev"
    demo_only_email = f"demo-only-{suffix}@demo.trustedoss.dev"

    async with db_factory() as session:
        demo_org = (
            await session.execute(
                select(Organization).where(
                    Organization.slug == seed_demo._DEMO_ORG_SLUG
                )
            )
        ).scalar_one()
        demo_team = (
            await session.execute(
                select(Team).where(Team.organization_id == demo_org.id).limit(1)
            )
        ).scalar_one()

        other_org = Organization(
            name=f"Co-Tenant {suffix}", slug=f"cotenant-{suffix}"
        )
        session.add(other_org)
        await session.flush()
        other_team = Team(
            organization_id=other_org.id, name="Other", slug=f"other-{suffix}"
        )
        session.add(other_team)
        await session.flush()

        from core.security import hash_password

        cross_user = User(
            email=cross_email,
            hashed_password=hash_password("Sup3rSecret!password"),
            full_name="Cross Tenant",
            is_active=True,
        )
        demo_only_user = User(
            email=demo_only_email,
            hashed_password=hash_password("Sup3rSecret!password"),
            full_name="Demo Only",
            is_active=True,
        )
        session.add_all([cross_user, demo_only_user])
        await session.flush()

        # cross_user: membership in BOTH demo team and the other org's team.
        session.add(
            Membership(
                user_id=cross_user.id, team_id=demo_team.id, role="developer"
            )
        )
        session.add(
            Membership(
                user_id=cross_user.id, team_id=other_team.id, role="team_admin"
            )
        )
        # demo_only_user: membership ONLY in the demo team.
        session.add(
            Membership(
                user_id=demo_only_user.id, team_id=demo_team.id, role="developer"
            )
        )
        await session.commit()
        cross_user_id = cross_user.id
        demo_only_user_id = demo_only_user.id
        other_org_id = other_org.id
        other_team_id = other_team.id

    # ── 3. Run the destructive drop. ────────────────────────────────────────
    try:
        summary = await reset_demo._drop_demo()
        assert summary["organizations_deleted"] == 1

        async with db_factory() as session:
            # Demo org gone.
            assert (
                await session.execute(
                    select(Organization).where(
                        Organization.slug == seed_demo._DEMO_ORG_SLUG
                    )
                )
            ).scalar_one_or_none() is None

            # demo-only user gone (had only a demo membership).
            assert (
                await session.execute(
                    select(User).where(User.id == demo_only_user_id)
                )
            ).scalar_one_or_none() is None

            # cross-tenant user PRESERVED despite the @demo.trustedoss.dev email
            # — they still belong to the other org.
            survivor = (
                await session.execute(
                    select(User).where(User.id == cross_user_id)
                )
            ).scalar_one_or_none()
            assert survivor is not None
            # Their non-demo membership survives (demo membership cascaded away
            # with the org, which is expected and harmless).
            remaining = (
                (
                    await session.execute(
                        select(Membership).where(
                            Membership.user_id == cross_user_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert {m.team_id for m in remaining} == {other_team_id}
    finally:
        # ── Cleanup: remove the co-tenant fixtures we created. ──────────────
        async with db_factory() as session:
            await session.execute(
                # delete cross user (its other-org membership cascades)
                delete(User).where(User.id == cross_user_id)
            )
            await session.execute(
                delete(Organization).where(Organization.id == other_org_id)
            )
            await session.commit()
        # Re-seed so other suites that assume the demo dataset still find it.
        await seed_demo._seed()


async def test_drop_demo_is_noop_when_org_absent(
    db_factory: async_sessionmaker[Any],
) -> None:
    """Idempotency: dropping when there is no demo-org returns a clean no-op.

    Exercises the ``org is None`` short-circuit in ``_drop_demo`` (the empty-DB
    path). We delete the demo-org first, then assert a second drop reports zero.
    """
    from models import Organization
    from scripts import reset_demo, seed_demo

    await seed_demo._seed()
    try:
        first = await reset_demo._drop_demo()
        assert first["organizations_deleted"] == 1
        # Org is gone now → a second drop is a no-op.
        async with db_factory() as session:
            assert (
                await session.execute(
                    select(Organization).where(
                        Organization.slug == seed_demo._DEMO_ORG_SLUG
                    )
                )
            ).scalar_one_or_none() is None
        second = await reset_demo._drop_demo()
        assert second == {"organizations_deleted": 0, "users_deleted": 0}
    finally:
        await seed_demo._seed()


async def test_demo_only_user_ids_empty_when_org_has_no_members(
    db_factory: async_sessionmaker[Any],
) -> None:
    """``_demo_only_user_ids`` returns [] for an org with zero memberships.

    Covers the early-return branch — a freshly created org with no members must
    yield no deletion candidates.
    """
    import uuid as _uuid

    from models import Organization
    from scripts import reset_demo

    slug = f"empty-org-{_uuid.uuid4().hex[:8]}"
    async with db_factory() as session:
        org = Organization(name="Empty", slug=slug)
        session.add(org)
        await session.commit()
        org_id = org.id
    try:
        async with db_factory() as session:
            ids = await reset_demo._demo_only_user_ids(session, org_id)
            assert ids == []
    finally:
        async with db_factory() as session:
            await session.execute(
                delete(Organization).where(Organization.id == org_id)
            )
            await session.commit()
