# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Automation identities over HTTP (prefix ``/v1/service-accounts``).

A team administrator's surface, matching who may issue a key for that team: a
service account is a credential holder, and deciding one should exist is the
same act as deciding credentials may be held there.

Deliberately not part of the admin user surface. These rows share a table with
people, but the actions a user list offers are wrong for them: there is nobody
to send a password reset to, and deactivating one from a leavers screen is a
pipeline outage that reads as tidying up.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.errors import problem_response
from core.security import CurrentUser, require_role
from schemas.service_account import (
    ServiceAccountCreateIn,
    ServiceAccountListOut,
    ServiceAccountOut,
    ServiceAccountStewardIn,
)
from services.service_account_service import (
    ServiceAccountError,
    assign_steward,
    create_service_account,
    deactivate_service_account,
    list_service_accounts,
)

router = APIRouter(prefix="/v1/service-accounts", tags=["service-accounts"])
log = structlog.get_logger("service_accounts.api")


def _problem_for(request: Request, exc: ServiceAccountError) -> Response:
    return problem_response(
        status_code=exc.status_code,
        title=exc.title,
        detail=str(exc) or exc.title,
        instance=request.url.path,
    )


def _json(model: object, code: int) -> Response:
    return Response(
        content=model.model_dump_json(),  # type: ignore[attr-defined]
        media_type="application/json",
        status_code=code,
    )


@router.post(
    "",
    response_model=ServiceAccountOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create an automation identity for a team",
    responses={
        201: {"description": "Created. The caller becomes the steward."},
        403: {"description": "Caller does not administer this team."},
        409: {"description": "A service account with this name already exists."},
        422: {"description": "The name cannot be an identifier, or the role is unknown."},
    },
)
async def create_endpoint(
    request: Request,
    payload: ServiceAccountCreateIn,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        account = await create_service_account(
            session,
            actor,
            team_id=payload.team_id,
            slug=payload.slug,
            display_name=payload.display_name,
            role=payload.role,
        )
    except ServiceAccountError as exc:
        return _problem_for(request, exc)
    return _json(ServiceAccountOut.model_validate(account), status.HTTP_201_CREATED)


@router.get(
    "",
    response_model=ServiceAccountListOut,
    summary="A team's automation identities",
    responses={
        404: {
            "description": (
                "Team not found, or the caller does not administer it. "
                "Returned in lieu of 403 so membership does not leak."
            )
        }
    },
)
async def list_endpoint(
    request: Request,
    team_id: uuid.UUID = Query(),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        rows = await list_service_accounts(session, actor, team_id=team_id)
    except ServiceAccountError as exc:
        return _problem_for(request, exc)
    items = [ServiceAccountOut.model_validate(row) for row in rows]
    return _json(
        ServiceAccountListOut(items=items, total=len(items)), status.HTTP_200_OK
    )


@router.put(
    "/{service_account_id}/steward",
    response_model=ServiceAccountOut,
    summary="Hand an account to somebody who will answer for it",
    responses={
        200: {"description": "Steward assigned. The account's keys are untouched."},
        404: {"description": "No such account, or not the caller's to manage."},
        422: {"description": "The proposed steward is not an active person."},
    },
)
async def assign_steward_endpoint(
    request: Request,
    service_account_id: uuid.UUID,
    payload: ServiceAccountStewardIn,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        account = await assign_steward(
            session,
            actor,
            service_account_id=service_account_id,
            steward_user_id=payload.steward_user_id,
        )
    except ServiceAccountError as exc:
        return _problem_for(request, exc)
    return _json(ServiceAccountOut.model_validate(account), status.HTTP_200_OK)


@router.delete(
    "/{service_account_id}",
    response_model=ServiceAccountOut,
    summary="Stop every key this account holds",
    responses={
        200: {
            "description": (
                "Deactivated, and with it every key it issued. Idempotent. "
                "The row stays so the audit trail keeps its actor."
            )
        },
        404: {"description": "No such account, or not the caller's to manage."},
    },
)
async def deactivate_endpoint(
    request: Request,
    service_account_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    """Deactivate rather than delete.

    The row is the actor on every audit entry its keys produced, and deleting
    it would either orphan those or cascade them away. Deactivating stops the
    credentials, which is what the caller is asking for.
    """
    try:
        account = await deactivate_service_account(
            session, actor, service_account_id=service_account_id
        )
    except ServiceAccountError as exc:
        return _problem_for(request, exc)
    return _json(ServiceAccountOut.model_validate(account), status.HTTP_200_OK)
