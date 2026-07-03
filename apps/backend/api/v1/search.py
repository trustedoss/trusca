"""
Global cross-project search API — BomLens parity backlog H-2.

Single endpoint:

  GET /v1/search?q=<str>&kinds=<csv>   Cross-project component + CVE search.

The router is thin: it validates nothing beyond FastAPI's query coercion and
delegates every read + the team-isolation decision to
:func:`services.search_service.global_search`, which routes ALL sub-queries
through the single choke-point :func:`core.authz.team_scope_filter`. A member
only ever sees hits in their own teams' projects; a super-admin sees all.

Auth: role >= developer (any authenticated user). Anonymous → 401
`application/problem+json` via the shared exception handlers.

Rate limit: per-actor (``_authenticated_user_key`` → ``user:<sub>``) using the
dedicated ``search`` budget (``SEARCH_RATE_LIMIT``, default 20/minute — tighter
than the CI-poll ``api_read`` budget because each keystroke fires a leading-
wildcard scan), so a cross-project scan is throttled per user rather than per IP
(many users / CI runners share an egress IP behind NAT).
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import search_rate_limit
from core.db import get_db
from core.ratelimit import _authenticated_user_key, limiter
from core.security import CurrentUser, require_role
from schemas.search import GlobalSearchResults
from services.search_service import global_search

router = APIRouter(prefix="/v1", tags=["search"])
log = structlog.get_logger("search.api")


@router.get(
    "/search",
    response_model=GlobalSearchResults,
    summary="Cross-project global search (components + vulnerabilities)",
    responses={
        200: {
            "description": (
                "Search results, each category capped at 20 rows. A query "
                "shorter than 2 chars (after trim) returns empty lists with a "
                "200 — not a 422 — so the debounced search palette can fire "
                "harmlessly on every keystroke."
            ),
        },
        401: {"description": "No / invalid bearer token."},
    },
)
@limiter.limit(search_rate_limit, key_func=_authenticated_user_key)
async def global_search_endpoint(
    request: Request,
    q: str = Query(
        ...,
        max_length=255,
        description=(
            "Search term. Trimmed; a term shorter than 2 characters yields "
            "empty results. Matched case-insensitively as a substring against "
            "component name/purl and CVE id. LIKE metacharacters (`%`, `_`) are "
            "escaped and matched literally."
        ),
    ),
    kinds: str | None = Query(
        default=None,
        description=(
            "Comma-separated categories to search: `components`, "
            "`vulnerabilities`. Unknown tokens are ignored; omit to search "
            "both."
        ),
    ),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    results = await global_search(session, actor=actor, q=q, kinds=kinds)
    return Response(
        content=results.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# slowapi's `@limiter.limit` wraps the endpoint with functools.wraps, whose
# `__globals__` points at slowapi's module. Under `from __future__ import
# annotations` FastAPI resolves the string annotations via
# get_type_hints(func, globalns=func.__globals__) and cannot see names defined
# here — misclassifying `q` / `kinds` and 422-ing every request. Seed the
# names the wrapper needs into its `__globals__` (the dict is mutable even
# though the attribute is read-only). Mirrors api/v1/sbom.py + auth.py.
for _name in ("AsyncSession", "Request", "Response", "Depends", "Query", "CurrentUser"):
    if _name in globals():
        global_search_endpoint.__globals__.setdefault(_name, globals()[_name])
del _name


__all__ = ["router"]
