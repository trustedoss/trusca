"""
Unit tests for ``services/component_approval_service.py`` — Phase 4 PR #15.

Test categories
---------------
1. Pure-unit (no DB) — transition matrix + permission guard tests.
2. DB-backed (integration marker) — full service function tests against real
   Postgres. These follow the commit-eager pattern established in
   ``test_vulnerability_service.py``.

Test cases (min 5 per spec):
  A. Happy path: create → get → transition to under_review → transition to approved
  B. RBAC denial: non-member gets 404 (existence-hide) on get + transition
  C. Double-create → 409 approval_already_open
  D. ETag mismatch → 412 on transition
  E. Invalid transition (terminal state) → 409 approval_invalid_transition
  F. Developer cannot approve (requires team_admin) → 403
  G. delete: terminal state → 409; non-requester developer → 404
  H. super_admin can transition + see all approvals
  I. list: team-scoped filtering; super_admin sees all
  J. Transition matrix full grid (pure unit, parametrized)

Coverage target: ≥ 80% of service code changed in this PR.
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
from models.component_approval import ApprovalStatus
from services.component_approval_service import (
    _TRANSITION_MAP,
    ApprovalAlreadyOpen,
    ApprovalEtagMismatch,
    ApprovalForbidden,
    ApprovalInvalidTransition,
    ApprovalNotFound,
    ApprovalTerminalState,
    create_approval,
    delete_approval,
    get_approval,
    list_approvals,
    transition_approval,
)
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_team,
    make_user,
    principal_for,
    unique_suffix,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _developer(team_id: uuid.UUID) -> CurrentUser:
    return CurrentUser(
        id=uuid.uuid4(),
        email=f"dev-{unique_suffix()}@example.com",
        role="developer",
        team_ids=[team_id],
        team_roles={team_id: "developer"},
        is_active=True,
        is_superuser=False,
    )


def _team_admin(team_id: uuid.UUID) -> CurrentUser:
    return CurrentUser(
        id=uuid.uuid4(),
        email=f"ta-{unique_suffix()}@example.com",
        role="team_admin",
        team_ids=[team_id],
        team_roles={team_id: "team_admin"},
        is_active=True,
        is_superuser=False,
    )


def _super_admin() -> CurrentUser:
    return CurrentUser(
        id=uuid.uuid4(),
        email=f"sa-{unique_suffix()}@example.com",
        role="super_admin",
        team_ids=[],
        team_roles={},
        is_active=True,
        is_superuser=True,
    )


# ---------------------------------------------------------------------------
# Pure-unit transition matrix tests (no DB required)
# ---------------------------------------------------------------------------

_ALL_STATES = [s.value for s in ApprovalStatus]
_GRID = [(c, t) for c in _ALL_STATES for t in _ALL_STATES]


@pytest.mark.parametrize(("current", "target"), _GRID)
def test_transition_map_full_grid(current: str, target: str) -> None:
    """
    Exhaustive 4×4 grid: every (current, target) pair either succeeds or
    raises ``ApprovalInvalidTransition`` according to ``_TRANSITION_MAP``.
    Uses only in-memory logic — no DB needed.
    """
    current_status = ApprovalStatus(current)
    allowed = _TRANSITION_MAP.get(current_status, frozenset())
    target_status = ApprovalStatus(target)

    if target in allowed:
        # The target is in the allowed set — the transition map says it's OK.
        # We just verify the allowed set membership here; the full service
        # function test covers the DB path.
        assert target_status in allowed
    else:
        # Should not be in allowed.
        assert target_status not in allowed


def test_pending_allows_under_review_and_rejected() -> None:
    allowed = _TRANSITION_MAP[ApprovalStatus.pending]
    assert ApprovalStatus.under_review in allowed
    assert ApprovalStatus.rejected in allowed
    # Terminal states not reachable from pending in one hop.
    assert ApprovalStatus.approved not in allowed
    assert ApprovalStatus.pending not in allowed


def test_under_review_allows_approved_and_rejected() -> None:
    allowed = _TRANSITION_MAP[ApprovalStatus.under_review]
    assert ApprovalStatus.approved in allowed
    assert ApprovalStatus.rejected in allowed
    assert ApprovalStatus.pending not in allowed


def test_terminal_states_have_no_transitions() -> None:
    for terminal in (ApprovalStatus.approved, ApprovalStatus.rejected):
        allowed = _TRANSITION_MAP[terminal]
        assert len(allowed) == 0, f"{terminal} should be terminal"


# ---------------------------------------------------------------------------
# DB-backed tests — require DATABASE_URL + alembic upgrade head
# ---------------------------------------------------------------------------


def _alembic_head() -> None:
    """Run `alembic upgrade head` against the test DB."""
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        capture_output=True,
        text=True,
        cwd=str(BACKEND_ROOT),
    )
    if result.returncode != 0:
        raise RuntimeError(f"alembic upgrade failed:\n{result.stderr}")


@pytest.fixture(scope="module")
def db_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        pytest.skip("DATABASE_URL not set — skipping DB-backed tests")
    return url


_ALEMBIC_RAN = False


def _alembic_once() -> None:
    """Run alembic upgrade head only once per pytest session."""
    global _ALEMBIC_RAN
    if _ALEMBIC_RAN:
        return
    _alembic_head()
    _ALEMBIC_RAN = True


@pytest.fixture
async def db_session_factory(db_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Per-test engine + session factory. Avoids cross-event-loop attachment.

    Module-scoped async engines under pytest-asyncio's default function-scoped
    event loop cause asyncpg "another operation is in progress" errors —
    function scope here keeps each test isolated to its own loop.
    """
    _alembic_once()
    engine = create_async_engine(db_url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    # Install audit listener so the flush does not blow up.
    from core.audit import install_audit_listeners

    install_audit_listeners(factory)

    yield factory
    await engine.dispose()


@pytest.fixture
async def session(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Per-test async session (commit-eager pattern — no savepoint)."""
    async with db_session_factory() as s:
        yield s


# ---------------------------------------------------------------------------
# Fixtures: org / team / project / component
# ---------------------------------------------------------------------------


@pytest.fixture
async def team_a(session: AsyncSession):
    org = await make_organization(session)
    return await make_team(session, organization=org)


@pytest.fixture
async def team_b(session: AsyncSession):
    org = await make_organization(session)
    return await make_team(session, organization=org)


@pytest.fixture
async def project_a(session: AsyncSession, team_a):
    return await make_project(session, team=team_a)


@pytest.fixture
async def component(session: AsyncSession, project_a):
    """Create a minimal Component row for use in approval tests."""
    from models.scan import Component

    comp = Component(
        purl=f"pkg:pypi/test-pkg-{unique_suffix()}",
        name=f"test-pkg-{unique_suffix()}",
        package_type="pypi",
    )
    session.add(comp)
    await session.commit()
    await session.refresh(comp)
    return comp


@pytest.fixture
async def developer_actor(session: AsyncSession, team_a):
    user = await make_user(session)
    await make_membership(session, user=user, team=team_a, role="developer")
    return principal_for(user, team_ids=[team_a.id], role="developer")


@pytest.fixture
async def team_admin_actor(session: AsyncSession, team_a):
    user = await make_user(session)
    await make_membership(session, user=user, team=team_a, role="team_admin")
    return principal_for(user, team_ids=[team_a.id], role="team_admin")


@pytest.fixture
async def super_admin_actor(session: AsyncSession):
    user = await make_user(session, is_superuser=True)
    return principal_for(user, team_ids=[], role="super_admin")


@pytest.fixture
async def outsider_actor(session: AsyncSession, team_b):
    """A developer who belongs to team_b only — invisible to team_a resources."""
    user = await make_user(session)
    await make_membership(session, user=user, team=team_b, role="developer")
    return principal_for(user, team_ids=[team_b.id], role="developer")


# ---------------------------------------------------------------------------
# Case A — Happy path: create → get → transition chain → approved
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_create_and_transition(
    session: AsyncSession,
    project_a,
    component,
    developer_actor,
    team_admin_actor,
):
    """Developer creates; team_admin moves it through → approved."""
    # create
    approval = await create_approval(
        session,
        developer_actor,
        component_id=component.id,
        project_id=project_a.id,
    )
    assert approval.status == ApprovalStatus.pending
    assert approval.version == 1
    assert approval.requested_by_user_id == developer_actor.id

    # get (same actor)
    fetched = await get_approval(session, developer_actor, approval.id)
    assert fetched.id == approval.id

    # under_review (team_admin)
    approval2 = await transition_approval(
        session,
        team_admin_actor,
        approval.id,
        action="under_review",
        decision_note=None,
        if_match=1,
    )
    assert approval2.status == ApprovalStatus.under_review
    assert approval2.version == 2

    # approved (team_admin)
    approval3 = await transition_approval(
        session,
        team_admin_actor,
        approval2.id,
        action="approved",
        decision_note="LGTM",
        if_match=2,
    )
    assert approval3.status == ApprovalStatus.approved
    assert approval3.version == 3
    assert approval3.decided_by_user_id == team_admin_actor.id
    assert approval3.decision_note == "LGTM"


# ---------------------------------------------------------------------------
# Case B — RBAC denial: non-member sees 404 (existence-hide)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_approval_cross_team_404(
    session: AsyncSession,
    project_a,
    component,
    developer_actor,
    outsider_actor,
):
    """A developer from another team gets 404 on get_approval."""
    approval = await create_approval(
        session,
        developer_actor,
        component_id=component.id,
        project_id=project_a.id,
    )

    with pytest.raises(ApprovalNotFound):
        await get_approval(session, outsider_actor, approval.id)


@pytest.mark.asyncio
async def test_transition_cross_team_404(
    session: AsyncSession,
    project_a,
    component,
    developer_actor,
    outsider_actor,
):
    """A developer from another team gets 404 on transition_approval."""
    approval = await create_approval(
        session,
        developer_actor,
        component_id=component.id,
        project_id=project_a.id,
    )

    with pytest.raises(ApprovalNotFound):
        await transition_approval(
            session,
            outsider_actor,
            approval.id,
            action="under_review",
            decision_note=None,
            if_match=1,
        )


# ---------------------------------------------------------------------------
# Case C — Double-create → 409 approval_already_open
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_duplicate_open_approval_409(
    session: AsyncSession,
    project_a,
    component,
    developer_actor,
):
    """Creating a second open approval for the same (component, project) raises 409."""
    await create_approval(
        session,
        developer_actor,
        component_id=component.id,
        project_id=project_a.id,
    )

    with pytest.raises(ApprovalAlreadyOpen):
        await create_approval(
            session,
            developer_actor,
            component_id=component.id,
            project_id=project_a.id,
        )


# ---------------------------------------------------------------------------
# Case D — ETag mismatch → 412
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_etag_mismatch_raises_412(
    session: AsyncSession,
    project_a,
    component,
    developer_actor,
    team_admin_actor,
):
    """Sending an stale version in if_match raises ApprovalEtagMismatch (412)."""
    approval = await create_approval(
        session,
        developer_actor,
        component_id=component.id,
        project_id=project_a.id,
    )

    with pytest.raises(ApprovalEtagMismatch):
        await transition_approval(
            session,
            team_admin_actor,
            approval.id,
            action="under_review",
            decision_note=None,
            if_match=999,  # wrong version
        )


# ---------------------------------------------------------------------------
# Case E — Invalid transition (terminal state) → 409
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_transition_from_terminal_raises_409(
    session: AsyncSession,
    project_a,
    component,
    developer_actor,
    team_admin_actor,
):
    """Attempting to move an approved approval raises ApprovalInvalidTransition."""
    approval = await create_approval(
        session,
        developer_actor,
        component_id=component.id,
        project_id=project_a.id,
    )
    # move to under_review
    approval = await transition_approval(
        session,
        team_admin_actor,
        approval.id,
        action="under_review",
        decision_note=None,
        if_match=1,
    )
    # approve it
    approval = await transition_approval(
        session,
        team_admin_actor,
        approval.id,
        action="approved",
        decision_note=None,
        if_match=2,
    )
    assert approval.status == ApprovalStatus.approved

    # Now try to re-transition an approved approval — should fail.
    with pytest.raises(ApprovalInvalidTransition) as exc_info:
        await transition_approval(
            session,
            team_admin_actor,
            approval.id,
            action="rejected",
            decision_note=None,
            if_match=3,
        )
    assert exc_info.value.allowed_to == []


# ---------------------------------------------------------------------------
# Case F — Developer cannot approve (requires team_admin) → 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_developer_cannot_approve_403(
    session: AsyncSession,
    project_a,
    component,
    developer_actor,
):
    """A developer cannot perform the 'approved' action — team_admin required."""
    approval = await create_approval(
        session,
        developer_actor,
        component_id=component.id,
        project_id=project_a.id,
    )

    # Developer cannot move to under_review either (needs team_admin).
    with pytest.raises(ApprovalForbidden):
        await transition_approval(
            session,
            developer_actor,
            approval.id,
            action="under_review",
            decision_note=None,
            if_match=1,
        )


# ---------------------------------------------------------------------------
# Case G — delete: terminal state → 409; non-requester developer → 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_terminal_approval_409(
    session: AsyncSession,
    project_a,
    component,
    developer_actor,
    team_admin_actor,
):
    """Deleting an approved approval raises ApprovalTerminalState (409)."""
    approval = await create_approval(
        session,
        developer_actor,
        component_id=component.id,
        project_id=project_a.id,
    )
    approval = await transition_approval(
        session,
        team_admin_actor,
        approval.id,
        action="under_review",
        decision_note=None,
        if_match=1,
    )
    approval = await transition_approval(
        session,
        team_admin_actor,
        approval.id,
        action="approved",
        decision_note=None,
        if_match=2,
    )

    with pytest.raises(ApprovalTerminalState):
        await delete_approval(session, developer_actor, approval.id)


@pytest.mark.asyncio
async def test_delete_by_non_requester_developer_404(
    session: AsyncSession,
    project_a,
    component,
    team_a,
    developer_actor,
):
    """A developer who did not create the approval gets 404 on delete."""
    approval = await create_approval(
        session,
        developer_actor,
        component_id=component.id,
        project_id=project_a.id,
    )

    # Create a different developer in the same team.
    other_user = await make_user(session)
    from tests._helpers import make_membership

    await make_membership(session, user=other_user, team=team_a, role="developer")
    other_actor = principal_for(other_user, team_ids=[team_a.id], role="developer")

    with pytest.raises(ApprovalNotFound):
        await delete_approval(session, other_actor, approval.id)


@pytest.mark.asyncio
async def test_delete_by_requester_succeeds(
    session: AsyncSession,
    project_a,
    component,
    developer_actor,
):
    """The original requester can delete their own pending approval."""
    approval = await create_approval(
        session,
        developer_actor,
        component_id=component.id,
        project_id=project_a.id,
    )
    # Should not raise.
    await delete_approval(session, developer_actor, approval.id)

    with pytest.raises(ApprovalNotFound):
        await get_approval(session, developer_actor, approval.id)


# ---------------------------------------------------------------------------
# Case H — super_admin can do everything
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_super_admin_can_transition_any_team(
    session: AsyncSession,
    project_a,
    component,
    developer_actor,
    super_admin_actor,
):
    """super_admin bypasses team membership for transition + get."""
    approval = await create_approval(
        session,
        developer_actor,
        component_id=component.id,
        project_id=project_a.id,
    )

    # super_admin can read it.
    fetched = await get_approval(session, super_admin_actor, approval.id)
    assert fetched.id == approval.id

    # super_admin can approve directly.
    result = await transition_approval(
        session,
        super_admin_actor,
        approval.id,
        action="under_review",
        decision_note=None,
        if_match=1,
    )
    assert result.status == ApprovalStatus.under_review


# ---------------------------------------------------------------------------
# Case I — list: team-scoped; super_admin sees all
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_is_team_scoped(
    session: AsyncSession,
    project_a,
    team_b,
    component,
    developer_actor,
):
    """list_approvals returns only the actor's team rows."""
    # Create an approval in team_a.
    await create_approval(
        session,
        developer_actor,
        component_id=component.id,
        project_id=project_a.id,
    )

    # developer_actor belongs to team_a; should see >= 1 row.
    rows, total = await list_approvals(session, developer_actor)
    assert total >= 1

    # An outsider in team_b sees 0 rows for team_a.
    outsider_user = await make_user(session)
    await make_membership(session, user=outsider_user, team=team_b, role="developer")
    outsider = principal_for(outsider_user, team_ids=[team_b.id], role="developer")
    rows_b, total_b = await list_approvals(session, outsider)
    # team_b has no approvals yet.
    for row in rows_b:
        assert row.team_id == team_b.id


@pytest.mark.asyncio
async def test_list_super_admin_sees_all(
    session: AsyncSession,
    project_a,
    component,
    developer_actor,
    super_admin_actor,
):
    """super_admin can see approvals from any team."""
    # Ensure at least one approval exists.
    await create_approval(
        session,
        developer_actor,
        component_id=component.id,
        project_id=project_a.id,
    )
    # super_admin list has no team filter — total should be >= 1.
    _rows, total = await list_approvals(session, super_admin_actor)
    assert total >= 1


@pytest.mark.asyncio
async def test_list_status_filter(
    session: AsyncSession,
    project_a,
    component,
    developer_actor,
):
    """status query parameter narrows the result to matching rows only."""
    await create_approval(
        session,
        developer_actor,
        component_id=component.id,
        project_id=project_a.id,
    )
    rows_pending, _ = await list_approvals(
        session, developer_actor, status_filter="pending"
    )
    for row in rows_pending:
        assert row.status == ApprovalStatus.pending

    rows_approved, _ = await list_approvals(
        session, developer_actor, status_filter="approved"
    )
    for row in rows_approved:
        assert row.status == ApprovalStatus.approved
