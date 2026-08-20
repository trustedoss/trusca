# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
The operational metrics endpoint (``GET /metrics``), off unless asked for.

Off means 404 rather than 403. A monitoring endpoint that answers "you are not
allowed" tells an outsider what this host is and who runs it, and a deployment
that has not asked for a scrape target should look like one that does not have
the feature.

The path was reserved in ``core.openapi.PUBLIC_PATHS`` before anything served
it, which is why this router does not add an authentication dependency: the
token check below is the whole gate, and it is optional because the usual
deployment keeps this off the public ingress and lets the monitoring system
reach it on the internal network.
"""

from __future__ import annotations

import secrets

import structlog
from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import metrics_enabled, metrics_token
from core.db import get_db
from services.metrics_service import render_metrics

router = APIRouter(tags=["metrics"])
log = structlog.get_logger("api.metrics")

#: What a scraper expects. The version parameter is part of the convention and
#: some collectors branch on it.
CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def _token_ok(request: Request) -> bool:
    """Whether the scraper presented the configured token.

    Constant-time comparison so a wrong token cannot be found one character at
    a time by measuring the refusals. Accepts either a bearer header or the
    bare token, because scrapers differ and neither shape is more correct.
    """
    expected = metrics_token()
    if expected is None:
        return True
    header = request.headers.get("authorization") or ""
    presented = header[7:] if header.lower().startswith("bearer ") else header
    return secrets.compare_digest(presented.strip(), expected)


@router.get(
    "/metrics",
    summary="Operational metrics, in the Prometheus text format",
    response_class=Response,
    responses={
        200: {"content": {"text/plain": {}}},
        404: {
            "description": (
                "The deployment does not publish metrics, or the scraper did "
                "not present the configured token. The same answer for both, "
                "so an endpoint that is switched off cannot be told from one "
                "that is guarded."
            )
        },
    },
)
async def metrics_endpoint(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> Response:
    if not metrics_enabled():
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    if not _token_ok(request):
        # Deliberately the same answer as switched off. A 401 here would
        # confirm to whoever asked that this deployment publishes metrics and
        # that they only need the token.
        log.info("metrics_scrape_refused")
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    body = await render_metrics(session)
    return Response(content=body, media_type=CONTENT_TYPE)


__all__ = ["router"]
