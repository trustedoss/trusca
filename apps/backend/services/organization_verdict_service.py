# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
An organization ruling on a component once, and what that means for a project.

The resolution rule is the whole of the design: a project's own answer wins
where it has one, and the organization's answer applies where it does not.
That direction is deliberate. A team that reviewed a component in the context
of its own use knows something the organization does not, and an organization
ruling that overrode them would make a local review pointless. In the other
direction the organization is answering a question nobody local has answered,
which is exactly what a default is for.

Nothing is written to reconcile the two. An organization ruling does not close
open project reviews and does not stamp anything onto projects; the fallback
happens when somebody reads. That keeps a ruling cheap to make and cheap to
undo, and it means neither surface has to know about the other's lifecycle.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal

import structlog
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.authz import assert_team_access
from core.security import CurrentUser
from models import (
    ComponentApproval,
    OrganizationComponentVerdict,
    Project,
    Team,
)
from models.component_approval import APPROVAL_STATUS_VALUES, ApprovalStatus

log = structlog.get_logger("services.organization_verdict")

#: A ruling still being worked on. Matches the partial unique index, and the
#: per-project surface's notion of "open", so neither drifts from the other.
OPEN_STATUSES: frozenset[str] = frozenset(
    {ApprovalStatus.pending, ApprovalStatus.under_review}
)

#: A ruling that has been made. Only these fall through to a project.
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {ApprovalStatus.approved, ApprovalStatus.rejected}
)

#: One page of rulings. The list is designed to grow to a package per row for
#: a whole organization, so it is paged from the start rather than after it
#: becomes slow for somebody.
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200

#: The shortest reason a ruling may carry. The same bar the VEX transitions
#: hold callers to: a note nobody can act on is not a record of anything.
MIN_JUSTIFICATION_LEN = 10


class OrganizationVerdictError(Exception):
    """Base for the failures the router renders as Problem Details."""

    status_code = 409
    title = "Conflict"


class VerdictNotFound(OrganizationVerdictError):
    """No such ruling, or it is not the caller's to see."""

    status_code = 404
    title = "Not Found"


class VerdictForbidden(OrganizationVerdictError):
    """Only a deployment administrator rules for the whole organization."""

    status_code = 403
    title = "Forbidden"


class VerdictAlreadyOpen(OrganizationVerdictError):
    """A ruling on this component is already being worked on."""


class VerdictTerminal(OrganizationVerdictError):
    """The ruling has been made; a new one is opened rather than edited."""


class VerdictJustificationRequired(OrganizationVerdictError):
    """A ruling that reaches every project has to say why."""

    status_code = 422
    title = "Justification Required"


class VerdictEtagMismatch(OrganizationVerdictError):
    """Somebody else decided this while the caller was reading it."""

    status_code = 412
    title = "Precondition Failed"


class VerdictInvalidTransition(OrganizationVerdictError):
    """The workflow does not allow this move."""

    status_code = 422
    title = "Invalid Transition"


#: The same matrix the per-project approvals use. Repeated rather than
#: imported so the two can diverge if they ever need to, and asserted equal in
#: a contract test so they do not diverge by accident.
_TRANSITIONS: dict[str, frozenset[str]] = {
    ApprovalStatus.pending: frozenset(
        {ApprovalStatus.under_review, ApprovalStatus.rejected}
    ),
    ApprovalStatus.under_review: frozenset(
        {ApprovalStatus.approved, ApprovalStatus.rejected}
    ),
    ApprovalStatus.approved: frozenset(),
    ApprovalStatus.rejected: frozenset(),
}


@dataclass(frozen=True)
class ResolvedVerdict:
    """What a project is actually judged by for one component.

    ``scope`` says where the answer came from, because a status shown without
    it invites the wrong edit: somebody who sees "rejected" and assumes their
    team decided it will go looking for a project row that does not exist.
    """

    status: str | None
    scope: str  # "project" | "organization" | "none"
    verdict_id: uuid.UUID | None = None
    justification: str | None = None


async def _db_now(session: AsyncSession) -> datetime:
    """The database's clock, read as a value.

    Which ruling wins is decided by comparing these timestamps, and two API
    replicas whose clocks differ by seconds would let an older decision sort
    after a newer one. The failure is silent and it opens rather than closes:
    a rejection recorded second could read as the approval that came first.

    Read rather than assigned as ``func.now()``, because the audit listener
    serialises the changed columns to JSON and a SQL expression sitting in the
    attribute fails there instead of in the write it belongs to.
    """
    return (await session.execute(select(func.now()))).scalar_one()


def _is_super_admin(actor: CurrentUser) -> bool:
    return bool(actor.is_superuser) or actor.role == "super_admin"


def _assert_justification(value: str | None) -> str:
    text = (value or "").strip()
    if len(text) < MIN_JUSTIFICATION_LEN:
        raise VerdictJustificationRequired(
            f"an organization ruling requires a reason of at least "
            f"{MIN_JUSTIFICATION_LEN} characters"
        )
    return text


async def _organization_of_project(
    session: AsyncSession, project_id: uuid.UUID
) -> uuid.UUID | None:
    row = (
        await session.execute(
            select(Team.organization_id)
            .join(Project, Project.team_id == Team.id)
            .where(Project.id == project_id)
        )
    ).scalar_one_or_none()
    return row


async def _assert_member_of_organization(
    session: AsyncSession, actor: CurrentUser, organization_id: uuid.UUID
) -> None:
    """The caller belongs to a team in this organization, or administers the box.

    Without it the list endpoint takes an organization id from the URL and
    hands back what another organization has ruled on, including the component
    names and the reasons. The ids are not secret but the rulings are somebody
    else's record of what they decided and why.
    """
    if _is_super_admin(actor):
        return
    if not actor.team_ids:
        raise VerdictNotFound(f"organization {organization_id} not found")
    belongs = (
        await session.execute(
            select(Team.id)
            .where(Team.organization_id == organization_id, Team.id.in_(actor.team_ids))
            .limit(1)
        )
    ).scalar_one_or_none()
    if belongs is None:
        # 404 rather than 403: whether an organization exists is not something
        # an outsider should be able to probe for.
        raise VerdictNotFound(f"organization {organization_id} not found")


#: For readers that run on the project's own behalf rather than a person's:
#: the build gate, a scheduled report. Spelled out at the call site so that
#: skipping the access check is a decision somebody wrote down.
SYSTEM_READER: Final = "system"


async def resolve_for_project(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    component_id: uuid.UUID,
    actor: CurrentUser | Literal["system"],
) -> ResolvedVerdict:
    """What this project is judged by for this component.

    The project's own decided approval wins. Only a decided organization
    ruling falls through: one still being argued about is not an answer, and
    treating it as one would let a component count as approved because
    somebody opened a review.
    """
    if actor != SYSTEM_READER:
        # No default on the parameter, so a caller that forgets this argument
        # fails to type-check rather than quietly reading another tenant's
        # answer. The next consumer of this function is the build gate, which
        # takes a project id from a CI payload.
        assert not isinstance(actor, str)
        team_id = (
            await session.execute(
                select(Project.team_id).where(Project.id == project_id)
            )
        ).scalar_one_or_none()
        if team_id is None:
            raise VerdictNotFound(f"project {project_id} not found")
        assert_team_access(
            actor,
            team_id,
            log=log,
            resource="organization_verdict",
            resource_id=str(project_id),
            deny=lambda: VerdictNotFound(f"project {project_id} not found"),
        )

    project_status = (
        await session.execute(
            select(ComponentApproval.status)
            .where(
                ComponentApproval.project_id == project_id,
                ComponentApproval.component_id == component_id,
                ComponentApproval.status.in_(sorted(TERMINAL_STATUSES)),
            )
            # Deterministic all the way down. Ordering on one nullable
            # timestamp leaves two decisions recorded in the same instant to be
            # resolved by whatever order the scan happened to return, and this
            # query picks which answer a project is judged by.
            .order_by(
                ComponentApproval.decided_at.desc().nullslast(),
                ComponentApproval.created_at.desc(),
                ComponentApproval.id.desc(),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if project_status is not None:
        return ResolvedVerdict(status=project_status, scope="project")

    organization_id = await _organization_of_project(session, project_id)
    if organization_id is None:
        return ResolvedVerdict(status=None, scope="none")

    row = (
        await session.execute(
            select(OrganizationComponentVerdict)
            .where(
                OrganizationComponentVerdict.organization_id == organization_id,
                OrganizationComponentVerdict.component_id == component_id,
                OrganizationComponentVerdict.status.in_(sorted(TERMINAL_STATUSES)),
            )
            .order_by(
                OrganizationComponentVerdict.decided_at.desc().nullslast(),
                OrganizationComponentVerdict.created_at.desc(),
                OrganizationComponentVerdict.id.desc(),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return ResolvedVerdict(status=None, scope="none")
    return ResolvedVerdict(
        status=row.status,
        scope="organization",
        verdict_id=row.id,
        justification=row.justification,
    )


async def open_verdict(
    session: AsyncSession,
    actor: CurrentUser,
    *,
    organization_id: uuid.UUID,
    component_id: uuid.UUID,
    justification: str,
) -> OrganizationComponentVerdict:
    """Start a ruling. Nothing about any project changes yet."""
    if not _is_super_admin(actor):
        raise VerdictForbidden(
            "ruling for a whole organization requires a deployment administrator"
        )
    reason = _assert_justification(justification)

    row = OrganizationComponentVerdict(
        organization_id=organization_id,
        component_id=component_id,
        status=ApprovalStatus.pending,
        justification=reason,
        requested_by_user_id=actor.id,
        version=1,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        # core.audit's after_soft_rollback listener clears any CREATE audit
        # row this flush staged (#170); a rollback used to leave it behind
        # for the next flush on this session to write against a ruling that
        # was never created.
        await session.rollback()
        if _is_missing_reference(exc):
            # A component or organization that is not there. Reporting it as
            # "a ruling is already open" would send the caller looking for a
            # queue entry that does not exist, and a bare 500 would call a bad
            # request a server fault.
            raise VerdictNotFound(
                f"component {component_id} or organization {organization_id} not found"
            ) from exc
        if not _is_open_verdict_conflict(exc):
            raise
        raise VerdictAlreadyOpen(
            f"component {component_id} already has a ruling being worked on"
        ) from exc
    await session.commit()
    await session.refresh(row)
    log.info(
        "organization_verdict_opened",
        organization_id=str(organization_id),
        component_id=str(component_id),
        actor_id=str(actor.id),
    )
    return row


#: Postgres SQLSTATE for a unique violation and for a foreign-key violation.
_UNIQUE_VIOLATION = "23505"
_FK_VIOLATION = "23503"
_OPEN_VERDICT_INDEX = "ix_org_component_verdicts_unique_open"


def _is_missing_reference(exc: IntegrityError) -> bool:
    """Whether the row pointed at something that is not there."""
    return getattr(getattr(exc, "orig", None), "sqlstate", None) == _FK_VIOLATION


def _is_open_verdict_conflict(exc: IntegrityError) -> bool:
    """Whether this is a second open ruling rather than some other violation.

    Matched on the SQLSTATE and index name: a missing component and a missing
    organization arrive as the same exception class, and reporting either as
    "a ruling is already open" sends the caller looking for a row that is not
    there.
    """
    orig = getattr(exc, "orig", None)
    if getattr(orig, "sqlstate", None) != _UNIQUE_VIOLATION:
        return False
    return _OPEN_VERDICT_INDEX in str(orig)


async def transition_verdict(
    session: AsyncSession,
    actor: CurrentUser,
    *,
    verdict_id: uuid.UUID,
    target_status: str,
    note: str | None,
    if_match_version: int | None,
) -> OrganizationComponentVerdict:
    """Move a ruling along, and record who moved it.

    The row is locked for the read. Two administrators deciding at once would
    otherwise both see the same version, both pass the check, and the later
    write would land with no sign that the earlier one happened.
    """
    if not _is_super_admin(actor):
        raise VerdictForbidden(
            "deciding an organization ruling requires a deployment administrator"
        )
    if target_status not in APPROVAL_STATUS_VALUES:
        raise VerdictInvalidTransition(f"unknown status: {target_status!r}")

    row = (
        await session.execute(
            select(OrganizationComponentVerdict)
            .where(OrganizationComponentVerdict.id == verdict_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise VerdictNotFound(f"organization ruling {verdict_id} not found")

    if row.status in TERMINAL_STATUSES:
        raise VerdictTerminal(
            f"ruling {verdict_id} is already {row.status}; open a new one to change it"
        )
    if if_match_version is not None and row.version != if_match_version:
        raise VerdictEtagMismatch(
            "this ruling changed since you read it; reload and try again"
        )
    if target_status not in _TRANSITIONS.get(row.status, frozenset()):
        raise VerdictInvalidTransition(
            f"cannot move a ruling from {row.status!r} to {target_status!r}"
        )

    now = await _db_now(session)
    row.status = target_status
    row.decision_note = note
    row.version += 1
    row.updated_at = now
    if target_status in TERMINAL_STATUSES:
        row.decided_by_user_id = actor.id
        row.decided_at = now
    await session.commit()
    await session.refresh(row)
    log.info(
        "organization_verdict_transitioned",
        verdict_id=str(verdict_id),
        status=row.status,
        actor_id=str(actor.id),
    )
    return row


async def list_verdicts(
    session: AsyncSession,
    actor: CurrentUser,
    *,
    organization_id: uuid.UUID,
    status_filter: list[str] | None = None,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> tuple[list[OrganizationComponentVerdict], int]:
    """The organization's rulings, newest first.

    Readable by any member of the organization: what the organization has
    ruled is the reason a component shows as approved in their project, and
    hiding it would leave them unable to explain their own screen.
    """
    await _assert_member_of_organization(session, actor, organization_id)
    stmt = select(OrganizationComponentVerdict).where(
        OrganizationComponentVerdict.organization_id == organization_id
    )
    if status_filter:
        unknown = sorted(set(status_filter) - set(APPROVAL_STATUS_VALUES))
        if unknown:
            raise VerdictInvalidTransition(
                f"unknown status filter: {', '.join(unknown)}"
            )
        stmt = stmt.where(OrganizationComponentVerdict.status.in_(status_filter))
    total = (
        await session.execute(
            select(func.count()).select_from(stmt.subquery())
        )
    ).scalar_one()
    size = max(1, min(page_size, MAX_PAGE_SIZE))
    rows = (
        await session.execute(
            stmt.order_by(
                OrganizationComponentVerdict.created_at.desc(),
                OrganizationComponentVerdict.id.desc(),
            )
            .offset((max(1, page) - 1) * size)
            .limit(size)
        )
    ).scalars()
    return list(rows), int(total)
