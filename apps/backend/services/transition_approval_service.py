# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Requesting and deciding a status change that one person may not make alone.

The rule the whole module exists for is that the requester and the approver are
different people. Without that, a policy naming a status only adds a step: the
same person clicks request and then approve, the record gains a second row, and
nothing has been reviewed. The check is on the user id rather than the grade,
because two team administrators reviewing each other is the arrangement this is
meant to support and a single administrator reviewing themselves is not.

Which statuses need this comes from the gate policy, and an organization that
names none keeps today's behaviour: the transition happens directly and no row
is written here.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.authz import assert_team_access
from core.security import CurrentUser
from models import Project, Scan, TransitionApproval, VulnerabilityFinding
from services.gate_policy_service import statuses_requiring_approval
from services.vulnerability_service import (
    _assert_can_transition,
    _assert_justification_sufficient,
    _has_team_admin,
    update_vulnerability_status,
)

log = structlog.get_logger("services.transition_approval")


class TransitionApprovalError(Exception):
    """Base for the failures the router renders as Problem Details."""


class ApprovalNotFound(TransitionApprovalError):
    """No such request, or it is not the caller's to see."""


class ApprovalForbidden(TransitionApprovalError):
    """The caller may not take this action on this request."""


class ApprovalSelfDecision(ApprovalForbidden):
    """The requester tried to decide their own request.

    Its own class because the answer to it is a different sentence: the caller
    is not lacking a grade, they are the wrong person, and telling them to ask
    someone else is more useful than telling them they lack permission.
    """


class ApprovalNotRequired(TransitionApprovalError):
    """The policy does not ask for a second person on this status.

    A request nobody is obliged to answer is worse than no request: it sits in
    a queue implying a control that was never configured. The caller is told to
    make the change directly instead.
    """


class ApprovalAlreadyOpen(TransitionApprovalError):
    """A request for this finding is already waiting for a decision."""


class ApprovalAlreadyDecided(TransitionApprovalError):
    """The request has been decided; decisions are not revisited in place."""


#: Postgres SQLSTATE for a unique violation.
_UNIQUE_VIOLATION = "23505"
_OPEN_REQUEST_INDEX = "uq_transition_approvals_open"


def _is_open_request_conflict(exc: IntegrityError) -> bool:
    """Whether this really is a second request on a finding that has one open.

    Matched on the SQLSTATE and the index name rather than on the exception
    type, because every foreign-key and check violation on this table arrives
    as the same class and only one of them means what the caller is told.
    """
    orig = getattr(exc, "orig", None)
    if getattr(orig, "sqlstate", None) != _UNIQUE_VIOLATION:
        return False
    return _OPEN_REQUEST_INDEX in str(orig)


async def open_request(
    session: AsyncSession,
    actor: CurrentUser,
    *,
    finding_id: uuid.UUID,
    team_id: uuid.UUID,
    target_status: str,
    justification: str,
) -> TransitionApproval:
    """Record a request and leave the finding where it is.

    The finding does not move now. Moving it and recording the request would
    mean the risk was accepted the moment it was proposed, which is the thing
    the policy asked to prevent.
    """
    row = TransitionApproval(
        finding_id=finding_id,
        team_id=team_id,
        target_status=target_status,
        justification=justification,
        requested_by_user_id=actor.id,
        state="pending",
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        if not _is_open_request_conflict(exc):
            # A different constraint failed: a finding deleted by a rescan's
            # wipe-and-replace, a vanished team. Reporting those as "a request
            # is already waiting" would send the caller to look for a queue
            # entry that does not exist, so they surface as the errors they are.
            raise
        # The partial unique index caught a second open request. Two people
        # queueing different outcomes would leave the approver deciding a
        # question they cannot see the whole of.
        raise ApprovalAlreadyOpen(
            f"finding {finding_id} already has a request waiting for a decision"
        ) from exc
    await session.commit()
    await session.refresh(row)
    log.info(
        "transition_approval_requested",
        finding_id=str(finding_id),
        target_status=target_status,
    )
    return row


async def decide(
    session: AsyncSession,
    actor: CurrentUser,
    *,
    approval_id: uuid.UUID,
    approve: bool,
    note: str | None,
) -> TransitionApproval:
    """Approve or reject, by someone who did not ask.

    Writes the decision and leaves the transaction open. Applying the change is
    the caller's job: this module owns the decision and the vulnerability
    service owns what a status change means, and folding the two together here
    would put the transition rules in two places. Committing here instead would
    split them across two transactions, which is worse than either.

    The row is locked for the read. Without it, two decisions on the same
    request both see ``pending``, both pass, and the later commit overwrites
    the earlier one's state, deciding user and note. One approver sending
    approve and reject together could then leave the finding suppressed and the
    record reading "rejected", which inverts the only thing this row is for.
    """
    row = (
        await session.execute(
            select(TransitionApproval)
            .where(TransitionApproval.id == approval_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise ApprovalNotFound(f"approval {approval_id} not found")

    if row.state != "pending":
        raise ApprovalAlreadyDecided(
            f"approval {approval_id} was already {row.state}"
        )

    if row.requested_by_user_id is None:
        # The requester's account is gone, so the row can no longer show that
        # two different people were involved. Deciding it would record an
        # agreement whose other half cannot be named, which is the opposite of
        # what the record exists for. A fresh request names somebody again.
        raise ApprovalForbidden(
            "this request no longer names who asked for it; open a new one"
        )

    if row.requested_by_user_id == actor.id:
        raise ApprovalSelfDecision(
            "the person who asked for this change cannot be the one who agrees to it"
        )

    row.state = "approved" if approve else "rejected"
    row.decided_by_user_id = actor.id
    row.decision_note = note
    row.decided_at = datetime.now(tz=UTC)
    await session.flush()
    log.info(
        "transition_approval_decided",
        approval_id=str(approval_id),
        state=row.state,
    )
    return row


async def _load_finding_scope(
    session: AsyncSession, finding_id: uuid.UUID
) -> tuple[VulnerabilityFinding, Project] | None:
    """The finding with the project that decides who may see it."""
    row = (
        await session.execute(
            select(VulnerabilityFinding, Project)
            .join(Scan, Scan.id == VulnerabilityFinding.scan_id)
            .join(Project, Project.id == Scan.project_id)
            .where(VulnerabilityFinding.id == finding_id)
        )
    ).first()
    if row is None:
        return None
    return row[0], row[1]


async def request_transition(
    session: AsyncSession,
    actor: CurrentUser,
    *,
    finding_id: uuid.UUID,
    target_status: str,
    justification: str,
) -> TransitionApproval:
    """Ask for a change the policy says one person may not make.

    Requesting is held to the same bar as making the change directly: the same
    transition matrix, the same grade. Letting a lower grade request would be a
    kinder workflow and may well be the right one later, but it widens who can
    start a risk acceptance, and widening later is easy where narrowing later
    breaks whatever people have built on it.
    """
    loaded = await _load_finding_scope(session, finding_id)
    if loaded is None:
        raise ApprovalNotFound(f"vulnerability finding {finding_id} not found")
    finding, project = loaded

    # Existence-hide, matching the transition endpoint: a caller outside the
    # team learns nothing about whether the finding exists.
    assert_team_access(
        actor,
        project.team_id,
        log=log,
        resource="transition_approval",
        resource_id=str(finding_id),
        deny=lambda: ApprovalNotFound(f"vulnerability finding {finding_id} not found"),
    )

    if target_status not in await statuses_requiring_approval(session, project.id):
        # Nothing to review. Writing a request here would leave a row nobody is
        # obliged to answer, and the caller can make this change themselves.
        raise ApprovalNotRequired(
            f"status {target_status!r} does not need a second person for this project"
        )

    _assert_can_transition(
        actor,
        current_status=finding.status,
        target_status=target_status,
        team_id=project.team_id,
    )
    _assert_justification_sufficient(target_status, justification)

    return await open_request(
        session,
        actor,
        finding_id=finding_id,
        team_id=project.team_id,
        target_status=target_status,
        justification=justification,
    )


async def decide_and_apply(
    session: AsyncSession,
    actor: CurrentUser,
    *,
    approval_id: uuid.UUID,
    approve: bool,
    note: str | None,
) -> TransitionApproval:
    """Record the decision and, when it is yes, make the change.

    The transition runs through the ordinary status-update path rather than a
    shortcut, so an approved change is audited, mirrored into the VEX analysis
    state, and validated against the matrix exactly like a direct one. A
    shortcut here would be a second place where a status change means something
    slightly different.
    """
    row = (
        await session.execute(
            select(TransitionApproval).where(TransitionApproval.id == approval_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise ApprovalNotFound(f"approval {approval_id} not found")

    assert_team_access(
        actor,
        row.team_id,
        log=log,
        resource="transition_approval",
        resource_id=str(approval_id),
        deny=lambda: ApprovalNotFound(f"approval {approval_id} not found"),
    )
    if not _has_team_admin(actor, row.team_id):
        raise ApprovalForbidden(
            "deciding a transition request requires role >= team_admin in the project's team"
        )

    if approve:
        # Check the transition before recording the agreement, not after: a
        # finding that moved while the request waited would otherwise be found
        # out only once the decision was written. The apply below runs in the
        # same transaction, so a late failure rolls the decision back too, but
        # failing here gives the caller the real reason instead of a rollback.
        scope = await _load_finding_scope(session, row.finding_id)
        if scope is None:
            raise ApprovalNotFound(f"vulnerability finding {row.finding_id} not found")
        finding, _project = scope
        if finding.status != row.target_status:
            _assert_can_transition(
                actor,
                current_status=finding.status,
                target_status=row.target_status,
                team_id=row.team_id,
            )

    decided = await decide(
        session, actor, approval_id=approval_id, approve=approve, note=note
    )
    if decided.state == "approved":
        # The justification travels with the request: the reason the approver
        # agreed to is the reason recorded on the finding, not a new one typed
        # at apply time.
        #
        # One transaction covers the decision and the change. Two would leave a
        # window where the row says approved and the finding never moved, and
        # the request could not be decided again to fix it.
        await update_vulnerability_status(
            session,
            finding_id=decided.finding_id,
            actor=actor,
            target_status=decided.target_status,
            justification=decided.justification,
            approved_request_id=decided.id,
            commit=False,
        )
        # Stamped after, not before: the check inside reads this column to see
        # whether the agreement has already been used, so setting it first
        # would make every approval look spent and refuse its own transition.
        decided.applied_at = datetime.now(tz=UTC)
    await session.commit()
    await session.refresh(decided)
    return decided


async def list_pending_for_teams(
    session: AsyncSession, *, team_ids: list[uuid.UUID], all_teams: bool = False
) -> list[TransitionApproval]:
    """The queue, oldest first, so the longest wait is answered first.

    ``all_teams`` is for the deployment administrator, whose job is the whole
    installation and who often belongs to no team at all; scoping them to their
    memberships would show them an empty queue while requests pile up.
    """
    stmt = select(TransitionApproval).where(TransitionApproval.state == "pending")
    if not all_teams:
        if not team_ids:
            return []
        stmt = stmt.where(TransitionApproval.team_id.in_(team_ids))
    rows = (
        await session.execute(stmt.order_by(TransitionApproval.created_at.asc()))
    ).scalars()
    return list(rows)
