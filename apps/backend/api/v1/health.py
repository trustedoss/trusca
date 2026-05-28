"""
Readiness probe — ``GET /health/ready`` (v2.1 / Track B B1).

PUBLIC / EXPLICITLY UNAUTHENTICATED. CLAUDE.md core rule #12 requires every API
endpoint to be JWT-gated *unless* explicitly marked as a public exception. This
endpoint is such an exception: orchestrators (docker-compose ``service_healthy``
gates, Kubernetes ``readinessProbe``) call it with no credentials, exactly like
the liveness ``/health`` route in ``main.py``. It is grouped under the OpenAPI
``public`` tag so the unauthenticated surface is enumerable in the docs.

Contract:
  * 200 ``{"status": "ready"}`` — the Postgres schema is at the Alembic HEAD.
  * 503 ``application/problem+json`` (RFC 7807) — schema behind HEAD, the
    ``alembic_version`` table is missing, or the DB is unreachable. The ``detail``
    summarises the revision mismatch with short revision ids only; no DSN /
    credential / driver traceback ever reaches the body (those are logged).

This is distinct from ``GET /v1/admin/health`` (super-admin, full system health)
which stays unchanged — this route is the lightweight, unauthenticated
schema-at-HEAD gate only.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.errors import problem_response
from core.readiness import check_schema_readiness

# No prefix: the route declares the absolute path /health/ready so it sits
# alongside the liveness /health in main.py rather than under /v1. The `public`
# OpenAPI tag is declared on the route itself (below) so it appears exactly once.
router = APIRouter()

# RFC 7807 ``type`` URI for the schema-not-ready problem. about:blank is the
# default; a stable, dereferenceable-looking URN lets clients branch on the
# specific failure class without parsing the human-readable title.
_NOT_READY_TYPE = "urn:trustedoss:problem:schema-not-ready"


@router.get(
    "/health/ready",
    tags=["public"],
    summary="Readiness probe (schema at Alembic HEAD) — PUBLIC, unauthenticated",
    responses={
        200: {
            "description": "Schema is at the Alembic HEAD revision.",
            "content": {"application/json": {"example": {"status": "ready"}}},
        },
        503: {
            "description": (
                "Schema is not at HEAD, the alembic_version table is missing, "
                "or the database is unreachable. RFC 7807 problem+json."
            ),
            "content": {
                "application/problem+json": {
                    "example": {
                        "type": _NOT_READY_TYPE,
                        "title": "Service Not Ready",
                        "status": 503,
                        "detail": (
                            "database schema is not at the expected Alembic HEAD "
                            "(expected: 0021_abc; current: 0020_def)"
                        ),
                        "instance": "/health/ready",
                        "ready": False,
                    }
                }
            },
        },
    },
)
async def health_ready(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Return 200 when the DB schema matches the Alembic HEAD, else 503.

    PUBLIC: no auth dependency by design (probe endpoint — see module docstring
    and CLAUDE.md core rule #12). The check is read-only (a single SELECT on
    ``alembic_version`` plus an in-image read of the script tree).
    """
    result = await check_schema_readiness(session)
    if result.ready:
        return JSONResponse({"status": "ready"}, status_code=status.HTTP_200_OK)
    return problem_response(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        title="Service Not Ready",
        detail=result.summary(),
        instance=request.url.path,
        type_=_NOT_READY_TYPE,
        ready=False,
    )


__all__ = ["router"]
