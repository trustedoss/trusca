# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Scheduled scans over HTTP (prefix ``/v1/scan-schedules``, N18).

Reads sit at the lowest grade and writes at the project's team administrator
(or, for the organization default, a super admin), the same split
``/v1/gate-policies`` uses. The route gate is a floor only; whether this
particular caller may write this particular project's schedule is decided in
the service, where the project's team is known.

The effective endpoint answers a different question from the row endpoints. A
row says what one scope decided; the effective view says what will actually
fire for a project, and which scope decided it.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.errors import problem_response
from core.security import CurrentUser, require_role
from schemas.scan_schedule import (
    EffectiveScanScheduleOut,
    ScanScheduleOut,
    ScanScheduleUpsertIn,
)
from services.scan_schedule_service import (
    ScanScheduleForbidden,
    ScanScheduleScopeNotFound,
    delete_project_schedule,
    get_project_schedule,
    resolve_for_project,
    upsert_org_schedule,
    upsert_project_schedule,
)

router = APIRouter(prefix="/v1/scan-schedules", tags=["scan-schedules"])
log = structlog.get_logger("scan_schedules.api")


def _problem_for(request: Request, exc: Exception) -> Response:
    if isinstance(exc, ScanScheduleScopeNotFound):
        return problem_response(
            status_code=status.HTTP_404_NOT_FOUND,
            title="Not Found",
            detail=str(exc),
            instance=request.url.path,
        )
    return problem_response(
        status_code=status.HTTP_403_FORBIDDEN,
        title="Forbidden",
        detail=str(exc),
        instance=request.url.path,
    )


@router.put(
    "/projects/{project_id}",
    response_model=ScanScheduleOut,
    summary="Create or replace a project's own scan schedule",
    responses={
        403: {"description": "Caller does not administer this project's team."},
        404: {"description": "Project not found."},
    },
)
async def upsert_project_schedule_endpoint(
    request: Request,
    project_id: uuid.UUID,
    payload: ScanScheduleUpsertIn,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        row = await upsert_project_schedule(
            session, actor, project_id=project_id, payload=payload
        )
    except (ScanScheduleForbidden, ScanScheduleScopeNotFound) as exc:
        return _problem_for(request, exc)
    return Response(
        content=ScanScheduleOut.model_validate(row).model_dump_json(),
        media_type="application/json",
    )


@router.put(
    "/org/{organization_id}",
    response_model=ScanScheduleOut,
    summary="Create or replace the organization default",
    responses={
        403: {"description": "Only a super admin may set the organization default."},
        404: {"description": "Organization not found."},
    },
)
async def upsert_org_schedule_endpoint(
    request: Request,
    organization_id: uuid.UUID,
    payload: ScanScheduleUpsertIn,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        row = await upsert_org_schedule(
            session, actor, organization_id=organization_id, payload=payload
        )
    except (ScanScheduleForbidden, ScanScheduleScopeNotFound) as exc:
        return _problem_for(request, exc)
    return Response(
        content=ScanScheduleOut.model_validate(row).model_dump_json(),
        media_type="application/json",
    )


@router.get(
    "/projects/{project_id}",
    response_model=ScanScheduleOut,
    summary="Read a project's own schedule row",
    responses={404: {"description": "Project not found, or it has written no schedule."}},
)
async def get_project_schedule_endpoint(
    request: Request,
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("viewer")),
) -> Response:
    try:
        row = await get_project_schedule(session, actor, project_id=project_id)
    except ScanScheduleScopeNotFound as exc:
        return _problem_for(request, exc)
    if row is None:
        # A project with no row of its own is not an error, but this endpoint
        # returns rows: the caller wanting "what applies here" asks the
        # effective endpoint instead.
        return problem_response(
            status_code=status.HTTP_404_NOT_FOUND,
            title="Not Found",
            detail=f"project {project_id} has no scan schedule of its own",
            instance=request.url.path,
        )
    return Response(
        content=ScanScheduleOut.model_validate(row).model_dump_json(),
        media_type="application/json",
    )


@router.delete(
    "/projects/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Drop a project's schedule so it follows its organization default again",
    responses={
        204: {"description": "Row removed."},
        403: {"description": "Caller does not administer this project's team."},
        404: {"description": "Project not found, or it had no schedule of its own."},
    },
)
async def delete_project_schedule_endpoint(
    request: Request,
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        removed = await delete_project_schedule(session, actor, project_id=project_id)
    except (ScanScheduleForbidden, ScanScheduleScopeNotFound) as exc:
        return _problem_for(request, exc)
    if not removed:
        return problem_response(
            status_code=status.HTTP_404_NOT_FOUND,
            title="Not Found",
            detail=f"project {project_id} has no scan schedule of its own",
            instance=request.url.path,
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/effective/{project_id}",
    response_model=EffectiveScanScheduleOut,
    summary="What will actually scan this project on a schedule, if anything",
)
async def effective_schedule_endpoint(
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("viewer")),
) -> EffectiveScanScheduleOut:
    resolved = await resolve_for_project(session, project_id)
    return EffectiveScanScheduleOut(
        project_id=project_id,
        is_active=resolved.is_active,
        cadence=resolved.cadence,
        hour=resolved.hour,
        day_of_week=resolved.day_of_week,
        timezone=resolved.timezone,
        source=resolved.source,
    )
