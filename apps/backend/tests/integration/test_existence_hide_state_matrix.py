"""
Existence-hide × resource-state matrix — testing-standards rule: a security
assertion must be parametrized over the permission × state combination, and
the permission denial (404 existence-hide / 403) must ALWAYS fire before any
state-derived 409.

Why this file exists (validation campaign, 2026-06): recheck §4-1 found that
a non-member cancelling another team's FINISHED scan got the terminal 409
before the team gate's 404 — confirming the scan exists cross-team. We had a
"other team → 404" test and a "terminal → 409" test, but never their cross
product; the defect lived exactly at the intersection. This file pins the
ordering for every service surface where a cross-team caller could otherwise
reach a state-derived 409:

  - scan delete   × active scan        → ScanNotFound  (not ScanDeleteConflict)
  - scan trigger  × scan-in-progress   → ScanForbidden (not ScanInProgressConflict)
  - sbom-ingest   × scan-in-progress   → ScanForbidden (not ScanInProgressConflict)
  - sbom-ingest   × archived project   → ScanForbidden (not ScanArchivedConflict)
  - vuln status   × stale if_match     → VulnerabilityNotFound (not VulnerabilityConflict)
  - vuln status   × approval required   → VulnerabilityNotFound (not
                                          VulnerabilityApprovalRequired)
  - transition request × already open   → ApprovalNotFound (not ApprovalAlreadyOpen)
  - transition decide  × decided        → ApprovalNotFound (not ApprovalAlreadyDecided)
  - org verdict list   × outsider       → VerdictNotFound (not a readable list)
  - effective verdict  × outsider       → VerdictNotFound (not the project's answer)
  - approval      × terminal state     → ApprovalNotFound (not ApprovalTerminalState /
                                          ApprovalInvalidTransition)

The two sbom-ingest rows pin the NEW 409 surfaces this feature introduces
(``POST /v1/projects/{id}/sbom-ingest``): the endpoint reuses
``prepare_scan_target`` + the partial-unique-index flush, so an active scan
(409 ScanInProgressConflict) and an archived project (409 ScanArchivedConflict)
are both state-derived 409s that MUST sit behind the ScanForbidden (403)
permission gate for a non-member — exactly the intersection the campaign found
unguarded for cancel.

scan cancel × terminal is covered where it was fixed:
``tests/unit/services/test_user_cancel_scan_service.py::
test_other_team_terminal_scan_is_404_not_409`` (#370).

Whether the permission denial renders as 404 (existence-hide) or 403 follows
each domain's existing contract — the property under test is "permission
beats state", not the specific 4xx.

Runs against real Postgres (the ordering lives in service code that loads
rows and locks them).
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.security import CurrentUser
from models import Team, VulnerabilityFinding
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_scan,
    make_team,
    make_user,
    principal_loaded_from_db,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip existence-hide matrix tests")
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
            f"alembic upgrade head failed; existence-hide matrix cannot run\n"
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


async def _outsider_and_resource_team(db_session: AsyncSession):
    """Seed two teams; return (outsider developer principal, owning team)."""
    org = await make_organization(db_session)
    owning_team = await make_team(db_session, organization=org)
    other_team = await make_team(db_session, organization=org)

    outsider = await make_user(db_session)
    await make_membership(db_session, user=outsider, team=other_team, role="developer")
    actor = await principal_loaded_from_db(db_session, user=outsider)
    return actor, owning_team


# ---------------------------------------------------------------------------
# scan delete × active scan
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("active_status", ["queued", "running"])
async def test_delete_other_team_active_scan_is_404_not_409(
    db_session: AsyncSession, active_status: str
) -> None:
    """An ACTIVE scan would 409 (scan_active) for a member — an outsider must
    get the same 404 as for a missing scan, never that 409."""
    from services.scan_service import ScanNotFound, delete_scan

    actor, owning_team = await _outsider_and_resource_team(db_session)
    project = await make_project(db_session, team=owning_team)
    scan = await make_scan(db_session, project=project, status=active_status)

    with pytest.raises(ScanNotFound):
        await delete_scan(db_session, scan_id=scan.id, actor=actor)


# ---------------------------------------------------------------------------
# scan trigger × scan already in progress
# ---------------------------------------------------------------------------


async def test_trigger_on_other_team_busy_project_is_permission_denial_not_409(
    db_session: AsyncSession,
) -> None:
    """A project with an active scan would 409 (ScanInProgressConflict) for a
    member triggering a duplicate — an outsider must hit the permission gate
    (403 per this domain's contract) before any in-progress probe."""
    from schemas.scan import ScanCreate
    from services.scan_service import ScanForbidden, trigger_scan

    actor, owning_team = await _outsider_and_resource_team(db_session)
    project = await make_project(db_session, team=owning_team)
    await make_scan(db_session, project=project, status="running")

    with pytest.raises(ScanForbidden):
        await trigger_scan(
            db_session,
            project_id=project.id,
            payload=ScanCreate(kind="source"),
            actor=actor,
        )


@pytest.mark.parametrize("active_status", ["queued", "running"])
async def test_outsider_never_learns_the_active_scan_id(
    db_session: AsyncSession, active_status: str
) -> None:
    """The 409's ``active_scan_id`` must not reach a non-member.

    That extension exists so a CI client can attach to the scan already holding
    its ref. It is a real scan id, so the permission gate has to fire first —
    otherwise a 409 would hand an outsider both the project's existence and a
    live id from it. This is the same intersection recheck §4-1 found unguarded
    for cancel, re-pinned for the field this feature adds.
    """
    from schemas.scan import ScanCreate
    from services.scan_service import ScanForbidden, ScanInProgressConflict, trigger_scan

    actor, owning_team = await _outsider_and_resource_team(db_session)
    project = await make_project(db_session, team=owning_team)
    await make_scan(db_session, project=project, status=active_status)

    with pytest.raises(ScanForbidden) as raised:
        await trigger_scan(
            db_session,
            project_id=project.id,
            payload=ScanCreate(kind="source"),
            actor=actor,
        )

    # Belt and braces: the denial carries no extensions at all, so no future
    # edit can leak the id by widening what ScanForbidden reports.
    assert not isinstance(raised.value, ScanInProgressConflict)
    assert getattr(raised.value, "extensions", {}) == {}


# ---------------------------------------------------------------------------
# sbom-ingest × scan already in progress / archived  (NEW 409 surfaces)
#
# POST /v1/projects/{id}/sbom-ingest reuses prepare_scan_target + the
# partial-unique-index flush, so it introduces TWO new state-derived 409
# surfaces (active-scan conflict, archived project). Both must sit behind the
# permission gate: a non-member uploading to a busy/archived project must hit
# ScanForbidden (403, this domain's contract — NOT existence-hiding 404, mirror
# of scan-trigger) BEFORE any 409 that would confirm the project + its state.
#
# The guard order is verified at the SERVICE layer: prepare_scan_target raises
# ScanForbidden before ingest_sbom ever reads the upload body, so we can pass a
# throwaway (never-read) UploadFile.
# ---------------------------------------------------------------------------


def _throwaway_upload() -> object:
    """A minimal CycloneDX UploadFile that the permission gate rejects BEFORE
    the body is ever read (prepare_scan_target runs first)."""
    import io

    from starlette.datastructures import Headers, UploadFile

    return UploadFile(
        file=io.BytesIO(b'{"bomFormat":"CycloneDX","specVersion":"1.5"}'),
        filename="bom.cdx.json",
        headers=Headers({"content-type": "application/json"}),
    )


@pytest.mark.parametrize("active_status", ["queued", "running"])
async def test_sbom_ingest_other_team_busy_project_is_permission_denial_not_409(
    db_session: AsyncSession, active_status: str
) -> None:
    """An active scan would 409 (ScanInProgressConflict) for a member ingesting
    a duplicate — an outsider must hit ScanForbidden (403) before any
    in-progress probe."""
    from services.sbom_ingest_service import ingest_sbom
    from services.scan_service import ScanForbidden

    actor, owning_team = await _outsider_and_resource_team(db_session)
    project = await make_project(db_session, team=owning_team)
    await make_scan(db_session, project=project, status=active_status)

    with pytest.raises(ScanForbidden):
        await ingest_sbom(
            db_session,
            project_id=project.id,
            upload=_throwaway_upload(),  # type: ignore[arg-type]
            actor=actor,
        )


async def test_sbom_ingest_other_team_archived_project_is_permission_denial_not_409(
    db_session: AsyncSession,
) -> None:
    """An archived project would 409 (ScanArchivedConflict) for a member — an
    outsider must hit ScanForbidden (403) before the archived-state check."""
    from datetime import UTC, datetime

    from services.sbom_ingest_service import ingest_sbom
    from services.scan_service import ScanForbidden

    actor, owning_team = await _outsider_and_resource_team(db_session)
    project = await make_project(db_session, team=owning_team)
    project.archived_at = datetime.now(tz=UTC)
    await db_session.commit()

    with pytest.raises(ScanForbidden):
        await ingest_sbom(
            db_session,
            project_id=project.id,
            upload=_throwaway_upload(),  # type: ignore[arg-type]
            actor=actor,
        )


# ---------------------------------------------------------------------------
# vulnerability status × stale if_match
# ---------------------------------------------------------------------------


async def test_vuln_status_other_team_stale_ifmatch_is_404_not_409(
    db_session: AsyncSession,
) -> None:
    """A stale if_match would 409 (VulnerabilityConflict) for a member — an
    outsider must get the existence-hiding 404 before the if_match compare."""
    from datetime import UTC, datetime

    from models import Component, ComponentVersion, Vulnerability, VulnerabilityFinding
    from services.vulnerability_service import (
        VulnerabilityNotFound,
        update_vulnerability_status,
    )

    actor, owning_team = await _outsider_and_resource_team(db_session)
    project = await make_project(db_session, team=owning_team)
    scan = await make_scan(db_session, project=project, status="succeeded")

    suffix = uuid.uuid4().hex[:10]
    component = Component(
        purl=f"pkg:npm/matrix-{suffix}", name=f"matrix-{suffix}", package_type="npm"
    )
    db_session.add(component)
    await db_session.flush()
    cv = ComponentVersion(
        component_id=component.id,
        version="1.0.0",
        purl_with_version=f"pkg:npm/matrix-{suffix}@1.0.0",
    )
    db_session.add(cv)
    await db_session.flush()
    # The whole suffix, not the first four characters. `external_id` carries a
    # global unique constraint, and four hex digits is a space of 65,536 — the
    # birthday bound puts a collision past even odds a few hundred rows into a
    # shared test database. It failed exactly that way on 2026-08-02, on a
    # commit that touched nothing near this file. Every other integration test
    # that mints a CVE id uses the full suffix; this line was the outlier.
    vuln = Vulnerability(
        external_id=f"CVE-2026-{suffix.upper()}", source="trivy", severity="high"
    )
    db_session.add(vuln)
    await db_session.flush()
    finding = VulnerabilityFinding(
        scan_id=scan.id,
        component_version_id=cv.id,
        vulnerability_id=vuln.id,
        status="new",
    )
    db_session.add(finding)
    await db_session.commit()
    await db_session.refresh(finding)

    stale_snapshot = datetime(2000, 1, 1, tzinfo=UTC)  # guaranteed mismatch
    with pytest.raises(VulnerabilityNotFound):
        await update_vulnerability_status(
            db_session,
            finding_id=finding.id,
            actor=actor,
            target_status="analyzing",
            if_match=stale_snapshot,
        )


# ---------------------------------------------------------------------------
# approval transition × terminal state
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("terminal_status", ["approved", "rejected"])
async def test_approval_transition_other_team_terminal_is_404_not_409(
    db_session: AsyncSession, terminal_status: str
) -> None:
    """A terminal approval would 409 for a member — an outsider must get the
    existence-hiding 404 before the terminal/transition checks."""
    from models import Component
    from models.component_approval import ComponentApproval
    from services.component_approval_service import (
        ApprovalNotFound,
        transition_approval,
    )

    actor, owning_team = await _outsider_and_resource_team(db_session)
    project = await make_project(db_session, team=owning_team)

    suffix = uuid.uuid4().hex[:10]
    component = Component(
        purl=f"pkg:npm/appr-{suffix}", name=f"appr-{suffix}", package_type="npm"
    )
    db_session.add(component)
    await db_session.flush()
    approval = ComponentApproval(
        component_id=component.id,
        project_id=project.id,
        team_id=owning_team.id,
        status=terminal_status,
    )
    db_session.add(approval)
    await db_session.commit()
    await db_session.refresh(approval)

    with pytest.raises(ApprovalNotFound):
        await transition_approval(
            db_session,
            actor,
            approval.id,
            action="approve",
            decision_note=None,
            if_match=approval.version,
        )


# ---------------------------------------------------------------------------
# transition approvals × the four 409s the two-person control introduces
# ---------------------------------------------------------------------------


async def _gated_finding(
    db_session: AsyncSession, *, statuses: list[str]
) -> tuple[CurrentUser, Team, VulnerabilityFinding]:
    """An outsider, plus a finding in a team whose policy gates ``statuses``."""
    from models import Component, ComponentVersion, GatePolicy, Vulnerability

    actor, owning_team = await _outsider_and_resource_team(db_session)
    project = await make_project(db_session, team=owning_team)
    scan = await make_scan(db_session, project=project, status="succeeded")
    db_session.add(
        GatePolicy(
            organization_id=owning_team.organization_id,
            team_id=None,
            approval_required_statuses=statuses,
        )
    )

    suffix = uuid.uuid4().hex[:10]
    component = Component(
        purl=f"pkg:npm/gate-{suffix}", name=f"gate-{suffix}", package_type="npm"
    )
    db_session.add(component)
    await db_session.flush()
    cv = ComponentVersion(
        component_id=component.id,
        version="1.0.0",
        purl_with_version=f"pkg:npm/gate-{suffix}@1.0.0",
    )
    db_session.add(cv)
    await db_session.flush()
    vuln = Vulnerability(
        external_id=f"CVE-2026-{suffix.upper()}", source="trivy", severity="high"
    )
    db_session.add(vuln)
    await db_session.flush()
    finding = VulnerabilityFinding(
        scan_id=scan.id,
        component_version_id=cv.id,
        vulnerability_id=vuln.id,
        status="analyzing",
    )
    db_session.add(finding)
    await db_session.commit()
    await db_session.refresh(finding)
    return actor, owning_team, finding


async def test_vuln_status_other_team_gated_status_is_404_not_409(
    db_session: AsyncSession,
) -> None:
    """A member would get the approval-required 409. An outsider must not.

    The 409 says the organization gates this status, which is a fact about a
    team the caller does not belong to.
    """
    from services.vulnerability_service import (
        VulnerabilityNotFound,
        update_vulnerability_status,
    )

    actor, _team, finding = await _gated_finding(db_session, statuses=["suppressed"])

    with pytest.raises(VulnerabilityNotFound):
        await update_vulnerability_status(
            db_session,
            finding_id=finding.id,
            actor=actor,
            target_status="suppressed",
            justification="accepted for this release",
        )


async def test_request_transition_other_team_ungated_status_is_404_not_409(
    db_session: AsyncSession,
) -> None:
    """The not-required 409 would tell an outsider what the policy does not gate."""
    from services.transition_approval_service import (
        ApprovalNotFound,
        request_transition,
    )

    actor, _team, finding = await _gated_finding(db_session, statuses=["not_affected"])

    with pytest.raises(ApprovalNotFound):
        await request_transition(
            db_session,
            actor,
            finding_id=finding.id,
            target_status="suppressed",
            justification="accepted for this release",
        )


async def test_request_transition_other_team_already_open_is_404_not_409(
    db_session: AsyncSession,
) -> None:
    """The already-open 409 would confirm somebody else is working on it."""
    from services.transition_approval_service import (
        ApprovalNotFound,
        open_request,
        request_transition,
    )
    from tests._helpers import principal_for

    actor, team, finding = await _gated_finding(db_session, statuses=["suppressed"])
    insider = await make_user(db_session)
    await open_request(
        db_session,
        principal_for(insider, team_ids=[team.id], role="team_admin"),
        finding_id=finding.id,
        team_id=team.id,
        target_status="suppressed",
        justification="accepted for this release",
    )

    with pytest.raises(ApprovalNotFound):
        await request_transition(
            db_session,
            actor,
            finding_id=finding.id,
            target_status="suppressed",
            justification="accepted for this release",
        )


@pytest.mark.parametrize("decided_state", ["approved", "rejected"])
async def test_decide_other_team_decided_request_is_404_not_409(
    db_session: AsyncSession, decided_state: str
) -> None:
    """The already-decided 409 would confirm the request exists at all."""
    from services.transition_approval_service import (
        ApprovalNotFound,
        decide_and_apply,
        open_request,
    )
    from tests._helpers import principal_for

    actor, team, finding = await _gated_finding(db_session, statuses=["suppressed"])
    insider = await make_user(db_session)
    row = await open_request(
        db_session,
        principal_for(insider, team_ids=[team.id], role="team_admin"),
        finding_id=finding.id,
        team_id=team.id,
        target_status="suppressed",
        justification="accepted for this release",
    )
    row.state = decided_state
    await db_session.commit()

    with pytest.raises(ApprovalNotFound):
        await decide_and_apply(
            db_session,
            actor,
            approval_id=row.id,
            approve=True,
            note=None,
        )


# ---------------------------------------------------------------------------
# organization verdicts × an outsider reaching across an organization
# ---------------------------------------------------------------------------


async def test_listing_another_organizations_verdicts_is_404(
    db_session: AsyncSession,
) -> None:
    """Both endpoints take an id from the URL, which is the shape that leaks.

    404 rather than 403 for the same reason as everywhere else here: whether
    an organization exists is not something an outsider gets to probe for by
    reading status codes.

    A separate organization on purpose. ``_outsider_and_resource_team`` puts
    its two teams in one organization, and that caller is a legitimate reader
    here: rulings are organization-wide, so anybody inside may see them. The
    boundary this pins is the organization, not the team.
    """
    from services.organization_verdict_service import VerdictNotFound, list_verdicts

    their_org = await make_organization(db_session)
    my_org = await make_organization(db_session)
    my_team = await make_team(db_session, organization=my_org)
    outsider = await make_user(db_session)
    await make_membership(db_session, user=outsider, team=my_team, role="developer")
    actor = await principal_loaded_from_db(db_session, user=outsider)

    with pytest.raises(VerdictNotFound):
        await list_verdicts(db_session, actor, organization_id=their_org.id)


async def test_reading_another_projects_effective_verdict_is_404(
    db_session: AsyncSession,
) -> None:
    """An inherited answer is still the other team's business."""
    from models import Component
    from services.organization_verdict_service import (
        VerdictNotFound,
        resolve_for_project,
    )

    actor, owning_team = await _outsider_and_resource_team(db_session)
    project = await make_project(db_session, team=owning_team)
    suffix = uuid.uuid4().hex[:10]
    component = Component(
        purl=f"pkg:npm/hide-{suffix}", name=f"hide-{suffix}", package_type="npm"
    )
    db_session.add(component)
    await db_session.commit()
    await db_session.refresh(component)

    with pytest.raises(VerdictNotFound):
        await resolve_for_project(
            db_session,
            project_id=project.id,
            component_id=component.id,
            actor=actor,
        )
