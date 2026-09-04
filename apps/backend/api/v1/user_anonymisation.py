# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
User anonymisation requests over HTTP (prefix ``/v1/user-anonymisation``).

Four routes, and none of them erase anything. Opening, approving and
cancelling a request are the parts that belong in the product, where two
people can see what they are agreeing to. The erasure itself runs as an
operator command on a server, because the database role that can reach inside
``audit_logs`` is deliberately not the one the application holds.

That split is why ``GET /awaiting-execution`` exists and why the admin screen
renders it. Between approval and execution the system is in a state where
nothing looks wrong and somebody is still waiting: two people agreed, the
subject's data is intact, and the only thing that will finish the job is a
person running a command. Erasure requests usually carry a statutory deadline,
so a backlog nobody can see is the failure this endpoint is here to prevent.

Every route is super-admin only, behind ``require_super_admin_or_404`` so that
a lower grade cannot learn from a 403 that a given user has a request open.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.errors import problem_response
from core.security import CurrentUser, require_super_admin_or_404
from schemas.user_anonymisation import (
    AnonymisationRequestIn,
    AnonymisationRequestOut,
    AwaitingExecutionListOut,
    AwaitingExecutionOut,
)
from services.user_anonymisation_service import (
    AnonymisationError,
    NotApprovable,
    RequestConflict,
    RequestExpired,
    RequestNotFound,
    SelfApproval,
    SubjectNotFound,
    approve,
    cancel,
    list_awaiting_execution,
    open_request,
)

router = APIRouter(prefix="/v1/user-anonymisation", tags=["user-anonymisation"])
log = structlog.get_logger("user_anonymisation.api")

_STATUS_FOR: dict[type[Exception], int] = {
    SubjectNotFound: status.HTTP_404_NOT_FOUND,
    RequestConflict: status.HTTP_409_CONFLICT,
    RequestNotFound: status.HTTP_409_CONFLICT,
    RequestExpired: status.HTTP_409_CONFLICT,
    SelfApproval: status.HTTP_409_CONFLICT,
    NotApprovable: status.HTTP_409_CONFLICT,
}

_TITLE_FOR: dict[int, str] = {
    status.HTTP_404_NOT_FOUND: "Not Found",
    status.HTTP_409_CONFLICT: "Conflict",
}

#: A stable token per failure, carried as a Problem extension. ``detail`` is
#: English and always will be; the token is what lets a Korean UI say the right
#: sentence. The three ``NotApprovable`` shapes share a status code and ask the
#: reader to do completely different things (open a fresh request; find a
#: different approver; nothing, it is already gone), so each carries its own.
#:
#: Looked up by exact type, not isinstance: all three subclass
#: ``NotApprovable``, and walking the hierarchy would label every one of them
#: with the parent's token and undo the distinction.
_REASON_FOR: dict[type[Exception], str] = {
    SubjectNotFound: "subject_not_found",
    RequestConflict: "request_already_open",
    RequestNotFound: "request_not_found",
    RequestExpired: "request_expired",
    SelfApproval: "self_approval",
    NotApprovable: "not_approvable",
}


def _problem(request: Request, exc: AnonymisationError) -> Response:
    code = _STATUS_FOR.get(type(exc), status.HTTP_409_CONFLICT)
    title = _TITLE_FOR.get(code, "Conflict")
    return problem_response(
        status_code=code,
        title=title,
        detail=str(exc) or title,
        instance=request.url.path,
        reason=_REASON_FOR.get(type(exc), "conflict"),
    )


def _json(model: AnonymisationRequestOut | AwaitingExecutionListOut, code: int) -> Response:
    return Response(
        content=model.model_dump_json(),
        media_type="application/json",
        status_code=code,
    )


@router.post(
    "",
    response_model=AnonymisationRequestOut,
    status_code=status.HTTP_201_CREATED,
    summary="Ask for a user's data to be anonymised",
    responses={
        201: {
            "description": (
                "Request recorded. Nothing has been erased and nothing will be "
                "until a second super admin approves and an operator runs the "
                "command."
            )
        },
        404: {"description": "No such user."},
        409: {
            "description": (
                "A request for this subject is already open, or the caller "
                "named themselves as the subject."
            )
        },
    },
)
async def open_request_endpoint(
    request: Request,
    payload: AnonymisationRequestIn,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),
) -> Response:
    try:
        row = await open_request(
            session,
            subject_user_id=payload.subject_user_id,
            requested_by_user_id=actor.id,
            reason=payload.reason,
        )
    except AnonymisationError as exc:
        return _problem(request, exc)
    return _json(
        AnonymisationRequestOut.model_validate(row), status.HTTP_201_CREATED
    )


@router.post(
    "/{request_id}/approval",
    response_model=AnonymisationRequestOut,
    summary="Agree to a request somebody else opened",
    responses={
        200: {
            "description": (
                "Approved. The erasure has NOT run: it is now waiting for an "
                "operator, and appears under /awaiting-execution until it does."
            )
        },
        409: {
            "description": (
                "No such request, or it is not pending, or it has expired, or "
                "the caller opened it. The approver must be a third party to "
                "both the requester and the subject."
            )
        },
    },
)
async def approve_endpoint(
    request: Request,
    request_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),
) -> Response:
    try:
        row = await approve(
            session, request_id=request_id, approved_by_user_id=actor.id
        )
    except AnonymisationError as exc:
        return _problem(request, exc)
    return _json(AnonymisationRequestOut.model_validate(row), status.HTTP_200_OK)


@router.delete(
    "/{request_id}",
    response_model=AnonymisationRequestOut,
    summary="Withdraw a request that has not run",
    responses={
        200: {"description": "Cancelled. Approved-but-unexecuted requests may also be cancelled."},
        409: {"description": "No such request, or it has already been executed."},
    },
)
async def cancel_endpoint(
    request: Request,
    request_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),
) -> Response:
    try:
        row = await cancel(session, request_id=request_id)
    except AnonymisationError as exc:
        return _problem(request, exc)
    log.info(
        "anonymisation_cancelled_via_api",
        request_id=str(request_id),
        actor_user_id=str(actor.id),
    )
    return _json(AnonymisationRequestOut.model_validate(row), status.HTTP_200_OK)


@router.get(
    "/awaiting-execution",
    response_model=AwaitingExecutionListOut,
    summary="Approved requests no operator has run yet",
    responses={
        200: {
            "description": (
                "The backlog, oldest first. A non-empty list is not an error "
                "and not a fault in the software: it is work the deployment "
                "owes people, and only an operator running the command "
                "discharges it."
            )
        },
    },
)
async def awaiting_execution_endpoint(
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),
) -> Response:
    rows = await list_awaiting_execution(session)
    payload = AwaitingExecutionListOut(
        items=[
            AwaitingExecutionOut(
                request_id=row.request_id,
                subject_user_id=row.subject_user_id,
                requested_by_user_id=row.requested_by_user_id,
                approved_by_user_id=row.approved_by_user_id,
                approved_at=row.approved_at,
                waiting_days=row.waiting_days,
            )
            for row in rows
        ],
        count=len(rows),
    )
    return _json(payload, status.HTTP_200_OK)
