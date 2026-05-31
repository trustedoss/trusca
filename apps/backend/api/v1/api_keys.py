"""
API Key management API — Phase 5 PR #16.

Endpoints:
  POST   /v1/api-keys              Create a new key (returns plaintext ONCE).
  GET    /v1/api-keys              Paginated list of keys visible to caller.
  DELETE /v1/api-keys/{id}         Soft-delete (revoke) a key.

All 4xx / 5xx responses are RFC 7807 ``application/problem+json``.

Auth: every endpoint requires a valid JWT (``require_role("developer")``).
Per-scope authorization is enforced inside :mod:`services.api_key_service` —
the router is a thin HTTP adapter and never makes RBAC decisions of its own.

Plaintext exposure:
  ``POST /v1/api-keys`` returns the wire bearer string (``tos_<prefix>_<secret>``)
  in the ``raw_key`` field. This is the ONLY place the plaintext is ever
  surfaced. The list endpoint omits it (the server cannot recover it from the
  bcrypt hash anyway).
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.errors import problem_response
from core.security import CurrentUser, require_role
from schemas.api_key import (
    APIKeyCreateIn,
    APIKeyCreateOut,
    APIKeyListItem,
    APIKeyListPage,
    APIKeyScope,
)
from services.api_key_service import (
    APIKeyError,
    issue_api_key,
    list_api_keys,
    revoke_api_key,
)

router = APIRouter(prefix="/v1/api-keys", tags=["api-keys"])
log = structlog.get_logger("api_keys.api")


# ---------------------------------------------------------------------------
# Error translation helper
# ---------------------------------------------------------------------------


def _problem_for_api_key_error(request: Request, exc: APIKeyError) -> Response:
    extensions: dict[str, Any] = {}
    return problem_response(
        status_code=exc.status_code,
        title=exc.title,
        detail=str(exc) or exc.title,
        instance=request.url.path,
        **extensions,
    )


# ---------------------------------------------------------------------------
# POST /v1/api-keys
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=APIKeyCreateOut,
    status_code=status.HTTP_201_CREATED,
    summary="Issue a new API key (plaintext returned ONCE in raw_key)",
    responses={
        201: {
            "description": (
                "Key created. ``raw_key`` is the wire bearer string — capture it; "
                "subsequent reads only return metadata."
            )
        },
        403: {"description": "Caller may not issue at the requested scope."},
        404: {"description": "Project not found (when scope='project')."},
        422: {
            "description": (
                "Scope/team_id/project_id combination is invalid "
                "(e.g. scope='team' with no team_id)."
            )
        },
        503: {
            "description": (
                "Could not allocate a unique key prefix after retries — "
                "transient and retryable."
            )
        },
    },
)
async def create_api_key_endpoint(
    request: Request,
    payload: APIKeyCreateIn,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        row, plaintext = await issue_api_key(
            session,
            actor,
            name=payload.name,
            scope=payload.scope,
            team_id=payload.team_id,
            project_id=payload.project_id,
            expires_in_days=payload.expires_in_days,
        )
    except APIKeyError as exc:
        return _problem_for_api_key_error(request, exc)

    body = APIKeyCreateOut(
        id=row.id,
        key_prefix=row.key_prefix,
        name=row.name,
        scope=row.scope,  # type: ignore[arg-type]  # Literal narrowed at the schema layer
        team_id=row.team_id,
        project_id=row.project_id,
        created_by_user_id=row.created_by_user_id,
        created_at=row.created_at,
        expires_at=row.expires_at,
        raw_key=plaintext,
    )
    # Drop the plaintext local immediately after we hand the response back.
    # The variable is no longer needed and explicit del keeps the intent
    # auditable.
    del plaintext
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_201_CREATED,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# GET /v1/api-keys
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=APIKeyListPage,
    summary="Paginated list of API keys visible to the caller",
)
async def list_api_keys_endpoint(
    request: Request,
    scope: APIKeyScope | None = Query(default=None),
    team_id: uuid.UUID | None = Query(default=None),
    project_id: uuid.UUID | None = Query(default=None),
    include_revoked: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        rows, total = await list_api_keys(
            session,
            actor,
            scope=scope,
            team_id=team_id,
            project_id=project_id,
            include_revoked=include_revoked,
            page=page,
            page_size=page_size,
        )
    except APIKeyError as exc:
        return _problem_for_api_key_error(request, exc)

    body = APIKeyListPage(
        items=[APIKeyListItem.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# DELETE /v1/api-keys/{api_key_id}
# ---------------------------------------------------------------------------


@router.delete(
    "/{api_key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke (soft-delete) an API key",
    responses={
        204: {"description": "Key revoked (or was already revoked — idempotent)."},
        403: {
            "description": (
                "Caller can see the key but lacks permission to revoke it "
                "(needs to be the issuer, a team_admin of the key's team, or super_admin)."
            )
        },
        404: {"description": "Key not found, or not visible to the caller."},
    },
)
async def revoke_api_key_endpoint(
    request: Request,
    api_key_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        await revoke_api_key(session, actor, api_key_id)
    except APIKeyError as exc:
        return _problem_for_api_key_error(request, exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
