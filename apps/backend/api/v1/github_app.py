"""
GitHub App credential management API — v2.2-b1.

Endpoints (prefix ``/v1/github-app-credentials``, tag ``github-app``):
  POST   ""                                      Register a credential (201).
  GET    ""                                      Paginated list (visible to caller).
  GET    "/{credential_id}"                      Fetch one credential's metadata.
  DELETE "/{credential_id}"                      Revoke (soft-delete) a credential.
  POST   "/{credential_id}/installations"        Link / opt-in an installation.
  GET    "/{credential_id}/installations"        List installations under a credential.
  DELETE "/{credential_id}/installations/{installation_row_id}"  Unlink an installation.

All 4xx / 5xx responses are RFC 7807 ``application/problem+json``.

Auth: every endpoint requires a valid JWT (``require_role("developer")`` floor).
Fine-grained, team-scoped authorization (team_admin for mutations, team member
for reads) is enforced INSIDE ``services.github_app_service`` — the router is a
thin HTTP adapter and never makes RBAC decisions of its own.

Key material: NO endpoint ever returns the PEM private key, the webhook secret,
or any ciphertext. The create / get / list responses are metadata-only
(``GitHubAppCredentialOut`` carries ``has_private_key`` / ``has_webhook_secret``
booleans). Token minting is the b1 FOUNDATION and is intentionally NOT exposed
as an endpoint here (b3 consumes it server-side).
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
from schemas.github_app import (
    GitHubAppCredentialCreateIn,
    GitHubAppCredentialListPage,
    GitHubAppCredentialOut,
    GitHubAppInstallationLinkIn,
    GitHubAppInstallationListPage,
    GitHubAppInstallationOut,
)
from services.github_app_service import (
    GitHubAppError,
    _credential_out_fields,
    get_credential,
    link_installation,
    list_credentials,
    list_installations,
    register_credential,
    revoke_credential,
    unlink_installation,
)

router = APIRouter(prefix="/v1/github-app-credentials", tags=["github-app"])
log = structlog.get_logger("github_app.api")


# ---------------------------------------------------------------------------
# Error translation helper
# ---------------------------------------------------------------------------


def _problem_for_github_app_error(request: Request, exc: GitHubAppError) -> Response:
    extensions: dict[str, Any] = {}
    return problem_response(
        status_code=exc.status_code,
        title=exc.title,
        detail=str(exc) or exc.title,
        instance=request.url.path,
        **extensions,
    )


# ---------------------------------------------------------------------------
# POST /v1/github-app-credentials
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=GitHubAppCredentialOut,
    status_code=status.HTTP_201_CREATED,
    summary="Register a GitHub App credential (private key encrypted at rest)",
    responses={
        201: {"description": "Credential registered. Private key is never returned."},
        403: {"description": "Caller is not a team admin of the target team."},
        409: {"description": "A credential for this (team, app_id) already exists."},
        422: {"description": "Malformed PEM / app_id / metadata, or unusable key."},
    },
)
async def register_credential_endpoint(
    request: Request,
    payload: GitHubAppCredentialCreateIn,
    team_id: uuid.UUID = Query(..., description="The team that owns this credential."),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        row = await register_credential(
            session,
            actor,
            team_id=team_id,
            app_id=payload.app_id,
            app_slug=payload.app_slug,
            private_key=payload.private_key,
            webhook_secret=payload.webhook_secret,
        )
    except GitHubAppError as exc:
        return _problem_for_github_app_error(request, exc)

    body = GitHubAppCredentialOut(**_credential_out_fields(row))
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_201_CREATED,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# GET /v1/github-app-credentials
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=GitHubAppCredentialListPage,
    summary="Paginated list of GitHub App credentials visible to the caller",
)
async def list_credentials_endpoint(
    request: Request,
    team_id: uuid.UUID | None = Query(default=None),
    include_revoked: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        rows, total = await list_credentials(
            session,
            actor,
            team_id=team_id,
            include_revoked=include_revoked,
            page=page,
            page_size=page_size,
        )
    except GitHubAppError as exc:
        return _problem_for_github_app_error(request, exc)

    body = GitHubAppCredentialListPage(
        items=[GitHubAppCredentialOut(**_credential_out_fields(r)) for r in rows],
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
# GET /v1/github-app-credentials/{credential_id}
# ---------------------------------------------------------------------------


@router.get(
    "/{credential_id}",
    response_model=GitHubAppCredentialOut,
    summary="Fetch one GitHub App credential's metadata",
    responses={404: {"description": "Not found, or not visible to the caller."}},
)
async def get_credential_endpoint(
    request: Request,
    credential_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        row = await get_credential(session, actor, credential_id)
    except GitHubAppError as exc:
        return _problem_for_github_app_error(request, exc)

    body = GitHubAppCredentialOut(**_credential_out_fields(row))
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# DELETE /v1/github-app-credentials/{credential_id}
# ---------------------------------------------------------------------------


@router.delete(
    "/{credential_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke (soft-delete) a GitHub App credential",
    responses={
        204: {"description": "Revoked (or already revoked — idempotent)."},
        403: {"description": "Caller is not a team admin of the credential's team."},
        404: {"description": "Not found, or not visible to the caller."},
    },
)
async def revoke_credential_endpoint(
    request: Request,
    credential_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        await revoke_credential(session, actor, credential_id)
    except GitHubAppError as exc:
        return _problem_for_github_app_error(request, exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# POST /v1/github-app-credentials/{credential_id}/installations
# ---------------------------------------------------------------------------


@router.post(
    "/{credential_id}/installations",
    response_model=GitHubAppInstallationOut,
    status_code=status.HTTP_201_CREATED,
    summary="Link (opt-in) an installation under a credential",
    responses={
        201: {"description": "Installation linked (idempotent on re-link)."},
        403: {
            "description": ("Caller is not a team admin, or the project belongs to another team.")
        },
        404: {"description": "Credential or project not found / not visible."},
    },
)
async def link_installation_endpoint(
    request: Request,
    credential_id: uuid.UUID,
    payload: GitHubAppInstallationLinkIn,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        row = await link_installation(
            session,
            actor,
            credential_id,
            installation_id=payload.installation_id,
            account_login=payload.account_login,
            repository_full_name=payload.repository_full_name,
            project_id=payload.project_id,
        )
    except GitHubAppError as exc:
        return _problem_for_github_app_error(request, exc)

    body = GitHubAppInstallationOut.model_validate(row)
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_201_CREATED,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# GET /v1/github-app-credentials/{credential_id}/installations
# ---------------------------------------------------------------------------


@router.get(
    "/{credential_id}/installations",
    response_model=GitHubAppInstallationListPage,
    summary="List installations under a credential",
    responses={404: {"description": "Credential not found / not visible."}},
)
async def list_installations_endpoint(
    request: Request,
    credential_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        rows, total = await list_installations(
            session, actor, credential_id, page=page, page_size=page_size
        )
    except GitHubAppError as exc:
        return _problem_for_github_app_error(request, exc)

    body = GitHubAppInstallationListPage(
        items=[GitHubAppInstallationOut.model_validate(r) for r in rows],
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
# DELETE /v1/github-app-credentials/{credential_id}/installations/{installation_row_id}
# ---------------------------------------------------------------------------


@router.delete(
    "/{credential_id}/installations/{installation_row_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Unlink an installation from a credential",
    responses={
        204: {"description": "Unlinked (idempotent — absent link is a no-op)."},
        403: {"description": "Caller is not a team admin of the credential's team."},
        404: {"description": "Credential not found / not visible."},
    },
)
async def unlink_installation_endpoint(
    request: Request,
    credential_id: uuid.UUID,
    installation_row_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        await unlink_installation(session, actor, credential_id, installation_row_id)
    except GitHubAppError as exc:
        return _problem_for_github_app_error(request, exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
