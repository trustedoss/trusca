# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Recording that an obligation was actually met.

The portal could always say what a licence asks for and generate the notice it
asks for. It could not say whether anybody published it, so the answer to "are
we compliant" lived in a spreadsheet next to the tool, and the spreadsheet was
the thing an auditor ended up reading.

One rule shapes everything here: none of this changes what a notice says. The
obligation text is the licence's words and the generated notice is derived from
the components, so marking work done records that somebody acted, and nothing
about the record feeds back into the document. A fulfilment that could edit a
notice would be a way to make a compliance artefact say what somebody wished
were true.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.authz import assert_team_access
from core.security import CurrentUser
from models import (
    OBLIGATION_FULFILMENT_STATUSES,
    Obligation,
    ObligationFulfilment,
    Project,
)
from services.assignee import is_assignable_to_team

log = structlog.get_logger("services.obligation_fulfilment")

#: Statuses that mean the work is finished, one way or the other. Both stop the
#: obligation counting as outstanding; they differ in what they say about why.
CLOSED_STATUSES: frozenset[str] = frozenset({"done", "not_applicable"})


class FulfilmentError(Exception):
    status_code = 409
    title = "Conflict"


class FulfilmentNotFound(FulfilmentError):
    status_code = 404
    title = "Not Found"


class FulfilmentForbidden(FulfilmentError):
    status_code = 403
    title = "Forbidden"


class FulfilmentInvalid(FulfilmentError):
    status_code = 422
    title = "Unprocessable"


class FulfilmentEtagMismatch(FulfilmentError):
    status_code = 412
    title = "Precondition Failed"


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _can_write(actor: CurrentUser, team_id: uuid.UUID) -> bool:
    """Who may record obligation work.

    Any member of the team, not only an administrator. The person who publishes
    the notice or adds the attribution file is usually the engineer doing the
    release, and a record only their manager can update is a record that stays
    empty while the work happens.
    """
    if actor.is_superuser or actor.role == "super_admin":
        return True
    return actor.team_roles.get(team_id) in {"developer", "team_admin", "super_admin"}


async def _load_project(session: AsyncSession, project_id: uuid.UUID) -> Project:
    project = (
        await session.execute(select(Project).where(Project.id == project_id))
    ).scalar_one_or_none()
    if project is None:
        raise FulfilmentNotFound(f"project {project_id} not found")
    return project


async def _assert_assignee_is_on_the_team(
    session: AsyncSession, assignee_id: uuid.UUID, team_id: uuid.UUID
) -> None:
    """The person named has to be somebody who could actually do it.

    The rule itself lives in :func:`services.assignee.is_assignable_to_team`,
    shared with vulnerability findings (ER28a). Two copies of "who may be
    assigned" would drift; this wrapper only turns the answer into this
    module's error type.
    """
    if not await is_assignable_to_team(session, assignee_id, team_id):
        raise FulfilmentInvalid(
            "the assignee must be an active person on this project's team"
        )


async def record_fulfilment(
    session: AsyncSession,
    actor: CurrentUser,
    *,
    project_id: uuid.UUID,
    obligation_id: uuid.UUID,
    status: str,
    assignee_user_id: uuid.UUID | None,
    due_on: date | None,
    evidence_note: str | None,
    evidence_url: str | None,
    if_match_version: int | None,
) -> ObligationFulfilment:
    """Create or update this project's record against one obligation.

    Upsert rather than separate create and update endpoints: the caller is
    saying what the state is now, and making them find out first whether a row
    exists is a round trip that answers a question about our schema rather than
    about their work.
    """
    if status not in OBLIGATION_FULFILMENT_STATUSES:
        raise FulfilmentInvalid(
            "status must be one of: " + ", ".join(OBLIGATION_FULFILMENT_STATUSES)
        )

    project = await _load_project(session, project_id)
    assert_team_access(
        actor,
        project.team_id,
        log=log,
        resource="obligation_fulfilment",
        resource_id=str(project_id),
        deny=lambda: FulfilmentNotFound(f"project {project_id} not found"),
    )
    if not _can_write(actor, project.team_id):
        raise FulfilmentForbidden(
            "recording obligation work requires membership of the project's team"
        )

    obligation_exists = (
        await session.execute(
            select(Obligation.id).where(Obligation.id == obligation_id)
        )
    ).scalar_one_or_none()
    if obligation_exists is None:
        raise FulfilmentNotFound(f"obligation {obligation_id} not found")

    if assignee_user_id is not None:
        await _assert_assignee_is_on_the_team(
            session, assignee_user_id, project.team_id
        )

    row = (
        await session.execute(
            select(ObligationFulfilment)
            .where(
                ObligationFulfilment.project_id == project_id,
                ObligationFulfilment.obligation_id == obligation_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    now = _now()
    if row is None:
        if if_match_version is not None:
            # The caller believed they were updating something. Telling them it
            # is not there is more useful than silently creating it, because
            # the version they held came from somewhere.
            raise FulfilmentNotFound(
                f"no record yet for obligation {obligation_id} on this project"
            )
        row = ObligationFulfilment(
            project_id=project_id,
            obligation_id=obligation_id,
            team_id=project.team_id,
            status=status,
            assignee_user_id=assignee_user_id,
            due_on=due_on,
            evidence_note=evidence_note,
            evidence_url=evidence_url,
            completed_at=now if status == "done" else None,
            completed_by_user_id=actor.id if status == "done" else None,
        )
        session.add(row)
        try:
            await session.flush()
        except IntegrityError as exc:
            await session.rollback()
            session.info.pop("_pending_audit_rows", None)
            raise FulfilmentError(
                "somebody recorded this obligation at the same time; reload "
                "and try again"
            ) from exc
    else:
        if if_match_version is not None and row.version != if_match_version:
            raise FulfilmentEtagMismatch(
                "this record changed since you read it; reload and try again"
            )
        was_done = row.status == "done"
        row.status = status
        row.assignee_user_id = assignee_user_id
        row.due_on = due_on
        row.evidence_note = evidence_note
        row.evidence_url = evidence_url
        row.version += 1
        row.updated_at = now
        if status == "done" and not was_done:
            row.completed_at = now
            row.completed_by_user_id = actor.id
        elif status != "done":
            # Reopened. The completion is cleared rather than kept, because a
            # row that says "not done, finished on Tuesday" is a record nobody
            # can read, and the constraint that done implies a timestamp is
            # only half of saying when something is finished.
            row.completed_at = None
            row.completed_by_user_id = None

    await session.commit()
    await session.refresh(row)
    log.info(
        "obligation_fulfilment_recorded",
        project_id=str(project_id),
        obligation_id=str(obligation_id),
        status=status,
        actor_id=str(actor.id),
    )
    return row


async def list_fulfilments(
    session: AsyncSession,
    actor: CurrentUser,
    *,
    project_id: uuid.UUID,
) -> list[ObligationFulfilment]:
    """Everything recorded for one project."""
    project = await _load_project(session, project_id)
    assert_team_access(
        actor,
        project.team_id,
        log=log,
        resource="obligation_fulfilment",
        resource_id=str(project_id),
        deny=lambda: FulfilmentNotFound(f"project {project_id} not found"),
    )
    rows = (
        await session.execute(
            select(ObligationFulfilment)
            .where(ObligationFulfilment.project_id == project_id)
            .order_by(ObligationFulfilment.updated_at.desc())
        )
    ).scalars()
    return list(rows)


async def clear_fulfilment(
    session: AsyncSession,
    actor: CurrentUser,
    *,
    project_id: uuid.UUID,
    obligation_id: uuid.UUID,
) -> bool:
    """Remove the record, putting the obligation back to nothing recorded.

    Distinct from marking it not-applicable: that is a judgement somebody made
    and is worth keeping. This is for a row created by mistake, and it returns
    the obligation to visibly waiting rather than answered.
    """
    project = await _load_project(session, project_id)
    assert_team_access(
        actor,
        project.team_id,
        log=log,
        resource="obligation_fulfilment",
        resource_id=str(project_id),
        deny=lambda: FulfilmentNotFound(f"project {project_id} not found"),
    )
    if not _can_write(actor, project.team_id):
        raise FulfilmentForbidden(
            "recording obligation work requires membership of the project's team"
        )
    row = (
        await session.execute(
            select(ObligationFulfilment).where(
                ObligationFulfilment.project_id == project_id,
                ObligationFulfilment.obligation_id == obligation_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    await session.delete(row)
    await session.commit()
    log.info(
        "obligation_fulfilment_cleared",
        project_id=str(project_id),
        obligation_id=str(obligation_id),
        actor_id=str(actor.id),
    )
    return True
