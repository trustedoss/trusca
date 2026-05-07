"""
Admin system-health HTTP route — Phase 4 PR #14.

Endpoint: ``GET /v1/admin/health`` — postgres / redis / celery / DT / disk /
active scans / last-24h errors in a single payload. Auth gated by the parent
admin router (super-admin only).

The dashboard polls this every 30s. Each per-component probe carries its own
``status`` (ok / degraded / down) and a one-line ``detail`` so the UI can
render mixed states without re-deriving the rules.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.security import CurrentUser, require_super_admin_or_404
from schemas.admin_ops import SystemHealthOut
from services.admin_health_service import get_system_health

router = APIRouter(prefix="/health", tags=["admin"])
log = structlog.get_logger("admin.health.api")


@router.get(
    "",
    response_model=SystemHealthOut,
    summary="System health summary (admin) — postgres / redis / celery / DT / disk",
)
async def get_health_endpoint(
    request: Request,  # noqa: ARG001
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),  # noqa: ARG001
) -> Response:
    out = await get_system_health(session)
    return Response(
        content=out.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


__all__ = ["router"]
