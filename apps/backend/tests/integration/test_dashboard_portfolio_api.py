"""``GET /v1/dashboard/portfolio`` — the team × project grid.

Service behaviour and the HTTP contract are asserted in one file because the
route has no path parameter: what the caller can see is decided entirely by
the scoping the service applies, so the isolation cases here *are* the access
control, not a supplement to a per-resource check.

Three things beyond the happy path are worth their own cases:

  - **a team the caller does not belong to must not be enumerable**, not even
    as an empty row carrying its name;
  - **"never scanned" is not "clean"** — both render as zero counts, and only
    the flag separates a project nobody has looked at from one that came back
    empty;
  - **truncation is reported**. A grid that silently showed the first dozen
    projects would read as the whole portfolio, and the reader would conclude
    the ones not shown are fine.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from services.dashboard_portfolio_service import (
    MAX_TEAMS,
    PROJECTS_PER_TEAM,
    get_dashboard_portfolio,
)
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_scan,
    make_team,
    make_user,
    principal_for,
    unique_suffix,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent

pytestmark = pytest.mark.integration

PASSWORD = "correct-horse-battery-staple"


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip dashboard portfolio tests")
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
        pytest.skip(f"alembic upgrade head failed:\n{result.stderr}")


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    from core.audit import install_audit_listeners
    from core.config import database_url

    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    install_audit_listeners(factory)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    from main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _user_with_password(session: AsyncSession):
    from core.security import hash_password
    from models import User

    suffix = unique_suffix()
    user = User(
        email=f"grid-{suffix}@example.com",
        full_name=f"Grid {suffix}",
        hashed_password=hash_password(PASSWORD),
        is_active=True,
        is_superuser=False,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def _login(client: httpx.AsyncClient, email: str) -> str:
    response = await client.post(
        "/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    assert isinstance(token, str)
    return token


async def _finding(
    session: AsyncSession, *, scan_id: uuid.UUID, severity: str = "critical"
) -> None:
    from models import (
        Component,
        ComponentVersion,
        ScanComponent,
        Vulnerability,
        VulnerabilityFinding,
    )

    suffix = unique_suffix()
    purl = f"pkg:npm/pkg-{suffix}"
    component = Component(purl=purl, package_type="npm", name=f"pkg-{suffix}")
    session.add(component)
    await session.commit()
    await session.refresh(component)

    cv = ComponentVersion(
        component_id=component.id, version="1.0.0", purl_with_version=f"{purl}@1.0.0"
    )
    session.add(cv)
    await session.commit()
    await session.refresh(cv)

    session.add(
        ScanComponent(scan_id=scan_id, component_version_id=cv.id, direct=True, raw_data={})
    )
    vuln = Vulnerability(
        external_id=f"CVE-2026-{suffix}",
        source="NVD",
        severity=severity,
        summary="grid fixture",
    )
    session.add(vuln)
    await session.commit()
    await session.refresh(vuln)

    session.add(
        VulnerabilityFinding(
            scan_id=scan_id, component_version_id=cv.id, vulnerability_id=vuln.id
        )
    )
    await session.commit()


async def _scanned_project(session: AsyncSession, *, team, criticals: int = 0):
    project = await make_project(session, team=team)
    scan = await make_scan(session, project=project, status="succeeded")
    for _ in range(criticals):
        await _finding(session, scan_id=scan.id)
    return project


# ---------------------------------------------------------------------------
# HTTP contract
# ---------------------------------------------------------------------------


async def test_requires_authentication(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/dashboard/portfolio")

    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_a_caller_with_no_projects_gets_an_empty_grid(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Empty, not an error — the widget renders an empty state, not a failure."""
    user = await _user_with_password(db_session)
    token = await _login(client, user.email)

    response = await client.get(
        "/v1/dashboard/portfolio", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["teams"] == []
    assert body["project_count"] == 0
    assert body["truncated"] is False


async def test_the_grid_carries_only_the_callers_teams(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """The access-control assertion for a route with no path parameter.

    Team B's row must be absent entirely — a row with its name and no
    projects would still disclose that the team exists and what it is called.
    """
    org = await make_organization(db_session)
    team_a = await make_team(db_session, organization=org)
    team_b = await make_team(db_session, organization=org)

    await _scanned_project(db_session, team=team_a, criticals=1)
    await _scanned_project(db_session, team=team_b, criticals=3)

    member_a = await _user_with_password(db_session)
    await make_membership(db_session, user=member_a, team=team_a, role="developer")
    token = await _login(client, member_a.email)

    response = await client.get(
        "/v1/dashboard/portfolio", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    body = response.json()
    assert [team["team_id"] for team in body["teams"]] == [str(team_a.id)]
    assert body["teams"][0]["projects"][0]["critical"] == 1
    assert body["project_count"] == 1


# ---------------------------------------------------------------------------
# Service behaviour
# ---------------------------------------------------------------------------


async def test_a_never_scanned_project_is_not_reported_as_clean(
    db_session: AsyncSession,
) -> None:
    """Zero counts mean two different things, and only the flag separates them.

    A project nobody has scanned and a project whose scan came back empty are
    identical in every number on this grid. Without ``scanned`` the UI would
    paint them the same colour and tell the reader the unscanned one is fine.
    """
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")

    clean = await _scanned_project(db_session, team=team, criticals=0)
    never = await make_project(db_session, team=team)

    grid = await get_dashboard_portfolio(
        db_session, actor=principal_for(user, team_ids=[team.id])
    )

    by_id = {p.project_id: p for p in grid.teams[0].projects}
    assert by_id[clean.id].scanned is True
    assert by_id[clean.id].last_scan_at is not None
    assert by_id[never.id].scanned is False
    assert by_id[never.id].last_scan_at is None
    assert by_id[never.id].critical == 0


async def test_projects_are_ordered_worst_first(db_session: AsyncSession) -> None:
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")

    quiet = await _scanned_project(db_session, team=team, criticals=0)
    worst = await _scanned_project(db_session, team=team, criticals=2)
    middle = await _scanned_project(db_session, team=team, criticals=1)

    grid = await get_dashboard_portfolio(
        db_session, actor=principal_for(user, team_ids=[team.id])
    )

    order = [p.project_id for p in grid.teams[0].projects]
    assert order[:3] == [worst.id, middle.id, quiet.id]


async def test_a_cut_row_says_how_much_it_cut(db_session: AsyncSession) -> None:
    """Truncation is reported, not silent.

    The reader has to be able to tell "these are all my projects" from "these
    are the worst twelve", because the conclusion drawn about the ones not
    shown is the opposite in each case.
    """
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")

    over_cap = PROJECTS_PER_TEAM + 3
    for _ in range(over_cap):
        await _scanned_project(db_session, team=team, criticals=1)

    grid = await get_dashboard_portfolio(
        db_session, actor=principal_for(user, team_ids=[team.id])
    )

    assert len(grid.teams[0].projects) == PROJECTS_PER_TEAM
    assert grid.teams[0].project_count == over_cap
    assert grid.project_count == over_cap
    assert grid.shown_project_count == PROJECTS_PER_TEAM
    assert grid.truncated is True


async def test_an_uncut_grid_says_so(db_session: AsyncSession) -> None:
    """The mirror: `truncated` must not be true by default.

    A flag that is always set carries no information, and the UI would show a
    "showing a subset" caption on a complete grid forever.
    """
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")
    await _scanned_project(db_session, team=team, criticals=1)

    grid = await get_dashboard_portfolio(
        db_session, actor=principal_for(user, team_ids=[team.id])
    )

    assert grid.truncated is False
    assert grid.shown_project_count == grid.project_count == 1


async def test_the_counts_match_the_project_list(db_session: AsyncSession) -> None:
    """The grid and the project list must not disagree about the same project.

    They share the aggregation helpers rather than each implementing "worst
    CVE per component over the latest succeeded scan", and this pins that:
    if the grid ever grows its own copy, the two drift and the user sees one
    number on the list and another on the dashboard.
    """
    from services.project_list_enrichment import (
        _latest_succeeded_scan_id_map,
        _severity_summary_map,
    )

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")

    project = await _scanned_project(db_session, team=team, criticals=2)
    scan = await make_scan(db_session, project=project, status="succeeded")
    await _finding(db_session, scan_id=scan.id, severity="high")

    grid = await get_dashboard_portfolio(
        db_session, actor=principal_for(user, team_ids=[team.id])
    )
    succeeded = await _latest_succeeded_scan_id_map(db_session, project_ids=[project.id])
    expected = (
        await _severity_summary_map(db_session, succeeded_by_project=succeeded)
    )[project.id]

    cell = next(p for p in grid.teams[0].projects if p.project_id == project.id)
    assert (cell.critical, cell.high, cell.medium, cell.low) == (
        expected["critical"],
        expected["high"],
        expected["medium"],
        expected["low"],
    )
    # And the newest succeeded scan is the anchor: the older scan's two
    # criticals must not leak into a summary that describes the newer one.
    assert cell.critical == 0
    assert cell.high == 1


# ---------------------------------------------------------------------------
# More than one team — the half of the service a single-team fixture cannot
# reach at all: the team ranking, the MAX_TEAMS cut, and the counts that
# describe it.
# ---------------------------------------------------------------------------


async def test_teams_are_ranked_by_their_worst_project(
    db_session: AsyncSession,
) -> None:
    """Worst team first — and ranked on the whole team, not on what fits.

    The subtle version of this bug ranks each row after truncating it. A
    team's worst `high` can sit on a project that sorts below the cut because
    its `critical` is lower, so the team gets ranked on a sample of itself and
    can be pushed down — or off — the grid by cells it was never shown.
    """
    org = await make_organization(db_session)
    user = await make_user(db_session)

    # Both teams tie on criticals; the tie breaks on `high`, which for the
    # first team lives on a project that the per-team cap will cut.
    loud = await make_team(db_session, organization=org)
    await make_membership(db_session, user=user, team=loud, role="developer")
    for _ in range(PROJECTS_PER_TEAM):
        await _scanned_project(db_session, team=loud, criticals=1)
    buried = await make_project(db_session, team=loud)
    buried_scan = await make_scan(db_session, project=buried, status="succeeded")
    for _ in range(5):
        await _finding(db_session, scan_id=buried_scan.id, severity="high")

    quiet = await make_team(db_session, organization=org)
    await make_membership(db_session, user=user, team=quiet, role="developer")
    project = await _scanned_project(db_session, team=quiet, criticals=1)
    quiet_scan = await make_scan(db_session, project=project, status="succeeded")
    await _finding(db_session, scan_id=quiet_scan.id, severity="critical")
    await _finding(db_session, scan_id=quiet_scan.id, severity="high")

    grid = await get_dashboard_portfolio(
        db_session, actor=principal_for(user, team_ids=[loud.id, quiet.id])
    )

    order = [team.team_id for team in grid.teams]
    assert order == [loud.id, quiet.id], (
        "the team with five highs ranked below the one with a single high — "
        "its worst project was cut before the ranking looked at it"
    )
    # And the cut project really is absent from the row that outranked on it.
    loud_row = next(team for team in grid.teams if team.team_id == loud.id)
    assert buried.id not in {p.project_id for p in loud_row.projects}


async def test_the_team_cap_reports_both_what_it_kept_and_what_it_dropped(
    db_session: AsyncSession,
) -> None:
    """A dropped team leaves no trace in ``teams`` — so the counts must.

    A row that is cut can say so itself. A team that is cut cannot: it is not
    in the response at all, so a reader counting rows against ``team_count``
    would conclude the shown projects are spread across every team.
    """
    org = await make_organization(db_session)
    user = await make_user(db_session)
    over_cap = MAX_TEAMS + 2
    team_ids = []
    for index in range(over_cap):
        team = await make_team(db_session, organization=org)
        await make_membership(db_session, user=user, team=team, role="developer")
        team_ids.append(team.id)
        # Descending risk, so the two teams that fall off the end are known.
        await _scanned_project(db_session, team=team, criticals=over_cap - index)

    grid = await get_dashboard_portfolio(
        db_session, actor=principal_for(user, team_ids=team_ids)
    )

    assert len(grid.teams) == MAX_TEAMS
    assert grid.shown_team_count == MAX_TEAMS
    assert grid.team_count == over_cap
    assert grid.project_count == over_cap
    assert grid.shown_project_count == MAX_TEAMS
    assert grid.truncated is True
    # The two least risky teams are the ones dropped, not two arbitrary ones.
    assert [team.team_id for team in grid.teams] == team_ids[:MAX_TEAMS]


async def test_a_full_tie_still_resolves_to_the_same_twelve(
    db_session: AsyncSession,
) -> None:
    """The tie-break has to be total, and this is the case that proves it.

    ``projects.name`` carries no unique constraint — only ``(team_id, slug)``
    does — so two projects in one team can share a name and all four counts.
    With the name as the last key component the order then falls back to
    whatever row order Postgres returned, and which of the two is shown flips
    between requests with no data change.
    """
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")

    shared_name = f"api-{unique_suffix()}"
    created: list[uuid.UUID] = []
    for _ in range(PROJECTS_PER_TEAM + 2):
        project = await make_project(db_session, team=team)
        project.name = shared_name
        await db_session.commit()
        scan = await make_scan(db_session, project=project, status="succeeded")
        await _finding(db_session, scan_id=scan.id)
        created.append(project.id)

    actor = principal_for(user, team_ids=[team.id])
    first = await get_dashboard_portfolio(db_session, actor=actor)
    second = await get_dashboard_portfolio(db_session, actor=actor)

    kept_first = [p.project_id for p in first.teams[0].projects]
    kept_second = [p.project_id for p in second.teams[0].projects]
    assert len(kept_first) == PROJECTS_PER_TEAM
    assert kept_first == kept_second, (
        "the twelve cells shown changed between two identical requests"
    )
    # Stability alone is a weak oracle — a query plan that happens not to vary
    # would satisfy it. The order has to be the one the key defines, which for
    # a full tie is by project id.
    assert kept_first == sorted(created, key=str)[:PROJECTS_PER_TEAM]
