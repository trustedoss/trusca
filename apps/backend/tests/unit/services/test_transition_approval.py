"""
Two people, or it is not a review.

The rule these tests exist for is that the person who asked cannot be the
person who agrees. Everything else here is arrangement; that one line is the
difference between a control and a formality, and it is checked on the user id
rather than the grade because two administrators reviewing each other is the
arrangement this supports and one administrator reviewing themselves is not.
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
from models import User
from services.gate_policy_service import statuses_requiring_approval
from services.transition_approval_service import (
    ApprovalAlreadyDecided,
    ApprovalAlreadyOpen,
    ApprovalForbidden,
    ApprovalNotFound,
    ApprovalSelfDecision,
    decide,
    list_pending_for_teams,
    open_request,
)
from tests._helpers import (
    make_organization,
    make_project,
    make_scan,
    make_team,
    make_user,
    principal_for,
    unique_suffix,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set: skip transition approval tests")
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
        pytest.skip(f"alembic upgrade head failed\n{result.stdout}\n{result.stderr}")


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    from core.config import database_url

    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session
    await engine.dispose()


def _principal(user: User, team_id: uuid.UUID) -> CurrentUser:
    return principal_for(user, team_ids=[team_id], role="team_admin")


async def _seed_finding(session: AsyncSession):
    from models import (
        Component,
        ComponentVersion,
        ScanComponent,
        Vulnerability,
        VulnerabilityFinding,
    )

    org = await make_organization(session)
    team = await make_team(session, organization=org)
    project = await make_project(session, team=team)
    scan = await make_scan(session, project=project, status="succeeded")

    suffix = unique_suffix()
    purl = f"pkg:npm/appr-{suffix}"
    component = Component(purl=purl, package_type="npm", name=f"appr-{suffix}")
    session.add(component)
    await session.commit()
    await session.refresh(component)

    version = ComponentVersion(
        component_id=component.id, version="1.0.0", purl_with_version=f"{purl}@1.0.0"
    )
    session.add(version)
    await session.commit()
    await session.refresh(version)

    session.add(
        ScanComponent(scan_id=scan.id, component_version_id=version.id, direct=True, raw_data={})
    )
    vulnerability = Vulnerability(
        external_id=f"CVE-2024-{suffix}", source="NVD", severity="high", summary="fixture"
    )
    session.add(vulnerability)
    await session.commit()
    await session.refresh(vulnerability)

    finding = VulnerabilityFinding(
        scan_id=scan.id,
        component_version_id=version.id,
        vulnerability_id=vulnerability.id,
        status="new",
    )
    session.add(finding)
    await session.commit()
    await session.refresh(finding)
    return org, team, project, finding


# ---------------------------------------------------------------------------
# Which transitions need a second person
# ---------------------------------------------------------------------------


async def test_no_policy_means_no_transition_needs_a_second_person(
    db_session: AsyncSession,
) -> None:
    """The default, and the reason this can ship before anyone configures it."""
    _, _, project, _ = await _seed_finding(db_session)

    assert await statuses_requiring_approval(db_session, project.id) == frozenset()


async def test_the_policy_names_which_statuses_need_one(db_session: AsyncSession) -> None:
    from models import GatePolicy

    org, _, project, _ = await _seed_finding(db_session)
    db_session.add(
        GatePolicy(
            organization_id=org.id,
            team_id=None,
            approval_required_statuses=["suppressed", "not_affected"],
        )
    )
    await db_session.commit()

    assert await statuses_requiring_approval(db_session, project.id) == frozenset(
        {"suppressed", "not_affected"}
    )


async def test_a_team_cannot_switch_off_what_its_organization_required(
    db_session: AsyncSession,
) -> None:
    """The finding that made this a union rather than a fall-through.

    Every other field on the policy falls through, so a team row wins wherever
    it has a value. An empty list is a value, so a team administrator could
    write one, watch the organization's requirement vanish, make the change
    alone, and put the list back. The only grade that can reach a gated status
    is the same grade that can write that row, so the control belonged to
    exactly the person it was aimed at.
    """
    from models import GatePolicy

    org, team, project, _ = await _seed_finding(db_session)
    db_session.add(
        GatePolicy(
            organization_id=org.id,
            team_id=None,
            approval_required_statuses=["suppressed"],
        )
    )
    db_session.add(
        GatePolicy(
            organization_id=org.id,
            team_id=team.id,
            approval_required_statuses=[],
        )
    )
    await db_session.commit()

    assert await statuses_requiring_approval(db_session, project.id) == frozenset(
        {"suppressed"}
    )


async def test_a_team_may_ask_for_more_than_its_organization(
    db_session: AsyncSession,
) -> None:
    """Stricter is the direction that is safe to delegate."""
    from models import GatePolicy

    org, team, project, _ = await _seed_finding(db_session)
    db_session.add(
        GatePolicy(
            organization_id=org.id,
            team_id=None,
            approval_required_statuses=["suppressed"],
        )
    )
    db_session.add(
        GatePolicy(
            organization_id=org.id,
            team_id=team.id,
            approval_required_statuses=["not_affected"],
        )
    )
    await db_session.commit()

    assert await statuses_requiring_approval(db_session, project.id) == frozenset(
        {"suppressed", "not_affected"}
    )


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------


async def test_the_requester_cannot_decide_their_own_request(
    db_session: AsyncSession,
) -> None:
    """Without this the policy adds a step and reviews nothing."""
    _, team, _, finding = await _seed_finding(db_session)
    requester = await make_user(db_session)
    row = await open_request(
        db_session,
        _principal(requester, team.id),
        finding_id=finding.id,
        team_id=team.id,
        target_status="suppressed",
        justification="accepted for this release",
    )

    with pytest.raises(ApprovalSelfDecision):
        await decide(
            db_session, _principal(requester, team.id), approval_id=row.id, approve=True, note=None
        )


async def test_somebody_else_may_decide_it(db_session: AsyncSession) -> None:
    _, team, _, finding = await _seed_finding(db_session)
    requester = await make_user(db_session)
    approver = await make_user(db_session)
    row = await open_request(
        db_session,
        _principal(requester, team.id),
        finding_id=finding.id,
        team_id=team.id,
        target_status="suppressed",
        justification="accepted for this release",
    )

    decided = await decide(
        db_session, _principal(approver, team.id), approval_id=row.id, approve=True, note="agreed"
    )

    assert decided.state == "approved"
    assert decided.decided_by_user_id == approver.id
    assert decided.decided_at is not None


async def test_a_rejection_is_recorded_rather_than_discarded(
    db_session: AsyncSession,
) -> None:
    """The record is the point: a refused request is evidence, not noise."""
    _, team, _, finding = await _seed_finding(db_session)
    requester = await make_user(db_session)
    approver = await make_user(db_session)
    row = await open_request(
        db_session,
        _principal(requester, team.id),
        finding_id=finding.id,
        team_id=team.id,
        target_status="suppressed",
        justification="wanted out of the way",
    )

    decided = await decide(
        db_session,
        _principal(approver, team.id),
        approval_id=row.id,
        approve=False,
        note="fix it instead",
    )

    assert decided.state == "rejected"
    assert decided.decision_note == "fix it instead"


# ---------------------------------------------------------------------------
# Sequences
# ---------------------------------------------------------------------------


async def test_only_one_request_may_be_open_for_a_finding(
    db_session: AsyncSession,
) -> None:
    """Otherwise an approver decides one of two questions and cannot tell which."""
    _, team, _, finding = await _seed_finding(db_session)
    first = await make_user(db_session)
    second = await make_user(db_session)
    await open_request(
        db_session,
        _principal(first, team.id),
        finding_id=finding.id,
        team_id=team.id,
        target_status="suppressed",
        justification="one",
    )

    with pytest.raises(ApprovalAlreadyOpen):
        await open_request(
            db_session,
            _principal(second, team.id),
            finding_id=finding.id,
            team_id=team.id,
            target_status="not_affected",
            justification="two",
        )


async def test_a_decided_request_reopens_the_slot(db_session: AsyncSession) -> None:
    """The index is partial, so history accumulates without blocking the next ask."""
    _, team, _, finding = await _seed_finding(db_session)
    requester = await make_user(db_session)
    approver = await make_user(db_session)
    first = await open_request(
        db_session,
        _principal(requester, team.id),
        finding_id=finding.id,
        team_id=team.id,
        target_status="suppressed",
        justification="one",
    )
    await decide(
        db_session, _principal(approver, team.id), approval_id=first.id, approve=False, note=None
    )

    second = await open_request(
        db_session,
        _principal(requester, team.id),
        finding_id=finding.id,
        team_id=team.id,
        target_status="not_affected",
        justification="two",
    )

    assert second.id != first.id
    assert second.state == "pending"


async def test_a_decision_is_not_revisited_in_place(db_session: AsyncSession) -> None:
    _, team, _, finding = await _seed_finding(db_session)
    requester = await make_user(db_session)
    approver = await make_user(db_session)
    other = await make_user(db_session)
    row = await open_request(
        db_session,
        _principal(requester, team.id),
        finding_id=finding.id,
        team_id=team.id,
        target_status="suppressed",
        justification="one",
    )
    await decide(
        db_session,
        _principal(approver, team.id),
        approval_id=row.id,
        approve=True,
        note=None,
    )

    with pytest.raises(ApprovalAlreadyDecided):
        await decide(
            db_session, _principal(other, team.id), approval_id=row.id, approve=False, note=None
        )


async def test_a_request_whose_asker_is_gone_cannot_be_decided(
    db_session: AsyncSession,
) -> None:
    """Two names, or no decision.

    The requester column is ``ON DELETE SET NULL``, so a removed account leaves
    a request nobody is named on. Deciding it would record an agreement whose
    other half cannot be shown, and the guard that stops self-approval has
    nothing left to compare.
    """
    _, team, _, finding = await _seed_finding(db_session)
    requester = await make_user(db_session)
    approver = await make_user(db_session)
    row = await open_request(
        db_session,
        _principal(requester, team.id),
        finding_id=finding.id,
        team_id=team.id,
        target_status="suppressed",
        justification="accepted for this release",
    )
    row.requested_by_user_id = None
    await db_session.commit()

    with pytest.raises(ApprovalForbidden):
        await decide(
            db_session,
            _principal(approver, team.id),
            approval_id=row.id,
            approve=True,
            note=None,
        )


async def test_an_unknown_request_is_not_found(db_session: AsyncSession) -> None:
    _, team, _, _ = await _seed_finding(db_session)
    approver = await make_user(db_session)

    with pytest.raises(ApprovalNotFound):
        await decide(
            db_session,
            _principal(approver, team.id),
            approval_id=uuid.uuid4(),
            approve=True,
            note=None,
        )


async def test_the_queue_is_scoped_to_the_teams_asked_for(db_session: AsyncSession) -> None:
    _, team_a, _, finding_a = await _seed_finding(db_session)
    _, team_b, _, finding_b = await _seed_finding(db_session)
    requester = await make_user(db_session)
    await open_request(
        db_session,
        _principal(requester, team_a.id),
        finding_id=finding_a.id,
        team_id=team_a.id,
        target_status="suppressed",
        justification="a",
    )
    await open_request(
        db_session,
        _principal(requester, team_b.id),
        finding_id=finding_b.id,
        team_id=team_b.id,
        target_status="suppressed",
        justification="b",
    )

    queue = await list_pending_for_teams(db_session, team_ids=[team_a.id])

    assert [row.finding_id for row in queue] == [finding_a.id]
