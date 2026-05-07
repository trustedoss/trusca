"""
Admin user-management HTTP routes — Phase 4 PR #13.

Endpoints under ``/v1/admin/users``:
  - GET    /v1/admin/users                                 — paginated list
  - GET    /v1/admin/users/{user_id}                       — detail
  - PATCH  /v1/admin/users/{user_id}/role                  — change role
  - PATCH  /v1/admin/users/{user_id}/deactivate            — deactivate + revoke refresh
  - PATCH  /v1/admin/users/{user_id}/activate              — re-activate
  - POST   /v1/admin/users/{user_id}/password-reset        — issue reset token (204)

Auth: every route is gated by the parent ``admin_router`` super-admin
dependency. Anonymous calls get 401; non-super-admin authenticated calls
get 404 (existence-hide). Service-layer 4xx (last-super-admin / cannot-modify-self
/ not-found) translates to RFC 7807 Problem Details with snake_case
extension fields.
"""

from __future__ import annotations

import uuid
from typing import Literal

import structlog
from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.errors import problem_response
from core.security import CurrentUser, require_super_admin_or_404
from schemas.admin import (
    AdminUserDetail,
    AdminUserListPage,
    AdminUserRoleUpdate,
)
from services.admin_user_service import (
    AdminUserError,
    activate_user,
    deactivate_user,
    get_user_detail,
    initiate_password_reset,
    list_users,
    update_user_role,
)

router = APIRouter(prefix="/users", tags=["admin"])
log = structlog.get_logger("admin.users.api")


# NOTE (security-reviewer F12 — Phase 4 PR #13 review):
#   ``detail = str(exc)`` is safe for the CURRENT admin-user error set because
#   every caller raises one of the typed ``AdminUserError`` subclasses defined
#   in ``services.admin_user_service`` with a controlled, hand-written
#   message string (no DB row data, no user-supplied content). The detail is
#   admin-only too — the route is ``require_super_admin_or_404`` gated.
#
#   Future contributors MUST NOT propagate raw DB or driver exception
#   messages through this translator. SQLAlchemy / Postgres error strings
#   can include schema names, constraint names, and (for unique-violation
#   shapes on PII columns) the offending value itself — surfacing those to
#   the client is a CWE-209 leak.
#
#   For Phase 6 PR #18 PUBLIC password-reset flow (and any other
#   unauthenticated surface): use a sanitised, hand-written detail string
#   only. Do NOT copy this translator unchanged. The trust boundary is
#   different there.
def _problem_for_admin_user_error(request: Request, exc: AdminUserError) -> Response:
    """Translate an AdminUserError into an RFC 7807 response with extensions."""
    # Pass extensions through as **kwargs so each surfaces as a top-level
    # snake_case field in the problem+json body (e.g. last_super_admin_protected,
    # cannot_modify_self, team_id from F9). The cast keeps mypy happy:
    # ``problem_response`` declares ``**extensions: object`` and pin-typed
    # dicts confuse the spread-conflict heuristic against the named
    # ``instance`` arg.
    extensions: dict[str, object] = dict(exc.extensions)
    return problem_response(
        status_code=exc.status_code,
        title=exc.title,
        detail=str(exc) or exc.title,
        instance=request.url.path,
        **extensions,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# GET /v1/admin/users
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=AdminUserListPage,
    summary="List users (admin) — paginated, filterable",
)
async def list_users_endpoint(
    request: Request,  # noqa: ARG001
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    # Strict enum validation (security-reviewer F3 — fail-closed): a free-form
    # ``str`` query was previously accepted, with the service silently
    # ignoring values it didn't recognize ("admin", "SUPER_ADMIN", trailing
    # whitespace, ...). FastAPI now rejects anything outside the canonical
    # 3-role set with a 422 BEFORE the service runs.
    role: Literal["super_admin", "team_admin", "developer"] | None = Query(default=None),
    active: bool | None = Query(default=None),
    search: str | None = Query(default=None, max_length=255),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),
) -> Response:
    page_obj = await list_users(
        session,
        actor=actor,
        page=page,
        page_size=page_size,
        role=role,
        active=active,
        search=search,
    )
    return Response(
        content=page_obj.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# GET /v1/admin/users/{user_id}
# ---------------------------------------------------------------------------


@router.get(
    "/{user_id}",
    response_model=AdminUserDetail,
    summary="Get one user (admin) — detail with memberships + scan count",
)
async def get_user_endpoint(
    request: Request,
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),
) -> Response:
    try:
        detail = await get_user_detail(session, actor=actor, user_id=user_id)
    except AdminUserError as exc:
        return _problem_for_admin_user_error(request, exc)

    return Response(
        content=detail.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# PATCH /v1/admin/users/{user_id}/role
# ---------------------------------------------------------------------------


@router.patch(
    "/{user_id}/role",
    response_model=AdminUserDetail,
    summary="Change a user's role (admin)",
)
async def update_user_role_endpoint(
    request: Request,
    user_id: uuid.UUID,
    payload: AdminUserRoleUpdate,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),
) -> Response:
    try:
        detail = await update_user_role(session, actor=actor, user_id=user_id, payload=payload)
    except AdminUserError as exc:
        return _problem_for_admin_user_error(request, exc)

    return Response(
        content=detail.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# PATCH /v1/admin/users/{user_id}/deactivate
# ---------------------------------------------------------------------------


@router.patch(
    "/{user_id}/deactivate",
    response_model=AdminUserDetail,
    summary="Deactivate a user (admin) — revokes refresh tokens",
)
async def deactivate_user_endpoint(
    request: Request,
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),
) -> Response:
    try:
        detail = await deactivate_user(session, actor=actor, user_id=user_id)
    except AdminUserError as exc:
        return _problem_for_admin_user_error(request, exc)

    return Response(
        content=detail.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# PATCH /v1/admin/users/{user_id}/activate
# ---------------------------------------------------------------------------


@router.patch(
    "/{user_id}/activate",
    response_model=AdminUserDetail,
    summary="Re-activate a user (admin)",
)
async def activate_user_endpoint(
    request: Request,
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),
) -> Response:
    try:
        detail = await activate_user(session, actor=actor, user_id=user_id)
    except AdminUserError as exc:
        return _problem_for_admin_user_error(request, exc)

    return Response(
        content=detail.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# POST /v1/admin/users/{user_id}/password-reset
# ---------------------------------------------------------------------------


@router.post(
    "/{user_id}/password-reset",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Initiate a password reset (admin) — Phase 6 wires email delivery",
)
async def password_reset_endpoint(
    request: Request,
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),
) -> Response:
    """
    Issues a one-shot reset token (bcrypt-hashed in storage) and returns 204.

    Phase 6 PR #18 will wire the SMTP / Slack delivery channel. Until then
    the plaintext token is generated, persisted as a hash, audit-logged via
    the listener (which masks the hash to ``***``), and discarded.

    -- Account-enumeration semantics (security-reviewer F5) -------------------

    This endpoint returns 404 when ``user_id`` does not exist. That IS an
    enumeration oracle in isolation, but it is acceptable HERE because the
    route is super-admin-gated by ``require_super_admin_or_404`` — any
    caller who can reach this code path is already authorised to read the
    full user list (``GET /v1/admin/users``), so the 404 leaks no
    information they did not already have. The trust boundary is ABOVE this
    endpoint, not at it.

    The Phase 6 PR #18 PUBLIC password-reset flow ("forgot password") MUST
    NOT copy this 404-on-miss pattern. That endpoint is unauthenticated, so
    a 404 vs. 204 split there would let an attacker enumerate registered
    emails (CWE-204 Observable Response Discrepancy). The public flow
    returns a uniform 204 regardless of whether the email exists, with the
    actual reset email sent only when a match is found. See
    ``docs/v2-execution-plan.md`` §3.7 for the Phase 6 contract.
    """
    try:
        reset_token_id = await initiate_password_reset(session, actor=actor, user_id=user_id)
    except AdminUserError as exc:
        return _problem_for_admin_user_error(request, exc)

    log.info(
        "admin.user.password_reset_initiated",
        actor_id=str(actor.id),
        target_user_id=str(user_id),
        reset_token_id=str(reset_token_id),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
