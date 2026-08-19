# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Asking before using, for organizations that work that way.

Approvals exist after a scan finds something. That suits an organization that
reviews what its code already depends on, and it leaves nowhere to record the
asking for one that decides first: the answer arrives when the dependency is
already in the build.

Two things keep this from becoming a second, parallel approval system.

The status vocabulary is the same one. A request and an approval are the same
question at different times, so they share ``approval_status`` and the same
transition matrix rather than growing four look-alike names of their own.

And a decision made here is carried onto the approval a later scan opens for
the same package, so somebody who asked early is not asked again. Without that,
the reward for following the process would be answering twice.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.authz import assert_team_access
from core.config import intake_requests_enabled
from core.security import CurrentUser
from models import ComponentIntakeRequest, Project
from models.component_approval import APPROVAL_STATUS_VALUES, ApprovalStatus

log = structlog.get_logger("services.component_intake")

#: The shortest reason a request may carry. The reviewer is being asked about
#: something that is not in the codebase yet, so this text is the whole of what
#: they have to go on.
MIN_JUSTIFICATION_LEN = 10

#: Purls are the identifier the rest of the portal speaks, so a request that
#: does not carry one cannot be matched to the component a scan later finds.
#: Loose on the tail, strict on the shape: ``pkg:type/name``.
_PURL_PATTERN = re.compile(r"^pkg:[a-zA-Z][a-zA-Z0-9.+-]*/[^\s]+$")

#: The same matrix the per-project approvals use, and asserted equal to theirs
#: in a contract test. Repeated rather than imported so the two can diverge if
#: they ever need to, and so the divergence would be a visible edit.
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

_TERMINAL = frozenset({ApprovalStatus.approved, ApprovalStatus.rejected})


class IntakeError(Exception):
    """Base for the failures the router renders as Problem Details."""

    status_code = 409
    title = "Conflict"


class IntakeDisabled(IntakeError):
    """This deployment does not use an ask-before-using queue.

    Rendered as 404 rather than 403, because "off" means the surface is not
    there. A 403 would tell somebody they lack permission for a feature their
    organization has not adopted, and they would go asking for the permission.
    """

    status_code = 404
    title = "Not Found"


class IntakeNotFound(IntakeError):
    status_code = 404
    title = "Not Found"


class IntakeForbidden(IntakeError):
    status_code = 403
    title = "Forbidden"


class IntakeInvalid(IntakeError):
    status_code = 422
    title = "Unprocessable"


class IntakeAlreadyOpen(IntakeError):
    """Somebody is already asking about this package for this project."""


class IntakeTerminal(IntakeError):
    """The request has been answered; a new one is opened rather than edited."""


class IntakeEtagMismatch(IntakeError):
    status_code = 412
    title = "Precondition Failed"


def _assert_enabled() -> None:
    if not intake_requests_enabled():
        raise IntakeDisabled("this deployment does not use intake requests")


def _has_team_admin(actor: CurrentUser, team_id: uuid.UUID) -> bool:
    if actor.is_superuser or actor.role == "super_admin":
        return True
    return actor.team_roles.get(team_id) in {"team_admin", "super_admin"}


def _now() -> datetime:
    return datetime.now(tz=UTC)


async def _project_for(session: AsyncSession, project_id: uuid.UUID) -> Project:
    project = (
        await session.execute(select(Project).where(Project.id == project_id))
    ).scalar_one_or_none()
    if project is None:
        raise IntakeNotFound(f"project {project_id} not found")
    return project


async def open_request(
    session: AsyncSession,
    actor: CurrentUser,
    *,
    project_id: uuid.UUID,
    purl: str,
    justification: str,
) -> ComponentIntakeRequest:
    """Ask to use a package.

    Open to any member of the project's team, including the lowest grade. That
    is the point of the queue: the person who wants to add a dependency is
    usually not the person who decides, and a surface only an administrator can
    file into is one people route around.
    """
    _assert_enabled()
    project = await _project_for(session, project_id)
    assert_team_access(
        actor,
        project.team_id,
        log=log,
        resource="component_intake_request",
        resource_id=str(project_id),
        deny=lambda: IntakeNotFound(f"project {project_id} not found"),
    )

    cleaned = purl.strip()
    if not _PURL_PATTERN.match(cleaned):
        raise IntakeInvalid(
            "purl must look like 'pkg:npm/lodash' or 'pkg:pypi/requests'"
        )
    reason = justification.strip()
    if len(reason) < MIN_JUSTIFICATION_LEN:
        raise IntakeInvalid(
            f"say why in at least {MIN_JUSTIFICATION_LEN} characters: the "
            f"reviewer has nothing else to go on for a package that is not "
            f"in the codebase yet"
        )

    row = ComponentIntakeRequest(
        project_id=project_id,
        team_id=project.team_id,
        purl=cleaned,
        justification=reason,
        status=ApprovalStatus.pending,
        requested_by_user_id=actor.id,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        session.info.pop("_pending_audit_rows", None)
        raise IntakeAlreadyOpen(
            f"somebody is already asking about {cleaned} for this project"
        ) from exc
    await session.commit()
    await session.refresh(row)
    log.info(
        "component_intake_requested",
        request_id=str(row.id),
        project_id=str(project_id),
        actor_id=str(actor.id),
    )
    return row


async def transition_request(
    session: AsyncSession,
    actor: CurrentUser,
    *,
    request_id: uuid.UUID,
    target_status: str,
    note: str | None,
    if_match_version: int | None,
) -> ComponentIntakeRequest:
    """Answer a request, or move it along.

    Deciding is a team administrator's act, matching who disposes an approval
    after a scan: it is the same judgement about the same package, and the two
    should not need different people.
    """
    _assert_enabled()
    if target_status not in APPROVAL_STATUS_VALUES:
        raise IntakeInvalid(f"unknown status: {target_status!r}")

    row = (
        await session.execute(
            select(ComponentIntakeRequest)
            .where(ComponentIntakeRequest.id == request_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise IntakeNotFound(f"intake request {request_id} not found")

    assert_team_access(
        actor,
        row.team_id,
        log=log,
        resource="component_intake_request",
        resource_id=str(request_id),
        deny=lambda: IntakeNotFound(f"intake request {request_id} not found"),
    )
    if not _has_team_admin(actor, row.team_id):
        raise IntakeForbidden(
            "answering an intake request requires role >= team_admin in the "
            "project's team"
        )

    if row.status in _TERMINAL:
        raise IntakeTerminal(
            f"request {request_id} was already {row.status}; open a new one"
        )
    if if_match_version is not None and row.version != if_match_version:
        raise IntakeEtagMismatch(
            "this request changed since you read it; reload and try again"
        )
    if target_status not in _TRANSITIONS.get(row.status, frozenset()):
        raise IntakeInvalid(
            f"cannot move a request from {row.status!r} to {target_status!r}"
        )

    row.status = target_status
    row.decision_note = note
    row.version += 1
    row.updated_at = _now()
    if target_status in _TERMINAL:
        row.decided_by_user_id = actor.id
        row.decided_at = _now()
    await session.commit()
    await session.refresh(row)
    log.info(
        "component_intake_transitioned",
        request_id=str(request_id),
        status=row.status,
        actor_id=str(actor.id),
    )
    return row


def _scoped(
    actor: CurrentUser,
    stmt: Select[tuple[ComponentIntakeRequest]],
) -> Select[tuple[ComponentIntakeRequest]]:
    if actor.is_superuser or actor.role == "super_admin":
        return stmt
    if not actor.team_ids:
        # Fail closed: an actor with no teams sees nothing rather than
        # everything, which an unfiltered query would give them.
        return stmt.where(ComponentIntakeRequest.team_id.is_(None))
    return stmt.where(ComponentIntakeRequest.team_id.in_(actor.team_ids))


async def list_requests(
    session: AsyncSession,
    actor: CurrentUser,
    *,
    project_id: uuid.UUID | None = None,
    status_filter: list[str] | None = None,
) -> list[ComponentIntakeRequest]:
    """The queue, oldest first, scoped to the caller's teams."""
    _assert_enabled()
    stmt = _scoped(actor, select(ComponentIntakeRequest))
    if project_id is not None:
        stmt = stmt.where(ComponentIntakeRequest.project_id == project_id)
    if status_filter:
        unknown = sorted(set(status_filter) - set(APPROVAL_STATUS_VALUES))
        if unknown:
            raise IntakeInvalid(f"unknown status filter: {', '.join(unknown)}")
        stmt = stmt.where(ComponentIntakeRequest.status.in_(status_filter))
    rows = (
        await session.execute(stmt.order_by(ComponentIntakeRequest.created_at.asc()))
    ).scalars()
    return list(rows)


def decided_intake_status_by_purl(
    session: object, *, project_id: uuid.UUID, purls: list[str]
) -> dict[str, str]:
    """The answers already given for these packages in this project.

    Sync, and takes a sync session: the only caller is the scan pipeline, which
    runs on ``core.db.sync_session_scope`` in a Celery worker with no request
    context, the same way ``auto_create_pending_approvals`` does.

    Only decided answers are returned. A request still being argued about is
    not an answer, and carrying it forward would mark a component approved
    because somebody had opened a review.
    """
    if not purls:
        return {}
    rows = session.execute(  # type: ignore[attr-defined]
        select(ComponentIntakeRequest.purl, ComponentIntakeRequest.status)
        .where(
            ComponentIntakeRequest.project_id == project_id,
            ComponentIntakeRequest.purl.in_(purls),
            ComponentIntakeRequest.status.in_(sorted(_TERMINAL)),
        )
        .order_by(ComponentIntakeRequest.decided_at.desc().nullslast())
    ).all()
    # First row per purl wins, which is the most recent decision: an
    # organization that changed its mind opened a new request rather than
    # editing the old one.
    answered: dict[str, str] = {}
    for purl, status in rows:
        answered.setdefault(purl, status)
    return answered


async def count_open_for_team(session: AsyncSession, team_id: uuid.UUID) -> int:
    """How many requests are waiting, for the badge on the queue."""
    if not intake_requests_enabled():
        return 0
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(ComponentIntakeRequest)
                .where(
                    ComponentIntakeRequest.team_id == team_id,
                    ComponentIntakeRequest.status.in_(
                        [ApprovalStatus.pending, ApprovalStatus.under_review]
                    ),
                )
            )
        ).scalar_one()
    )
