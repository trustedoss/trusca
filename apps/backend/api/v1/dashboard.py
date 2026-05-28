"""
``/v1/dashboard`` — portfolio overview aggregate.

A single read-only endpoint backing the app-root Dashboard page::

    GET /v1/dashboard/summary  → DashboardSummary

Auth: requires :func:`get_current_user` (JWT). There is no ``team_id`` /
``project_id`` path parameter — the caller's identity *is* the scope. The
service (``services.dashboard_service``) restricts every aggregate to the
caller's accessible projects (super-admin → all; otherwise → projects owned by
a team the caller belongs to). Cross-team isolation is enforced there, not here.

All 4xx/5xx responses are RFC 7807 problem+json via the app-wide exception
handlers (``core.errors.install_exception_handlers``); this router raises no
bare ``HTTPException`` and hand-rolls no error envelope. The only failure modes
are the auth dependency's 401 (missing/invalid token) — also rendered as
problem+json by the shared handlers.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.security import CurrentUser, get_current_user
from schemas.dashboard import DashboardSummary
from services.dashboard_service import get_dashboard_summary

router = APIRouter(prefix="/v1/dashboard", tags=["dashboard"])


@router.get(
    "/summary",
    response_model=DashboardSummary,
    status_code=status.HTTP_200_OK,
    summary="Portfolio overview for the caller's accessible projects (auth required)",
)
async def get_dashboard_summary_endpoint(
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(get_current_user),
) -> DashboardSummary:
    """Aggregate counts (projects, scans, severities, licenses, approvals) plus
    the 10 most recent scans, scoped to the caller's accessible projects."""
    return await get_dashboard_summary(session, actor=actor)
