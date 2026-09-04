# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Admin user-management HTTP routes — Phase 4 PR #13.

Endpoints under ``/v1/admin/users``:
  - GET    /v1/admin/users                                 — paginated list
  - GET    /v1/admin/users/{user_id}                       — detail
  - PATCH  /v1/admin/users/{user_id}/role                  — change role
  - PATCH  /v1/admin/users/{user_id}/deactivate            — deactivate + revoke refresh
  - PATCH  /v1/admin/users/{user_id}/activate              — re-activate
  - POST   /v1/admin/users/{user_id}/password-reset        — issue reset token (204)
  - POST   /v1/admin/users                                 add one person
  - POST   /v1/admin/users/bulk                            add many, row by row
  - POST   /v1/admin/users/bulk-deactivate                 deactivate many
  - GET    /v1/admin/users/export                          the roster as CSV

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

from core.audit import get_audit_context
from core.db import get_db
from core.errors import problem_response
from core.login_throttle import clear as clear_login_throttle
from core.login_throttle import throttle_keys
from core.pagination import PAGE_MAX
from core.security import CurrentUser, require_super_admin_or_404
from models import AuditLog, User
from schemas.admin import (
    AdminUserCreateIn,
    AdminUserDetail,
    AdminUserListPage,
    AdminUserRoleUpdate,
    BulkDeactivateIn,
    BulkResultOut,
    BulkUserCreateIn,
)
from services.admin_user_service import (
    AdminUserError,
    AdminUserNotFound,
    activate_user,
    bulk_create_users,
    bulk_deactivate_users,
    create_user,
    deactivate_user,
    export_users_csv,
    get_user_detail,
    initiate_password_reset,
    list_users,
    update_user_role,
)
from services.csv_export import CSV_MEDIA_TYPE
from services.mfa_service import clear_for_user as clear_mfa_for_user
from services.notification_service import create_notification

router = APIRouter(prefix="/users", tags=["admin"])
log = structlog.get_logger("admin.users.api")


# NOTE (security review finding — Phase 4 PR #13 review):
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
    page: int = Query(default=1, ge=1, le=PAGE_MAX),
    page_size: int = Query(default=50, ge=1, le=200),
    # Strict enum validation (security review finding — fail-closed): a free-form
    # ``str`` query was previously accepted, with the service silently
    # ignoring values it didn't recognize ("admin", "SUPER_ADMIN", trailing
    # whitespace, ...). FastAPI now rejects anything outside the canonical
    # 3-role set with a 422 BEFORE the service runs.
    role: Literal["super_admin", "team_admin", "developer", "viewer"] | None = Query(default=None),
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


# Declared before ``/{user_id}``: FastAPI matches in declaration order, and
# the other way round "export" is read as a user id and answered 422.
@router.get(
    "/export",
    summary="The roster as CSV, in the shape the bulk import accepts back",
    response_class=Response,
    responses={
        200: {"content": {"text/csv": {}}},
        413: {"description": "More people than one export will build; page the API instead."},
    },
)
async def export_users_endpoint(
    request: Request,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),
) -> Response:
    try:
        body = await export_users_csv(session, actor=actor)
    except AdminUserError as exc:
        # 413 for a roster larger than this will build. Answered before the
        # first row rather than after most of them, because nothing inside a
        # truncated CSV says it is truncated.
        return _problem_for_admin_user_error(request, exc)
    return Response(
        content=body,
        status_code=status.HTTP_200_OK,
        media_type=CSV_MEDIA_TYPE,
        headers={"Content-Disposition": 'attachment; filename="users.csv"'},
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
    summary="Initiate a password reset (admin) — email delivery is wired separately",
)
async def password_reset_endpoint(
    request: Request,
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),
) -> Response:
    """
    Issues a one-shot reset token (bcrypt-hashed in storage) and returns 204.

    A follow-up change wires the SMTP / Slack delivery channel. Until then
    the plaintext token is generated, persisted as a hash, audit-logged via
    the listener (which masks the hash to ``***``), and discarded.

    -- Account-enumeration semantics ------------------------------------------

    This endpoint returns 404 when ``user_id`` does not exist. That IS an
    enumeration oracle in isolation, but it is acceptable HERE because the
    route is super-admin-gated by ``require_super_admin_or_404`` — any
    caller who can reach this code path is already authorised to read the
    full user list (``GET /v1/admin/users``), so the 404 leaks no
    information they did not already have. The trust boundary is ABOVE this
    endpoint, not at it.

    The PUBLIC password-reset flow ("forgot password") MUST NOT copy this
    404-on-miss pattern. That endpoint is unauthenticated, so
    a 404 vs. 204 split there would let an attacker enumerate registered
    emails (CWE-204 Observable Response Discrepancy). The public flow
    returns a uniform 204 regardless of whether the email exists, with the
    actual reset email sent only when a match is found.
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


@router.post(
    "/{user_id}/clear-mfa",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Clear a person's second factor",
)
async def clear_mfa_endpoint(
    request: Request,
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),
) -> Response:
    """
    Undoes somebody's second-factor enrolment: the flag, the secret, the replay
    counter and every unused recovery code, with existing sessions ended.

    This exists because losing an authenticator is the ordinary failure of a
    second factor, and the alternatives do not cover it. A password reset
    cannot: unlocking the factor by mailbox would reduce it to owning the
    mailbox, which is what the first factor already proves. Recovery codes do,
    for anybody who kept them.

    -- What this does not protect against ------------------------------------

    A compromised super admin. That is not new, and it is not created here:
    the same account can already change roles and create users, so it can mint
    itself a fresh super admin with no second factor. What this adds is the
    ability to impersonate one *specific* existing person, and the notification
    below is what narrows that: somebody who did not ask for their factor to be
    cleared finds out that it was.

    Deliberately not two-person: an unlock is reversible, since the owner can
    enrol again, and making somebody who is locked out wait for a second
    approver costs more than it buys.
    """
    user = await session.get(User, user_id)
    if user is None:
        return _problem_for_admin_user_error(request, AdminUserNotFound("user not found"))

    if not user.mfa_enabled and user.mfa_secret_encrypted is None:
        return _problem_for_admin_user_error(
            request, AdminUserNotFound("no second factor to clear")
        )

    # The row first, then the effect. The reason this endpoint exists is that
    # clearing somebody's factor should be reviewable afterwards, and a commit
    # that fails with the factor already gone leaves the one action it was
    # added for unrecorded.
    ctx = get_audit_context()
    session.add(
        AuditLog(
            actor_user_id=actor.id,
            team_id=None,
            target_table="users",
            target_id=str(user_id),
            action="user.mfa_cleared",
            request_id=ctx.get("request_id"),
            ip=ctx.get("ip"),
            user_agent=ctx.get("user_agent"),
            diff={},
        )
    )
    await session.commit()

    await clear_mfa_for_user(session, user=user)

    # Told, not just logged. An audit row is read by whoever goes looking; this
    # reaches the person whose account changed, which is the only way somebody
    # learns their factor was removed without them asking.
    # In-app rather than through the dispatcher: this must not depend on a
    # Celery worker being up, and the person it is for is by definition able to
    # sign in and read it once they enrol again. Written after the clear so a
    # failure here cannot leave a notice about something that did not happen.
    await create_notification(
        session,
        user_id=user_id,
        kind="security",
        title="Your second factor was removed",
        body=(
            "An administrator cleared the authenticator app on your account. "
            "If you did not ask for this, tell your administrator and set up "
            "two-factor sign-in again straight away."
        ),
        link="/profile",
        target_table="users",
        target_id=user_id,
    )
    await session.commit()

    log.info(
        "admin.user.mfa_cleared",
        actor_id=str(actor.id),
        target_user_id=str(user_id),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{user_id}/unlock-sign-in",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Clear a person's failed sign-in count",
)
async def unlock_sign_in_endpoint(
    request: Request,
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),
) -> Response:
    """
    Clears the per-address failed sign-in count so the person can try again.

    Failed sign-ins are counted per address, and a refusal lasts until its
    window runs out. That is a slowdown for somebody guessing and an
    inconvenience for somebody who mistyped, but it is also a way to keep an
    account's owner out on purpose: anybody who knows an email can supply
    failures for it. Two things answer that. Completing a password reset
    clears the count, which needs the inbox and so cannot be blocked by the
    person doing the guessing. This is the other, for somebody who has lost
    access to that inbox.

    It is not otherwise recoverable by hand. The counter is keyed by an HMAC
    of the address, which is deliberate -- Redis then holds no list of who has
    tried to sign in -- and the cost is that an operator cannot find or delete
    one person's key without the deployment secret and a script.

    Super-admin only, and audited: this acts on somebody else's account, and
    clearing a count during an attack is a decision somebody should be able to
    review afterwards.

    Returns 404 for an unknown user, on the same reasoning as the sibling
    password-reset route: the caller can already list every user, so the 404
    tells them nothing new. The public sign-in path must not copy it.
    """
    user = await session.get(User, user_id)
    if user is None:
        return _problem_for_admin_user_error(request, AdminUserNotFound("user not found"))

    # Written by hand rather than by the ORM listener, which only sees row
    # changes: this clears a Redis counter and touches no table, so without
    # this the action would leave no trace at all.
    throttle_key = throttle_keys(user.email)[0]
    ctx = get_audit_context()
    session.add(
        AuditLog(
            actor_user_id=actor.id,
            team_id=None,
            target_table="users",
            target_id=str(user_id),
            action="user.sign_in_unlocked",
            request_id=ctx.get("request_id"),
            ip=ctx.get("ip"),
            user_agent=ctx.get("user_agent"),
            # The key the throttle logs under, so this row can be lined up with
            # the `auth.login_throttled` events it answers. The address itself
            # is not recorded: the counter is keyed by a digest precisely so
            # that nothing accumulates a list of who has tried to sign in.
            diff={"throttle_key": throttle_key},
        )
    )
    await session.commit()

    # After the row, not before it. The endpoint exists so that clearing a count
    # during an attack can be reviewed afterwards, and a commit that fails with
    # the count already gone leaves the one action this route was added for with
    # no record of it. The clear is idempotent and best effort, so it is the
    # half that can safely go second.
    await clear_login_throttle(user.email)

    log.info(
        "admin.user.sign_in_unlocked",
        actor_id=str(actor.id),
        target_user_id=str(user_id),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]


# ---------------------------------------------------------------------------
# Adding people (N4)
#
# The three write routes below all end in ``create_user`` /
# ``deactivate_user``. Nothing here reaches the database on its own, which is
# the property worth stating: a bulk endpoint that writes rows itself would
# skip the password policy, the team check and the audit rows the single path
# produces, and an import is where that goes unnoticed longest.
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=AdminUserDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Add one person",
    responses={
        409: {"description": "That address already has an account."},
        422: {"description": "Weak password, or a team that does not exist."},
    },
)
async def create_user_endpoint(
    request: Request,
    payload: AdminUserCreateIn,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),
) -> Response:
    try:
        detail = await create_user(session, actor=actor, payload=payload)
    except AdminUserError as exc:
        return _problem_for_admin_user_error(request, exc)
    return Response(
        content=detail.model_dump_json(),
        status_code=status.HTTP_201_CREATED,
        media_type="application/json",
    )


@router.post(
    "/bulk",
    response_model=BulkResultOut,
    summary="Add many people, reporting each row",
    responses={
        200: {
            "description": (
                "Every row is reported, including the ones that worked. 200 "
                "even when rows failed: a batch is not all-or-nothing, and a "
                "4xx would say the request was malformed when it was "
                "understood exactly."
            )
        },
    },
)
async def bulk_create_users_endpoint(
    request: Request,  # noqa: ARG001
    payload: BulkUserCreateIn,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),
) -> Response:
    result = await bulk_create_users(session, actor=actor, rows=payload.users)
    return Response(
        content=result.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


@router.post(
    "/bulk-deactivate",
    response_model=BulkResultOut,
    summary="Deactivate many people, reporting each row",
)
async def bulk_deactivate_users_endpoint(
    request: Request,  # noqa: ARG001
    payload: BulkDeactivateIn,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),
) -> Response:
    result = await bulk_deactivate_users(session, actor=actor, user_ids=payload.user_ids)
    return Response(
        content=result.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )
