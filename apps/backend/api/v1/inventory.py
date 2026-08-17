# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Organization-wide inventory endpoints (S2).

Why `/v1/inventory/*` and not `/v1/components`: `/v1/components/{id}` already
exists and takes a **component_version** id, returning the per-project drawer
payload. Hanging a collection off the same prefix would put two different
grains behind one word. These routes are their own noun.

Every endpoint fans out across projects, so team isolation is the dominant
concern and lives entirely in the service layer's single choke-point
(`core.authz.team_scope_filter`). The reverse lookups answer 404 when the
actor's projects do not reach the requested component / CVE, conflating "does
not exist" with "not yours" — probing another team's ids must teach nothing.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, Path, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api.v1._csv_export_response import csv_stream_response
from core.config import csv_export_rate_limit, search_rate_limit
from core.db import get_db
from core.errors import problem_response
from core.ratelimit import _authenticated_user_key, limiter
from core.security import CurrentUser, require_role
from schemas.inventory import (
    InventoryComponentListResponse,
    InventoryProjectUsageListResponse,
    InventoryVulnerabilityImpactResponse,
)
from services.inventory_service import (
    LIMIT_DEFAULT,
    LIMIT_MAX,
    InventoryError,
    list_component_usage,
    list_inventory_components,
    list_vulnerability_impact,
)
from services.table_export_service import stream_inventory_csv

router = APIRouter(prefix="/v1/inventory", tags=["inventory"])
log = structlog.get_logger("inventory.api")


def _problem_for_inventory_error(request: Request, exc: InventoryError) -> Response:
    """Map a service error to RFC 7807, keeping the existence-hiding wording."""
    return problem_response(
        status_code=exc.status_code,
        title=exc.title,
        detail=str(exc) or exc.title,
        instance=request.url.path,
    )


@router.get(
    "/components",
    response_model=InventoryComponentListResponse,
    summary="Organization-wide component inventory",
    responses={
        200: {
            "description": (
                "One page of packages in use across every project the caller "
                "can read. 'In use' means present in a project's latest "
                "succeeded scan; a project whose newest scan attempt failed "
                "still contributes its last succeeded one."
            )
        },
        401: {"description": "No / invalid bearer token."},
    },
)
@limiter.limit(search_rate_limit, key_func=_authenticated_user_key)
async def list_inventory_components_endpoint(
    request: Request,
    limit: int = Query(default=LIMIT_DEFAULT, ge=1, le=LIMIT_MAX),
    offset: int = Query(default=0, ge=0),
    q: str | None = Query(
        default=None,
        max_length=255,
        description=(
            "Substring match on package name or purl. LIKE metacharacters are "
            "escaped and matched literally."
        ),
    ),
    package_type: list[str] | None = Query(default=None),
    severity: list[str] | None = Query(
        default=None,
        description="Worst-severity buckets to keep. Unknown tokens are ignored.",
    ),
    license_category: list[str] | None = Query(default=None),
    eol: bool | None = Query(default=None),
    outdated: bool | None = Query(default=None),
    sort: str = Query(
        default="project_count",
        pattern="^(name|project_count|severity|license)$",
    ),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("viewer")),
) -> Response:
    body = await list_inventory_components(
        session,
        actor=actor,
        limit=limit,
        offset=offset,
        q=q,
        package_type=package_type,
        severity=severity,
        license_category=license_category,
        eol=eol,
        outdated=outdated,
        sort=sort,
        order=order,
    )
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# GET /v1/inventory/components/export.csv  (B5)
# ---------------------------------------------------------------------------


@router.get(
    "/components/export.csv",
    response_class=StreamingResponse,
    summary="The filtered inventory as CSV",
)
# The export gets its own budget rather than the list's. One export walks the
# list service up to five hundred times (the chunk size is clamped to this
# service's own page cap of 200) and each page re-resolves the org-wide
# current-scan set, so pricing it as a search would be wrong by two orders of
# magnitude. The row cap bounds a single request; this bounds the sequence.
@limiter.limit(csv_export_rate_limit, key_func=_authenticated_user_key)
async def export_inventory_components_csv_endpoint(
    request: Request,
    q: str | None = Query(default=None, max_length=255),
    package_type: list[str] | None = Query(default=None),
    severity: list[str] | None = Query(default=None),
    license_category: list[str] | None = Query(default=None),
    eol: bool | None = Query(default=None),
    outdated: bool | None = Query(default=None),
    sort: str = Query(
        default="project_count",
        pattern="^(name|project_count|severity|license)$",
    ),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("viewer")),
) -> Response:
    """
    The same rows the list endpoint would return, without the paging.

    The scope is the caller's own: the list service resolves which teams the
    actor can read and the export calls that same service, so a member of one
    team never receives another team's packages.
    """
    stream = stream_inventory_csv(
        session,
        actor=actor,
        filters={
            "q": q,
            "package_type": package_type,
            "severity": severity,
            "license_category": license_category,
            "eol": eol,
            "outdated": outdated,
            "sort": sort,
            "order": order,
        },
    )
    return await csv_stream_response(
        request,
        stream=stream,
        filename="inventory.csv",
    )


@router.get(
    "/components/{component_id}/projects",
    response_model=InventoryProjectUsageListResponse,
    summary="Projects using a component",
    responses={
        404: {
            "description": (
                "The component does not exist, or none of the caller's "
                "projects use it. The two are deliberately indistinguishable."
            )
        },
    },
)
@limiter.limit(search_rate_limit, key_func=_authenticated_user_key)
async def list_component_usage_endpoint(
    request: Request,
    component_id: uuid.UUID = Path(...),
    limit: int = Query(default=LIMIT_DEFAULT, ge=1, le=LIMIT_MAX),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("viewer")),
) -> Response:
    try:
        body = await list_component_usage(
            session,
            actor=actor,
            component_id=component_id,
            limit=limit,
            offset=offset,
        )
    except InventoryError as exc:
        return _problem_for_inventory_error(request, exc)
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


@router.get(
    "/vulnerabilities/{external_id}/projects",
    response_model=InventoryVulnerabilityImpactResponse,
    summary="Projects affected by a CVE",
    responses={
        404: {
            "description": (
                "The CVE does not exist, or affects none of the caller's "
                "projects. The two are deliberately indistinguishable."
            )
        },
    },
)
@limiter.limit(search_rate_limit, key_func=_authenticated_user_key)
async def list_vulnerability_impact_endpoint(
    request: Request,
    external_id: str = Path(..., max_length=64),
    limit: int = Query(default=LIMIT_DEFAULT, ge=1, le=LIMIT_MAX),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("viewer")),
) -> Response:
    try:
        body = await list_vulnerability_impact(
            session,
            actor=actor,
            external_id=external_id,
            limit=limit,
            offset=offset,
        )
    except InventoryError as exc:
        return _problem_for_inventory_error(request, exc)
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# slowapi's `@limiter.limit` wraps each endpoint with functools.wraps, whose
# `__globals__` points at slowapi's module. Under `from __future__ import
# annotations` FastAPI resolves the string annotations via
# get_type_hints(func, globalns=func.__globals__) and cannot see names defined
# here — misclassifying every parameter and 422-ing every request. Seed the
# names the wrappers need into their `__globals__` (the dict is mutable even
# though the attribute is read-only). Mirrors api/v1/search.py + sbom.py.
for _endpoint in (
    list_inventory_components_endpoint,
    list_component_usage_endpoint,
    list_vulnerability_impact_endpoint,
):
    for _name in (
        "AsyncSession",
        "Request",
        "Response",
        "Depends",
        "Path",
        "Query",
        "CurrentUser",
        "uuid",
    ):
        if _name in globals():
            _endpoint.__globals__.setdefault(_name, globals()[_name])
del _endpoint, _name
