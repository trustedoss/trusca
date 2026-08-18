# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Organization rulings over HTTP (prefix ``/v1/organization-verdicts``).

Writing is a deployment administrator's act because the answer reaches every
team, and the gate sits on the route dependency so the permission contract
file records it truthfully. The service checks the same thing again, which is
defence in depth rather than duplication: the contract file is an oracle, and
a gate that lives only inside a service reads there as the route's floor.

Reading is not restricted that way: a component shows as approved in somebody's project
because of a row here, and hiding it would leave them unable to explain their
own screen or to tell an inherited answer from one their team gave.

The effective endpoint answers the question people actually have. A ruling
says what the organization decided; the effective view says what a project
will be judged by and which scope supplied it, and only the second is useful
when you are looking at a component and wondering why it is marked as it is.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.errors import problem_response
from core.pagination import PAGE_MAX
from core.security import CurrentUser, require_role, require_super_admin_or_404
from schemas.organization_verdict import (
    EffectiveVerdictOut,
    OrganizationVerdictAdminListOut,
    OrganizationVerdictAdminOut,
    OrganizationVerdictListOut,
    OrganizationVerdictOpenIn,
    OrganizationVerdictOut,
    OrganizationVerdictTransitionIn,
)
from services.organization_verdict_service import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    OrganizationVerdictError,
    list_verdicts,
    open_verdict,
    resolve_for_project,
    transition_verdict,
)

router = APIRouter(prefix="/v1/organization-verdicts", tags=["organization-verdicts"])
log = structlog.get_logger("organization_verdicts.api")


def _problem_for(request: Request, exc: OrganizationVerdictError) -> Response:
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


@router.post(
    "/org/{organization_id}",
    response_model=OrganizationVerdictAdminOut,
    status_code=status.HTTP_201_CREATED,
    summary="Start an organization-wide ruling on a component",
    responses={
        201: {"description": "Ruling opened. No project changes until it is decided."},
        404: {
            "description": (
                "The caller is not a deployment administrator. 404 rather "
                "than 403, matching the other administrator surfaces: whether "
                "the route exists is not something to confirm by status code."
            )
        },
        409: {"description": "A ruling on this component is already being worked on."},
        422: {"description": "The reason is missing or too short."},
    },
)
async def open_verdict_endpoint(
    request: Request,
    organization_id: uuid.UUID,
    payload: OrganizationVerdictOpenIn,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),
) -> Response:
    try:
        row = await open_verdict(
            session,
            actor,
            organization_id=organization_id,
            component_id=payload.component_id,
            justification=payload.justification,
        )
    except OrganizationVerdictError as exc:
        return _problem_for(request, exc)
    return _json(
        OrganizationVerdictAdminOut.model_validate(row), status.HTTP_201_CREATED
    )


@router.patch(
    "/{verdict_id}",
    response_model=OrganizationVerdictAdminOut,
    summary="Move an organization ruling along",
    responses={
        200: {"description": "Ruling moved. The new version is returned as the ETag."},
        404: {"description": "No such ruling, or the caller does not administer the deployment."},
        409: {"description": "The ruling is decided; open a new one to change it."},
        412: {"description": "The If-Match version is stale; reload and retry."},
        422: {"description": "The workflow does not allow this move."},
    },
)
async def transition_verdict_endpoint(
    request: Request,
    verdict_id: uuid.UUID,
    payload: OrganizationVerdictTransitionIn,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),
) -> Response:
    try:
        row = await transition_verdict(
            session,
            actor,
            verdict_id=verdict_id,
            target_status=payload.status,
            note=payload.note,
            if_match_version=_if_match_version(request),
        )
    except OrganizationVerdictError as exc:
        return _problem_for(request, exc)
    response = _json(
        OrganizationVerdictAdminOut.model_validate(row), status.HTTP_200_OK
    )
    response.headers["ETag"] = f'"{row.version}"'
    return response


def _if_match_version(request: Request) -> int | None:
    """The version the caller believes they are acting on.

    Absent is allowed, and means the caller accepts whatever it finds. That
    matches the per-project approvals, which the UI drives with the ETag it
    was handed and scripts often drive without one.
    """
    raw = request.headers.get("If-Match")
    if raw is None:
        return None
    candidate = raw.strip()
    if candidate == "*":
        # RFC 7232: match any current representation. The row was found by the
        # time this is compared, so "any" is satisfied and there is no version
        # to check. Treating it as unparseable would make a standard HTTP
        # client's most conservative header the one that always fails.
        return None
    # A list is allowed by the RFC; the first entry that parses is the one the
    # caller most recently held.
    for token in candidate.split(","):
        try:
            return int(token.strip().removeprefix("W/").strip('"'))
        except ValueError:
            continue
    # Nothing parsed. This is not a version anybody holds, so it can never
    # match; returning None would silently drop the caller's precondition.
    return -1


@router.get(
    "/org/{organization_id}",
    response_model=OrganizationVerdictListOut,
    summary="The organization's rulings, newest first",
)
async def list_verdicts_endpoint(
    request: Request,
    organization_id: uuid.UUID,
    status_filter: list[str] | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1, le=PAGE_MAX),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("viewer")),
) -> Response:
    """Rulings for one organization, readable by anybody inside it.

    What each caller sees is not the same. The published reason goes to
    everybody, because it is what explains an inherited status on their own
    screen. The deliberation around it, and the names of the people involved,
    go only to callers who could have written them: a note an administrator
    made while deciding is not part of explaining the outcome.
    """
    try:
        rows, total = await list_verdicts(
            session,
            actor,
            organization_id=organization_id,
            status_filter=status_filter,
            page=page,
            page_size=page_size,
        )
    except OrganizationVerdictError as exc:
        return _problem_for(request, exc)
    if actor.is_superuser or actor.role == "super_admin":
        return _json(
            OrganizationVerdictAdminListOut(
                items=[OrganizationVerdictAdminOut.model_validate(r) for r in rows],
                total=total,
                page=page,
                page_size=page_size,
            ),
            status.HTTP_200_OK,
        )
    return _json(
        OrganizationVerdictListOut(
            items=[OrganizationVerdictOut.model_validate(r) for r in rows],
            total=total,
            page=page,
            page_size=page_size,
        ),
        status.HTTP_200_OK,
    )


@router.get(
    "/effective/{project_id}/{component_id}",
    response_model=EffectiveVerdictOut,
    summary="What this project is judged by for this component",
    responses={
        404: {
            "description": (
                "The project does not exist, or the caller is not a member of "
                "its team. Returned in lieu of 403 so membership does not leak."
            )
        },
    },
)
async def effective_verdict_endpoint(
    request: Request,
    project_id: uuid.UUID,
    component_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("viewer")),
) -> Response:
    try:
        resolved = await resolve_for_project(
            session, project_id=project_id, component_id=component_id, actor=actor
        )
    except OrganizationVerdictError as exc:
        return _problem_for(request, exc)
    return _json(
        EffectiveVerdictOut(
            project_id=project_id,
            component_id=component_id,
            status=resolved.status,
            scope=resolved.scope,
            justification=resolved.justification,
        ),
        status.HTTP_200_OK,
    )
