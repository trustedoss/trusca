# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Requesting, approving and tracking user anonymisation (ER32).

The erasure itself is not here. It runs as an operator command with the owner
database role, because the runtime containers deliberately never receive that
role (``docker-compose.yml``: "a runtime RCE cannot DROP TRIGGER on
audit_logs"), and the scrub function that reaches inside ``audit_logs`` is not
executable by the application. What lives here is everything around it: who
asked, who agreed, when it expires, and which requests are still waiting.

The gap this module has to make visible
---------------------------------------
Because approval happens in the product and execution happens on a server,
there is a state in between: approved, agreed by two people, and not yet done.
Nothing in the system is wrong while a request sits there, which is exactly
why it is dangerous. An erasure request usually carries a statutory deadline,
and a request that everybody approved and nobody ran will pass that deadline
with every screen looking healthy.

So "approved but not executed" is a first-class query here
(:func:`list_awaiting_execution`), surfaced on the admin screen rather than
left for somebody to think to look for.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from models import AuditLog, User, UserAnonymisationRequest
from models.user_anonymisation_request import (
    ANONYMISATION_APPROVED,
    ANONYMISATION_CANCELLED,
    ANONYMISATION_EXECUTED,
    ANONYMISATION_EXPIRED,
    ANONYMISATION_PENDING,
)

log = structlog.get_logger("services.user_anonymisation")

#: How long an undecided request stays approvable. Fixed rather than
#: configurable: a deployment that widened it would be widening the window in
#: which an irreversible act sits half-authorised, and a deployment that set it
#: to something enormous would have a permanent standing approval nobody
#: remembers granting.
REQUEST_TTL = timedelta(days=7)


class AnonymisationError(Exception):
    """Base for refusals this module raises."""


class SubjectNotFound(AnonymisationError):
    """No such user, or the user is already anonymised."""


class RequestConflict(AnonymisationError):
    """A live request already exists for this subject."""


class NotApprovable(AnonymisationError):
    """The request cannot be acted on. See the subclasses for which reason.

    Kept as the base so a caller that does not care can catch one thing, while
    the API can still tell a reader which of three unrelated situations they
    are in. They share a status code and ask for completely different actions.
    """


class RequestNotFound(NotApprovable):
    """No such request, or it is no longer in a state this action accepts."""


class RequestExpired(NotApprovable):
    """The approval window closed. A fresh request has to be opened."""


class SelfApproval(NotApprovable):
    """The actor may not approve this: they opened it, or they are the subject."""


@dataclass(frozen=True)
class AwaitingExecution:
    """One approved request nobody has run yet, and how long it has waited."""

    request_id: uuid.UUID
    subject_user_id: uuid.UUID
    # Both parties travel with the entry. The operator about to run an
    # irreversible command needs to know WHO asked and WHO agreed, because a
    # row saying "approved" is only a row: anything that can write to this
    # table can produce one. These are the names an operator can check against
    # people before acting.
    requested_by_user_id: uuid.UUID
    approved_by_user_id: uuid.UUID | None
    approved_at: datetime
    waiting_days: int


async def open_request(
    session: AsyncSession,
    *,
    subject_user_id: uuid.UUID,
    requested_by_user_id: uuid.UUID,
    reason: str | None = None,
    now: datetime | None = None,
) -> UserAnonymisationRequest:
    """Open a pending request. The caller must already be a super-admin.

    Refuses a request against oneself. That is not a courtesy check: the whole
    control is that two different people agree, and a self-request would leave
    only one other person to convince.
    """
    moment = now or datetime.now(tz=UTC)
    if subject_user_id == requested_by_user_id:
        raise SelfApproval("a user cannot request their own anonymisation")

    subject = await session.get(User, subject_user_id)
    if subject is None:
        raise SubjectNotFound(str(subject_user_id))

    # A pending request past its window still occupies the partial unique
    # index, so without this the seven-day TTL would lock the subject out
    # rather than release them: nobody could approve the stale request and
    # nobody could open a fresh one. Expiry is applied here, at the point
    # where staleness does damage, instead of by a scheduled task that would
    # be one more thing to run and one more thing to notice had stopped.
    await _expire_stale_for(session, subject_user_id=subject_user_id, now=moment)

    row = UserAnonymisationRequest(
        subject_user_id=subject_user_id,
        requested_by_user_id=requested_by_user_id,
        state=ANONYMISATION_PENDING,
        reason=(reason or None),
        expires_at=moment + REQUEST_TTL,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        # The partial unique index. Two open requests would let two requesters
        # each find their own approver and reach the same erasure twice.
        raise RequestConflict(str(subject_user_id)) from exc

    # Committed here rather than left to the caller, matching
    # ``transition_approval_service``. ``get_db`` yields a session and closes
    # it without committing, so a flush-only service returns a populated
    # object over HTTP and persists nothing: the route answers 201 with a real
    # id for a request that will not exist a moment later.
    await session.commit()

    log.info(
        "anonymisation_requested",
        request_id=str(row.id),
        subject_user_id=str(subject_user_id),
        requested_by_user_id=str(requested_by_user_id),
    )
    return row


async def approve(
    session: AsyncSession,
    *,
    request_id: uuid.UUID,
    approved_by_user_id: uuid.UUID,
    now: datetime | None = None,
) -> UserAnonymisationRequest:
    """Second-person approval. Still does not erase anything.

    The three-party separation is enforced by CHECK constraints as well as
    here, so a future second call site cannot skip it by forgetting to.
    """
    moment = now or datetime.now(tz=UTC)
    row = await session.get(UserAnonymisationRequest, request_id)
    if row is None or row.state != ANONYMISATION_PENDING:
        raise RequestNotFound(str(request_id))
    if row.expires_at <= moment:
        row.state = ANONYMISATION_EXPIRED
        # Committed before raising. The refusal is the caller's answer, but
        # retiring the row is a change the system decided to make, and over
        # HTTP an uncommitted one is discarded: the row would stay pending and
        # keep the subject's slot in the partial unique index.
        await session.commit()
        raise RequestExpired(f"request {request_id} expired at {row.expires_at}")
    if approved_by_user_id in (row.requested_by_user_id, row.subject_user_id):
        raise SelfApproval(
            "the approver must be someone other than the requester and the subject"
        )

    row.state = ANONYMISATION_APPROVED
    row.approved_by_user_id = approved_by_user_id
    row.approved_at = moment
    await session.commit()

    # WARNING rather than INFO: from here the deployment owes somebody an
    # erasure that only a human running a command will deliver.
    log.warning(
        "anonymisation_approved_awaiting_execution",
        request_id=str(row.id),
        subject_user_id=str(row.subject_user_id),
    )
    return row


async def cancel(
    session: AsyncSession, *, request_id: uuid.UUID, now: datetime | None = None
) -> UserAnonymisationRequest:
    """Withdraw a request that has not been executed."""
    row = await session.get(UserAnonymisationRequest, request_id)
    if row is None or row.state not in (ANONYMISATION_PENDING, ANONYMISATION_APPROVED):
        raise RequestNotFound(str(request_id))
    row.state = ANONYMISATION_CANCELLED
    await session.commit()
    log.info("anonymisation_cancelled", request_id=str(row.id))
    return row


async def list_awaiting_execution(
    session: AsyncSession, *, now: datetime | None = None
) -> list[AwaitingExecution]:
    """Approved requests nobody has run yet, oldest first.

    The admin screen renders this. A request here is not an error and not a
    warning about the software: it is work the deployment owes a person, and
    the only thing that will discharge it is somebody running the operator
    command. Sorting oldest first puts whatever is closest to a deadline at
    the top.
    """
    moment = now or datetime.now(tz=UTC)
    rows = (
        (
            await session.execute(
                select(UserAnonymisationRequest)
                .where(UserAnonymisationRequest.state == ANONYMISATION_APPROVED)
                .order_by(UserAnonymisationRequest.approved_at.asc())
            )
        )
        .scalars()
        .all()
    )
    out: list[AwaitingExecution] = []
    for row in rows:
        approved_at = row.approved_at or row.created_at
        out.append(
            AwaitingExecution(
                request_id=row.id,
                subject_user_id=row.subject_user_id,
                requested_by_user_id=row.requested_by_user_id,
                approved_by_user_id=row.approved_by_user_id,
                approved_at=approved_at,
                waiting_days=max(0, (moment - approved_at).days),
            )
        )
    return out


def _select_and_expire_due(
    session: Session,
    *,
    now: datetime,
    subject_user_id: uuid.UUID | None = None,
) -> list[UserAnonymisationRequest]:
    """Sync core: select ``pending`` rows past their window and mark them
    ``expired`` in the identity map. Neither flushes nor commits.

    Only ``pending`` rows. An approved request never expires: it is a
    commitment two people made, and quietly retiring it would erase the very
    backlog :func:`list_awaiting_execution` exists to show. Somebody has to
    cancel that one deliberately.

    ``subject_user_id=None`` sweeps every subject - the global beat
    (``tasks.anonymisation_expiry_sweep``) needs that shape, since a subject
    nobody touches again after opening a request never triggers the
    per-subject callers below.

    Written sync-first, on purpose, and called from two directions rather
    than written twice:

      - the async API path (:func:`expire_due_requests`) drives it through
        ``AsyncSession.run_sync``, which runs a plain callback against the
        session's underlying sync ``Session`` inside the greenlet bridge -
        the documented way to do ORM work that has no async form without
        opening a second engine;
      - the Celery beat (``tasks.anonymisation_expiry_sweep``) is a sync
        worker with a plain sync ``Session`` from ``core.db.sync_session_scope``
        and calls this directly.

    One predicate, one place it is written (hardening rule #2): a future
    change to what "due" means cannot update one path and miss the other.
    """
    stmt = select(UserAnonymisationRequest).where(
        UserAnonymisationRequest.state == ANONYMISATION_PENDING,
        UserAnonymisationRequest.expires_at <= now,
    )
    if subject_user_id is not None:
        stmt = stmt.where(UserAnonymisationRequest.subject_user_id == subject_user_id)
    rows = session.execute(stmt).scalars().all()
    for row in rows:
        row.state = ANONYMISATION_EXPIRED
    return list(rows)


async def expire_due_requests(
    session: AsyncSession,
    *,
    now: datetime,
    subject_user_id: uuid.UUID | None = None,
) -> list[UserAnonymisationRequest]:
    """Async-session wrapper around :func:`_select_and_expire_due`.

    Given a subject it does exactly what the old ``_expire_stale_for`` did,
    and :func:`_expire_stale_for` still exists as that named, flush-and-log
    wrapper so ``open_request`` / ``approve`` are unchanged.

    Neither flushes nor commits - same contract as before: the per-subject
    callers flush inline (see :func:`_expire_stale_for`) because they act on
    the row in the same transaction, and the global sweep owns and commits
    its own session.
    """
    return await session.run_sync(
        lambda sync_session: _select_and_expire_due(
            sync_session, now=now, subject_user_id=subject_user_id
        )
    )


async def _expire_stale_for(
    session: AsyncSession, *, subject_user_id: uuid.UUID, now: datetime
) -> int:
    """Retire this subject's undecided requests that are past their window.

    Thin wrapper around :func:`expire_due_requests` scoped to one subject,
    kept as its own name because ``open_request`` and ``approve`` call it at
    the point where staleness would otherwise do damage (see their call
    sites), not on a schedule.
    """
    rows = await expire_due_requests(session, now=now, subject_user_id=subject_user_id)
    if rows:
        await session.flush()
        log.info(
            "anonymisation_requests_expired",
            subject_user_id=str(subject_user_id),
            count=len(rows),
        )
    return len(rows)


async def approved_request_for(
    session: AsyncSession, *, subject_user_id: uuid.UUID
) -> UserAnonymisationRequest | None:
    """The approved, unexecuted request for a subject, if there is one.

    The operator command reads this before doing anything. The database also
    checks it inside ``audit_logs_scrub_pii``, so this is the readable half of
    a rule that is enforced whether or not the caller consults it.
    """
    return (
        (
            await session.execute(
                select(UserAnonymisationRequest)
                .where(UserAnonymisationRequest.subject_user_id == subject_user_id)
                .where(UserAnonymisationRequest.state == ANONYMISATION_APPROVED)
            )
        )
        .scalars()
        .first()
    )


async def corroborating_audit_actions(
    session: AsyncSession, *, request_id: uuid.UUID
) -> set[str]:
    """Which audit actions were recorded against this request row.

    Why an operator command consults this at all: ``user_anonymisation_requests``
    is a table the application role can INSERT into and UPDATE, because that is
    how the two-person flow works. Anything that reaches SQL through the
    application can therefore write a row that says ``approved`` without any
    person having decided anything, and that row appears on the operator's
    backlog looking exactly like a real one.

    Requests opened and approved through the API pass through the ORM, so the
    audit listener records a ``create`` and an ``update`` against the row. A
    row conjured by direct SQL has neither. That is not proof of consent, and
    it is not claimed to be: an attacker who can INSERT here can also INSERT
    into ``audit_logs``. What it does is remove the cheapest version of the
    attack and leave evidence of the more expensive one, in a table that
    cannot be edited afterwards.

    The control that actually decides is the two people, which is why the
    backlog carries their ids for the operator to check.
    """
    rows = (
        await session.execute(
            select(AuditLog.action).where(
                AuditLog.target_table == "user_anonymisation_requests",
                AuditLog.target_id == str(request_id),
            )
        )
    ).scalars().all()
    return set(rows)


async def mark_executed(
    session: AsyncSession,
    *,
    request_id: uuid.UUID,
    now: datetime | None = None,
) -> UserAnonymisationRequest:
    """Record that the erasure ran.

    Without this the same request can be executed twice, and more importantly
    it would stay on the awaiting-execution list forever, so the one screen
    that shows outstanding obligations would show a permanent false one and
    stop being read.
    """
    moment = now or datetime.now(tz=UTC)
    row = await session.get(UserAnonymisationRequest, request_id)
    if row is None or row.state != ANONYMISATION_APPROVED:
        raise RequestNotFound(str(request_id))
    row.state = ANONYMISATION_EXECUTED
    row.executed_at = moment
    # Flush, not commit: unlike the routes above, the only caller is the
    # operator command, which is midway through erasing an account and owns
    # the transaction. Committing here would end that transaction before the
    # caller decided the whole erasure had succeeded.
    await session.flush()
    log.warning(
        "anonymisation_executed",
        request_id=str(row.id),
        subject_user_id=str(row.subject_user_id),
    )
    return row


__all__ = [
    "REQUEST_TTL",
    "AnonymisationError",
    "AwaitingExecution",
    "NotApprovable",
    "RequestExpired",
    "RequestNotFound",
    "SelfApproval",
    "RequestConflict",
    "SubjectNotFound",
    "approve",
    "approved_request_for",
    "cancel",
    "corroborating_audit_actions",
    "expire_due_requests",
    "list_awaiting_execution",
    "mark_executed",
    "open_request",
]
