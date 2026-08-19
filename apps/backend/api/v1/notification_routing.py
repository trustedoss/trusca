# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Notification routing rules over HTTP (prefix ``/v1/notification-rules``).

Who else hears about what. Scope decides who may write: an organization rule
is a statement about the whole deployment and a super admin writes it; a team
rule is that team's business and its administrator does. The route gate is a
floor, and the service decides whether this caller may write this scope, where
the scope is known.

Reads sit lower than writes for the same reason they do on the policy screens:
what reaches a team is something the team should be able to see without being
able to change it.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.errors import problem_response
from core.security import CurrentUser, require_role
from schemas.notification_routing import (
    NotificationRoutingRuleIn,
    NotificationRoutingRuleListOut,
    NotificationRoutingRuleOut,
)
from services.notification_routing_service import (
    RoutingRuleError,
    RoutingRuleForbidden,
    RoutingRuleScopeNotFound,
    create_rule,
    delete_rule,
    list_rules,
)

router = APIRouter(prefix="/v1/notification-rules", tags=["notifications"])
log = structlog.get_logger("api.notification_routing")


def _problem(request: Request, exc: RoutingRuleError) -> Response:
    if isinstance(exc, RoutingRuleScopeNotFound):
        status_code, title = status.HTTP_404_NOT_FOUND, "Not Found"
    elif isinstance(exc, RoutingRuleForbidden):
        status_code, title = status.HTTP_403_FORBIDDEN, "Forbidden"
    else:
        status_code, title = status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid Rule"
    return problem_response(
        status_code=status_code,
        title=title,
        detail=str(exc),
        instance=request.url.path,
    )


@router.get(
    "/org/{organization_id}",
    response_model=NotificationRoutingRuleListOut,
    summary="Organization-wide rules",
)
async def list_org_rules_endpoint(
    request: Request,
    organization_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("viewer")),
) -> Response:
    try:
        rules = await list_rules(
            session, actor, organization_id=organization_id, team_id=None
        )
    except RoutingRuleError as exc:
        return _problem(request, exc)
    items = [NotificationRoutingRuleOut.model_validate(rule) for rule in rules]
    return Response(
        content=NotificationRoutingRuleListOut(
            items=items, total=len(items)
        ).model_dump_json(),
        media_type="application/json",
    )


@router.get(
    "/teams/{team_id}",
    response_model=NotificationRoutingRuleListOut,
    summary="Rules that reach one team, including the organization's own",
)
async def list_team_rules_endpoint(
    request: Request,
    team_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("viewer")),
) -> Response:
    try:
        rules = await list_rules(session, actor, organization_id=None, team_id=team_id)
    except RoutingRuleError as exc:
        return _problem(request, exc)
    items = [NotificationRoutingRuleOut.model_validate(rule) for rule in rules]
    return Response(
        content=NotificationRoutingRuleListOut(
            items=items, total=len(items)
        ).model_dump_json(),
        media_type="application/json",
    )


@router.post(
    "/org/{organization_id}",
    response_model=NotificationRoutingRuleOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add an organization-wide rule",
    responses={
        403: {"description": "Only a super admin writes at this scope."},
        422: {"description": "The rule names no channel and no recipient."},
    },
)
async def create_org_rule_endpoint(
    request: Request,
    organization_id: uuid.UUID,
    payload: NotificationRoutingRuleIn,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("team_admin")),
) -> Response:
    try:
        rule = await create_rule(
            session,
            actor,
            organization_id=organization_id,
            team_id=None,
            payload=payload,
        )
    except RoutingRuleError as exc:
        return _problem(request, exc)
    return Response(
        content=NotificationRoutingRuleOut.model_validate(rule).model_dump_json(),
        status_code=status.HTTP_201_CREATED,
        media_type="application/json",
    )


@router.post(
    "/teams/{team_id}",
    response_model=NotificationRoutingRuleOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a rule for one team",
)
async def create_team_rule_endpoint(
    request: Request,
    team_id: uuid.UUID,
    payload: NotificationRoutingRuleIn,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("team_admin")),
) -> Response:
    try:
        rule = await create_rule(
            session, actor, organization_id=None, team_id=team_id, payload=payload
        )
    except RoutingRuleError as exc:
        return _problem(request, exc)
    return Response(
        content=NotificationRoutingRuleOut.model_validate(rule).model_dump_json(),
        status_code=status.HTTP_201_CREATED,
        media_type="application/json",
    )


@router.delete(
    "/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a rule",
    responses={404: {"description": "No such rule, or not the caller's to remove."}},
)
async def delete_rule_endpoint(
    request: Request,
    rule_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("team_admin")),
) -> Response:
    try:
        await delete_rule(session, actor, rule_id=rule_id)
    except RoutingRuleError as exc:
        return _problem(request, exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
