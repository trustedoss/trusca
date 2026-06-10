"""
Team-scoped audit-log read — M-3.

Endpoint under ``/v1/audit``:
  - GET /v1/audit  — paginated JSON search, scoped to the caller's teams.

Auth / RBAC (the guide describes a two-tier model that the super-admin-only
``/v1/admin/audit`` did not satisfy):
  - ``super_admin``  — every row (no team restriction).
  - ``team_admin``   — only rows for the teams where the caller holds the
    ``team_admin`` role. The scope is derived from ``CurrentUser.team_roles``,
    never from the coarse ``role`` field, so a user who is ``team_admin`` in
    team A and ``developer`` in team B sees only team A's audit (CWE-863).
  - ``developer``    — rejected by ``require_role("team_admin")`` (403).

The team scope is enforced server-side as a WHERE the caller cannot widen via
query params; passing ``actor_user_id`` / ``q`` / etc. only narrows within the
authorized teams. Filtering surface mirrors :class:`schemas.admin_ops.
AuditSearchQuery`, identical to the admin endpoint.

CSV export stays super-admin-only on ``/v1/admin/audit/export.csv``; this read
path is the team_admin's forensic view, not a bulk-export surface.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import structlog
from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.security import CurrentUser, require_role
from schemas.admin_ops import (
    AuditLogListPage,
    AuditSearchQuery,
    AuditTargetTable,
)
from services.admin_audit_service import search_audit

router = APIRouter(prefix="/v1/audit", tags=["audit"])
log = structlog.get_logger("audit.api")


def _scope_for(actor: CurrentUser) -> set[uuid.UUID] | None:
    """Resolve the audit team scope for the principal.

    ``None`` means unrestricted (super_admin). Otherwise the set of teams
    where the actor holds ``team_admin``. An actor with no ``team_admin``
    membership gets an empty set — which matches zero rows (fail-closed).
    """
    if actor.role == "super_admin":
        return None
    return {
        team_id
        for team_id, role in actor.team_roles.items()
        if role == "team_admin"
    }


@router.get(
    "",
    response_model=AuditLogListPage,
    summary="Search audit log scoped to the caller's teams (team_admin+)",
)
async def search_team_audit_endpoint(
    request: Request,  # noqa: ARG001
    actor_user_id: uuid.UUID | None = Query(default=None),
    target_table: AuditTargetTable | None = Query(default=None),
    action: str | None = Query(default=None, max_length=64),
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
    q: str | None = Query(default=None, max_length=255),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("team_admin")),
) -> Response:
    query = AuditSearchQuery.model_validate(
        {
            "actor_user_id": actor_user_id,
            "target_table": target_table,
            "action": action,
            "from": from_,
            "to": to,
            "q": q,
            "page": page,
            "page_size": page_size,
        }
    )
    page_obj = await search_audit(
        session,
        actor=actor,
        query=query,
        allowed_team_ids=_scope_for(actor),
    )
    return Response(
        content=page_obj.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


__all__ = ["router"]
