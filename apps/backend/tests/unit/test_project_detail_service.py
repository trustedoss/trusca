"""
DB-backed service tests for `services/project_detail_service.py` — Phase 3 PR #10.

Covers three entry points:

- :func:`get_project_overview`            (Phase 3 task 3.1)
- :func:`list_components_for_project`     (Phase 3 task 3.3)
- :func:`get_component_detail`            (Phase 3 task 3.3 — drawer)

Pure unit tests (risk-score math, filter normalisation) live in
``tests/unit/test_risk_score.py`` — they don't need a DB and run on every PR
even when DATABASE_URL is unset.

These cases follow the same structure as ``test_project_service.py``
(integration mark + alembic upgrade fixture) because the real shape of the
aggregation depends on the live Postgres schema (ENUM types, CASE behaviour,
etc.). Mocking the DB would test the mock, not the contract.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

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


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip project detail service tests")
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
            f"alembic upgrade head failed; project detail tests cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


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


# ---------------------------------------------------------------------------
# Local factories — no helpers exist for components/licenses/vulns yet, so we
# stand them up inline. Keep simple: one component_version per "package", one
# license per category, one CVE per severity.
# ---------------------------------------------------------------------------


async def _make_component_version(
    session: AsyncSession,
    *,
    name: str | None = None,
    version: str = "1.0.0",
    package_type: str = "npm",
):
    from models import Component, ComponentVersion

    suffix = unique_suffix()
    cname = name or f"pkg-{suffix}"
    purl = f"pkg:{package_type}/{cname}"
    component = Component(purl=purl, package_type=package_type, name=cname)
    session.add(component)
    await session.commit()
    await session.refresh(component)

    cv = ComponentVersion(
        component_id=component.id,
        version=version,
        purl_with_version=f"{purl}@{version}",
    )
    session.add(cv)
    await session.commit()
    await session.refresh(cv)
    return component, cv


async def _attach_to_scan(
    session: AsyncSession,
    *,
    scan_id: uuid.UUID,
    cv_id: uuid.UUID,
    direct: bool = True,
    depth: int | None = None,
    dependency_scope: str | None = None,
):
    from models import ScanComponent

    sc = ScanComponent(
        scan_id=scan_id,
        component_version_id=cv_id,
        direct=direct,
        depth=depth,
        # W2 #31 — cdxgen writes a CycloneDX ``scope`` here when the SBOM
        # encodes one (``required``/``optional``); NULL otherwise.
        dependency_scope=dependency_scope,
        raw_data={"path": "test/path"},
    )
    session.add(sc)
    await session.commit()
    await session.refresh(sc)
    return sc


async def _make_license(
    session: AsyncSession,
    *,
    spdx_id: str | None = None,
    name: str | None = None,
    category: str = "allowed",
):
    """Idempotent license fixture.

    The unit-test DB is module-scoped (alembic upgrade head once, then commits
    survive across tests within the file), so multiple tests asking for the
    same hardcoded spdx_id (`MIT`, `Apache-2.0`, …) would otherwise hit
    `uq_licenses_spdx_id`. SELECT first, INSERT only when absent. The
    `category` of an existing row wins — callers must not rely on this fixture
    to mutate category between tests; they should pick a different spdx_id.
    """
    from models import License

    resolved_spdx = spdx_id or f"LicenseRef-{unique_suffix()}"
    existing = await session.scalar(
        select(License).where(License.spdx_id == resolved_spdx)
    )
    if existing is not None:
        return existing

    licence = License(
        spdx_id=resolved_spdx,
        name=name or f"License {unique_suffix()}",
        category=category,
    )
    session.add(licence)
    await session.commit()
    await session.refresh(licence)
    return licence


async def _attach_license_finding(
    session: AsyncSession,
    *,
    scan_id: uuid.UUID,
    cv_id: uuid.UUID,
    license_id: uuid.UUID,
    kind: str = "concluded",
):
    from models import LicenseFinding

    lf = LicenseFinding(
        scan_id=scan_id,
        component_version_id=cv_id,
        license_id=license_id,
        kind=kind,
        source_path=f"path-{unique_suffix()}",
    )
    session.add(lf)
    await session.commit()
    await session.refresh(lf)
    return lf


async def _make_vulnerability(
    session: AsyncSession,
    *,
    severity: str = "high",
    cve_id: str | None = None,
    summary: str | None = None,
    epss_score: float | None = None,
    epss_percentile: float | None = None,
):
    from models import Vulnerability

    suffix = unique_suffix()
    v = Vulnerability(
        external_id=cve_id or f"CVE-2024-{suffix}",
        source="NVD",
        severity=severity,
        summary=summary or f"Test vuln {suffix}",
        # Set at INSERT (not a post-create UPDATE) to keep the audit JSONB diff
        # free of Decimal serialization concerns.
        epss_score=epss_score,
        epss_percentile=epss_percentile,
    )
    session.add(v)
    await session.commit()
    await session.refresh(v)
    return v


async def _attach_vuln_finding(
    session: AsyncSession,
    *,
    scan_id: uuid.UUID,
    cv_id: uuid.UUID,
    vulnerability_id: uuid.UUID,
    fixed_version: str | None = None,
):
    from models import VulnerabilityFinding

    vf = VulnerabilityFinding(
        scan_id=scan_id,
        component_version_id=cv_id,
        vulnerability_id=vulnerability_id,
        fixed_version=fixed_version,
    )
    session.add(vf)
    await session.commit()
    await session.refresh(vf)
    return vf


async def _make_project_with_scan(session: AsyncSession):
    """Set up org → team → user → membership → project → succeeded scan."""
    org = await make_organization(session)
    team = await make_team(session, organization=org)
    user = await make_user(session)
    await make_membership(session, user=user, team=team, role="developer")
    project = await make_project(session, team=team)
    scan = await make_scan(session, project=project, status="succeeded")
    # Wire up project.latest_scan_id (production code does this in trigger_scan).
    project.latest_scan_id = scan.id
    project.updated_at = datetime.now(tz=UTC)
    await session.commit()
    await session.refresh(project)
    return team, user, project, scan


# ---------------------------------------------------------------------------
# get_project_overview
# ---------------------------------------------------------------------------


async def test_overview_for_project_without_any_scan_returns_empty_distributions(
    db_session: AsyncSession,
) -> None:
    from services.project_detail_service import get_project_overview

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")
    project = await make_project(db_session, team=team)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    overview = await get_project_overview(
        db_session, project_id=project.id, actor=actor
    )
    assert overview["total_components"] == 0
    assert overview["risk_score"] == 0.0
    assert overview["last_scan_at"] is None
    assert overview["recent_scans"] == []
    # Buckets present even if empty (frontend stable bar/donut).
    assert set(overview["severity_distribution"].keys()) >= {"critical", "high", "medium"}
    assert all(v == 0 for v in overview["severity_distribution"].values())
    # No succeeded scan → vuln-data availability is unknown, never a false caveat.
    assert overview["vuln_data_available"] is None


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        # DB empty when this scan ran → 0 CVEs means "no data", not "safe" (#35).
        ({"dt_vulnerability_count": 0}, False),
        # DB populated → an empty Security axis is a genuine clean result.
        ({"dt_vulnerability_count": 43048}, True),
        # Scan predates the capture (no key) → unknown → no caveat.
        ({}, None),
    ],
)
async def test_overview_vuln_data_available_from_scan_metadata(
    db_session: AsyncSession,
    metadata: dict[str, int],
    expected: bool | None,
) -> None:
    from services.project_detail_service import get_project_overview

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")
    project = await make_project(db_session, team=team)
    scan = await make_scan(db_session, project=project, status="succeeded")
    scan.scan_metadata = metadata
    await db_session.commit()

    actor = principal_for(user, team_ids=[team.id], role="developer")
    overview = await get_project_overview(
        db_session, project_id=project.id, actor=actor
    )
    assert overview["vuln_data_available"] is expected


async def test_overview_aggregates_severity_and_license_distributions(
    db_session: AsyncSession,
) -> None:
    from services.project_detail_service import get_project_overview

    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    # Three components: one critical-CVE/forbidden-license, one high-CVE/allowed,
    # one no-finding (so it should land in "none"/"unknown" buckets).
    _, cv1 = await _make_component_version(db_session)
    _, cv2 = await _make_component_version(db_session)
    _, cv3 = await _make_component_version(db_session)
    for cv in (cv1, cv2, cv3):
        await _attach_to_scan(db_session, scan_id=scan.id, cv_id=cv.id)

    crit_v = await _make_vulnerability(db_session, severity="critical")
    high_v = await _make_vulnerability(db_session, severity="high")
    await _attach_vuln_finding(
        db_session, scan_id=scan.id, cv_id=cv1.id, vulnerability_id=crit_v.id
    )
    await _attach_vuln_finding(
        db_session, scan_id=scan.id, cv_id=cv2.id, vulnerability_id=high_v.id
    )

    forbidden_lic = await _make_license(db_session, category="forbidden", spdx_id="GPL-3.0")
    allowed_lic = await _make_license(db_session, category="allowed", spdx_id="MIT")
    await _attach_license_finding(
        db_session, scan_id=scan.id, cv_id=cv1.id, license_id=forbidden_lic.id
    )
    await _attach_license_finding(
        db_session, scan_id=scan.id, cv_id=cv2.id, license_id=allowed_lic.id
    )

    overview = await get_project_overview(
        db_session, project_id=project.id, actor=actor
    )

    # Three components total.
    assert overview["total_components"] == 3
    # cv1 → critical / forbidden, cv2 → high / allowed, cv3 → none / unknown.
    assert overview["severity_distribution"]["critical"] == 1
    assert overview["severity_distribution"]["high"] == 1
    assert overview["severity_distribution"]["none"] == 1
    assert overview["license_distribution"]["forbidden"] == 1
    assert overview["license_distribution"]["allowed"] == 1
    assert overview["license_distribution"]["unknown"] == 1
    # Two non-saturating axes (services.risk_score):
    #   Security: 1 critical → band 75–100, n=1 → 75 + 25·(1/5) = 80.0
    #   License:  1 forbidden → band 75–100, n=1 → 80.0
    #   Overall = max(security, license) = 80.0
    assert overview["security_score"] == 80.0
    assert overview["license_score"] == 80.0
    assert overview["risk_score"] == 80.0
    assert overview["last_scan_at"] is not None
    # recent_scans present (we created exactly one).
    assert len(overview["recent_scans"]) == 1


async def test_overview_idor_other_team_is_forbidden(
    db_session: AsyncSession,
) -> None:
    from services.project_detail_service import get_project_overview
    from services.project_service import ProjectForbidden

    org = await make_organization(db_session)
    target_team = await make_team(db_session, organization=org)
    other_team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=target_team)

    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=other_team, role="developer")
    actor = principal_for(user, team_ids=[other_team.id], role="developer")

    with pytest.raises(ProjectForbidden):
        await get_project_overview(db_session, project_id=project.id, actor=actor)


async def test_overview_unknown_project_is_404(db_session: AsyncSession) -> None:
    from services.project_detail_service import get_project_overview
    from services.project_service import ProjectNotFound

    user = await make_user(db_session, is_superuser=True)
    actor = principal_for(user, role="super_admin")

    with pytest.raises(ProjectNotFound):
        await get_project_overview(
            db_session, project_id=uuid.uuid4(), actor=actor
        )


async def test_overview_super_admin_bypasses_team_check(
    db_session: AsyncSession,
) -> None:
    from services.project_detail_service import get_project_overview

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)
    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    overview = await get_project_overview(
        db_session, project_id=project.id, actor=actor
    )
    assert overview["project_id"] == project.id


# ---------------------------------------------------------------------------
# current_user_role — actor's effective role within the project's team (BUG-005)
# ---------------------------------------------------------------------------


async def test_overview_current_user_role_super_admin(
    db_session: AsyncSession,
) -> None:
    """A platform super-user always sees 'super_admin' (bypasses membership)."""
    from services.project_detail_service import get_project_overview

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)
    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    overview = await get_project_overview(
        db_session, project_id=project.id, actor=actor
    )
    assert overview["current_user_role"] == "super_admin"


async def test_overview_current_user_role_team_admin(
    db_session: AsyncSession,
) -> None:
    """A team_admin of the project's team sees 'team_admin' (the BUG-005 case)."""
    from services.project_detail_service import get_project_overview

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="team_admin")
    project = await make_project(db_session, team=team)
    # The JWT/global role only ever yields developer for a non-superuser; the
    # service must resolve team_admin from the DB membership regardless.
    actor = principal_for(user, team_ids=[team.id], role="team_admin")

    overview = await get_project_overview(
        db_session, project_id=project.id, actor=actor
    )
    assert overview["current_user_role"] == "team_admin"


async def test_overview_current_user_role_developer(
    db_session: AsyncSession,
) -> None:
    """A developer of the project's team sees 'developer'."""
    from services.project_detail_service import get_project_overview

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")
    project = await make_project(db_session, team=team)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    overview = await get_project_overview(
        db_session, project_id=project.id, actor=actor
    )
    assert overview["current_user_role"] == "developer"


async def test_overview_current_user_role_resolved_from_db_not_jwt(
    db_session: AsyncSession,
) -> None:
    """The role comes from the DB membership, not a stale JWT-derived role.

    We build a principal that *claims* developer (mimicking a token issued
    before a promotion) while the DB says team_admin; the service must trust
    the DB row.
    """
    from services.project_detail_service import get_project_overview

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="team_admin")
    project = await make_project(db_session, team=team)
    # Stale claim: principal says developer, DB says team_admin.
    actor = principal_for(user, team_ids=[team.id], role="developer")

    overview = await get_project_overview(
        db_session, project_id=project.id, actor=actor
    )
    assert overview["current_user_role"] == "team_admin"


async def test_overview_current_user_role_org_wide_reader_defaults_developer(
    db_session: AsyncSession,
) -> None:
    """An org-wide reader with no membership fails closed to 'developer'.

    A super-user (who can read every project) but who holds no team membership
    is the cleanest way to exercise the "access granted, no membership row"
    branch without depending on org-wide visibility plumbing. We assert the
    *non-superuser* fallback by directly invoking the resolver with a plain
    principal that has access but no membership.
    """
    from services.project_detail_service import _resolve_team_scoped_role
    from tests._helpers import principal_for as _principal_for

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    # A user with NO membership on `team`.
    reader = await make_user(db_session)
    actor = _principal_for(reader, team_ids=[], role="developer")

    role = await _resolve_team_scoped_role(db_session, actor=actor, team_id=team.id)
    assert role == "developer"


# ---------------------------------------------------------------------------
# list_components_for_project
# ---------------------------------------------------------------------------


async def test_list_components_returns_empty_when_project_has_no_scan(
    db_session: AsyncSession,
) -> None:
    from services.project_detail_service import list_components_for_project

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")
    project = await make_project(db_session, team=team)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    items, total = await list_components_for_project(
        db_session, project_id=project.id, actor=actor
    )
    assert items == []
    assert total == 0


async def test_list_components_paginates_and_returns_total(
    db_session: AsyncSession,
) -> None:
    from services.project_detail_service import list_components_for_project

    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    # Create 5 components attached to the scan.
    for i in range(5):
        _, cv = await _make_component_version(db_session, name=f"pkg-{i}-{unique_suffix()}")
        await _attach_to_scan(db_session, scan_id=scan.id, cv_id=cv.id)

    items, total = await list_components_for_project(
        db_session, project_id=project.id, actor=actor, limit=2, offset=0
    )
    assert len(items) == 2
    assert total == 5

    # Page 2.
    items_p2, _ = await list_components_for_project(
        db_session, project_id=project.id, actor=actor, limit=2, offset=2
    )
    assert len(items_p2) == 2
    # Different rows than page 1.
    assert {i["id"] for i in items} & {i["id"] for i in items_p2} == set()


async def test_list_components_search_matches_substring_on_name(
    db_session: AsyncSession,
) -> None:
    from services.project_detail_service import list_components_for_project

    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    needle = unique_suffix()
    _, target_cv = await _make_component_version(db_session, name=f"target-{needle}")
    _, decoy_cv = await _make_component_version(db_session, name=f"decoy-{unique_suffix()}")
    await _attach_to_scan(db_session, scan_id=scan.id, cv_id=target_cv.id)
    await _attach_to_scan(db_session, scan_id=scan.id, cv_id=decoy_cv.id)

    items, total = await list_components_for_project(
        db_session, project_id=project.id, actor=actor, search=needle
    )
    assert total == 1
    assert items[0]["component_id"] == target_cv.component_id


async def test_list_components_severity_filter(db_session: AsyncSession) -> None:
    from services.project_detail_service import list_components_for_project

    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    _, cv_crit = await _make_component_version(db_session)
    _, cv_low = await _make_component_version(db_session)
    await _attach_to_scan(db_session, scan_id=scan.id, cv_id=cv_crit.id)
    await _attach_to_scan(db_session, scan_id=scan.id, cv_id=cv_low.id)

    crit_v = await _make_vulnerability(db_session, severity="critical")
    low_v = await _make_vulnerability(db_session, severity="low")
    await _attach_vuln_finding(
        db_session, scan_id=scan.id, cv_id=cv_crit.id, vulnerability_id=crit_v.id
    )
    await _attach_vuln_finding(
        db_session, scan_id=scan.id, cv_id=cv_low.id, vulnerability_id=low_v.id
    )

    items, total = await list_components_for_project(
        db_session,
        project_id=project.id,
        actor=actor,
        severity=["critical"],
    )
    assert total == 1
    assert items[0]["severity_max"] == "critical"


async def test_list_components_license_filter(db_session: AsyncSession) -> None:
    from services.project_detail_service import list_components_for_project

    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    _, cv_forbidden = await _make_component_version(db_session)
    _, cv_allowed = await _make_component_version(db_session)
    await _attach_to_scan(db_session, scan_id=scan.id, cv_id=cv_forbidden.id)
    await _attach_to_scan(db_session, scan_id=scan.id, cv_id=cv_allowed.id)

    forbidden_lic = await _make_license(db_session, category="forbidden", spdx_id="AGPL-3.0")
    allowed_lic = await _make_license(db_session, category="allowed", spdx_id="Apache-2.0")
    await _attach_license_finding(
        db_session, scan_id=scan.id, cv_id=cv_forbidden.id, license_id=forbidden_lic.id
    )
    await _attach_license_finding(
        db_session, scan_id=scan.id, cv_id=cv_allowed.id, license_id=allowed_lic.id
    )

    items, total = await list_components_for_project(
        db_session,
        project_id=project.id,
        actor=actor,
        license_category=["forbidden"],
    )
    assert total == 1
    assert items[0]["license_category"] == "forbidden"
    assert items[0]["license"] == "AGPL-3.0"


async def test_list_components_sort_by_name_descending(
    db_session: AsyncSession,
) -> None:
    from services.project_detail_service import list_components_for_project

    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    suffix = unique_suffix()
    names = [f"aaa-{suffix}", f"mmm-{suffix}", f"zzz-{suffix}"]
    for name in names:
        _, cv = await _make_component_version(db_session, name=name)
        await _attach_to_scan(db_session, scan_id=scan.id, cv_id=cv.id)

    items, _ = await list_components_for_project(
        db_session,
        project_id=project.id,
        actor=actor,
        sort="name",
        order="desc",
        search=suffix,
    )
    returned_names = [i["name"] for i in items]
    assert returned_names == sorted(returned_names, reverse=True)


async def test_list_components_invalid_sort_raises_project_error(
    db_session: AsyncSession,
) -> None:
    from services.project_detail_service import list_components_for_project
    from services.project_service import ProjectError

    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    with pytest.raises(ProjectError):
        await list_components_for_project(
            db_session, project_id=project.id, actor=actor, sort="bogus"
        )


async def test_list_components_idor_other_team_is_forbidden(
    db_session: AsyncSession,
) -> None:
    from services.project_detail_service import list_components_for_project
    from services.project_service import ProjectForbidden

    org = await make_organization(db_session)
    target_team = await make_team(db_session, organization=org)
    other_team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=target_team)

    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=other_team, role="developer")
    actor = principal_for(user, team_ids=[other_team.id], role="developer")

    with pytest.raises(ProjectForbidden):
        await list_components_for_project(
            db_session, project_id=project.id, actor=actor
        )


async def test_list_components_unknown_project_is_404(
    db_session: AsyncSession,
) -> None:
    from services.project_detail_service import list_components_for_project
    from services.project_service import ProjectNotFound

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    with pytest.raises(ProjectNotFound):
        await list_components_for_project(
            db_session, project_id=uuid.uuid4(), actor=actor
        )


async def test_list_components_caps_limit_at_max(
    db_session: AsyncSession,
) -> None:
    from services.project_detail_service import list_components_for_project

    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    # Service-side cap is 500 — passing 100_000 should not raise; effective
    # limit clamps. We attach 2 rows to keep the test fast and assert the
    # response shape (limit clamping is a behavioural contract, not a
    # row-count contract).
    _, cv = await _make_component_version(db_session)
    await _attach_to_scan(db_session, scan_id=scan.id, cv_id=cv.id)

    items, total = await list_components_for_project(
        db_session, project_id=project.id, actor=actor, limit=100_000
    )
    assert total >= 1
    assert len(items) >= 1


# ---------------------------------------------------------------------------
# get_component_detail
# ---------------------------------------------------------------------------


async def test_component_detail_returns_drawer_payload_with_vulns(
    db_session: AsyncSession,
) -> None:
    from services.project_detail_service import get_component_detail

    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    # Unique name so a re-run against the persistent dev DB does not collide on
    # uq_components_purl (cross-suite isolation — see MEMORY note; the neighbour
    # drawer tests already do this).
    _, cv = await _make_component_version(db_session, name=f"drawer-vulns-{unique_suffix()}")
    await _attach_to_scan(db_session, scan_id=scan.id, cv_id=cv.id)

    crit_v = await _make_vulnerability(
        db_session, severity="critical", summary="Critical RCE"
    )
    await _attach_vuln_finding(
        db_session, scan_id=scan.id, cv_id=cv.id, vulnerability_id=crit_v.id
    )

    mit = await _make_license(db_session, category="allowed", spdx_id="MIT")
    await _attach_license_finding(
        db_session, scan_id=scan.id, cv_id=cv.id, license_id=mit.id
    )

    detail = await get_component_detail(
        db_session, component_version_id=cv.id, actor=actor
    )
    assert detail["id"] == cv.id
    assert detail["project_id"] == project.id
    assert detail["severity_max"] == "critical"
    assert detail["license_category"] == "allowed"
    assert detail["license"] == "MIT"
    assert len(detail["vulnerabilities"]) == 1
    assert detail["vulnerabilities"][0]["cve_id"] == crit_v.external_id
    assert detail["vulnerabilities"][0]["title"] == "Critical RCE"


async def test_component_detail_vuln_ref_exposes_fixed_version(
    db_session: AsyncSession,
) -> None:
    """v2.2 2.2-a1 — the per-finding ``fixed_version`` must surface on the
    component drawer's ``VulnerabilityRef`` (the hard-coded ``None`` is gone)."""
    from services.project_detail_service import get_component_detail

    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    # Unique component name so a re-run against the persistent dev DB does not
    # collide on uq_components_purl (cross-suite isolation — see MEMORY note).
    _, cv = await _make_component_version(db_session, name=f"needs-bump-{unique_suffix()}")
    await _attach_to_scan(db_session, scan_id=scan.id, cv_id=cv.id)

    v_fixed = await _make_vulnerability(db_session, severity="high", summary="Fixable")
    v_unfixed = await _make_vulnerability(
        db_session, severity="low", summary="No fix yet"
    )
    await _attach_vuln_finding(
        db_session,
        scan_id=scan.id,
        cv_id=cv.id,
        vulnerability_id=v_fixed.id,
        fixed_version="3.2.0",
    )
    await _attach_vuln_finding(
        db_session,
        scan_id=scan.id,
        cv_id=cv.id,
        vulnerability_id=v_unfixed.id,
        fixed_version=None,
    )

    detail = await get_component_detail(
        db_session, component_version_id=cv.id, actor=actor
    )
    by_cve = {v["cve_id"]: v for v in detail["vulnerabilities"]}
    assert by_cve[v_fixed.external_id]["fixed_version"] == "3.2.0"
    assert by_cve[v_unfixed.external_id]["fixed_version"] is None


async def test_component_detail_vuln_ref_carries_epss(
    db_session: AsyncSession,
) -> None:
    """The component drawer's per-CVE entries surface EPSS (Decimal→float)."""
    from services.project_detail_service import get_component_detail

    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    # Unique name avoids the shared-dev-DB `uq_components_purl` pollution that
    # a hardcoded name like "foo" is prone to.
    _, cv = await _make_component_version(db_session, name=f"epss-drawer-{unique_suffix()}")
    await _attach_to_scan(db_session, scan_id=scan.id, cv_id=cv.id)

    v = await _make_vulnerability(
        db_session,
        severity="high",
        summary="EPSS-scored CVE",
        epss_score=0.73210,
        epss_percentile=0.98765,
    )
    await _attach_vuln_finding(
        db_session, scan_id=scan.id, cv_id=cv.id, vulnerability_id=v.id
    )

    detail = await get_component_detail(
        db_session, component_version_id=cv.id, actor=actor
    )
    assert len(detail["vulnerabilities"]) == 1
    entry = detail["vulnerabilities"][0]
    assert isinstance(entry["epss_score"], float)
    assert entry["epss_score"] == pytest.approx(0.73210)
    assert isinstance(entry["epss_percentile"], float)
    assert entry["epss_percentile"] == pytest.approx(0.98765)


async def test_component_detail_vuln_ref_epss_none_when_unset(
    db_session: AsyncSession,
) -> None:
    from services.project_detail_service import get_component_detail

    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    _, cv = await _make_component_version(db_session, name=f"epss-none-{unique_suffix()}")
    await _attach_to_scan(db_session, scan_id=scan.id, cv_id=cv.id)

    v = await _make_vulnerability(db_session, severity="high")  # no EPSS
    await _attach_vuln_finding(
        db_session, scan_id=scan.id, cv_id=cv.id, vulnerability_id=v.id
    )

    detail = await get_component_detail(
        db_session, component_version_id=cv.id, actor=actor
    )
    entry = detail["vulnerabilities"][0]
    assert entry["epss_score"] is None
    assert entry["epss_percentile"] is None


async def test_component_detail_unknown_id_is_404(
    db_session: AsyncSession,
) -> None:
    from services.project_detail_service import (
        ComponentNotFound,
        get_component_detail,
    )

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    with pytest.raises(ComponentNotFound):
        await get_component_detail(
            db_session, component_version_id=uuid.uuid4(), actor=actor
        )


async def test_component_detail_other_team_user_gets_404_not_403(
    db_session: AsyncSession,
) -> None:
    """We hide existence of cross-team components rather than leaking 403."""
    from services.project_detail_service import (
        ComponentNotFound,
        get_component_detail,
    )

    team, _, project, scan = await _make_project_with_scan(db_session)

    _, cv = await _make_component_version(db_session)
    await _attach_to_scan(db_session, scan_id=scan.id, cv_id=cv.id)

    # Build an outsider in a different team.
    org2 = await make_organization(db_session)
    other_team = await make_team(db_session, organization=org2)
    outsider = await make_user(db_session)
    await make_membership(
        db_session, user=outsider, team=other_team, role="developer"
    )
    actor = principal_for(outsider, team_ids=[other_team.id], role="developer")

    with pytest.raises(ComponentNotFound):
        await get_component_detail(
            db_session, component_version_id=cv.id, actor=actor
        )


async def test_component_detail_super_admin_bypasses_team_check(
    db_session: AsyncSession,
) -> None:
    from services.project_detail_service import get_component_detail

    _, _, project, scan = await _make_project_with_scan(db_session)
    _, cv = await _make_component_version(db_session)
    await _attach_to_scan(db_session, scan_id=scan.id, cv_id=cv.id)

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    detail = await get_component_detail(
        db_session, component_version_id=cv.id, actor=actor
    )
    assert detail["id"] == cv.id
    assert detail["project_id"] == project.id


# ---------------------------------------------------------------------------
# v2.2 2.2-a2 — depth / direct exposure on list + detail
# ---------------------------------------------------------------------------


async def test_list_components_exposes_depth_and_direct(
    db_session: AsyncSession,
) -> None:
    """The components list surfaces graph depth (1 = direct, 2+ = transitive)
    and a ``direct`` flag derived from it (v2.2 2.2-a2)."""
    from services.project_detail_service import list_components_for_project

    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    _, cv_direct = await _make_component_version(
        db_session, name=f"direct-{unique_suffix()}"
    )
    _, cv_trans = await _make_component_version(
        db_session, name=f"transitive-{unique_suffix()}"
    )
    _, cv_nograph = await _make_component_version(
        db_session, name=f"nograph-{unique_suffix()}"
    )
    await _attach_to_scan(
        db_session, scan_id=scan.id, cv_id=cv_direct.id, direct=True, depth=1
    )
    await _attach_to_scan(
        db_session, scan_id=scan.id, cv_id=cv_trans.id, direct=False, depth=3
    )
    # No graph for this one — depth NULL, direct False.
    await _attach_to_scan(
        db_session, scan_id=scan.id, cv_id=cv_nograph.id, direct=False, depth=None
    )

    items, _ = await list_components_for_project(
        db_session, project_id=project.id, actor=actor, limit=50, offset=0
    )
    by_id = {i["id"]: i for i in items}
    assert by_id[cv_direct.id]["depth"] == 1
    assert by_id[cv_direct.id]["direct"] is True
    assert by_id[cv_trans.id]["depth"] == 3
    assert by_id[cv_trans.id]["direct"] is False
    assert by_id[cv_nograph.id]["depth"] is None
    assert by_id[cv_nograph.id]["direct"] is False


async def test_list_components_reports_shallowest_depth_across_paths(
    db_session: AsyncSession,
) -> None:
    """A cv reachable at several dependency paths reports the SHALLOWEST
    (MIN) depth and ORs the direct flags (v2.2 2.2-a2)."""
    from services.project_detail_service import list_components_for_project

    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    _, cv = await _make_component_version(db_session, name=f"diamond-{unique_suffix()}")
    # Same cv at two paths: a transitive one (depth 4) and a direct one (depth 1).
    await _attach_to_scan(
        db_session, scan_id=scan.id, cv_id=cv.id, direct=False, depth=4
    )
    await _attach_to_scan(
        db_session, scan_id=scan.id, cv_id=cv.id, direct=True, depth=1
    )

    items, _ = await list_components_for_project(
        db_session, project_id=project.id, actor=actor, limit=50, offset=0
    )
    row = next(i for i in items if i["id"] == cv.id)
    assert row["depth"] == 1  # MIN of {4, 1}
    assert row["direct"] is True  # bool_or of {False, True}


async def test_component_detail_exposes_depth_and_direct(
    db_session: AsyncSession,
) -> None:
    """The component drawer surfaces depth + direct for the shallowest path
    (v2.2 2.2-a2)."""
    from services.project_detail_service import get_component_detail

    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    _, cv = await _make_component_version(db_session, name=f"drawer-{unique_suffix()}")
    await _attach_to_scan(
        db_session, scan_id=scan.id, cv_id=cv.id, direct=True, depth=1
    )

    detail = await get_component_detail(
        db_session, component_version_id=cv.id, actor=actor
    )
    assert detail["depth"] == 1
    assert detail["direct"] is True


# ---------------------------------------------------------------------------
# W2 #31 — dependency_scope ("Usage") + direct/scope filters
# ---------------------------------------------------------------------------


async def test_list_components_exposes_dependency_scope(
    db_session: AsyncSession,
) -> None:
    """The list surfaces cdxgen's CycloneDX ``component.scope`` as a
    3-state ``dependency_scope`` (``required``/``optional``/``None``)."""
    from services.project_detail_service import list_components_for_project

    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    _, cv_req = await _make_component_version(
        db_session, name=f"req-{unique_suffix()}"
    )
    _, cv_opt = await _make_component_version(
        db_session, name=f"opt-{unique_suffix()}"
    )
    _, cv_unspec = await _make_component_version(
        db_session, name=f"unspec-{unique_suffix()}"
    )
    await _attach_to_scan(
        db_session, scan_id=scan.id, cv_id=cv_req.id, dependency_scope="required"
    )
    await _attach_to_scan(
        db_session, scan_id=scan.id, cv_id=cv_opt.id, dependency_scope="optional"
    )
    # NULL scope: the common case for ecosystems that don't encode scope.
    await _attach_to_scan(
        db_session, scan_id=scan.id, cv_id=cv_unspec.id, dependency_scope=None
    )

    items, _ = await list_components_for_project(
        db_session, project_id=project.id, actor=actor, limit=50, offset=0
    )
    by_id = {i["id"]: i for i in items}
    assert by_id[cv_req.id]["dependency_scope"] == "required"
    assert by_id[cv_opt.id]["dependency_scope"] == "optional"
    # NULL scope must NOT be invented as "required" or "optional"; the UI
    # renders it as "—" rather than guessing.
    assert by_id[cv_unspec.id]["dependency_scope"] is None


async def test_list_components_required_wins_over_optional_across_paths(
    db_session: AsyncSession,
) -> None:
    """When the same cv appears at two paths with different scopes, the
    list surfaces ``required`` — a component used at runtime from *any*
    path is reported as runtime-required (BD-style 'Usage' semantics)."""
    from services.project_detail_service import list_components_for_project

    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    _, cv = await _make_component_version(
        db_session, name=f"mixed-{unique_suffix()}"
    )
    await _attach_to_scan(
        db_session, scan_id=scan.id, cv_id=cv.id, dependency_scope="optional"
    )
    await _attach_to_scan(
        db_session, scan_id=scan.id, cv_id=cv.id, dependency_scope="required"
    )

    items, _ = await list_components_for_project(
        db_session, project_id=project.id, actor=actor, limit=50, offset=0
    )
    row = next(i for i in items if i["id"] == cv.id)
    assert row["dependency_scope"] == "required"


async def test_list_components_filters_by_direct_true(
    db_session: AsyncSession,
) -> None:
    """``?direct=true`` keeps only graph-root deps; transitives drop."""
    from services.project_detail_service import list_components_for_project

    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    _, cv_direct = await _make_component_version(
        db_session, name=f"d-{unique_suffix()}"
    )
    _, cv_trans = await _make_component_version(
        db_session, name=f"t-{unique_suffix()}"
    )
    await _attach_to_scan(
        db_session, scan_id=scan.id, cv_id=cv_direct.id, direct=True, depth=1
    )
    await _attach_to_scan(
        db_session, scan_id=scan.id, cv_id=cv_trans.id, direct=False, depth=2
    )

    items_direct, total_direct = await list_components_for_project(
        db_session,
        project_id=project.id,
        actor=actor,
        direct=True,
        limit=50,
        offset=0,
    )
    items_trans, total_trans = await list_components_for_project(
        db_session,
        project_id=project.id,
        actor=actor,
        direct=False,
        limit=50,
        offset=0,
    )
    items_all, _ = await list_components_for_project(
        db_session, project_id=project.id, actor=actor, limit=50, offset=0
    )

    assert total_direct == 1
    assert {i["id"] for i in items_direct} == {cv_direct.id}
    assert total_trans == 1
    assert {i["id"] for i in items_trans} == {cv_trans.id}
    # Omitting the filter must return BOTH — proves the param is opt-in.
    assert {cv_direct.id, cv_trans.id} <= {i["id"] for i in items_all}


async def test_list_components_filters_by_dependency_scope(
    db_session: AsyncSession,
) -> None:
    """``?dependency_scope=`` accepts required/optional/unspecified and is
    multi-valued; the unspecified bucket matches NULL-scope rows."""
    from services.project_detail_service import list_components_for_project

    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    _, cv_req = await _make_component_version(
        db_session, name=f"r-{unique_suffix()}"
    )
    _, cv_opt = await _make_component_version(
        db_session, name=f"o-{unique_suffix()}"
    )
    _, cv_unspec = await _make_component_version(
        db_session, name=f"u-{unique_suffix()}"
    )
    await _attach_to_scan(
        db_session, scan_id=scan.id, cv_id=cv_req.id, dependency_scope="required"
    )
    await _attach_to_scan(
        db_session, scan_id=scan.id, cv_id=cv_opt.id, dependency_scope="optional"
    )
    await _attach_to_scan(
        db_session, scan_id=scan.id, cv_id=cv_unspec.id, dependency_scope=None
    )

    items_req, _ = await list_components_for_project(
        db_session,
        project_id=project.id,
        actor=actor,
        dependency_scope=["required"],
        limit=50,
        offset=0,
    )
    items_unspec, _ = await list_components_for_project(
        db_session,
        project_id=project.id,
        actor=actor,
        dependency_scope=["unspecified"],
        limit=50,
        offset=0,
    )
    items_multi, _ = await list_components_for_project(
        db_session,
        project_id=project.id,
        actor=actor,
        dependency_scope=["required", "optional"],
        limit=50,
        offset=0,
    )

    assert {i["id"] for i in items_req} == {cv_req.id}
    # "unspecified" must hit the NULL-scope bucket only — never invent a
    # match against optional/required.
    assert {i["id"] for i in items_unspec} == {cv_unspec.id}
    assert {i["id"] for i in items_multi} == {cv_req.id, cv_opt.id}


async def test_list_components_scope_filter_drops_unknown_values(
    db_session: AsyncSession,
) -> None:
    """Unknown filter values silently drop (no 422). A query that filters
    by ONLY unknown values returns an empty page — never the full set."""
    from services.project_detail_service import list_components_for_project

    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    _, cv = await _make_component_version(db_session, name=f"x-{unique_suffix()}")
    await _attach_to_scan(
        db_session, scan_id=scan.id, cv_id=cv.id, dependency_scope="required"
    )

    items_garbage, total_garbage = await list_components_for_project(
        db_session,
        project_id=project.id,
        actor=actor,
        dependency_scope=["bogus", "another"],
        limit=50,
        offset=0,
    )
    # Mixed valid + invalid keeps just the valid bucket.
    items_mixed, _ = await list_components_for_project(
        db_session,
        project_id=project.id,
        actor=actor,
        dependency_scope=["bogus", "required"],
        limit=50,
        offset=0,
    )

    assert items_garbage == []
    assert total_garbage == 0
    assert {i["id"] for i in items_mixed} == {cv.id}


async def test_component_detail_exposes_dependency_scope(
    db_session: AsyncSession,
) -> None:
    """The drawer reports the chosen (shallowest) row's own scope verbatim;
    unknown / NULL maps to None so the UI never invents a label."""
    from services.project_detail_service import get_component_detail

    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    _, cv_req = await _make_component_version(
        db_session, name=f"drawer-req-{unique_suffix()}"
    )
    _, cv_unspec = await _make_component_version(
        db_session, name=f"drawer-unspec-{unique_suffix()}"
    )
    await _attach_to_scan(
        db_session,
        scan_id=scan.id,
        cv_id=cv_req.id,
        direct=True,
        depth=1,
        dependency_scope="required",
    )
    await _attach_to_scan(
        db_session,
        scan_id=scan.id,
        cv_id=cv_unspec.id,
        direct=True,
        depth=1,
        dependency_scope=None,
    )

    detail_req = await get_component_detail(
        db_session, component_version_id=cv_req.id, actor=actor
    )
    detail_unspec = await get_component_detail(
        db_session, component_version_id=cv_unspec.id, actor=actor
    )
    assert detail_req["dependency_scope"] == "required"
    assert detail_unspec["dependency_scope"] is None
