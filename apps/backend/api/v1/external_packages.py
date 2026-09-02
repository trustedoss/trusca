# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Pre-adoption catalog lookup: package and advisory search over deps.dev.

Two endpoints:

  GET /v1/external-packages?ecosystem=<slug>&name=<str>   Exact package lookup.
  GET /v1/external-advisories/{advisory_id}                Advisory lookup by CVE/GHSA id.

Both delegate every outbound call to :mod:`integrations.depsdev` and are gated
together by ``core.config.external_package_lookup_enabled`` -- off means 404,
not an empty result, so an air-gapped deployment can hide the feature rather
than have every call time out.

Auth: role >= viewer (same floor as ``GET /v1/search``). Rate limits are
separate per endpoint (``core.config.external_package_lookup_rate_limit`` /
``external_advisory_lookup_rate_limit``) since one is a deliberate button
click and the other rides the ``/search`` page's debounced typing.
"""

from __future__ import annotations

import httpx
import structlog
from fastapi import APIRouter, Depends, Path, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import (
    external_advisory_lookup_rate_limit,
    external_package_lookup_enabled,
    external_package_lookup_rate_limit,
)
from core.db import get_db
from core.errors import problem_response
from core.ratelimit import _authenticated_user_key, limiter
from core.security import CurrentUser, require_role
from integrations.depsdev import (
    SYSTEM_SLUGS,
    DepsDevUpstreamError,
    lookup_advisory,
    lookup_package,
)
from schemas.external_packages import ExternalAdvisoryOut, ExternalPackageLookupOut
from services.external_package_usage import internal_usage_by_purl

router = APIRouter(prefix="/v1", tags=["external-packages"])
log = structlog.get_logger("external_packages.api")

_SORTED_SLUGS = ", ".join(sorted(SYSTEM_SLUGS))


def _not_enabled(request: Request) -> Response:
    return problem_response(
        status_code=status.HTTP_404_NOT_FOUND,
        title="Not Found",
        detail="the external package lookup is not enabled on this deployment",
        instance=request.url.path,
    )


@router.get(
    "/external-packages",
    response_model=ExternalPackageLookupOut,
    summary="Look up a package on deps.dev by exact ecosystem and name",
    responses={
        200: {
            "description": (
                "Lookup result. `found=false` means deps.dev has no such "
                "package -- not an error."
            ),
        },
        401: {"description": "No / invalid bearer token."},
        404: {"description": "The lookup is disabled on this deployment."},
        422: {"description": "Unknown ecosystem, or an invalid package name."},
        502: {"description": "deps.dev did not answer cleanly."},
    },
)
@limiter.limit(external_package_lookup_rate_limit, key_func=_authenticated_user_key)
async def lookup_external_package(
    request: Request,
    ecosystem: str = Query(
        ...,
        max_length=64,
        description=f"One of: {_SORTED_SLUGS}.",
    ),
    name: str = Query(..., min_length=1, max_length=255),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("viewer")),
) -> Response:
    if not external_package_lookup_enabled():
        return _not_enabled(request)

    try:
        result = await lookup_package(ecosystem, name)
    except ValueError as exc:
        return problem_response(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Unprocessable Entity",
            detail=str(exc),
            instance=request.url.path,
        )
    except (DepsDevUpstreamError, httpx.HTTPError, httpx.InvalidURL) as exc:
        log.warning(
            "external_package_lookup_upstream_failure",
            ecosystem=ecosystem,
            error_type=type(exc).__name__,
        )
        return problem_response(
            status_code=status.HTTP_502_BAD_GATEWAY,
            title="Bad Gateway",
            detail="deps.dev did not answer this lookup cleanly",
            instance=request.url.path,
        )

    internal_projects = (
        await internal_usage_by_purl(session, actor=actor, purl=result.purl)
        if result.found and result.purl
        else []
    )

    # The only signal an operator has today into how much this feature is
    # actually used, or how often it comes back empty -- there is no metrics
    # series for it yet (upstream failures already warn above).
    log.info(
        "external_package_lookup_completed",
        ecosystem=ecosystem,
        found=result.found,
        advisory_count=result.advisory_count,
    )

    out = ExternalPackageLookupOut(
        ecosystem=result.ecosystem,
        name=result.name,
        found=result.found,
        version=result.version,
        purl=result.purl,
        licenses=result.licenses,
        advisory_count=result.advisory_count,
        advisory_ids=result.advisory_ids,
        homepage_url=result.homepage_url,
        source_repo_url=result.source_repo_url,
        internal_projects=internal_projects,
    )
    return Response(
        content=out.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


@router.get(
    "/external-advisories/{advisory_id}",
    response_model=ExternalAdvisoryOut,
    summary="Look up advisory metadata on deps.dev by CVE or GHSA id",
    responses={
        200: {
            "description": (
                "Lookup result. `found=false` means deps.dev has no such "
                "advisory -- not an error."
            ),
        },
        401: {"description": "No / invalid bearer token."},
        404: {"description": "The lookup is disabled on this deployment."},
        422: {"description": "Invalid advisory id."},
        502: {"description": "deps.dev did not answer cleanly."},
    },
)
@limiter.limit(external_advisory_lookup_rate_limit, key_func=_authenticated_user_key)
async def lookup_external_advisory(
    request: Request,
    advisory_id: str = Path(..., min_length=1, max_length=64),
    _actor: CurrentUser = Depends(require_role("viewer")),
) -> Response:
    if not external_package_lookup_enabled():
        return _not_enabled(request)

    try:
        result = await lookup_advisory(advisory_id)
    except ValueError as exc:
        return problem_response(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="Unprocessable Entity",
            detail=str(exc),
            instance=request.url.path,
        )
    except (DepsDevUpstreamError, httpx.HTTPError, httpx.InvalidURL) as exc:
        log.warning(
            "external_advisory_lookup_upstream_failure",
            advisory_id=advisory_id[:64],
            error_type=type(exc).__name__,
        )
        return problem_response(
            status_code=status.HTTP_502_BAD_GATEWAY,
            title="Bad Gateway",
            detail="deps.dev did not answer this lookup cleanly",
            instance=request.url.path,
        )

    log.info(
        "external_advisory_lookup_completed",
        advisory_id=advisory_id[:64],
        found=result.found,
    )

    out = ExternalAdvisoryOut(
        advisory_id=result.advisory_id,
        found=result.found,
        title=result.title,
        cvss3_score=result.cvss3_score,
        cvss3_vector=result.cvss3_vector,
        aliases=result.aliases,
    )
    return Response(
        content=out.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# slowapi's `@limiter.limit` wraps the endpoint with functools.wraps, whose
# `__globals__` points at slowapi's module. Under `from __future__ import
# annotations` FastAPI resolves the string annotations via
# get_type_hints(func, globalns=func.__globals__) and cannot see names defined
# here -- misclassifying the parameters and 422-ing every request. Seed the
# names the wrapper needs into its `__globals__` (the dict is mutable even
# though the attribute is read-only). Mirrors api/v1/search.py + auth.py.
for _endpoint, _names in (
    (
        lookup_external_package,
        ("AsyncSession", "Request", "Response", "Depends", "Query", "CurrentUser"),
    ),
    (
        lookup_external_advisory,
        ("Request", "Response", "Depends", "Path", "CurrentUser"),
    ),
):
    for _name in _names:
        if _name in globals():
            _endpoint.__globals__.setdefault(_name, globals()[_name])
del _endpoint, _names, _name


__all__ = ["router"]
