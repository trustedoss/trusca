# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
``/v1/dashboard`` — portfolio overview aggregate.

Read-only endpoints backing the app-root Dashboard page::

    GET /v1/dashboard/summary       → DashboardSummary
    GET /v1/dashboard/action-queue  → ActionQueue
    GET /v1/dashboard/trends        → DashboardTrends
    GET /v1/dashboard/portfolio     → DashboardPortfolio

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

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import api_read_rate_limit
from core.db import get_db
from core.ratelimit import _authenticated_user_key, limiter
from core.security import CurrentUser, get_current_user
from schemas.action_queue import ActionQueue
from schemas.dashboard import DashboardSummary
from schemas.dashboard_portfolio import DashboardPortfolio
from schemas.dashboard_trends import DashboardTrends, TrendWindow
from services.action_queue_service import get_action_queue
from services.dashboard_portfolio_service import get_dashboard_portfolio
from services.dashboard_service import get_dashboard_summary
from services.dashboard_trends_service import get_dashboard_trends

router = APIRouter(prefix="/v1/dashboard", tags=["dashboard"])


@router.get(
    "/summary",
    response_model=DashboardSummary,
    status_code=status.HTTP_200_OK,
    summary="Portfolio overview for the caller's accessible projects (auth required)",
)
@limiter.limit(api_read_rate_limit, key_func=_authenticated_user_key)
async def get_dashboard_summary_endpoint(
    request: Request,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(get_current_user),
) -> DashboardSummary:
    """Aggregate counts (projects, scans, severities, licenses, approvals) plus
    the 10 most recent scans, scoped to the caller's accessible projects.

    Rate limited per actor on the same bucket and budget as the other three
    dashboard routes (``/action-queue``, ``/trends``, ``/portfolio``): this
    route used to be the one dashboard endpoint without a limiter, which made
    it the cheapest way to repeatedly re-run the portfolio-wide aggregate.
    """
    return await get_dashboard_summary(session, actor=actor)


@router.get(
    "/action-queue",
    response_model=ActionQueue,
    status_code=status.HTTP_200_OK,
    summary="Work waiting on a person, across the caller's projects (auth required)",
)
@limiter.limit(api_read_rate_limit, key_func=_authenticated_user_key)
async def get_action_queue_endpoint(
    request: Request,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(get_current_user),
) -> ActionQueue:
    """Pending approvals, KEV findings against their CISA deadline, projects the
    build gate would block, and projects nothing has scanned recently.

    Same scoping contract as ``/summary``: the caller's identity is the scope,
    enforced in the service through the shared accessible-projects helper. The
    gate bucket aggregates the gate's inputs rather than calling
    ``evaluate_gate`` per project — see ``services.action_queue_service`` for
    why, and for the parity test that keeps the two in agreement.

    Rate limited per actor. ``BUCKET_LIMIT`` caps the rows returned but not
    the work done: the aggregates scan every open finding across the caller's
    accessible projects, so cost grows with portfolio size even though query
    count does not. Without a limit, one token could hold the connection pool
    on a large deployment.
    """
    return await get_action_queue(session, actor=actor)


@router.get(
    "/trends",
    response_model=DashboardTrends,
    status_code=status.HTTP_200_OK,
    summary="Daily risk series for the caller's accessible projects (auth required)",
)
@limiter.limit(api_read_rate_limit, key_func=_authenticated_user_key)
async def get_dashboard_trends_endpoint(
    request: Request,
    days: Annotated[
        TrendWindow,
        Query(description="Window length in days, inclusive of today."),
    ] = TrendWindow.MONTH,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(get_current_user),
) -> DashboardTrends:
    """New and resolved exposures per day, plus the standing critical / KEV
    counts, over the caller's accessible projects.

    ``days`` is a closed set rather than a free integer: an arbitrary window
    would let one request walk years of scan history, and the service raises
    on anything outside it, so a widened query parameter cannot quietly become
    an unbounded scan. It is an ``IntEnum`` rather than a literal of ints
    because a query string arrives as ``"7"`` and a literal refuses to coerce
    it — the literal spelling rejected every window a caller asked for while
    still honouring the default, which is a shape no test of the default can
    see.

    Same scoping contract as ``/summary`` — the caller's identity is the
    scope, enforced in the service through the shared accessible-projects
    helper. Rate limited per actor for the same reason as the action queue:
    query count is fixed but the exposure sets read grow with the portfolio.
    """
    return await get_dashboard_trends(session, actor=actor, days=days)


@router.get(
    "/portfolio",
    response_model=DashboardPortfolio,
    status_code=status.HTTP_200_OK,
    summary="Teams and their projects, by risk (auth required)",
)
@limiter.limit(api_read_rate_limit, key_func=_authenticated_user_key)
async def get_dashboard_portfolio_endpoint(
    request: Request,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(get_current_user),
) -> DashboardPortfolio:
    """Every project the caller can see, grouped by the team that owns it.

    The grouping is the point: a list answers "which project is worst", this
    answers "which team is carrying the risk". Same scoping contract as the
    other three — the caller's identity is the scope, and team names are read
    only for teams that own a visible project, so a caller cannot enumerate
    the organisation's teams through an empty row.

    Both the per-team and the overall project caps are display limits, and the
    response reports what they cut: a grid that silently showed a subset would
    invite the reader to conclude the rest is clean.
    """
    return await get_dashboard_portfolio(session, actor=actor)
