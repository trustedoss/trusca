"""
Admin disk-telemetry HTTP route — Phase 4 PR #14.

Endpoint: ``GET /v1/admin/disk`` — workspace + DT volume + Postgres + Redis
in one shot. Auth gated by the parent admin router (super-admin only,
existence-hide for everyone else).
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.security import CurrentUser, require_super_admin_or_404
from schemas.admin_ops import AdminDiskOut
from services.admin_disk_service import get_disk_telemetry

router = APIRouter(prefix="/disk", tags=["admin"])
log = structlog.get_logger("admin.disk.api")


@router.get(
    "",
    response_model=AdminDiskOut,
    summary="Disk usage telemetry (admin) — workspace / DT volume / Postgres / Redis",
)
async def get_disk_endpoint(
    request: Request,  # noqa: ARG001
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),  # noqa: ARG001
) -> Response:
    out = await get_disk_telemetry(session)
    return Response(
        content=out.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


__all__ = ["router"]
