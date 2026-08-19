# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
The ask-before-using queue over HTTP (prefix ``/v1/intake-requests``).

Off by default, and off means 404 rather than 403. Whether an organization
reviews a package before it is pulled in or after a scan finds it is a decision
about how they work; a route that answers "you may not" would tell somebody
they lack permission for a feature their organization has not adopted, and they
would go and ask for the permission.

Filing is open to any member of the project's team, including the lowest grade,
because the person who wants to add a dependency is usually not the person who
decides. Answering is a team administrator's act, matching who disposes an
approval after a scan: the same judgement about the same package.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.errors import problem_response
from core.security import CurrentUser, require_role
from schemas.component_intake import (
    IntakeRequestCreateIn,
    IntakeRequestListOut,
    IntakeRequestOut,
    IntakeRequestTransitionIn,
)
from services.component_intake_service import (
    IntakeError,
    list_requests,
    open_request,
    transition_request,
)

router = APIRouter(prefix="/v1/intake-requests", tags=["intake-requests"])
log = structlog.get_logger("component_intake.api")


def _problem_for(request: Request, exc: IntakeError) -> Response:
    return problem_response(
        status_code=exc.status_code,
        title=exc.title,
        detail=str(exc) or exc.title,
        instance=request.url.path,
    )


def _json(model: object, code: int) -> Response:
    return Response(
        content=model.model_dump_json(),  # type: ignore[attr-defined]
        media_type="application/json",
        status_code=code,
    )


def _if_match_version(request: Request) -> int | None:
    raw = request.headers.get("If-Match")
    if raw is None:
        return None
    candidate = raw.strip()
    if candidate == "*":
        return None
    for token in candidate.split(","):
        try:
            return int(token.strip().removeprefix("W/").strip('"'))
        except ValueError:
            continue
    return -1


@router.post(
    "",
    response_model=IntakeRequestOut,
    status_code=status.HTTP_201_CREATED,
    summary="Ask to use a package",
    responses={
        201: {"description": "Recorded. Nothing about the project changes yet."},
        404: {
            "description": (
                "This deployment does not use an intake queue, or the project "
                "is not the caller's to see. One code for both: whether the "
                "feature is on is not worth telling somebody who cannot use it."
            )
        },
        409: {"description": "Somebody is already asking about this package here."},
        422: {"description": "The purl is malformed, or the reason is too thin."},
    },
)
async def open_endpoint(
    request: Request,
    payload: IntakeRequestCreateIn,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("viewer")),
) -> Response:
    try:
        row = await open_request(
            session,
            actor,
            project_id=payload.project_id,
            purl=payload.purl,
            justification=payload.justification,
        )
    except IntakeError as exc:
        return _problem_for(request, exc)
    return _json(IntakeRequestOut.model_validate(row), status.HTTP_201_CREATED)


@router.patch(
    "/{request_id}",
    response_model=IntakeRequestOut,
    summary="Answer a request, or move it along",
    responses={
        200: {"description": "Moved. The new version is returned as the ETag."},
        403: {"description": "Caller does not administer the project's team."},
        404: {"description": "Not on in this deployment, or no such request."},
        409: {"description": "Already answered; open a new request instead."},
        412: {"description": "The If-Match version is stale; reload and retry."},
        422: {"description": "The workflow does not allow this move."},
    },
)
async def transition_endpoint(
    request: Request,
    request_id: uuid.UUID,
    payload: IntakeRequestTransitionIn,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("viewer")),
) -> Response:
    try:
        row = await transition_request(
            session,
            actor,
            request_id=request_id,
            target_status=payload.status,
            note=payload.note,
            if_match_version=_if_match_version(request),
        )
    except IntakeError as exc:
        return _problem_for(request, exc)
    response = _json(IntakeRequestOut.model_validate(row), status.HTTP_200_OK)
    response.headers["ETag"] = f'"{row.version}"'
    return response


@router.get(
    "",
    response_model=IntakeRequestListOut,
    summary="Requests in the caller's teams, oldest first",
    responses={404: {"description": "This deployment does not use an intake queue."}},
)
async def list_endpoint(
    request: Request,
    project_id: uuid.UUID | None = Query(default=None),
    status_filter: list[str] | None = Query(default=None, alias="status"),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("viewer")),
) -> Response:
    try:
        rows = await list_requests(
            session, actor, project_id=project_id, status_filter=status_filter
        )
    except IntakeError as exc:
        return _problem_for(request, exc)
    items = [IntakeRequestOut.model_validate(row) for row in rows]
    return _json(IntakeRequestListOut(items=items, total=len(items)), status.HTTP_200_OK)
