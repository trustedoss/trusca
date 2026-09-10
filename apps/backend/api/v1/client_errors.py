# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Frontend crash-report intake (#423).

  * ``POST /v1/client-errors``: `apps/frontend`'s `ErrorBoundary` posts here
    instead of stopping at `console.error`.

Unauthenticated (CLAUDE.md core rule #12's exception list): a render crash
can happen on the login screen, before there is a token to attach, and this
endpoint accepts a report either way. When a bearer token IS present and
valid, the actor id rides along in the log line so an operator can find
"which user hit this", but a missing/expired/invalid token is not an error
here the way it is on a protected route; it is simply the anonymous case.
See ``services.client_error_service`` for what happens to the report.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response, status

from core.config import client_error_report_rate_limit
from core.ratelimit import limiter
from core.security import CurrentUser, get_optional_current_user
from schemas.client_error import ClientErrorReportIn
from services.client_error_service import record_client_error

router = APIRouter(prefix="/v1/client-errors", tags=["client-errors"])


@router.post(
    "",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Report a frontend render crash",
)
@limiter.limit(client_error_report_rate_limit)
async def report_client_error_endpoint(
    request: Request,
    report: ClientErrorReportIn,
    actor: CurrentUser | None = Depends(get_optional_current_user),
) -> Response:
    record_client_error(
        report,
        actor_user_id=actor.id if actor is not None else None,
        user_agent=request.headers.get("user-agent"),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
