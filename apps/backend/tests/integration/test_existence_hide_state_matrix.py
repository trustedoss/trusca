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
  - vuln status   × stale if_match     → VulnerabilityNotFound (not VulnerabilityConflict)
  - approval      × terminal state     → ApprovalNotFound (not ApprovalTerminalState /
                                          ApprovalInvalidTransition)

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
    vuln = Vulnerability(
        external_id=f"CVE-2026-{suffix[:4].upper()}", source="trivy", severity="high"
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
