"""
Admin Trivy DB-health HTTP route — W6-#43e.

Endpoint: ``GET /v1/admin/trivy/health`` — exposes the on-disk Trivy
vulnerability DB state (last_update, freshness, vuln_count, db_version,
cache_dir, repository) for the admin/health Trivy panel. Auth gated by the
parent admin router (super-admin only; existence-hide for everyone else per
memory ``feedback_admin_existence_hide_pattern``).

W6-#43e exposes *state only* — the W6-#44 follow-up owns the weekly Celery
beat that actually refreshes the DB. The endpoint is therefore a pure read
of the worker's ``$TRIVY_CACHE_DIR/db/metadata.json`` plus the configured
cadence env vars; no side effects, no Celery dispatch.

The service layer caches the snapshot for 60s so admin/health (which polls
every 30s) does not re-stat the cache directory on every poll.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Request, Response, status

from core.security import CurrentUser, require_super_admin_or_404
from schemas.admin_ops import TrivyDbStatusOut
from services.trivy_health_service import get_trivy_db_health

router = APIRouter(prefix="/trivy", tags=["admin"])
log = structlog.get_logger("admin.trivy.api")


@router.get(
    "/health",
    response_model=TrivyDbStatusOut,
    summary="Trivy vulnerability DB status (admin) — last_update / freshness / vuln_count",
)
async def get_trivy_db_health_endpoint(
    request: Request,  # noqa: ARG001
    actor: CurrentUser = Depends(require_super_admin_or_404()),  # noqa: ARG001
) -> Response:
    out = get_trivy_db_health()
    return Response(
        content=out.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


__all__ = ["router"]
