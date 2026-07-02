"""
Admin KEV feed-health HTTP route — Phase C (C2).

Endpoint: ``GET /v1/admin/kev/health`` — exposes the CISA KEV catalog sync
state (last success / last attempt / skip reason / reconcile counters / live
``kev=true`` total / next beat fire / feed host) for the admin/health KEV
panel. Auth gated by the parent admin router (super-admin only;
existence-hide for everyone else, same as the sibling ``trivy`` sub-router).

Pure read: one PK lookup on the single-row ``kev_sync_state`` table plus one
partial-index count — no side effects, no Celery dispatch. The writer is the
daily ``tasks/kev_catalog_refresh`` beat; unlike the Trivy panel (which reads
on-disk DB metadata) the KEV reconcile leaves no file behind, so the status
row is the only durable source (see ``models/kev_sync_state.py``).
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.security import CurrentUser, require_super_admin_or_404
from schemas.admin_ops import KevFeedStatusOut
from services.kev_health_service import get_kev_feed_health

router = APIRouter(prefix="/kev", tags=["admin"])
log = structlog.get_logger("admin.kev.api")


@router.get(
    "/health",
    response_model=KevFeedStatusOut,
    summary="CISA KEV feed sync status (admin) — last sync / counters / next beat",
)
async def get_kev_feed_health_endpoint(
    request: Request,  # noqa: ARG001
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),  # noqa: ARG001
) -> Response:
    out = await get_kev_feed_health(session)
    return Response(
        content=out.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


__all__ = ["router"]
