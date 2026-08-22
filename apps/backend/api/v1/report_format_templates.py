# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Report formatting templates over HTTP (prefix ``/v1/report-format-templates``, N22).

Super-admin only for writes: this formatting covers every report the
deployment generates, not one team's, matching the NOTICE-template and
scan-schedule organization-default split. Reads sit at developer, matching
who can already download the report.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.errors import problem_response
from core.security import CurrentUser, require_role
from schemas.report_format_template import (
    ReportFormatTemplateOut,
    ReportFormatTemplateUpsertIn,
)
from services.report_format_template_service import (
    ReportFormatTemplateForbidden,
    ReportFormatTemplateScopeNotFound,
    delete_template,
    get_template,
    upsert_template,
)

router = APIRouter(prefix="/v1/report-format-templates", tags=["report-format-templates"])
log = structlog.get_logger("report_format_templates.api")


def _problem_for(request: Request, exc: Exception) -> Response:
    if isinstance(exc, ReportFormatTemplateScopeNotFound):
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
    "/org/{organization_id}",
    response_model=ReportFormatTemplateOut,
    summary="Create or replace the organization's report formatting defaults",
    responses={
        403: {"description": "Only a super admin may write report formatting templates."},
        404: {"description": "Organization not found."},
    },
)
async def upsert_template_endpoint(
    request: Request,
    organization_id: uuid.UUID,
    payload: ReportFormatTemplateUpsertIn,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        row = await upsert_template(
            session, actor, organization_id=organization_id, payload=payload
        )
    except (ReportFormatTemplateForbidden, ReportFormatTemplateScopeNotFound) as exc:
        return _problem_for(request, exc)
    return Response(
        content=ReportFormatTemplateOut.model_validate(row).model_dump_json(),
        media_type="application/json",
    )


@router.get(
    "/org/{organization_id}",
    response_model=ReportFormatTemplateOut,
    summary="Read the organization's report formatting defaults",
    responses={404: {"description": "The organization has written no report formatting row."}},
)
async def get_template_endpoint(
    request: Request,
    organization_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    row = await get_template(session, organization_id=organization_id)
    if row is None:
        return problem_response(
            status_code=status.HTTP_404_NOT_FOUND,
            title="Not Found",
            detail=f"organization {organization_id} has no report formatting row",
            instance=request.url.path,
        )
    return Response(
        content=ReportFormatTemplateOut.model_validate(row).model_dump_json(),
        media_type="application/json",
    )


@router.delete(
    "/org/{organization_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove the organization's report formatting defaults",
    responses={
        204: {"description": "Row removed."},
        403: {"description": "Only a super admin may write report formatting templates."},
        404: {"description": "The organization has written no report formatting row."},
    },
)
async def delete_template_endpoint(
    request: Request,
    organization_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        removed = await delete_template(session, actor, organization_id=organization_id)
    except ReportFormatTemplateForbidden as exc:
        return _problem_for(request, exc)
    if not removed:
        return problem_response(
            status_code=status.HTTP_404_NOT_FOUND,
            title="Not Found",
            detail=f"organization {organization_id} has no report formatting row",
            instance=request.url.path,
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
