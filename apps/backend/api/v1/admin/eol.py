"""
Admin EOL snapshot-health HTTP route — Phase M (PR M-3).

Endpoint: ``GET /v1/admin/eol/health`` — exposes the endoflife.date dataset
state (effective snapshot date + origin, rule/product counts, live flagged
total, last beat tick outcome, next fire) for the admin/health EOL panel.
Auth gated by the parent admin router (super-admin only; existence-hide for
everyone else — the ``kev`` sub-router convention).

Pure read: one PK lookup on the single-row ``eol_sync_state`` table, one
partial-index count, plus the in-process vendored-snapshot parse. The writer
is the weekly ``tasks/eol_catalog_refresh`` beat.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.security import CurrentUser, require_super_admin_or_404
from schemas.admin_ops import EolStatusOut
from services.eol_health_service import get_eol_health

router = APIRouter(prefix="/eol", tags=["admin"])
log = structlog.get_logger("admin.eol.api")


@router.get(
    "/health",
    response_model=EolStatusOut,
    summary="endoflife.date snapshot status (admin) — dataset age / counters / next beat",
)
async def get_eol_health_endpoint(
    request: Request,  # noqa: ARG001
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),  # noqa: ARG001
) -> Response:
    out = await get_eol_health(session)
    return Response(
        content=out.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


__all__ = ["router"]
