# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Admin routes for private container registry logins (ER3).

Endpoints under ``/v1/admin/organizations/{organization_id}/registry-credentials``:
  - GET    list an organization's credentials (never the passwords)
  - PUT    create or replace the credential for one registry
  - DELETE remove one

Super-admin only, like every other route in this package: a registry login is
deployment infrastructure, and the blast radius of a leaked one is every image
in that registry.

The password is write-only. It goes in on ``PUT`` and is never returned by any
route, because an operator does not need to read it back and a response body
travels through logs, proxies and browser history.
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
from services.registry_credential_service import (
    OrganizationNotFound,
    RegistryCredentialError,
    RegistryNotAllowed,
    delete_credential,
    list_credentials,
    upsert_credential,
)

router = APIRouter(prefix="/organizations", tags=["admin"])
log = structlog.get_logger("admin.registry_credentials.api")


class RegistryCredentialIn(BaseModel):
    """Write model. ``password`` is accepted and never returned."""

    registry_host: str = Field(
        min_length=1,
        max_length=255,
        description=(
            "Registry host as it appears in an image reference: `ghcr.io`, "
            "`registry.example.com`, `registry:5000`. A pasted "
            "`https://ghcr.io/` is normalised to `ghcr.io`, because the "
            "scan-time lookup uses the host parsed out of the image reference "
            "and would otherwise never match."
        ),
    )
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(
        min_length=1,
        description=(
            "Registry password or token. Stored as Fernet ciphertext and never "
            "returned by any route."
        ),
    )


class RegistryCredentialOut(BaseModel):
    """Read model. Deliberately has no password field at all."""

    id: uuid.UUID
    registry_host: str
    username: str
    allowed: bool = Field(
        description=(
            "Whether `CONTAINER_SCAN_ALLOWED_REGISTRIES` currently admits this "
            "registry. Computed live, so a credential saved before the list was "
            "tightened shows as `false` rather than silently never being used. "
            "Saving a credential for an excluded registry is rejected outright; "
            "this flag covers the case where the list changed afterwards."
        )
    )


class RegistryCredentialListOut(BaseModel):
    items: list[RegistryCredentialOut]


@router.get(
    "/{organization_id}/registry-credentials",
    response_model=RegistryCredentialListOut,
    summary="List an organization's registry logins (admin), never the passwords",
)
async def list_registry_credentials_endpoint(
    request: Request,  # noqa: ARG001
    organization_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),  # noqa: ARG001
) -> Response:
    views = await list_credentials(session, organization_id=organization_id)
    out = RegistryCredentialListOut(
        items=[
            RegistryCredentialOut(
                id=v.id, registry_host=v.registry_host, username=v.username, allowed=v.allowed
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
    "/{organization_id}/registry-credentials",
    response_model=RegistryCredentialOut,
    summary="Create or replace an organization's login for one registry (admin)",
)
async def put_registry_credential_endpoint(
    request: Request,
    organization_id: uuid.UUID,
    payload: RegistryCredentialIn,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),
) -> Response:
    try:
        view = await upsert_credential(
            session,
            organization_id=organization_id,
            registry_host=payload.registry_host,
            username=payload.username,
            password=payload.password,
            created_by_user_id=actor.id,
        )
    except OrganizationNotFound:
        # Ahead of the 422 branches: an id that names nothing is not a
        # configuration problem. Super admins see every organization, so there
        # is nothing to hide behind a different status here.
        return problem_response(
            status_code=status.HTTP_404_NOT_FOUND,
            title="Organization Not Found",
            detail="No such organization.",
            instance=request.url.path,
        )
    except RegistryNotAllowed as exc:
        # 422 rather than 403: the request is well-formed and the caller is
        # authorised; the configuration makes it meaningless. The detail names
        # the setting so the operator knows what to change.
        return problem_response(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Registry Not Allowed",
            detail=str(exc),
            instance=request.url.path,
        )
    except RegistryCredentialError as exc:
        return problem_response(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Invalid Registry Credential",
            detail=str(exc),
            instance=request.url.path,
        )

    out = RegistryCredentialOut(
        id=view.id,
        registry_host=view.registry_host,
        username=view.username,
        allowed=view.allowed,
    )
    return Response(
        content=out.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


@router.delete(
    "/{organization_id}/registry-credentials/{credential_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove an organization's login for one registry (admin)",
)
async def delete_registry_credential_endpoint(
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
        # Existence-hiding: a credential id belonging to another organization
        # reads the same as one that never existed.
        return problem_response(
            status_code=status.HTTP_404_NOT_FOUND,
            title="Registry Credential Not Found",
            detail="No such registry credential for this organization.",
            instance=request.url.path,
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
