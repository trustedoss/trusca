# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Group read API — group-hierarchy Phase 4 PR 4-A.

Endpoints under ``/v1/groups``:
  - GET /v1/groups                    — list (flat search via ``q``, or
                                         drill-down via ``parent_id``)
  - GET /v1/groups/{group_id}         — detail: ancestors + 30-day subtree
                                         summary
  - GET /v1/groups/{group_id}/members — direct vs. cascade-inherited members

The first non-admin surface over ``groups`` — until this PR every group
read went through ``/v1/admin/teams`` (super_admin only, ``require_super_
admin_or_404``). List and detail are ``role>=viewer``: any authenticated
member reads the groups the accessible-set predicate lets them see. Members
is ``role>=developer`` — it is the one route here that hands out member
identities (email/full_name), not just counts, matching the precedent
``GET /v1/projects/{project_id}/assignable-members`` already set (see that
route's own gate and ``tests/contracts/viewer-target-matrix.json``, which
denies both to viewer). Enumeration and information-leak concerns
(existence-hide on detail/members, the narrow breadcrumb shape) are handled
entirely in ``services.group_directory_service`` / ``schemas.group`` — this
router only translates that service's domain exceptions into RFC 7807.

Security-reviewer pointers (this PR's own summary calls these out too):
  - ``GET /{group_id}`` and ``GET /{group_id}/members`` 404 uniformly for
    "does not exist" and "exists, not accessible" — see
    ``group_directory_service._load_accessible_group``.
  - ``GET /`` never emits a row for a group outside the caller's accessible
    set — see ``group_directory_service._actor_visibility_predicate``.
  - The detail response's ``ancestors`` field is typed
    ``list[GroupBreadcrumbEntry]`` (id/name/slug only, enforced by that
    model's own field set, not by router-side trimming) — see
    ``schemas.group``'s module docstring.
  - ``ancestors`` (and a project's ``group_path``) deliberately name every
    ancestor up to the root even when one of them is outside the caller's
    own accessible set — a position indicator, not an enumeration
    primitive. See ``group_directory_service``'s module docstring for why
    this doesn't weaken the existence-hide guarantee above.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import group_search_rate_limit
from core.db import get_db
from core.errors import problem_response
from core.pagination import PAGE_MAX
from core.ratelimit import _authenticated_user_key, limiter
from core.security import CurrentUser, require_role
from schemas.group import GroupDetail, GroupListPage, GroupMembersResponse
from services.group_directory_service import (
    GroupError,
    get_group_detail,
    get_group_members,
    list_groups,
)

router = APIRouter(prefix="/v1/groups", tags=["groups"])
log = structlog.get_logger("groups.api")


def _problem_for_group_error(request: Request, exc: GroupError) -> Response:
    return problem_response(
        status_code=exc.status_code,
        title=exc.title,
        detail=str(exc) or exc.title,
        instance=request.url.path,
    )


# ---------------------------------------------------------------------------
# GET /v1/groups
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=GroupListPage,
    summary="List groups visible to the caller (flat search or one-level drill-down)",
)
# group-hierarchy Phase 6 security review: this had no rate limit at all
# before the project-creation combobox turned the `q` mode into a
# per-keystroke live search (see group_search_rate_limit's own docstring).
# Applies to the whole endpoint, drill-down included -- see that docstring
# for why splitting the two modes across separate routes isn't worth it
# for one cost difference.
@limiter.limit(group_search_rate_limit, key_func=_authenticated_user_key)
async def list_groups_endpoint(
    request: Request,  # noqa: ARG001
    q: str | None = Query(
        default=None,
        max_length=255,
        description=(
            "Flat, whole-tree name search. When set, `parent_id` is ignored — "
            "a match is returned regardless of which branch it lives under."
        ),
    ),
    parent_id: uuid.UUID | None = Query(
        default=None,
        description=(
            "Drill-down mode (only consulted when `q` is unset). Omitted "
            "returns root groups; set, returns that group's direct children. "
            "An inaccessible or nonexistent parent_id returns an empty page, "
            "not a 404 — the list surface never confirms a hidden group's "
            "existence."
        ),
    ),
    page: int = Query(default=1, ge=1, le=PAGE_MAX),
    page_size: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("viewer")),
) -> Response:
    body = await list_groups(
        session,
        actor=actor,
        q=q,
        parent_id=parent_id,
        page=page,
        page_size=page_size,
    )
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# GET /v1/groups/{group_id}
# ---------------------------------------------------------------------------


@router.get(
    "/{group_id}",
    response_model=GroupDetail,
    summary="Group detail: ancestors + 30-day subtree activity summary",
    responses={
        404: {
            "description": (
                "The group does not exist, or the caller cannot reach it "
                "(existence-hidden — both cases return the same body)."
            ),
            "content": {"application/problem+json": {}},
        },
    },
)
async def get_group_endpoint(
    request: Request,
    group_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("viewer")),
) -> Response:
    try:
        body = await get_group_detail(session, actor=actor, group_id=group_id)
    except GroupError as exc:
        return _problem_for_group_error(request, exc)

    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# GET /v1/groups/{group_id}/members
# ---------------------------------------------------------------------------


@router.get(
    "/{group_id}/members",
    response_model=GroupMembersResponse,
    summary="Group members — direct memberships vs. cascade-inherited ones",
    responses={
        404: {
            "description": (
                "The group does not exist, or the caller cannot reach it "
                "(existence-hidden — same body as the detail endpoint's 404)."
            ),
            "content": {"application/problem+json": {}},
        },
    },
)
async def get_group_members_endpoint(
    request: Request,
    group_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    # role>=developer, NOT viewer, unlike the list/detail routes above — this
    # is the one route in this router that returns member identities (email +
    # full_name). Mirrors GET /v1/projects/{project_id}/assignable-members,
    # the existing precedent for "a route that hands out who's on a team is
    # gated a grade above the routes that just say how many": see
    # tests/contracts/viewer-target-matrix.json, which denies both to viewer.
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        body = await get_group_members(session, actor=actor, group_id=group_id)
    except GroupError as exc:
        return _problem_for_group_error(request, exc)

    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


__all__ = ["router"]
