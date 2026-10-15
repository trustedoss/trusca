"""
Public-demo admin scope against the real app and Postgres.

The demo's published accounts include super admins (admin@ and explore@). On
DEMO_READ_ONLY the people and audit screens only list the demo organisation,
admin reads outside an allow-list are refused, and report downloads leave no
history row. Each case is run with the flag on AND off, so a passing "hidden"
assertion is shown to come from the demo scope and not from the fixture
(hardening rules 7 and 8).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from core.security import create_access_token
from tests._db_required import migrate_to_head
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_team,
    make_user,
)

pytestmark = pytest.mark.integration

EXPLORE_EMAIL = "explore@demo.trustedoss.dev"


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    # The seed refuses outside APP_ENV dev/demo.
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.delenv("DEMO_ALLOW_SANDBOX_SCANS", raising=False)


@pytest.fixture(autouse=True)
async def _drop_demo_after(
    db_factory: async_sessionmaker[Any], monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[None]:
    """Leave no demo dataset behind.

    The tests here seed the demo early in the alphabetical run. Tests further
    on call ``seed_demo._seed`` expecting a complete dataset, but ``_seed``
    only tops up an existing one, so a demo seeded here and then thinned out
    by unrelated tests in between would reach them incomplete. Dropping it
    makes their own ``_seed`` call build it fresh.
    """
    monkeypatch.setenv("APP_ENV", "dev")
    yield
    from scripts import reset_demo

    # The last-active-super-admin trigger refuses the drop when the demo's
    # super admins are the only ones, so keep one outside the demo.
    async with db_factory() as session:
        await make_user(session, is_superuser=True)
    await reset_demo._drop_demo()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from main import app

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


def _bearer(user_id: uuid.UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(subject=str(user_id), role=None)}"}


async def _user_id(db_factory: async_sessionmaker[Any], email: str) -> uuid.UUID:
    from models import User

    async with db_factory() as session:
        user_id: uuid.UUID = (
            await session.execute(select(User.id).where(User.email == email))
        ).scalar_one()
    return user_id


class _World:
    """The seeded demo plus one organisation outside it."""

    explore_id: uuid.UUID
    dev_id: uuid.UUID
    frontend_team_id: uuid.UUID
    outsider_id: uuid.UUID
    outsider_email: str
    outsider_team_id: uuid.UUID
    marker: str


@pytest.fixture
async def world(db_factory: async_sessionmaker[Any]) -> _World:
    from models import AuditLog, Organization, Team
    from scripts import seed_demo

    # Idempotent: seeds on a fresh database, tops up an existing demo.
    await seed_demo._seed(demo_only=True)

    w = _World()
    w.explore_id = await _user_id(db_factory, EXPLORE_EMAIL)
    w.dev_id = await _user_id(db_factory, "dev@demo.trustedoss.dev")
    suffix = uuid.uuid4().hex[:10]
    w.marker = f"dscope-{suffix}"
    async with db_factory() as session:
        w.frontend_team_id = (
            await session.execute(
                select(Team.id)
                .join(Organization, Organization.id == Team.organization_id)
                .where(Organization.slug == seed_demo._DEMO_ORG_SLUG, Team.slug == "frontend")
            )
        ).scalar_one()

        # The host's own administrator, outside the demo organisation, with a
        # team of their own and audit history.
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        w.outsider_email = f"real-admin-{suffix}@example.com"
        outsider = await make_user(session, email=w.outsider_email, is_superuser=True)
        await make_membership(session, user=outsider, team=team, role="group_admin")
        w.outsider_id = outsider.id
        w.outsider_team_id = team.id

        rows = [
            # outsider actor, outsider team: hidden on the demo
            (outsider.id, team.id, "outsider"),
            # outsider actor, no team: hidden
            (outsider.id, None, "outsider-noteam"),
            # demo actor: shown
            (w.dev_id, None, "demo-actor"),
            # no actor, demo team: shown
            (None, w.frontend_team_id, "demo-team"),
            # neither: hidden
            (None, None, "system"),
        ]
        for actor, group, label in rows:
            session.add(
                AuditLog(
                    actor_user_id=actor,
                    group_id=group,
                    action=f"{w.marker}",
                    target_table="projects",
                    target_id=label,
                    diff={"label": label},
                )
            )
        await session.commit()
    return w


def _set_demo(monkeypatch: pytest.MonkeyPatch, on: bool) -> None:
    if on:
        monkeypatch.setenv("DEMO_READ_ONLY", "true")
    else:
        monkeypatch.delenv("DEMO_READ_ONLY", raising=False)


# --------------------------------------------------------------------------- #
# Seed: explore@ is a super admin and keeps its Frontend group_admin seat.
# --------------------------------------------------------------------------- #


async def test_explore_account_is_super_admin_with_frontend_seat(
    db_factory: async_sessionmaker[Any],
) -> None:
    """A FRESH seed creates explore@ as a super admin with its Frontend seat.

    The demo is dropped first: on an already-seeded database ``_seed`` takes
    the existing-dataset path and would not create the account at all, so
    the assertion would be about whatever an earlier test left behind.
    """
    from models import Membership, Team, User
    from scripts import reset_demo, seed_demo

    # The last-active-super-admin trigger refuses the drop when the demo super
    # admins are the only ones, so keep one outside the demo.
    async with db_factory() as session:
        await make_user(session, is_superuser=True)

    await reset_demo._drop_demo()
    summary = await seed_demo._seed(demo_only=True)
    roles = {u["email"]: u["role"] for u in summary["users"]}
    assert roles[EXPLORE_EMAIL] == "super_admin"

    explore_id = await _user_id(db_factory, EXPLORE_EMAIL)
    async with db_factory() as session:
        user = (await session.execute(select(User).where(User.id == explore_id))).scalar_one()
        seats = (
            await session.execute(
                select(Team.slug, Membership.role)
                .join(Team, Team.id == Membership.group_id)
                .where(Membership.user_id == explore_id)
            )
        ).all()
    assert user.is_superuser is True
    assert user.is_active is True
    assert [tuple(s) for s in seats] == [("frontend", "group_admin")]


async def test_seed_summary_reports_explore_as_super_admin(world: _World) -> None:
    from scripts import seed_demo

    # Already seeded by the fixture, so this takes the existing-dataset path.
    summary = await seed_demo._seed(demo_only=True)
    roles = {u["email"]: u["role"] for u in summary["users"]}
    assert roles[EXPLORE_EMAIL] == "super_admin"
    assert roles["admin@demo.trustedoss.dev"] == "super_admin"
    assert roles["frontend-admin@demo.trustedoss.dev"] == "group_admin:frontend"


# --------------------------------------------------------------------------- #
# Users screen.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("demo", [True, False])
async def test_user_list_scope(
    client: AsyncClient,
    world: _World,
    monkeypatch: pytest.MonkeyPatch,
    demo: bool,
) -> None:
    _set_demo(monkeypatch, demo)
    headers = _bearer(world.explore_id)

    resp = await client.get(
        "/v1/admin/users", params={"page_size": 200, "search": world.outsider_email},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    found = [u["email"] for u in resp.json()["items"]]
    assert (world.outsider_email in found) is (not demo)

    resp = await client.get(
        "/v1/admin/users", params={"page_size": 200, "role": "super_admin"}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    emails = {u["email"] for u in body["items"]}
    assert {EXPLORE_EMAIL, "admin@demo.trustedoss.dev"} <= emails
    assert (world.outsider_email in emails) is (not demo)
    if demo:
        # The total is scoped too, not only the page.
        assert body["total"] == len(body["items"])
        assert all(e.endswith("@demo.trustedoss.dev") for e in emails)


@pytest.mark.parametrize("demo", [True, False])
async def test_user_detail_scope(
    client: AsyncClient,
    world: _World,
    monkeypatch: pytest.MonkeyPatch,
    demo: bool,
) -> None:
    _set_demo(monkeypatch, demo)
    headers = _bearer(world.explore_id)

    outsider = await client.get(f"/v1/admin/users/{world.outsider_id}", headers=headers)
    assert outsider.status_code == (404 if demo else 200), outsider.text
    if demo:
        assert outsider.headers["content-type"].startswith("application/problem+json")

    insider = await client.get(f"/v1/admin/users/{world.dev_id}", headers=headers)
    assert insider.status_code == 200, insider.text


@pytest.mark.parametrize("demo", [True, False])
async def test_user_detail_hides_memberships_outside_demo(
    client: AsyncClient,
    world: _World,
    db_factory: async_sessionmaker[Any],
    monkeypatch: pytest.MonkeyPatch,
    demo: bool,
) -> None:
    """A demo user who also sits in another organisation shows only demo seats."""
    async with db_factory() as session:
        from models import Team

        outsider_team = (
            await session.execute(select(Team).where(Team.id == world.outsider_team_id))
        ).scalar_one()
        dual = await make_user(session)
        await make_membership(session, user=dual, team=outsider_team, role="developer")
        frontend = (
            await session.execute(select(Team).where(Team.id == world.frontend_team_id))
        ).scalar_one()
        await make_membership(session, user=dual, team=frontend, role="developer")

    _set_demo(monkeypatch, demo)
    resp = await client.get(f"/v1/admin/users/{dual.id}", headers=_bearer(world.explore_id))
    assert resp.status_code == 200, resp.text
    teams = {m["team_id"] for m in resp.json()["memberships"]}
    assert str(world.frontend_team_id) in teams
    assert (str(world.outsider_team_id) in teams) is (not demo)


# --------------------------------------------------------------------------- #
# Audit screens: the admin search and the team-scoped /v1/audit.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("path", ["/v1/admin/audit", "/v1/audit"])
@pytest.mark.parametrize("demo", [True, False])
async def test_audit_scope(
    client: AsyncClient,
    world: _World,
    monkeypatch: pytest.MonkeyPatch,
    demo: bool,
    path: str,
) -> None:
    _set_demo(monkeypatch, demo)
    resp = await client.get(
        path,
        params={"action": world.marker, "page_size": 200},
        headers=_bearer(world.explore_id),
    )
    assert resp.status_code == 200, resp.text
    labels = {row["target_id"] for row in resp.json()["items"]}
    if demo:
        assert labels == {"demo-actor", "demo-team"}
    else:
        assert labels == {"outsider", "outsider-noteam", "demo-actor", "demo-team", "system"}


# --------------------------------------------------------------------------- #
# The middleware is wired into the real app for a real super admin.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("path", "allowed"),
    [
        ("/v1/admin/health", True),
        ("/v1/admin/organizations", True),
        ("/v1/admin/backup", False),
        ("/v1/admin/users/export", False),
        ("/v1/admin/audit/export.csv", False),
        ("/v1/admin/api-keys/hash-migration", False),
        ("/v1/user-anonymisation/awaiting-execution", False),
    ],
)
@pytest.mark.parametrize("demo", [True, False])
async def test_admin_read_guard_in_app(
    client: AsyncClient,
    world: _World,
    monkeypatch: pytest.MonkeyPatch,
    demo: bool,
    path: str,
    allowed: bool,
) -> None:
    _set_demo(monkeypatch, demo)
    resp = await client.get(path, headers=_bearer(world.explore_id))
    if demo and not allowed:
        assert resp.status_code == 403, resp.text
        body = resp.json()
        assert body["type"] == "urn:trustedoss:problem:demo-read-only"
        assert body["demo_admin_hidden"] is True
    else:
        # Reached the route: whatever it answers, it is not the demo refusal.
        assert "demo_admin_hidden" not in resp.text, resp.text


# --------------------------------------------------------------------------- #
# Report downloads leave no history row on the demo.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("demo", [True, False])
async def test_report_download_history_skipped_on_demo(
    world: _World,
    db_factory: async_sessionmaker[Any],
    monkeypatch: pytest.MonkeyPatch,
    demo: bool,
) -> None:
    from core.security import CurrentUser
    from models import ReportDownload, Team
    from services.report_download_service import record_report_download

    async with db_factory() as session:
        team = (
            await session.execute(select(Team).where(Team.id == world.outsider_team_id))
        ).scalar_one()
        project = await make_project(session, team=team)

    _set_demo(monkeypatch, demo)
    actor = CurrentUser(
        id=world.outsider_id,
        email=world.outsider_email,
        role="super_admin",
        is_superuser=True,
        is_active=True,
        team_ids=[],
        team_roles={},
    )
    async with db_factory() as session:
        await record_report_download(
            session,
            project=project,
            scan_id=None,
            user=actor,
            report_type="vuln_pdf",
            fmt="pdf",
            size_bytes=10,
            request=None,
        )
    async with db_factory() as session:
        count = (
            await session.execute(
                select(func.count())
                .select_from(ReportDownload)
                .where(ReportDownload.project_id == project.id)
            )
        ).scalar_one()
    assert count == (0 if demo else 1)
