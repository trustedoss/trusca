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
    make_scan,
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
    outsider_team_name: str
    outsider_project_id: uuid.UUID
    outsider_project_name: str
    outsider_scan_id: uuid.UUID
    outsider_org_id: uuid.UUID
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
        w.outsider_team_name = team.name
        w.outsider_org_id = org.id
        project = await make_project(session, team=team, created_by=outsider)
        scan = await make_scan(
            session, project=project, requested_by=outsider, status="succeeded"
        )
        w.outsider_scan_id = scan.id
        w.outsider_project_id = project.id
        w.outsider_project_name = project.name

        rows = [
            # outsider actor, outsider team: hidden on the demo
            (outsider.id, team.id, "outsider"),
            # outsider actor, no team: hidden
            (outsider.id, None, "outsider-noteam"),
            # demo actor: shown
            (w.dev_id, None, "demo-actor"),
            # no actor, demo team: shown
            (None, w.frontend_team_id, "demo-team"),
            # outsider actor on a demo team: the row is shown, the actor is not
            (outsider.id, w.frontend_team_id, "outsider-on-demo"),
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
        assert labels == {"demo-actor", "demo-team", "outsider-on-demo"}
    else:
        assert labels == {
            "outsider",
            "outsider-noteam",
            "demo-actor",
            "demo-team",
            "system",
            "outsider-on-demo",
        }
    by_label = {row["target_id"]: row for row in resp.json()["items"]}
    outsider_row = by_label["outsider-on-demo"]
    if demo:
        assert outsider_row["actor_email"] is None
        assert outsider_row["actor_user_id"] is None
    else:
        assert outsider_row["actor_email"] == world.outsider_email
        assert outsider_row["actor_user_id"] == str(world.outsider_id)
    # A demo actor keeps their identity either way.
    assert by_label["demo-actor"]["actor_email"] == "dev@demo.trustedoss.dev"
    assert by_label["demo-actor"]["actor_user_id"] == str(world.dev_id)


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


# --------------------------------------------------------------------------- #
# Teams, organizations, scans, group directory, projects: the super-admin
# bypass stops at the demo organisation.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("demo", [True, False])
async def test_admin_teams_orgs_scans_scope(
    client: AsyncClient,
    world: _World,
    monkeypatch: pytest.MonkeyPatch,
    demo: bool,
) -> None:
    _set_demo(monkeypatch, demo)
    headers = _bearer(world.explore_id)

    teams = await client.get(
        "/v1/admin/teams", params={"search": world.outsider_team_name, "page_size": 200},
        headers=headers,
    )
    assert teams.status_code == 200, teams.text
    team_ids = {t["id"] for t in teams.json()["items"]}
    assert (str(world.outsider_team_id) in team_ids) is (not demo)

    detail = await client.get(f"/v1/admin/teams/{world.outsider_team_id}", headers=headers)
    assert detail.status_code == (404 if demo else 200), detail.text
    if not demo:
        assert world.outsider_email in {m["email"] for m in detail.json()["members"]}
    demo_detail = await client.get(f"/v1/admin/teams/{world.frontend_team_id}", headers=headers)
    assert demo_detail.status_code == 200, demo_detail.text

    orgs = await client.get("/v1/admin/organizations", params={"page_size": 200}, headers=headers)
    assert orgs.status_code == 200, orgs.text
    slugs = {o["slug"] for o in orgs.json()["items"]}
    if demo:
        assert slugs == {"demo-org"}
        assert orgs.json()["total"] == 1
    else:
        # Unscoped, every organisation counts (at least the demo and the
        # outsider's). The page is oldest-first, so on a database other tests
        # have filled the two need not be on it.
        assert orgs.json()["total"] >= 2

    scans = await client.get(
        "/v1/admin/scans", params={"project": world.outsider_project_name}, headers=headers
    )
    assert scans.status_code == 200, scans.text
    assert (scans.json()["total"] >= 1) is (not demo)


@pytest.mark.parametrize("demo", [True, False])
async def test_group_directory_scope(
    client: AsyncClient,
    world: _World,
    monkeypatch: pytest.MonkeyPatch,
    demo: bool,
) -> None:
    _set_demo(monkeypatch, demo)
    headers = _bearer(world.explore_id)

    members = await client.get(f"/v1/groups/{world.outsider_team_id}/members", headers=headers)
    assert members.status_code == (404 if demo else 200), members.text
    if not demo:
        assert world.outsider_email in members.text

    listing = await client.get(
        "/v1/groups", params={"q": world.outsider_team_name}, headers=headers
    )
    assert listing.status_code == 200, listing.text
    assert (str(world.outsider_team_id) in listing.text) is (not demo)

    demo_members = await client.get(f"/v1/groups/{world.frontend_team_id}/members", headers=headers)
    assert demo_members.status_code == 200, demo_members.text


@pytest.mark.parametrize(
    "suffix", ["", "/assignable-members", "/scans", "/components"]
)
@pytest.mark.parametrize("demo", [True, False])
async def test_project_reads_scope(
    client: AsyncClient,
    world: _World,
    monkeypatch: pytest.MonkeyPatch,
    demo: bool,
    suffix: str,
) -> None:
    _set_demo(monkeypatch, demo)
    resp = await client.get(
        f"/v1/projects/{world.outsider_project_id}{suffix}", headers=_bearer(world.explore_id)
    )
    if demo:
        assert resp.status_code in (403, 404), resp.text
    else:
        assert resp.status_code == 200, resp.text


@pytest.mark.parametrize("demo", [True, False])
async def test_project_list_scope(
    client: AsyncClient,
    world: _World,
    monkeypatch: pytest.MonkeyPatch,
    demo: bool,
) -> None:
    _set_demo(monkeypatch, demo)
    headers = _bearer(world.explore_id)
    resp = await client.get(
        "/v1/projects", params={"q": world.outsider_project_name}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert (str(world.outsider_project_id) in resp.text) is (not demo)
    # The demo's own projects stay visible to its super admin.
    demo_list = await client.get("/v1/projects", params={"q": "portal-web"}, headers=headers)
    assert demo_list.status_code == 200, demo_list.text
    assert "portal-web" in demo_list.text



@pytest.mark.parametrize("demo", [True, False])
async def test_dashboard_scope(
    world: _World,
    db_factory: async_sessionmaker[Any],
    monkeypatch: pytest.MonkeyPatch,
    demo: bool,
) -> None:
    from core.security import CurrentUser
    from services.dashboard_service import _accessible_project_ids

    _set_demo(monkeypatch, demo)
    actor = CurrentUser(
        id=world.explore_id,
        email=EXPLORE_EMAIL,
        role="super_admin",
        is_superuser=True,
        team_ids=[world.frontend_team_id],
        team_roles={world.frontend_team_id: "group_admin"},
    )
    async with db_factory() as session:
        ids = set(await _accessible_project_ids(session, actor=actor))
    assert (world.outsider_project_id in ids) is (not demo)
    # More than the explorer's own Frontend team: the demo-wide view remains.
    assert len(ids) >= 5


@pytest.mark.parametrize("demo", [True, False])
async def test_user_counts_scope(
    client: AsyncClient,
    world: _World,
    db_factory: async_sessionmaker[Any],
    monkeypatch: pytest.MonkeyPatch,
    demo: bool,
) -> None:
    """team_count and scan_count count only the demo organisation in demo mode."""
    from models import Project, Team

    async with db_factory() as session:
        outsider_team = (
            await session.execute(select(Team).where(Team.id == world.outsider_team_id))
        ).scalar_one()
        frontend = (
            await session.execute(select(Team).where(Team.id == world.frontend_team_id))
        ).scalar_one()
        dual = await make_user(session)
        await make_membership(session, user=dual, team=outsider_team, role="developer")
        await make_membership(session, user=dual, team=frontend, role="developer")
        project = (
            await session.execute(select(Project).where(Project.id == world.outsider_project_id))
        ).scalar_one()
        await make_scan(session, project=project, requested_by=dual)

    _set_demo(monkeypatch, demo)
    headers = _bearer(world.explore_id)
    listing = await client.get(
        "/v1/admin/users", params={"search": dual.email}, headers=headers
    )
    assert listing.status_code == 200, listing.text
    [item] = listing.json()["items"]
    assert item["team_count"] == (1 if demo else 2)

    detail = await client.get(f"/v1/admin/users/{dual.id}", headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["scan_count"] == (0 if demo else 1)


@pytest.mark.parametrize("demo", [True, False])
async def test_self_export_refused_on_demo(
    client: AsyncClient,
    world: _World,
    monkeypatch: pytest.MonkeyPatch,
    demo: bool,
) -> None:
    _set_demo(monkeypatch, demo)
    resp = await client.get("/v1/users/me/export", headers=_bearer(world.explore_id))
    if demo:
        assert resp.status_code == 403, resp.text
        assert resp.json()["type"] == "urn:trustedoss:problem:demo-read-only"
    else:
        assert resp.status_code == 200, resp.text


@pytest.mark.parametrize(
    "raw",
    [
        "/v1/admin/backup/..%5C..%5Cusers/download",
        "/v1/admin/backup/..%5C..%5C..%5Chealth/download",
    ],
)
async def test_backslash_param_does_not_reach_a_hidden_admin_route(
    client: AsyncClient,
    world: _World,
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
) -> None:
    _set_demo(monkeypatch, True)
    resp = await client.get(raw, headers=_bearer(world.explore_id))
    assert resp.status_code == 403, resp.text
    assert resp.json().get("demo_admin_hidden") is True


@pytest.mark.parametrize("demo", [True, False])
async def test_scan_list_scope(
    client: AsyncClient,
    world: _World,
    monkeypatch: pytest.MonkeyPatch,
    demo: bool,
) -> None:
    _set_demo(monkeypatch, demo)
    resp = await client.get(
        "/v1/scans", params={"size": 100}, headers=_bearer(world.explore_id)
    )
    assert resp.status_code == 200, resp.text
    ids = {item["id"] for item in resp.json()["items"]}
    assert (str(world.outsider_scan_id) in ids) is (not demo)
    # The demo's own scans stay visible to its super admin.
    assert resp.json()["total"] >= 1


@pytest.mark.parametrize(
    ("path", "off_status"),
    [
        ("/v1/scans/{scan}", 200),
        ("/v1/scans/{scan}/log", None),
        ("/v1/projects/{project}/source-tree", None),
    ],
)
@pytest.mark.parametrize("demo", [True, False])
async def test_outsider_scan_reads_scope(
    client: AsyncClient,
    world: _World,
    monkeypatch: pytest.MonkeyPatch,
    demo: bool,
    path: str,
    off_status: int | None,
) -> None:
    _set_demo(monkeypatch, demo)
    url = path.format(scan=world.outsider_scan_id, project=world.outsider_project_id)
    resp = await client.get(url, headers=_bearer(world.explore_id))
    if demo:
        assert resp.status_code in (403, 404), resp.text
    else:
        # Off, the request reaches the resource. A route without data for this
        # fixture may answer 404 for its own reasons, so only the authz
        # refusal is ruled out where no exact status is pinned.
        if off_status is not None:
            assert resp.status_code == off_status, resp.text
        else:
            assert resp.status_code != 403, resp.text


@pytest.mark.parametrize("demo", [True, False])
async def test_outsider_scan_websocket_scope(
    world: _World,
    monkeypatch: pytest.MonkeyPatch,
    demo: bool,
) -> None:
    import asyncio
    import json

    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from main import app

    _set_demo(monkeypatch, demo)
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://localhost:5173")
    monkeypatch.setenv("WEBSOCKET_AUTH_TIMEOUT_SECONDS", "2.0")
    token = create_access_token(subject=str(world.explore_id), role=None)

    ws_path = f"/ws/scans/{world.outsider_scan_id}"

    def _connect() -> int | None:
        with TestClient(app) as tc, tc.websocket_connect(ws_path) as ws:
            ws.send_text(json.dumps({"type": "auth", "token": token}))
            try:
                ws.receive_text()
            except WebSocketDisconnect as exc:
                return int(exc.code)
            return None

    code = await asyncio.to_thread(_connect)
    if demo:
        assert code in (4403, 4404), code
    else:
        # Off, the initial progress frame arrives instead of a close.
        assert code is None, code
