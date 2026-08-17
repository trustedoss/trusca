# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Paged search endpoint for the full search page (S3).

Deliberately separate from ``GET /v1/search``, which backs the ⌘K palette and
whose response shape the palette's tests pin exactly. Paging and facets here
would have made that shape depend on which parameters were sent; two endpoints
with one job each is the cheaper contract.

Team isolation lives in the service layer's single choke-point, as it does for
every cross-project surface.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import search_rate_limit
from core.db import get_db
from core.errors import problem_response
from core.pagination import PAGE_MAX
from core.ratelimit import _authenticated_user_key, limiter
from core.security import CurrentUser, require_role
from schemas.search_results import SearchResultsPage
from services.search_results_service import (
    SIZE_DEFAULT,
    SIZE_MAX,
    SearchResultsError,
    search_results,
)

router = APIRouter(prefix="/v1/search", tags=["search"])
log = structlog.get_logger("search.results.api")


@router.get(
    "/results",
    response_model=SearchResultsPage,
    summary="Paged, faceted search results for one kind",
    responses={
        200: {
            "description": (
                "One page of results. A query shorter than 2 characters "
                "returns an empty page with a 200, not a 422, so the page can "
                "fire as the user types."
            )
        },
        422: {"description": "Unknown kind."},
    },
)
@limiter.limit(search_rate_limit, key_func=_authenticated_user_key)
async def search_results_endpoint(
    request: Request,
    kind: str = Query(
        ...,
        pattern="^(projects|components|vulnerabilities|licenses)$",
        description="Which result set to page through.",
    ),
    q: str = Query(..., max_length=255),
    page: int = Query(default=1, ge=1, le=PAGE_MAX),
    size: int = Query(default=SIZE_DEFAULT, ge=1, le=SIZE_MAX),
    severity: list[str] | None = Query(default=None),
    finding_status: list[str] | None = Query(default=None, alias="status"),
    package_type: list[str] | None = Query(default=None),
    license_category: list[str] | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("viewer")),
) -> Response:
    try:
        body = await search_results(
            session,
            actor=actor,
            kind=kind,
            q=q,
            page=page,
            size=size,
            severity=severity,
            status=finding_status,
            package_type=package_type,
            license_category=license_category,
        )
    except SearchResultsError as exc:
        return problem_response(
            status_code=exc.status_code,
            title=exc.title,
            detail=str(exc) or exc.title,
            instance=request.url.path,
        )
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# slowapi's `@limiter.limit` wraps the endpoint with functools.wraps, whose
# `__globals__` points at slowapi's module. Under `from __future__ import
# annotations` FastAPI resolves the string annotations through
# get_type_hints(func, globalns=func.__globals__) and cannot see names defined
# here — misclassifying every parameter and 422-ing every request. Seed the
# names the wrapper needs (the dict is mutable even though the attribute is
# read-only). Mirrors api/v1/search.py + inventory.py.
for _name in ("AsyncSession", "Request", "Response", "Depends", "Query", "CurrentUser"):
    if _name in globals():
        search_results_endpoint.__globals__.setdefault(_name, globals()[_name])
del _name
