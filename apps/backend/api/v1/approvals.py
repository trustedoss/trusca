"""
Component Approval Workflow API — Phase 4 PR #15.

Endpoints:
  GET    /v1/approvals                         Paginated list (team-scoped)
  GET    /v1/approvals/{id}                    Single approval + ETag header
  POST   /v1/approvals                         Open a new approval request
  PATCH  /v1/approvals/{id}/transition         Transition status (If-Match required)
  DELETE /v1/approvals/{id}                    Delete a non-terminal approval

Auth: JWT required on every endpoint (``require_role("developer")`` minimum).
RBAC enforcement is in the service layer — the router is a thin HTTP adapter.

ETag pattern (MEMORY ``feedback_optimistic_concurrency_pattern``):
  - GET /v1/approvals/{id} returns ``ETag: "{version}"`` header.
  - PATCH /v1/approvals/{id}/transition requires ``If-Match: "{version}"`` header.
    If the header is absent → 400.
    If the header version does not match the row → 412 (approval_etag_mismatch).

All 4xx / 5xx responses are ``application/problem+json`` (RFC 7807).
No bare ``HTTPException`` is raised from endpoint handlers; domain exceptions are
translated by ``_problem_for_approval_error`` using ``problem_response``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.errors import problem_response
from core.security import CurrentUser, require_role
from schemas.approvals import (
    ApprovalCreateIn,
    ApprovalListPage,
    ApprovalOut,
    ApprovalTransitionIn,
)
from services.component_approval_service import (
    ApprovalAlreadyOpen,
    ApprovalError,
    ApprovalEtagMismatch,
    ApprovalForbidden,
    ApprovalInvalidTransition,
    ApprovalTerminalState,
    create_approval,
    delete_approval,
    get_approval,
    list_approvals,
    transition_approval,
)

router = APIRouter(prefix="/v1/approvals", tags=["approvals"])
log = structlog.get_logger("approvals.api")


# ---------------------------------------------------------------------------
# Error translation helper
# ---------------------------------------------------------------------------


def _problem_for_approval_error(request: Request, exc: ApprovalError) -> Response:
    """Translate an approval domain exception into an RFC 7807 problem response."""
    extensions: dict[str, Any] = {}

    if isinstance(exc, ApprovalInvalidTransition):
        extensions = {
            "approval_invalid_transition": True,
            "allowed_to": exc.allowed_to,
        }
    elif isinstance(exc, ApprovalAlreadyOpen):
        extensions = {"approval_already_open": True}
    elif isinstance(exc, ApprovalEtagMismatch):
        extensions = {"approval_etag_mismatch": True}
    elif isinstance(exc, ApprovalTerminalState):
        extensions = {"approval_terminal_state": True}

    return problem_response(
        status_code=exc.status_code,
        title=exc.title,
        detail=str(exc) or exc.title,
        instance=request.url.path,
        **extensions,
    )


# ---------------------------------------------------------------------------
# GET /v1/approvals
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=ApprovalListPage,
    summary="Paginated list of approval requests (team-scoped; super_admin sees all)",
)
async def list_approvals_endpoint(
    request: Request,
    status_filter: str | None = Query(
        default=None,
        alias="status",
        pattern=r"^(pending|under_review|approved|rejected)$",
    ),
    team_id: uuid.UUID | None = Query(default=None),
    requested_by_user_id: uuid.UUID | None = Query(default=None),
    from_dt: datetime | None = Query(default=None),
    to_dt: datetime | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        rows, total = await list_approvals(
            session,
            actor,
            status_filter=status_filter,
            team_id=team_id,
            requested_by_user_id=requested_by_user_id,
            from_dt=from_dt,
            to_dt=to_dt,
            page=page,
            page_size=page_size,
        )
    except ApprovalError as exc:
        return _problem_for_approval_error(request, exc)

    body = ApprovalListPage(
        items=[ApprovalOut.from_list_row(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# GET /v1/approvals/{approval_id}
# ---------------------------------------------------------------------------


@router.get(
    "/{approval_id}",
    response_model=ApprovalOut,
    summary="Single approval detail — includes ETag header for optimistic concurrency",
    responses={
        200: {
            "description": "Approval found. ETag header contains the current version.",
            "headers": {"ETag": {"description": "Current version as quoted string"}},
        },
        404: {"description": "Approval not found, or not visible to the caller."},
    },
)
async def get_approval_endpoint(
    request: Request,
    approval_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        row = await get_approval(session, actor, approval_id)
    except ApprovalError as exc:
        return _problem_for_approval_error(request, exc)

    body = ApprovalOut.model_validate(row)
    headers = {"ETag": f'"{row.version}"'}
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
        headers=headers,
    )


# ---------------------------------------------------------------------------
# POST /v1/approvals
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=ApprovalOut,
    status_code=status.HTTP_201_CREATED,
    summary="Open a new approval request for a component in a project",
    responses={
        201: {"description": "Approval created."},
        404: {"description": "Project or component not found, or caller lacks team access."},
        409: {
            "description": (
                "An open approval already exists for this component + project. "
                "``approval_already_open = true`` extension in the Problem Details body."
            )
        },
    },
)
async def create_approval_endpoint(
    request: Request,
    payload: ApprovalCreateIn,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        row = await create_approval(
            session,
            actor,
            component_id=payload.component_id,
            project_id=payload.project_id,
        )
    except ApprovalError as exc:
        return _problem_for_approval_error(request, exc)

    body = ApprovalOut.model_validate(row)
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_201_CREATED,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# PATCH /v1/approvals/{approval_id}/transition
# ---------------------------------------------------------------------------


@router.patch(
    "/{approval_id}/transition",
    response_model=ApprovalOut,
    summary="Transition an approval's status (requires If-Match header)",
    responses={
        200: {"description": "Approval transitioned. Body is the post-commit approval."},
        400: {"description": "If-Match header is missing or cannot be parsed."},
        403: {
            "description": (
                "Caller's role is insufficient for the requested transition "
                "(under_review / approved / rejected require team_admin or super_admin)."
            )
        },
        404: {"description": "Approval not found, or not visible to the caller."},
        409: {
            "description": (
                "Transition is not permitted by the state machine. "
                "``approval_invalid_transition = true`` + ``allowed_to`` extension "
                "lists the valid next states."
            )
        },
        412: {
            "description": (
                "If-Match version did not match the row's current version. "
                "Re-fetch the approval and retry."
            )
        },
    },
)
async def transition_approval_endpoint(
    request: Request,
    approval_id: uuid.UUID,
    payload: ApprovalTransitionIn,
    if_match: str | None = Header(default=None, alias="if-match"),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    # If-Match is mandatory for this endpoint.
    if if_match is None:
        return problem_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            title="If-Match Required",
            detail="The If-Match header (ETag version) is required for this operation.",
            instance=request.url.path,
        )

    # Parse the quoted version integer from the ETag string.
    # The ETag header is sent as a quoted string: `"3"`. Strip surrounding quotes.
    etag_raw = if_match.strip().strip('"')
    try:
        version_int = int(etag_raw)
    except ValueError:
        return problem_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            title="Invalid If-Match Header",
            detail=f"Cannot parse If-Match value as an integer version: {if_match!r}",
            instance=request.url.path,
        )

    try:
        row = await transition_approval(
            session,
            actor,
            approval_id,
            action=payload.action,
            decision_note=payload.decision_note,
            if_match=version_int,
        )
    except ApprovalForbidden as exc:
        return _problem_for_approval_error(request, exc)
    except ApprovalError as exc:
        return _problem_for_approval_error(request, exc)

    body = ApprovalOut.model_validate(row)
    headers = {"ETag": f'"{row.version}"'}
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
        headers=headers,
    )


# ---------------------------------------------------------------------------
# DELETE /v1/approvals/{approval_id}
# ---------------------------------------------------------------------------


@router.delete(
    "/{approval_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a non-terminal approval (original requester, team_admin, or super_admin)",
    responses={
        204: {"description": "Approval deleted."},
        404: {"description": "Approval not found, or not visible / deletable by the caller."},
        409: {
            "description": (
                "Approval is in a terminal state (approved / rejected) and cannot be deleted. "
                "``approval_terminal_state = true`` extension."
            )
        },
    },
)
async def delete_approval_endpoint(
    request: Request,
    approval_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        await delete_approval(session, actor, approval_id)
    except ApprovalError as exc:
        return _problem_for_approval_error(request, exc)

    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
