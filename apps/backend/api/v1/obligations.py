"""
Obligations API — Phase 3 PR #13 (Obligations tab + NOTICE generator).

Three read-only endpoints, all project-scoped:

  GET /v1/projects/{project_id}/obligations
      Per-(license, kind) rows + per-kind distribution for the latest scan.
  GET /v1/projects/{project_id}/obligations/{obligation_id}
      Drawer detail (license + obligation text + affected components).
  GET /v1/projects/{project_id}/notice
      NOTICE attribution body. Defaults to text/plain inline; pass
      ``download=true`` for an attachment ``Content-Disposition``, or
      ``format=markdown`` for a markdown variant.

Why read-only?
--------------
Obligations are a per-license policy catalog: the row carries no analyst
workflow state, no transition matrix, no audit log. Authority lives upstream
(ORT rule packs, future SPDX exception ingestion, manual catalog edits via
admin tooling), not in user-facing PATCH endpoints. Mirrors the Licenses
tab (PR #12).

Cross-team policy
-----------------
- All endpoints (List, Notice, Detail): 404 on cross-team (security-reviewer
  Low #4). Project-scoped reads existence-hide so a non-member cannot
  distinguish "exists but forbidden" from "does not exist" — uniform with the
  report / source-tree / SBOM endpoints.

All cross-team rejections emit ``log.warning("authz.cross_team_attempt", ...)``
in the service layer *before* raising, so SOC tooling sees the rejection
regardless of the HTTP status the caller observes.

All 4xx/5xx responses are RFC 7807 ``application/problem+json``.
"""

from __future__ import annotations

import re
import urllib.parse
import uuid

import structlog
from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.errors import problem_response
from core.ratelimit import limiter
from core.security import CurrentUser, require_role
from schemas.obligation_detail import (
    AffectedComponentByObligation,
    ObligationDetailResponse,
    ObligationListItem,
    ObligationListResponse,
)
from services.obligation_service import (
    ObligationError,
    generate_notice,
    get_obligation_detail,
    list_project_obligations,
)
from services.project_service import (
    ProjectError,
    ProjectForbidden,
    ProjectNotFound,
    get_project,
)
from services.report_download_service import record_report_download
from services.scan_resolution import SnapshotScanNotFound, latest_succeeded_scan_id

router = APIRouter(prefix="/v1", tags=["obligations"])
log = structlog.get_logger("obligations.api")


# ---------------------------------------------------------------------------
# Error translation helper
# ---------------------------------------------------------------------------


def _problem_for_obligation_error(
    request: Request,
    exc: ProjectError,
) -> Response:
    """Convert an obligation/project domain exception into a Problem Details response."""
    return problem_response(
        status_code=exc.status_code,
        title=exc.title,
        detail=str(exc) or exc.title,
        instance=request.url.path,
    )


def _problem_for_snapshot_not_found(request: Request) -> Response:
    """RFC 7807 404 for an unresolvable ``?scan_id=`` snapshot anchor (feature #28).

    Existence-hide: uniform detail whether the scan is nonexistent, in another
    project (IDOR probe), or not succeeded.
    """
    return problem_response(
        status_code=status.HTTP_404_NOT_FOUND,
        title="Scan Snapshot Not Found",
        detail="No succeeded scan with that id exists for this project.",
        instance=request.url.path,
    )


# ---------------------------------------------------------------------------
# GET /v1/projects/{project_id}/obligations
# ---------------------------------------------------------------------------


@router.get(
    "/projects/{project_id}/obligations",
    response_model=ObligationListResponse,
    summary="Per-(license, kind) obligation rows + distribution for the project's latest scan",
)
async def list_project_obligations_endpoint(
    request: Request,
    project_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    kind: list[str] | None = Query(default=None),
    category: list[str] | None = Query(default=None),
    search: str | None = Query(default=None, max_length=255),
    sort: str = Query(
        default="category",
        pattern=r"^(category|license_name|kind|affected_count)$",
    ),
    order: str = Query(default="desc", pattern=r"^(asc|desc)$"),
    scan_id: uuid.UUID | None = Query(
        default=None,
        description=(
            "Optional release-snapshot anchor (feature #28). When given, list "
            "obligation rows of this SPECIFIC succeeded scan instead of the "
            "project's latest succeeded scan. Must belong to this project and be "
            "succeeded, else 404. Omit for the default latest-succeeded behaviour."
        ),
    ),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        items, distribution, total = await list_project_obligations(
            session,
            project_id=project_id,
            actor=actor,
            limit=limit,
            offset=offset,
            kinds=kind,
            categories=category,
            search=search,
            sort=sort,
            order=order,
            snapshot_scan_id=scan_id,
        )
    except SnapshotScanNotFound:
        return _problem_for_snapshot_not_found(request)
    except (ObligationError, ProjectError) as exc:
        return _problem_for_obligation_error(request, exc)

    body = ObligationListResponse(
        items=[ObligationListItem.model_validate(item) for item in items],
        distribution=distribution,
        total=total,
    )
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# GET /v1/projects/{project_id}/obligations/{obligation_id}
# ---------------------------------------------------------------------------


@router.get(
    "/projects/{project_id}/obligations/{obligation_id}",
    response_model=ObligationDetailResponse,
    summary="Obligation drawer payload (404 if invisible to caller within this project)",
    responses={
        404: {
            "description": (
                "Obligation does not exist, exists in a team the caller "
                "cannot access, or is not surfaced by the project's latest "
                "scan. Returned in lieu of 403 to avoid leaking existence."
            ),
        },
    },
)
async def get_obligation_endpoint(
    request: Request,
    project_id: uuid.UUID,
    obligation_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        payload = await get_obligation_detail(
            session,
            project_id=project_id,
            obligation_id=obligation_id,
            actor=actor,
        )
    except (ObligationError, ProjectError) as exc:
        return _problem_for_obligation_error(request, exc)

    body = ObligationDetailResponse(
        id=payload["id"],
        license_id=payload["license_id"],
        license_spdx_id=payload["license_spdx_id"],
        license_name=payload["license_name"],
        license_category=payload["license_category"],
        license_reference_url=payload["license_reference_url"],
        kind=payload["kind"],
        text=payload["text"],
        text_ko=payload["text_ko"],
        text_truncated=payload["text_truncated"],
        link=payload["link"],
        affected_components=[
            AffectedComponentByObligation.model_validate(c)
            for c in payload["affected_components"]
        ],
        affected_components_truncated=payload["affected_components_truncated"],
        affected_components_total=payload["affected_components_total"],
        created_at=payload["created_at"],
        updated_at=payload["updated_at"],
    )
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# GET /v1/projects/{project_id}/notice
# ---------------------------------------------------------------------------

# Defense-in-depth: the project name flows into Content-Disposition's filename
# parameter when ``download=true``. Quote it conservatively — RFC 6266 allows
# UTF-8 via ``filename*=UTF-8''…`` but we want to keep the ASCII fallback
# free of CR/LF and quoting metacharacters, so we strip everything outside
# ``[A-Za-z0-9._-]`` and fall back to ``NOTICE`` if the result is empty.
_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_filename_token(name: str) -> str:
    token = _FILENAME_SAFE_RE.sub("-", name).strip("-")
    return token or "project"


def _format_content_disposition(project_name: str, ext: str) -> str:
    """Build an RFC 6266-compliant ``Content-Disposition: attachment`` value.

    Emits both the ASCII ``filename=`` fallback (for legacy clients) and the
    UTF-8 ``filename*=UTF-8''…`` extended parameter (for browsers that
    understand it). The two parameters carry the same logical name; the
    ASCII version is sanitised with ``_safe_filename_token`` so a filesystem
    can persist it without quoting risks, while the UTF-8 version preserves
    the original project name (including non-ASCII characters) percent-
    encoded so the user sees a readable name in their downloads.
    """
    token = _safe_filename_token(project_name)
    ascii_filename = f"NOTICE-{token}.{ext}"
    utf8_full = f"NOTICE-{project_name}.{ext}"
    # RFC 5987 percent-encoding — quote() with empty `safe=` so even '/'
    # and ',' are escaped. The encoded value never contains characters
    # the header parser would treat specially.
    utf8_encoded = urllib.parse.quote(utf8_full, safe="")
    return (
        f'attachment; filename="{ascii_filename}"; '
        f"filename*=UTF-8''{utf8_encoded}"
    )


@router.get(
    "/projects/{project_id}/notice",
    summary="Compose a NOTICE attribution body for the project's latest scan",
    responses={
        200: {
            "description": (
                "NOTICE body in the requested format (plain text, markdown, or "
                "a self-contained HTML document). ``Content-Disposition`` is set "
                "to ``attachment`` when ``download=true``; otherwise the body "
                "streams inline."
            ),
            "content": {
                "text/plain": {},
                "text/markdown": {},
                "text/html": {},
            },
        },
        404: {
            "description": (
                "Project does not exist, or the caller is not a member of the "
                "project's team (existence-hidden)."
            )
        },
        429: {
            "description": "Rate limit exceeded for this client IP.",
            "content": {"application/problem+json": {}},
        },
    },
)
@limiter.limit("10/minute")
async def get_project_notice_endpoint(
    request: Request,
    project_id: uuid.UUID,
    fmt: str = Query(
        default="text",
        alias="format",
        pattern=r"^(text|markdown|html)$",
        description=(
            "Output format. ``text`` returns text/plain, ``markdown`` returns "
            "text/markdown, ``html`` returns a self-contained text/html document."
        ),
    ),
    download: bool = Query(
        default=False,
        description=(
            "When true, set ``Content-Disposition: attachment`` so browsers "
            "save the body as a file. Default is inline."
        ),
    ),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        payload = await generate_notice(
            session,
            project_id=project_id,
            actor=actor,
            fmt=fmt,
        )
    except (ObligationError, ProjectError) as exc:
        return _problem_for_obligation_error(request, exc)

    media_type = {
        "text": "text/plain; charset=utf-8",
        "markdown": "text/markdown; charset=utf-8",
        "html": "text/html; charset=utf-8",
    }[fmt]
    headers = {
        "X-Notice-Generated-At": payload["generated_at"]
        .replace(microsecond=0)
        .isoformat(),
        "X-Notice-License-Count": str(payload["license_count"]),
        "X-Notice-Obligation-Count": str(payload["obligation_count"]),
    }
    if fmt == "html":
        # Defense-in-depth (security-reviewer Low #5): the HTML NOTICE is served
        # text/html, same-origin with the API, and inline when ``download`` is
        # false. Escaping in ``_render_notice_html`` is the primary XSS control;
        # a restrictive CSP neutralises any future escaping regression. The
        # document only needs its own inline ``<style>`` — no scripts, no
        # external assets — so lock everything else down.
        headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'"
    if download:
        ext = {"text": "txt", "markdown": "md", "html": "html"}[fmt]
        headers["Content-Disposition"] = _format_content_disposition(
            payload["project_name"], ext
        )

    # Emit the Reports-center history row (W3 #32a). ``generate_notice`` has
    # already enforced existence-hide team membership, so reaching this point
    # means the actor is allowed to read this project. We load the project for
    # its ``team_id`` (denormalised onto every history row so admin / team-wide
    # queries do not need a join) and resolve the latest succeeded scan as the
    # snapshot anchor, mirroring what the NOTICE service rendered against.
    # Best-effort: ANY DB error inside the helper is logged + swallowed.
    body_bytes = (
        payload["body"].encode("utf-8")
        if isinstance(payload["body"], str)
        else payload["body"]
    )
    try:
        project = await get_project(session, project_id=project_id, actor=actor)
    except (ProjectError, ProjectNotFound, ProjectForbidden):
        # Defensive: the prior generate_notice call has already authorised the
        # caller, so a forbid/notfound here would be a race (project deleted
        # between the two reads). Skip the emit silently — never 5xx.
        project = None
    if project is not None:
        resolved_scan_id = await latest_succeeded_scan_id(session, project_id)
        await record_report_download(
            session,
            project=project,
            scan_id=resolved_scan_id,
            user=actor,
            report_type="notice",
            fmt=fmt,
            size_bytes=len(body_bytes),
            request=request,
        )

    return Response(
        content=payload["body"],
        status_code=status.HTTP_200_OK,
        media_type=media_type,
        headers=headers,
    )


# slowapi's `@limiter.limit` wraps the endpoint with functools.wraps, which
# preserves __annotations__ but inherits slowapi's own module as the
# wrapper's __globals__. With `from __future__ import annotations` enabled,
# FastAPI calls `typing.get_type_hints()` on the wrapper to resolve string
# annotations and fails to find names like `uuid` and `AsyncSession` —
# Pydantic raises "TypeAdapter is not fully defined" and the endpoint
# returns 500 on every request. Mirror auth.py's fix by patching the names
# the wrapper needs into its `__globals__` (we can't reassign __globals__
# itself — it's read-only — but mutating the dict in place works).
for _name in ("uuid", "AsyncSession", "Request", "Response", "Depends", "Query", "CurrentUser"):
    if _name in globals():
        get_project_notice_endpoint.__globals__.setdefault(_name, globals()[_name])
del _name


__all__ = ["router"]
