"""
An organization ruling once, and a project keeping the right to disagree.

The fallback direction is the design and the tests are mostly about it: a
project's own answer wins where it has one, and the organization's answer
applies where it does not. Getting that backwards would make local review
pointless, and it would do so silently, because both orders produce a status
and only one of them is right.

The other thing pinned here is that the existing per-project constraint is
untouched. This unit added a table beside it rather than widening it, and the
whole reversal story rests on that index still meaning exactly what it said.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.security import CurrentUser
from models import Component, ComponentApproval, OrganizationComponentVerdict, User
from models.component_approval import ApprovalStatus
from services.organization_verdict_service import (
    SYSTEM_READER,
    VerdictAlreadyOpen,
    VerdictEtagMismatch,
    VerdictForbidden,
    VerdictInvalidTransition,
    VerdictJustificationRequired,
    VerdictNotFound,
    VerdictTerminal,
    list_verdicts,
    open_verdict,
    resolve_for_project,
    transition_verdict,
)
from tests._db_required import migrate_to_head
from tests._helpers import (
    make_organization,
    make_project,
    make_team,
    make_user,
    principal_for,
    unique_suffix,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    from core.config import database_url

    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session
    await engine.dispose()


def _super_admin(user: User) -> CurrentUser:
    return principal_for(user, role="super_admin")


async def _scene(session: AsyncSession):
    """An organization with one project and one component in it."""
    org = await make_organization(session)
    team = await make_team(session, organization=org)
    project = await make_project(session, team=team)
    suffix = unique_suffix()
    component = Component(
        purl=f"pkg:npm/orgv-{suffix}", package_type="npm", name=f"orgv-{suffix}"
    )
    session.add(component)
    await session.commit()
    await session.refresh(component)
    return org, team, project, component


async def _rule(
    session: AsyncSession,
    actor: CurrentUser,
    *,
    organization_id: uuid.UUID,
    component_id: uuid.UUID,
    outcome: str,
) -> OrganizationComponentVerdict:
    """Open a ruling and carry it through to a decided state."""
    row = await open_verdict(
        session,
        actor,
        organization_id=organization_id,
        component_id=component_id,
        justification="reviewed centrally for the whole organization",
    )
    row = await transition_verdict(
        session,
        actor,
        verdict_id=row.id,
        target_status=ApprovalStatus.under_review,
        note=None,
        if_match_version=row.version,
    )
    return await transition_verdict(
        session,
        actor,
        verdict_id=row.id,
        target_status=outcome,
        note="decided",
        if_match_version=row.version,
    )


# ---------------------------------------------------------------------------
# What a project is judged by
# ---------------------------------------------------------------------------


async def test_no_ruling_anywhere_leaves_the_project_undecided(
    db_session: AsyncSession,
) -> None:
    """The default, and the reason this can ship before anybody rules."""
    _, _, project, component = await _scene(db_session)

    resolved = await resolve_for_project(
        db_session,
        project_id=project.id,
        component_id=component.id,
        actor=SYSTEM_READER,
    )

    assert resolved.status is None
    assert resolved.scope == "none"


async def test_an_organization_ruling_reaches_a_project_with_no_answer(
    db_session: AsyncSession,
) -> None:
    org, _, project, component = await _scene(db_session)
    admin = _super_admin(await make_user(db_session, is_superuser=True))
    await _rule(
        db_session,
        admin,
        organization_id=org.id,
        component_id=component.id,
        outcome=ApprovalStatus.approved,
    )

    resolved = await resolve_for_project(
        db_session,
        project_id=project.id,
        component_id=component.id,
        actor=SYSTEM_READER,
    )

    assert resolved.status == ApprovalStatus.approved
    assert resolved.scope == "organization"
    assert resolved.justification is not None


async def test_the_projects_own_answer_wins(db_session: AsyncSession) -> None:
    """The direction that makes a local review worth doing.

    A team that reviewed a component in the context of its own use knows
    something the organization does not. If the organization overrode them the
    local review would be theatre, and nothing would say so on screen.
    """
    org, team, project, component = await _scene(db_session)
    admin = _super_admin(await make_user(db_session, is_superuser=True))
    await _rule(
        db_session,
        admin,
        organization_id=org.id,
        component_id=component.id,
        outcome=ApprovalStatus.approved,
    )
    db_session.add(
        ComponentApproval(
            component_id=component.id,
            project_id=project.id,
            team_id=team.id,
            status=ApprovalStatus.rejected,
            decided_at=datetime.now(UTC),
        )
    )
    await db_session.commit()

    resolved = await resolve_for_project(
        db_session,
        project_id=project.id,
        component_id=component.id,
        actor=SYSTEM_READER,
    )

    assert resolved.status == ApprovalStatus.rejected
    assert resolved.scope == "project"


async def test_an_open_project_review_does_not_count_as_an_answer(
    db_session: AsyncSession,
) -> None:
    """Under review is a question, not an answer, so the organization still applies."""
    org, team, project, component = await _scene(db_session)
    admin = _super_admin(await make_user(db_session, is_superuser=True))
    await _rule(
        db_session,
        admin,
        organization_id=org.id,
        component_id=component.id,
        outcome=ApprovalStatus.approved,
    )
    db_session.add(
        ComponentApproval(
            component_id=component.id,
            project_id=project.id,
            team_id=team.id,
            status=ApprovalStatus.under_review,
        )
    )
    await db_session.commit()

    resolved = await resolve_for_project(
        db_session,
        project_id=project.id,
        component_id=component.id,
        actor=SYSTEM_READER,
    )

    assert resolved.status == ApprovalStatus.approved
    assert resolved.scope == "organization"


async def test_an_undecided_organization_ruling_reaches_nobody(
    db_session: AsyncSession,
) -> None:
    """Otherwise opening a review would approve a component by itself."""
    org, _, project, component = await _scene(db_session)
    admin = _super_admin(await make_user(db_session, is_superuser=True))
    await open_verdict(
        db_session,
        admin,
        organization_id=org.id,
        component_id=component.id,
        justification="starting a central review of this package",
    )

    resolved = await resolve_for_project(
        db_session,
        project_id=project.id,
        component_id=component.id,
        actor=SYSTEM_READER,
    )

    assert resolved.status is None
    assert resolved.scope == "none"


async def test_another_organizations_ruling_does_not_reach_this_project(
    db_session: AsyncSession,
) -> None:
    _, _, project, component = await _scene(db_session)
    other_org = await make_organization(db_session)
    admin = _super_admin(await make_user(db_session, is_superuser=True))
    await _rule(
        db_session,
        admin,
        organization_id=other_org.id,
        component_id=component.id,
        outcome=ApprovalStatus.approved,
    )

    resolved = await resolve_for_project(
        db_session,
        project_id=project.id,
        component_id=component.id,
        actor=SYSTEM_READER,
    )

    assert resolved.status is None
    assert resolved.scope == "none"


# ---------------------------------------------------------------------------
# The constraint this unit promised not to touch
# ---------------------------------------------------------------------------


async def test_the_per_project_open_constraint_still_holds(
    db_session: AsyncSession,
) -> None:
    """The reversal story rests on this index meaning exactly what it said.

    A new table beside ``component_approvals`` rather than a nullable column
    inside it, so "one open approval per component and project" is still
    enforced by the database and not by anybody remembering.
    """
    _, team, project, component = await _scene(db_session)
    db_session.add(
        ComponentApproval(
            component_id=component.id,
            project_id=project.id,
            team_id=team.id,
            status=ApprovalStatus.pending,
        )
    )
    await db_session.commit()
    db_session.add(
        ComponentApproval(
            component_id=component.id,
            project_id=project.id,
            team_id=team.id,
            status=ApprovalStatus.pending,
        )
    )

    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_an_organization_ruling_does_not_disturb_an_open_project_review(
    db_session: AsyncSession,
) -> None:
    """The two are independent, which is what the design chose.

    An organization ruling closing a team's in-flight review would decide on
    the team's behalf, and the team would find their work gone with no record
    of who ended it.
    """
    org, team, project, component = await _scene(db_session)
    db_session.add(
        ComponentApproval(
            component_id=component.id,
            project_id=project.id,
            team_id=team.id,
            status=ApprovalStatus.under_review,
        )
    )
    await db_session.commit()
    admin = _super_admin(await make_user(db_session, is_superuser=True))
    await _rule(
        db_session,
        admin,
        organization_id=org.id,
        component_id=component.id,
        outcome=ApprovalStatus.approved,
    )

    still_open = (
        await db_session.execute(
            text(
                "SELECT status FROM component_approvals "
                "WHERE component_id = :c AND project_id = :p"
            ),
            {"c": component.id, "p": project.id},
        )
    ).scalar_one()
    assert still_open == ApprovalStatus.under_review


# ---------------------------------------------------------------------------
# Ruling
# ---------------------------------------------------------------------------


async def test_only_a_deployment_administrator_may_rule(
    db_session: AsyncSession,
) -> None:
    """A ruling reaches every team, so it is not a team administrator's to make."""
    org, team, _, component = await _scene(db_session)
    team_admin = principal_for(
        await make_user(db_session), team_ids=[team.id], role="team_admin"
    )

    with pytest.raises(VerdictForbidden):
        await open_verdict(
            db_session,
            team_admin,
            organization_id=org.id,
            component_id=component.id,
            justification="we should approve this everywhere",
        )


async def test_a_ruling_has_to_say_why(db_session: AsyncSession) -> None:
    org, _, _, component = await _scene(db_session)
    admin = _super_admin(await make_user(db_session, is_superuser=True))

    with pytest.raises(VerdictJustificationRequired):
        await open_verdict(
            db_session,
            admin,
            organization_id=org.id,
            component_id=component.id,
            justification="ok",
        )


async def test_only_one_ruling_at_a_time_per_component(
    db_session: AsyncSession,
) -> None:
    org, _, _, component = await _scene(db_session)
    admin = _super_admin(await make_user(db_session, is_superuser=True))
    await open_verdict(
        db_session,
        admin,
        organization_id=org.id,
        component_id=component.id,
        justification="starting a central review of this package",
    )

    with pytest.raises(VerdictAlreadyOpen):
        await open_verdict(
            db_session,
            admin,
            organization_id=org.id,
            component_id=component.id,
            justification="starting a second central review by mistake",
        )


async def test_changing_its_mind_means_ruling_again(db_session: AsyncSession) -> None:
    """A decided row is a record. The organization rules again rather than editing it."""
    org, _, project, component = await _scene(db_session)
    admin = _super_admin(await make_user(db_session, is_superuser=True))
    first = await _rule(
        db_session,
        admin,
        organization_id=org.id,
        component_id=component.id,
        outcome=ApprovalStatus.approved,
    )

    with pytest.raises(VerdictTerminal):
        await transition_verdict(
            db_session,
            admin,
            verdict_id=first.id,
            target_status=ApprovalStatus.rejected,
            note="changed our minds",
            if_match_version=first.version,
        )

    second = await _rule(
        db_session,
        admin,
        organization_id=org.id,
        component_id=component.id,
        outcome=ApprovalStatus.rejected,
    )
    resolved = await resolve_for_project(
        db_session,
        project_id=project.id,
        component_id=component.id,
        actor=SYSTEM_READER,
    )

    assert second.id != first.id
    assert resolved.status == ApprovalStatus.rejected


async def test_a_stale_version_is_refused(db_session: AsyncSession) -> None:
    """Two administrators deciding at once must not lose one of the decisions."""
    org, _, _, component = await _scene(db_session)
    admin = _super_admin(await make_user(db_session, is_superuser=True))
    row = await open_verdict(
        db_session,
        admin,
        organization_id=org.id,
        component_id=component.id,
        justification="starting a central review of this package",
    )

    with pytest.raises(VerdictEtagMismatch):
        await transition_verdict(
            db_session,
            admin,
            verdict_id=row.id,
            target_status=ApprovalStatus.under_review,
            note=None,
            if_match_version=row.version + 5,
        )


async def test_the_workflow_refuses_a_jump(db_session: AsyncSession) -> None:
    """Pending straight to approved skips the review the states exist to record."""
    org, _, _, component = await _scene(db_session)
    admin = _super_admin(await make_user(db_session, is_superuser=True))
    row = await open_verdict(
        db_session,
        admin,
        organization_id=org.id,
        component_id=component.id,
        justification="starting a central review of this package",
    )

    with pytest.raises(VerdictInvalidTransition):
        await transition_verdict(
            db_session,
            admin,
            verdict_id=row.id,
            target_status=ApprovalStatus.approved,
            note=None,
            if_match_version=row.version,
        )


async def test_an_unknown_ruling_is_not_found(db_session: AsyncSession) -> None:
    admin = _super_admin(await make_user(db_session, is_superuser=True))

    with pytest.raises(VerdictNotFound):
        await transition_verdict(
            db_session,
            admin,
            verdict_id=uuid.uuid4(),
            target_status=ApprovalStatus.under_review,
            note=None,
            if_match_version=None,
        )


async def test_the_list_is_scoped_to_one_organization(
    db_session: AsyncSession,
) -> None:
    org, _, _, component = await _scene(db_session)
    other_org, _, _, other_component = await _scene(db_session)
    admin = _super_admin(await make_user(db_session, is_superuser=True))
    await open_verdict(
        db_session,
        admin,
        organization_id=org.id,
        component_id=component.id,
        justification="starting a central review of this package",
    )
    await open_verdict(
        db_session,
        admin,
        organization_id=other_org.id,
        component_id=other_component.id,
        justification="an unrelated review in another organization",
    )

    rows, total = await list_verdicts(db_session, admin, organization_id=org.id)

    assert [row.component_id for row in rows] == [component.id]
    assert total == 1
