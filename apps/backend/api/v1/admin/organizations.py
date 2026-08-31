# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Admin organization HTTP routes, self-resource-validation-plan-2026-08-30.md §6-5.

Endpoints under ``/v1/admin/organizations``:
  - GET /v1/admin/organizations, paginated list

Read-only, deliberately. An Organization is created implicitly by
self-signup or OAuth signup (each user's own, for tenant isolation of
org-scoped data), never through an admin-driven API call, so there is no
POST/PATCH/DELETE here. This endpoint exists so a super_admin can discover
the ``organization_id`` that ``POST /v1/admin/teams`` now requires once a
deployment has more than one organization (``services.admin_team_service
.MultipleOrganizationsConfigured``); see that module's docstring for why
the single-organization assumption stopped holding.

Auth: gated by the parent ``admin_router`` super-admin dependency.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.pagination import PAGE_MAX
from core.security import CurrentUser, require_super_admin_or_404
from schemas.admin import AdminOrganizationListPage
from services.admin_team_service import list_organizations

router = APIRouter(prefix="/organizations", tags=["admin"])


@router.get(
    "",
    response_model=AdminOrganizationListPage,
    summary="List organizations (admin), paginated",
)
async def list_organizations_endpoint(
    page: int = Query(default=1, ge=1, le=PAGE_MAX),
    page_size: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),
) -> Response:
    page_obj = await list_organizations(session, actor=actor, page=page, page_size=page_size)
    return Response(
        content=page_obj.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )
