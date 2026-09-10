# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Admin routes for ticket-tracker logins (#385).

Endpoints under ``/v1/admin/organizations/{organization_id}/ticket-credentials``:
  - GET    list an organization's credentials (never the tokens)
  - PUT    create or replace the credential for one tracker host
  - DELETE remove one

Super-admin only, matching ``registry_credentials``: a tracker login is
deployment infrastructure, and the blast radius of a leaked one is every
issue that tracker holds.

The token is write-only, same as ``registry_credentials.password``: it goes
in on ``PUT`` and is never returned by any route.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.errors import problem_response
from core.security import CurrentUser, require_super_admin_or_404
from models import DEFAULT_AUTH_SCHEME
from services.ticket_credential_service import (
    SUPPORTED_AUTH_SCHEMES,
    AuthSchemeNotSupported,
    OrganizationNotFound,
    TicketCredentialError,
    delete_credential,
    list_credentials,
    upsert_credential,
)

router = APIRouter(prefix="/organizations", tags=["admin"])
log = structlog.get_logger("admin.ticket_credentials.api")


class TicketCredentialIn(BaseModel):
    """Write model. ``api_token`` is accepted and never returned."""

    host: str = Field(
        min_length=1,
        max_length=255,
        description=(
            "Tracker host as it appears in a finding's `ticket_url`: "
            "`mycompany.atlassian.net`. A pasted `https://mycompany."
            "atlassian.net/` is normalised to the bare host, because the "
            "read-time lookup uses the host parsed out of that URL and "
            "would otherwise never match."
        ),
    )
    auth_scheme: str = Field(
        default=DEFAULT_AUTH_SCHEME,
        description=f"One of {sorted(SUPPORTED_AUTH_SCHEMES)}.",
    )
    username: str | None = Field(
        default=None,
        max_length=255,
        description=(
            "The tracker account's login (Jira Cloud: its email). Required "
            "for `jira_basic`."
        ),
    )
    api_token: str = Field(
        min_length=1,
        description=(
            "Tracker API token or password. Stored as Fernet ciphertext and "
            "never returned by any route."
        ),
    )


class TicketCredentialOut(BaseModel):
    """Read model. Deliberately has no token field at all."""

    id: uuid.UUID
    host: str
    auth_scheme: str
    username: str | None


class TicketCredentialListOut(BaseModel):
    items: list[TicketCredentialOut]


@router.get(
    "/{organization_id}/ticket-credentials",
    response_model=TicketCredentialListOut,
    summary="List an organization's ticket-tracker logins (admin), never the tokens",
)
async def list_ticket_credentials_endpoint(
    request: Request,  # noqa: ARG001
    organization_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),  # noqa: ARG001
) -> Response:
    views = await list_credentials(session, organization_id=organization_id)
    out = TicketCredentialListOut(
        items=[
            TicketCredentialOut(
                id=v.id, host=v.host, auth_scheme=v.auth_scheme, username=v.username
            )
            for v in views
        ]
    )
    return Response(
        content=out.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


@router.put(
    "/{organization_id}/ticket-credentials",
    response_model=TicketCredentialOut,
    summary="Create or replace an organization's login for one ticket tracker (admin)",
)
async def put_ticket_credential_endpoint(
    request: Request,
    organization_id: uuid.UUID,
    payload: TicketCredentialIn,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),
) -> Response:
    try:
        view = await upsert_credential(
            session,
            organization_id=organization_id,
            host=payload.host,
            auth_scheme=payload.auth_scheme,
            username=payload.username,
            api_token=payload.api_token,
            created_by_user_id=actor.id,
        )
    except OrganizationNotFound:
        return problem_response(
            status_code=status.HTTP_404_NOT_FOUND,
            title="Organization Not Found",
            detail="No such organization.",
            instance=request.url.path,
        )
    except AuthSchemeNotSupported as exc:
        return problem_response(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Auth Scheme Not Supported",
            detail=str(exc),
            instance=request.url.path,
        )
    except TicketCredentialError as exc:
        return problem_response(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Invalid Ticket Credential",
            detail=str(exc),
            instance=request.url.path,
        )

    out = TicketCredentialOut(
        id=view.id, host=view.host, auth_scheme=view.auth_scheme, username=view.username
    )
    return Response(
        content=out.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


@router.delete(
    "/{organization_id}/ticket-credentials/{credential_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove an organization's login for one ticket tracker (admin)",
)
async def delete_ticket_credential_endpoint(
    request: Request,
    organization_id: uuid.UUID,
    credential_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),  # noqa: ARG001
) -> Response:
    removed = await delete_credential(
        session, organization_id=organization_id, credential_id=credential_id
    )
    if not removed:
        return problem_response(
            status_code=status.HTTP_404_NOT_FOUND,
            title="Ticket Credential Not Found",
            detail="No such ticket credential for this organization.",
            instance=request.url.path,
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
