# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Admin malicious-snapshot health route — #26 (MAL-2b).

Endpoint: ``GET /v1/admin/malicious/health`` — snapshot date and staleness,
package count, live flagged total, and the last beat tick's outcome for the
admin/health panel. Auth gated by the parent admin router (super-admin only;
existence-hide for everyone else — the ``eol`` sub-router convention).

Pure read: one PK lookup on the single-row status table, one partial-index
count, plus the already-loaded snapshot's metadata. The writer is the weekly
``tasks/malicious_catalog_refresh`` beat.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.security import CurrentUser, require_super_admin_or_404
from schemas.admin_ops import MaliciousStatusOut
from services.malicious_health_service import get_malicious_health

router = APIRouter(prefix="/malicious", tags=["admin"])
log = structlog.get_logger("admin.malicious.api")


@router.get(
    "/health",
    response_model=MaliciousStatusOut,
    summary="Malicious-package snapshot status (admin) — age / counters / next beat",
)
async def get_malicious_health_endpoint(
    request: Request,  # noqa: ARG001
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),  # noqa: ARG001
) -> Response:
    out = await get_malicious_health(session)
    return Response(
        content=out.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


__all__ = ["router"]
