# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
NOTICE boilerplate templates over HTTP (prefix ``/v1/notice-templates``, N21).

Super-admin only for writes: the boilerplate covers every NOTICE the
deployment generates, not one team's, so the grade that sets it is the one
that answers for the deployment (the same split the scan-schedule and gate
organization defaults use). Reads sit at developer, matching who can already
generate a NOTICE.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, Path, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.errors import problem_response
from core.security import CurrentUser, require_role
from models import NOTICE_TEMPLATE_FORMAT_VALUES
from schemas.notice_template import NoticeTemplateOut, NoticeTemplateUpsertIn
from services.notice_template_service import (
    NoticeTemplateForbidden,
    NoticeTemplateScopeNotFound,
    delete_template,
    get_template,
    upsert_template,
)

router = APIRouter(prefix="/v1/notice-templates", tags=["notice-templates"])
log = structlog.get_logger("notice_templates.api")

_FORMAT_PATTERN = r"^(" + "|".join(NOTICE_TEMPLATE_FORMAT_VALUES) + ")$"


def _problem_for(request: Request, exc: Exception) -> Response:
    if isinstance(exc, NoticeTemplateScopeNotFound):
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
    "/org/{organization_id}/{format}",
    response_model=NoticeTemplateOut,
    summary="Create or replace the organization's NOTICE template for one format",
    responses={
        403: {"description": "Only a super admin may write NOTICE templates."},
        404: {"description": "Organization not found."},
    },
)
async def upsert_template_endpoint(
    request: Request,
    organization_id: uuid.UUID,
    payload: NoticeTemplateUpsertIn,
    format: str = Path(pattern=_FORMAT_PATTERN),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        row = await upsert_template(
            session,
            actor,
            organization_id=organization_id,
            format=format,
            payload=payload,
        )
    except (NoticeTemplateForbidden, NoticeTemplateScopeNotFound) as exc:
        return _problem_for(request, exc)
    return Response(
        content=NoticeTemplateOut.model_validate(row).model_dump_json(),
        media_type="application/json",
    )


@router.get(
    "/org/{organization_id}/{format}",
    response_model=NoticeTemplateOut,
    summary="Read the organization's NOTICE template for one format",
    responses={404: {"description": "The organization has written no template for this format."}},
)
async def get_template_endpoint(
    request: Request,
    organization_id: uuid.UUID,
    format: str = Path(pattern=_FORMAT_PATTERN),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    row = await get_template(session, organization_id=organization_id, format=format)
    if row is None:
        return problem_response(
            status_code=status.HTTP_404_NOT_FOUND,
            title="Not Found",
            detail=f"organization {organization_id} has no {format} NOTICE template",
            instance=request.url.path,
        )
    return Response(
        content=NoticeTemplateOut.model_validate(row).model_dump_json(),
        media_type="application/json",
    )


@router.delete(
    "/org/{organization_id}/{format}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove the organization's NOTICE template for one format",
    responses={
        204: {"description": "Row removed."},
        403: {"description": "Only a super admin may write NOTICE templates."},
        404: {"description": "The organization has written no template for this format."},
    },
)
async def delete_template_endpoint(
    request: Request,
    organization_id: uuid.UUID,
    format: str = Path(pattern=_FORMAT_PATTERN),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        removed = await delete_template(
            session, actor, organization_id=organization_id, format=format
        )
    except NoticeTemplateForbidden as exc:
        return _problem_for(request, exc)
    if not removed:
        return problem_response(
            status_code=status.HTTP_404_NOT_FOUND,
            title="Not Found",
            detail=f"organization {organization_id} has no {format} NOTICE template",
            instance=request.url.path,
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
