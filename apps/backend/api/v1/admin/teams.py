# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Admin team-management HTTP routes — Phase 4 PR #13.

Endpoints under ``/v1/admin/teams``:
  - GET    /v1/admin/teams                         — paginated list
  - GET    /v1/admin/teams/{team_id}               — detail w/ members + counts
  - POST   /v1/admin/teams                         — create team
  - PATCH  /v1/admin/teams/{team_id}               — update name/slug/description
  - DELETE /v1/admin/teams/{team_id}               — delete (archives projects first)
  - POST   /v1/admin/teams/{team_id}/members       — add or update membership
  - DELETE /v1/admin/teams/{team_id}/members/{uid} — remove membership
  - POST   /v1/admin/teams/{group_id}/reparent     : move a group (+ subtree, Phase 5)
  - POST   /v1/admin/teams/{parent_id}/subgroups   : create a nested group (Phase 5)

Auth: gated by the parent ``admin_router`` super-admin dependency.
Service-layer 4xx (last-team-admin, team-has-active-scans, slug conflict,
not-found, group-hierarchy cycle/cross-org/slug conflicts) translates to
RFC 7807 Problem Details with snake_case extension fields.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.errors import problem_response
from core.pagination import PAGE_MAX
from core.security import CurrentUser, require_super_admin_or_404
from schemas.admin import (
    AdminGroupCreateSubgroup,
    AdminGroupReparentRequest,
    AdminTeamCreate,
    AdminTeamDetail,
    AdminTeamListPage,
    AdminTeamMemberAdd,
    AdminTeamUpdate,
)
from services.admin_team_service import (
    AdminTeamError,
    add_team_member,
    create_team,
    delete_team,
    get_team_detail,
    list_teams,
    remove_team_member,
    update_team,
)
from services.group_service import GroupHierarchyError, create_subgroup, reparent

router = APIRouter(prefix="/teams", tags=["admin"])
log = structlog.get_logger("admin.teams.api")


def _problem_for_admin_team_error(
    request: Request, exc: AdminTeamError | GroupHierarchyError
) -> Response:
    # See api.v1.admin.users for the rationale behind the cast — keeping
    # the extension-spread pattern consistent across the admin subpackage.
    # Shared between AdminTeamError (admin_team_service) and
    # GroupHierarchyError (group_service, Phase 5 reparent/create_subgroup):
    # both carry the same status_code/title/extensions shape by convention,
    # not by a common base class: group_service deliberately has no
    # dependency on admin_team_service (see group_service's Phase 5 section
    # banner), so this router is where the two vocabularies meet.
    #
    # M3 — PII echo caution: exc.extensions carries structural hints for the
    # admin UI (conflicting_slug, team_id, cycle_detected, etc.). Do NOT add
    # email addresses, full names, or other PII to *.extensions at the call
    # sites in services/admin_team_service.py or services/group_service.py,
    # those values would be reflected here verbatim in the response body and
    # audit log.
    extensions: dict[str, object] = dict(exc.extensions)
    return problem_response(
        status_code=exc.status_code,
        title=exc.title,
        detail=str(exc) or exc.title,
        instance=request.url.path,
        **extensions,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# GET /v1/admin/teams
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=AdminTeamListPage,
    summary="List teams (admin) — paginated, name search",
)
async def list_teams_endpoint(
    request: Request,  # noqa: ARG001
    page: int = Query(default=1, ge=1, le=PAGE_MAX),
    page_size: int = Query(default=50, ge=1, le=200),
    search: str | None = Query(default=None, max_length=255),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),
) -> Response:
    page_obj = await list_teams(
        session,
        actor=actor,
        page=page,
        page_size=page_size,
        search=search,
    )
    return Response(
        content=page_obj.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# GET /v1/admin/teams/{team_id}
# ---------------------------------------------------------------------------


@router.get(
    "/{team_id}",
    response_model=AdminTeamDetail,
    summary="Get one team (admin) — detail with members and project count",
)
async def get_team_endpoint(
    request: Request,
    team_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),
) -> Response:
    try:
        detail = await get_team_detail(session, actor=actor, team_id=team_id)
    except AdminTeamError as exc:
        return _problem_for_admin_team_error(request, exc)

    return Response(
        content=detail.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# POST /v1/admin/teams
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=AdminTeamDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Create a team (admin)",
)
async def create_team_endpoint(
    request: Request,
    payload: AdminTeamCreate,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),
) -> Response:
    try:
        detail = await create_team(session, actor=actor, payload=payload)
    except AdminTeamError as exc:
        return _problem_for_admin_team_error(request, exc)

    return Response(
        content=detail.model_dump_json(),
        status_code=status.HTTP_201_CREATED,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# PATCH /v1/admin/teams/{team_id}
# ---------------------------------------------------------------------------


@router.patch(
    "/{team_id}",
    response_model=AdminTeamDetail,
    summary="Update a team (admin)",
)
async def update_team_endpoint(
    request: Request,
    team_id: uuid.UUID,
    payload: AdminTeamUpdate,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),
) -> Response:
    try:
        detail = await update_team(session, actor=actor, team_id=team_id, payload=payload)
    except AdminTeamError as exc:
        return _problem_for_admin_team_error(request, exc)

    return Response(
        content=detail.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# DELETE /v1/admin/teams/{team_id}
# ---------------------------------------------------------------------------


@router.delete(
    "/{team_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a team (admin) — archives projects, refuses on active scans",
)
async def delete_team_endpoint(
    request: Request,
    team_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),
) -> Response:
    try:
        await delete_team(session, actor=actor, team_id=team_id)
    except AdminTeamError as exc:
        return _problem_for_admin_team_error(request, exc)

    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# POST /v1/admin/teams/{team_id}/members
# ---------------------------------------------------------------------------


@router.post(
    "/{team_id}/members",
    response_model=AdminTeamDetail,
    summary="Add (or update) a team member (admin)",
)
async def add_member_endpoint(
    request: Request,
    team_id: uuid.UUID,
    payload: AdminTeamMemberAdd,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),
) -> Response:
    try:
        detail = await add_team_member(session, actor=actor, team_id=team_id, payload=payload)
    except AdminTeamError as exc:
        return _problem_for_admin_team_error(request, exc)

    return Response(
        content=detail.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# DELETE /v1/admin/teams/{team_id}/members/{user_id}
# ---------------------------------------------------------------------------


@router.delete(
    "/{team_id}/members/{user_id}",
    response_model=AdminTeamDetail,
    summary="Remove a team member (admin)",
)
async def remove_member_endpoint(
    request: Request,
    team_id: uuid.UUID,
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),
) -> Response:
    try:
        detail = await remove_team_member(session, actor=actor, team_id=team_id, user_id=user_id)
    except AdminTeamError as exc:
        return _problem_for_admin_team_error(request, exc)

    return Response(
        content=detail.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# POST /v1/admin/teams/{group_id}/reparent (group-hierarchy Phase 5)
# ---------------------------------------------------------------------------


@router.post(
    "/{group_id}/reparent",
    response_model=AdminTeamDetail,
    summary="Move a group (and its subtree) under a new parent, or to root (admin)",
)
async def reparent_group_endpoint(
    request: Request,
    group_id: uuid.UUID,
    payload: AdminGroupReparentRequest,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),
) -> Response:
    try:
        await reparent(
            session,
            actor=actor,
            group_id=group_id,
            new_parent_id=payload.new_parent_id,
        )
        detail = await get_team_detail(session, actor=actor, team_id=group_id)
    except (AdminTeamError, GroupHierarchyError) as exc:
        return _problem_for_admin_team_error(request, exc)

    return Response(
        content=detail.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# POST /v1/admin/teams/{parent_group_id}/subgroups (group-hierarchy Phase 5)
# ---------------------------------------------------------------------------


@router.post(
    "/{parent_group_id}/subgroups",
    response_model=AdminTeamDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Create a group nested directly under parent_group_id (admin)",
)
async def create_subgroup_endpoint(
    request: Request,
    parent_group_id: uuid.UUID,
    payload: AdminGroupCreateSubgroup,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),
) -> Response:
    try:
        child = await create_subgroup(
            session,
            actor=actor,
            parent_group_id=parent_group_id,
            name=payload.name,
            slug=payload.slug,
            description=payload.description,
        )
        detail = await get_team_detail(session, actor=actor, team_id=child.id)
    except (AdminTeamError, GroupHierarchyError) as exc:
        return _problem_for_admin_team_error(request, exc)

    return Response(
        content=detail.model_dump_json(),
        status_code=status.HTTP_201_CREATED,
        media_type="application/json",
    )


__all__ = ["router"]
