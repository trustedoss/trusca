# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Transition approvals over HTTP (prefix ``/v1/transition-approvals``).

Three routes, deliberately separate from the transition endpoint itself. A
PATCH that silently opened a request instead of doing what it was asked would
report success for a change that had not happened; here the caller asks for a
request explicitly, somebody else answers it explicitly, and the finding moves
on the second call.

The queue is readable at the lowest grade because who is waiting on what is
something an auditor reads, and deciding is held at the team's administrator
because agreeing to accept a risk is an administrator's act.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.errors import problem_response
from core.security import CurrentUser, require_role
from schemas.transition_approval import (
    TransitionApprovalDecisionIn,
    TransitionApprovalListOut,
    TransitionApprovalOut,
    TransitionApprovalRequestIn,
)
from services.project_service import ProjectError
from services.transition_approval_service import (
    ApprovalAlreadyDecided,
    ApprovalAlreadyOpen,
    ApprovalForbidden,
    ApprovalNotFound,
    ApprovalNotRequired,
    ApprovalSelfDecision,
    TransitionApprovalError,
    decide_and_apply,
    list_pending_for_teams,
    request_transition,
)
from services.vulnerability_service import VulnerabilityError

router = APIRouter(prefix="/v1/transition-approvals", tags=["transition-approvals"])
log = structlog.get_logger("transition_approvals.api")

#: Which failure gets which code. Self-decision is 403 rather than 409 because
#: no state has to change for the caller to succeed: they are the wrong person,
#: and they will still be the wrong person after anything they could do.
_STATUS_FOR: dict[type[Exception], int] = {
    ApprovalNotFound: status.HTTP_404_NOT_FOUND,
    ApprovalSelfDecision: status.HTTP_403_FORBIDDEN,
    ApprovalForbidden: status.HTTP_403_FORBIDDEN,
    ApprovalAlreadyOpen: status.HTTP_409_CONFLICT,
    ApprovalAlreadyDecided: status.HTTP_409_CONFLICT,
    ApprovalNotRequired: status.HTTP_409_CONFLICT,
}

_TITLE_FOR: dict[int, str] = {
    status.HTTP_404_NOT_FOUND: "Not Found",
    status.HTTP_403_FORBIDDEN: "Forbidden",
    status.HTTP_409_CONFLICT: "Conflict",
}

#: A stable token per failure, carried as a Problem extension.
#:
#: ``detail`` is written in English and always will be, so a UI that renders it
#: puts English in front of a Korean reader on the one screen where the reason
#: matters most. Two of these also share a status code: refusing to decide your
#: own request and lacking the grade are both 403, and they ask the reader to do
#: entirely different things. The token lets the client say the right sentence
#: in the right language.
_REASON_FOR: dict[type[Exception], str] = {
    ApprovalSelfDecision: "self_decision",
    ApprovalForbidden: "not_team_admin",
    ApprovalNotRequired: "approval_not_required",
    ApprovalAlreadyOpen: "already_open",
    ApprovalAlreadyDecided: "already_decided",
    ApprovalNotFound: "not_found",
}


def _problem_for(request: Request, exc: Exception) -> Response:
    """Render a domain failure as Problem Details.

    Domain errors raised by the vulnerability service (an illegal transition,
    a justification that is too thin) reach here when a request is opened, and
    they keep the codes that endpoint already uses so a client does not have to
    learn two vocabularies for the same refusal.
    """
    if isinstance(exc, VulnerabilityError | ProjectError):
        return problem_response(
            status_code=exc.status_code,
            title=exc.title,
            detail=str(exc) or exc.title,
            instance=request.url.path,
        )
    code = _STATUS_FOR.get(type(exc), status.HTTP_409_CONFLICT)
    title = _TITLE_FOR.get(code, "Conflict")
    return problem_response(
        status_code=code,
        title=title,
        detail=str(exc) or title,
        instance=request.url.path,
        # Exact type, not isinstance: ApprovalSelfDecision subclasses
        # ApprovalForbidden, and walking the hierarchy would label it with the
        # parent's token and lose the distinction this exists for.
        reason=_REASON_FOR.get(type(exc), "conflict"),
    )


def _json(model: TransitionApprovalOut | TransitionApprovalListOut, code: int) -> Response:
    return Response(
        content=model.model_dump_json(),
        media_type="application/json",
        status_code=code,
    )


@router.post(
    "",
    response_model=TransitionApprovalOut,
    status_code=status.HTTP_201_CREATED,
    summary="Ask for a status change that needs a second person",
    responses={
        201: {"description": "Request recorded. The finding has not moved."},
        403: {"description": "Caller may not make this transition even with agreement."},
        404: {
            "description": (
                "Finding does not exist, or the caller is not a member of its "
                "team. Returned in lieu of 403 so membership does not leak."
            )
        },
        409: {
            "description": (
                "The policy does not ask for approval on this status (make the "
                "change directly), or a request for this finding is already open."
            )
        },
        422: {"description": "Transition is not legal from the current status."},
    },
)
async def request_transition_endpoint(
    request: Request,
    payload: TransitionApprovalRequestIn,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        row = await request_transition(
            session,
            actor,
            finding_id=payload.finding_id,
            target_status=payload.target_status,
            justification=payload.justification,
        )
    except (TransitionApprovalError, VulnerabilityError, ProjectError) as exc:
        return _problem_for(request, exc)
    return _json(TransitionApprovalOut.model_validate(row), status.HTTP_201_CREATED)


@router.post(
    "/{approval_id}/decision",
    response_model=TransitionApprovalOut,
    summary="Agree to or refuse a request somebody else opened",
    responses={
        200: {
            "description": (
                "Decision recorded. On approval the finding has been "
                "transitioned, audited like any other status change."
            )
        },
        403: {
            "description": (
                "Caller opened this request, or does not administer the team. "
                "The requester may not decide their own request."
            )
        },
        404: {"description": "No such request, or it is not the caller's to see."},
        409: {"description": "The request has already been decided."},
    },
)
async def decide_endpoint(
    request: Request,
    approval_id: uuid.UUID,
    payload: TransitionApprovalDecisionIn,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        row = await decide_and_apply(
            session,
            actor,
            approval_id=approval_id,
            approve=payload.approve,
            note=payload.note,
        )
    except (TransitionApprovalError, VulnerabilityError, ProjectError) as exc:
        return _problem_for(request, exc)
    return _json(TransitionApprovalOut.model_validate(row), status.HTTP_200_OK)


@router.get(
    "",
    response_model=TransitionApprovalListOut,
    summary="Requests waiting on a decision, for the caller's teams",
)
async def list_pending_endpoint(
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("viewer")),
) -> Response:
    """Scoped to the caller's own teams, so the queue never shows another team's work."""
    rows = await list_pending_for_teams(
        session, team_ids=list(actor.team_ids), all_teams=actor.is_superuser
    )
    items = [TransitionApprovalOut.model_validate(row) for row in rows]
    return _json(
        TransitionApprovalListOut(items=items, total=len(items)), status.HTTP_200_OK
    )
