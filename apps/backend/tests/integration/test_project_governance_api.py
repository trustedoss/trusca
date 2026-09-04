"""``GET /v1/projects/{id}/governance`` — the band above the project tabs.

Two kinds of case, and the second is the point of the endpoint.

The access contract is the ordinary project-scoped one, asserted here because
this route is new: a project on another team is a 403 and a missing one a 404,
and neither runs a single aggregation first.

The rest is about the band agreeing with the screens it summarises. Every
number comes from the service that owns it elsewhere, so what these tests pin
is that the composition kept the ownership: the gate matches ``evaluate_gate``
(not a reimplementation of the threshold), the score matches the Overview
tab's, and "never scanned" stays distinguishable from "clean".
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from services.project_governance_service import (
    TREND_POINTS,
    get_project_governance,
)
from tests._db_required import migrate_to_head
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

pytestmark = pytest.mark.integration

PASSWORD = "correct-horse-battery-staple"


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


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
        email=f"band-{suffix}@example.com",
        full_name=f"Band {suffix}",
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


async def _critical_finding(
    session: AsyncSession,
    *,
    scan_id: uuid.UUID,
    kev_due: object = None,
    severity: str = "critical",
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
        summary="band fixture",
        kev=kev_due is not None,
        kev_due_date=kev_due,
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


async def _unlicensed_component(session: AsyncSession, *, scan_id: uuid.UUID) -> None:
    """A component the scan saw and could not attribute a licence to.

    No ``LicenseFinding`` row at all — the ordinary result when cdxgen finds
    no licence metadata. It is not an absence: it lands in the ``unknown``
    licence band, which is scored.
    """
    from models import Component, ComponentVersion, ScanComponent

    suffix = unique_suffix()
    purl = f"pkg:npm/unlicensed-{suffix}"
    component = Component(purl=purl, package_type="npm", name=f"unlicensed-{suffix}")
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
    await session.commit()


async def _team_project(session: AsyncSession):
    org = await make_organization(session)
    team = await make_team(session, organization=org)
    user = await make_user(session)
    await make_membership(session, user=user, team=team, role="developer")
    project = await make_project(session, team=team)
    return team, user, project


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------


async def test_requires_authentication(client: httpx.AsyncClient) -> None:
    response = await client.get(f"/v1/projects/{uuid.uuid4()}/governance")

    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_another_teams_project_is_refused(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    org = await make_organization(db_session)
    team_a = await make_team(db_session, organization=org)
    team_b = await make_team(db_session, organization=org)
    project_b = await make_project(db_session, team=team_b)

    member_a = await _user_with_password(db_session)
    await make_membership(db_session, user=member_a, team=team_a, role="developer")
    token = await _login(client, member_a.email)

    response = await client.get(
        f"/v1/projects/{project_b.id}/governance",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_a_missing_project_is_404(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    user = await _user_with_password(db_session)
    token = await _login(client, user.email)

    response = await client.get(
        f"/v1/projects/{uuid.uuid4()}/governance",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")


# ---------------------------------------------------------------------------
# The band agrees with what it summarises
# ---------------------------------------------------------------------------


async def test_a_never_scanned_project_reports_no_verdict(
    db_session: AsyncSession,
) -> None:
    """A gate that has never run is not a gate that passed.

    Every number is zero either way, so ``scanned`` and a null gate status are
    the only things standing between "nobody has looked" and "looked, clean".
    """
    _, user, project = await _team_project(db_session)

    band = await get_project_governance(
        db_session,
        project_id=project.id,
        actor=principal_for(user, team_ids=[project.team_id]),
    )

    assert band.scanned is False
    assert band.gate.status is None
    assert band.gate.scan_id is None
    assert band.risk_score == 0
    assert band.trend == []


async def test_the_gate_matches_evaluate_gate(db_session: AsyncSession) -> None:
    """The verdict is the evaluator's, not a second copy of the threshold.

    The dashboard's action queue aggregates the gate's *inputs* because it
    needs a verdict per project across a portfolio; this route has one project
    and calls the evaluator. If it ever grows its own threshold, the two
    answers drift and this fails.
    """
    from services.policy_gate import evaluate_gate

    _, user, project = await _team_project(db_session)
    scan = await make_scan(db_session, project=project, status="succeeded")
    await _critical_finding(db_session, scan_id=scan.id)

    band = await get_project_governance(
        db_session,
        project_id=project.id,
        actor=principal_for(user, team_ids=[project.team_id]),
    )
    verdict = await evaluate_gate(db_session, project.id)

    assert band.gate.status == verdict.gate == "fail"
    assert band.gate.critical_cve_count == verdict.critical_cve_count == 1
    assert band.gate.scan_id == verdict.scan_id == scan.id


async def test_the_score_matches_the_overview_tab(db_session: AsyncSession) -> None:
    """The band sits three pixels above the tab; they cannot disagree.

    Both read ``services.risk_score`` over the same snapshot, and this pins
    that rather than trusting it — a band computing its own score is the
    defect this endpoint exists to avoid.
    """
    from services.project_detail_service import get_project_overview

    _, user, project = await _team_project(db_session)
    scan = await make_scan(db_session, project=project, status="succeeded")
    await _critical_finding(db_session, scan_id=scan.id)
    await _critical_finding(db_session, scan_id=scan.id, severity="high")

    actor = principal_for(user, team_ids=[project.team_id])
    band = await get_project_governance(db_session, project_id=project.id, actor=actor)
    overview = await get_project_overview(db_session, project_id=project.id, actor=actor)

    assert band.risk_score == overview["risk_score"]
    assert band.risk_score > 0


async def test_kev_deadlines_split_overdue_from_due_soon(
    db_session: AsyncSession,
) -> None:
    _, user, project = await _team_project(db_session)
    scan = await make_scan(db_session, project=project, status="succeeded")
    today = datetime.now(tz=UTC).date()
    await _critical_finding(db_session, scan_id=scan.id, kev_due=today - timedelta(days=3))
    await _critical_finding(db_session, scan_id=scan.id, kev_due=today + timedelta(days=2))
    # Beyond the week — real, but not a deadline anyone has to act on today.
    await _critical_finding(db_session, scan_id=scan.id, kev_due=today + timedelta(days=60))

    band = await get_project_governance(
        db_session,
        project_id=project.id,
        actor=principal_for(user, team_ids=[project.team_id]),
    )

    assert band.kev_sla.overdue == 1
    assert band.kev_sla.due_soon == 1


async def test_the_trend_is_oldest_first_and_bounded(
    db_session: AsyncSession,
) -> None:
    """A sparkline reads left to right, and the band is not a history page."""
    _, user, project = await _team_project(db_session)
    now = datetime.now(tz=UTC)
    made = []
    for index in range(TREND_POINTS + 3):
        scan = await make_scan(
            db_session,
            project=project,
            status="succeeded",
            created_at=now - timedelta(days=TREND_POINTS + 3 - index),
        )
        made.append(scan.id)

    band = await get_project_governance(
        db_session,
        project_id=project.id,
        actor=principal_for(user, team_ids=[project.team_id]),
    )

    assert len(band.trend) == TREND_POINTS
    assert [point.scan_id for point in band.trend] == made[-TREND_POINTS:]
    stamps = [point.scanned_at for point in band.trend]
    assert stamps == sorted(stamps)


async def test_a_failed_scan_does_not_become_the_current_snapshot(
    db_session: AsyncSession,
) -> None:
    """Failure means "we did not measure", not "we measured nothing".

    The band would otherwise report a clean gate the moment a scan failed,
    which is the same trap ``scan_resolution`` documents for the Overview tab.
    """
    _, user, project = await _team_project(db_session)
    now = datetime.now(tz=UTC)
    succeeded = await make_scan(
        db_session,
        project=project,
        status="succeeded",
        created_at=now - timedelta(days=2),
    )
    await _critical_finding(db_session, scan_id=succeeded.id)
    await make_scan(
        db_session, project=project, status="failed", created_at=now - timedelta(hours=1)
    )

    band = await get_project_governance(
        db_session,
        project_id=project.id,
        actor=principal_for(user, team_ids=[project.team_id]),
    )

    assert band.gate.scan_id == succeeded.id
    assert band.gate.status == "fail"
    assert band.scanned is True


async def test_the_score_matches_the_tab_when_the_licence_axis_decides_it(
    db_session: AsyncSession,
) -> None:
    """The parity case the first version of this endpoint failed.

    A fixture with CVEs proves nothing about the licence axis: the score is
    the worse of the two, so a security-dominant project agrees no matter how
    the licence side is computed. Here there are no vulnerabilities at all and
    two components with no licence row — which is the ``unknown`` band, not an
    absence. The band read 0 while the tab read 8.7, because the two started
    from different tables.
    """
    from services.project_detail_service import get_project_overview

    _, user, project = await _team_project(db_session)
    scan = await make_scan(db_session, project=project, status="succeeded")
    for _ in range(2):
        await _unlicensed_component(db_session, scan_id=scan.id)

    actor = principal_for(user, team_ids=[project.team_id])
    band = await get_project_governance(db_session, project_id=project.id, actor=actor)
    overview = await get_project_overview(db_session, project_id=project.id, actor=actor)

    assert overview["severity_distribution"]["critical"] == 0
    assert overview["license_distribution"]["unknown"] == 2
    assert band.risk_score == overview["risk_score"]
    # Pinned, so a change that made both sides zero would not pass as agreement.
    assert band.risk_score > 0


async def test_every_number_anchors_on_the_newest_succeeded_scan(
    db_session: AsyncSession,
) -> None:
    """One scan cannot prove an anchor — there is nothing else to pick.

    Two succeeded scans with different contents, and the older one deliberately
    worse. Score, gate and KEV must all describe the newer, and the trend must
    carry both in order.
    """
    _, user, project = await _team_project(db_session)
    now = datetime.now(tz=UTC)
    today = now.date()

    older = await make_scan(
        db_session, project=project, status="succeeded", created_at=now - timedelta(days=5)
    )
    for _ in range(3):
        await _critical_finding(db_session, scan_id=older.id)
    await _critical_finding(
        db_session, scan_id=older.id, kev_due=today - timedelta(days=10)
    )

    newer = await make_scan(
        db_session, project=project, status="succeeded", created_at=now - timedelta(days=1)
    )
    await _critical_finding(db_session, scan_id=newer.id)

    band = await get_project_governance(
        db_session,
        project_id=project.id,
        actor=principal_for(user, team_ids=[project.team_id]),
    )

    assert band.gate.scan_id == newer.id
    assert band.gate.critical_cve_count == 1, "the gate read the older scan"
    # The older scan's overdue KEV must not be counted: it is not the current
    # posture, and counting every historical snapshot would inflate the tile.
    assert band.kev_sla.overdue == 0
    # The trend carries both, oldest first, with each scan's own count.
    assert [point.scan_id for point in band.trend] == [older.id, newer.id]
    assert [point.critical for point in band.trend] == [4, 1]


async def test_another_projects_approvals_are_not_counted(
    db_session: AsyncSession,
) -> None:
    """The tile's number is this project's, and nothing guards that but this.

    Widening the scope to the team or the organisation would be invisible on a
    single-project fixture and would put another project's queue on this
    project's band.
    """
    from models import Component, ComponentApproval

    team, user, project = await _team_project(db_session)
    neighbour = await make_project(db_session, team=team)

    suffix = unique_suffix()
    purl = f"pkg:npm/appr-{suffix}"
    component = Component(purl=purl, package_type="npm", name=f"appr-{suffix}")
    db_session.add(component)
    await db_session.commit()
    await db_session.refresh(component)

    db_session.add(
        ComponentApproval(
            project_id=project.id,
            component_id=component.id,
            team_id=team.id,
            status="pending",
        )
    )
    db_session.add(
        ComponentApproval(
            project_id=neighbour.id,
            component_id=component.id,
            team_id=team.id,
            status="pending",
        )
    )
    await db_session.commit()

    band = await get_project_governance(
        db_session,
        project_id=project.id,
        actor=principal_for(user, team_ids=[project.team_id]),
    )

    assert band.pending_approvals == 1


async def test_the_route_returns_the_band_over_http(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """A 200 body, not only the three refusals.

    Without this, the route could be wired to the wrong service, or the schema
    could stop serialising a field, and every access test would still pass.
    """
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)
    scan = await make_scan(db_session, project=project, status="succeeded")
    await _critical_finding(db_session, scan_id=scan.id)

    member = await _user_with_password(db_session)
    await make_membership(db_session, user=member, team=team, role="developer")
    token = await _login(client, member.email)

    response = await client.get(
        f"/v1/projects/{project.id}/governance",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["project_id"] == str(project.id)
    assert body["scanned"] is True
    assert body["gate"]["status"] == "fail"
    assert body["gate"]["critical_cve_count"] == 1
    assert body["risk_score"] > 0
    assert set(body) == {
        "project_id",
        "scanned",
        "risk_score",
        "gate",
        "kev_sla",
        "pending_approvals",
        "trend",
    }
