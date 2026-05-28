"""
Project reports HTTP surface — Scan-gap G2 (vulnerability PDF report).

Endpoint:
  - GET /v1/projects/{project_id}/vulnerability-report.pdf

A single authenticated download that composes a project-level risk report:
risk summary + severity / license distribution + vulnerabilities (grouped by
severity, with CVE id / CVSS) + components. It reuses the existing read
services (``project_detail_service`` + ``vulnerability_service``) so there are
no duplicate queries, and renders HTML → PDF via weasyprint
(``services.report_service``).

Auth + IDOR
-----------
Mirrors the NOTICE endpoint (``obligations.get_project_notice_endpoint``):
``require_role("developer")`` (role >= developer) plus a team-membership guard
via ``assert_team_access``. Cross-team callers see **404 existence-hide** —
the same posture as the SBOM export, because this document can leak structural
details (component names, versions, CVEs) about a project.

All 4xx / 5xx responses are RFC 7807 ``application/problem+json``; the success
response is a PDF download with ``Content-Disposition: attachment``.

PDF generation is in-request (the data is already materialized in PostgreSQL;
weasyprint renders a bounded document in seconds), so this is NOT a Celery
task — same rationale as the SBOM export and NOTICE generator. The blocking
weasyprint call is offloaded to the threadpool (``run_in_threadpool``) so it
never stalls the async event loop under concurrent load.
"""

from __future__ import annotations

import re
import urllib.parse
import uuid
from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession

from core.authz import assert_team_access
from core.db import get_db
from core.errors import problem_response
from core.ratelimit import limiter
from core.security import CurrentUser, require_role
from schemas.report_download import ReportHistoryResponse, ReportType
from services.project_detail_service import (
    get_project_overview,
    list_components_for_project,
)
from services.project_service import (
    ProjectError,
    ProjectForbidden,
    ProjectNotFound,
    get_project,
)
from services.report_download_service import (
    PAGE_SIZE_DEFAULT,
    PAGE_SIZE_MAX,
    PAGE_SIZE_MIN,
    ReportHistoryError,
    ReportHistoryNotFound,
    list_report_history,
    record_report_download,
)
from services.report_service import (
    ReportRenderingError,
    build_report_html,
    render_report_pdf,
)
from services.scan_resolution import latest_succeeded_scan_id
from services.vulnerability_service import list_project_vulnerabilities

router = APIRouter(prefix="/v1", tags=["reports"])
log = structlog.get_logger("reports.api")

# How many rows we pull into the report. The HTML builder caps again at its
# own limit; this is the SQL-side bound so we never materialize an unbounded
# result set for a pathological scan.
_REPORT_ROW_LIMIT = 1000


# ---------------------------------------------------------------------------
# Content-Disposition filename helper (RFC 6266) — mirrors obligations.py
# ---------------------------------------------------------------------------

# The project name flows into the Content-Disposition filename. Strip
# everything outside ``[A-Za-z0-9._-]`` for the ASCII fallback so a filesystem
# can persist it without quoting risks (and so CR/LF can never reach the
# header), then carry the original name percent-encoded in the UTF-8 extended
# parameter so the user still sees a readable download name.
_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_filename_token(name: str) -> str:
    token = _FILENAME_SAFE_RE.sub("-", name).strip("-")
    return token or "project"


def _format_content_disposition(project_name: str) -> str:
    """Build an RFC 6266 ``Content-Disposition: attachment`` value for the PDF.

    Emits both the ASCII ``filename=`` fallback and the UTF-8
    ``filename*=UTF-8''…`` extended parameter, exactly like the NOTICE
    endpoint. Filename shape: ``vulnerability-report-<name>.pdf``.
    """
    token = _safe_filename_token(project_name)
    ascii_filename = f"vulnerability-report-{token}.pdf"
    utf8_full = f"vulnerability-report-{project_name}.pdf"
    utf8_encoded = urllib.parse.quote(utf8_full, safe="")
    return f'attachment; filename="{ascii_filename}"; filename*=UTF-8\'\'{utf8_encoded}'


# ---------------------------------------------------------------------------
# Error translation — existence-hide forbidden as 404 (SBOM posture)
# ---------------------------------------------------------------------------


def _problem_for_project_error(request: Request, exc: ProjectError) -> Response:
    if isinstance(exc, ProjectForbidden):
        return problem_response(
            status_code=status.HTTP_404_NOT_FOUND,
            title="Project Not Found",
            detail="Project not found.",
            instance=request.url.path,
        )
    return problem_response(
        status_code=exc.status_code,
        title=exc.title,
        detail=str(exc) or exc.title,
        instance=request.url.path,
    )


# ---------------------------------------------------------------------------
# GET /v1/projects/{project_id}/vulnerability-report.pdf
# ---------------------------------------------------------------------------


@router.get(
    "/projects/{project_id}/vulnerability-report.pdf",
    summary="Download a vulnerability PDF report for the project's latest scan",
    response_class=Response,
    responses={
        200: {
            "description": "PDF report download",
            "content": {"application/pdf": {}},
        },
        401: {"description": "Authentication required"},
        404: {"description": "Project not found or not accessible"},
        500: {
            "description": "PDF rendering failed (e.g. weasyprint unavailable)",
            "content": {"application/problem+json": {}},
        },
    },
)
async def get_vulnerability_report_pdf_endpoint(
    request: Request,
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    # IDOR guard — reuse ``get_project`` so the "may the actor see this
    # project?" decision lives in one place. ProjectForbidden surfaces as 404
    # to outsiders (existence-hide), matching the SBOM export.
    try:
        project = await get_project(session, project_id=project_id, actor=actor)
    except (ProjectNotFound, ProjectForbidden) as exc:
        return _problem_for_project_error(request, exc)
    except ProjectError as exc:  # pragma: no cover - defensive catch-all
        return _problem_for_project_error(request, exc)

    # Belt-and-braces: re-assert team membership through the central audit
    # helper so a cross_team_attempt is logged for any unexpected gap.
    assert_team_access(
        actor,
        project.team_id,
        log=log,
        resource="vulnerability_report",
        resource_id=str(project_id),
        deny=lambda: ProjectForbidden(f"actor is not a member of team {project.team_id}"),
    )

    # Gather report data by reusing the existing read services — no duplicate
    # queries. Each enforces its own team guard internally as well.
    overview = await get_project_overview(session, project_id=project_id, actor=actor)
    components, components_total = await list_components_for_project(
        session,
        project_id=project_id,
        actor=actor,
        limit=_REPORT_ROW_LIMIT,
        offset=0,
        sort="severity",
        order="desc",
    )
    vulnerabilities, vulnerabilities_total, _ = await list_project_vulnerabilities(
        session,
        project_id=project_id,
        actor=actor,
        limit=_REPORT_ROW_LIMIT,
        offset=0,
        sort="severity",
        order="desc",
    )

    html_str = build_report_html(
        project_name=overview["project_name"],
        generated_at=datetime.now(tz=UTC),
        risk_score=overview["risk_score"],
        total_components=overview["total_components"],
        severity_distribution=overview["severity_distribution"],
        license_distribution=overview["license_distribution"],
        components=components,
        vulnerabilities=vulnerabilities,
        components_total=components_total,
        vulnerabilities_total=vulnerabilities_total,
    )

    # Client-abandonment guard (Tier 6): skip the expensive weasyprint render if
    # the caller has already disconnected (closed the tab / hit a download then
    # navigated away). Avoids burning CPU rendering a PDF nobody will receive —
    # which, under a 10k-user load with abandoned downloads, is real waste.
    if await request.is_disconnected():
        log.info("report.client_disconnected_before_render", project_id=str(project_id))
        return Response(status_code=499)

    try:
        # weasyprint rendering is CPU-bound and blocking; this endpoint is
        # ``async def``, so calling it inline would block the event loop and
        # serialize every concurrent request on this worker. Offload to the
        # threadpool so the loop stays free under load (10k-user profile).
        pdf_bytes = await run_in_threadpool(render_report_pdf, html_str)
    except ReportRenderingError as exc:
        # weasyprint missing (image not yet rebuilt) or a render failure.
        # Log with the stack and return a 500 problem+json — never a stack to
        # the caller.
        log.error(
            "report.render_failed",
            project_id=str(project_id),
            exc_info=exc,
        )
        return problem_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            title="Report Rendering Failed",
            detail="The vulnerability report could not be generated.",
            instance=request.url.path,
        )

    log.info(
        "report.generated",
        project_id=str(project_id),
        component_count=components_total,
        vulnerability_count=vulnerabilities_total,
        pdf_bytes=len(pdf_bytes),
    )

    # Emit the Reports-center history row (W3 #32a). Resolve the latest
    # succeeded scan so the row carries the same anchor the report was rendered
    # against; ``None`` is a valid value (project has never had a succeeded
    # scan) and the column is nullable. Best-effort: ANY DB error inside the
    # helper is logged and swallowed so a 5xx never reaches the caller for a
    # download that has already succeeded.
    resolved_scan_id = await latest_succeeded_scan_id(session, project_id)
    await record_report_download(
        session,
        project=project,
        scan_id=resolved_scan_id,
        user=actor,
        report_type="vuln_pdf",
        fmt="pdf",
        size_bytes=len(pdf_bytes),
        request=request,
    )

    headers = {
        "content-disposition": _format_content_disposition(overview["project_name"]),
    }
    return Response(
        content=pdf_bytes,
        status_code=status.HTTP_200_OK,
        media_type="application/pdf",
        headers=headers,
    )


# ---------------------------------------------------------------------------
# GET /v1/projects/{project_id}/reports/history — W3 #32a-2
# ---------------------------------------------------------------------------


def _problem_for_history_error(request: Request, exc: ReportHistoryError) -> Response:
    return problem_response(
        status_code=exc.status_code,
        title=exc.title,
        detail=str(exc) or exc.title,
        instance=request.url.path,
    )


@router.get(
    "/projects/{project_id}/reports/history",
    summary="List download / export history for the project's Reports center",
    response_model=ReportHistoryResponse,
    responses={
        200: {"description": "Paginated report-download history (newest first)."},
        401: {"description": "Authentication required"},
        404: {
            "description": (
                "Project does not exist, or the caller is not a member of its "
                "team (existence-hidden — same envelope either way)."
            ),
            "content": {"application/problem+json": {}},
        },
        422: {
            "description": (
                "Invalid query parameter (unknown report_type, malformed scan_id, "
                "page/page_size out of range)."
            ),
            "content": {"application/problem+json": {}},
        },
        429: {
            "description": "Rate limit exceeded for this client.",
            "content": {"application/problem+json": {}},
        },
    },
)
@limiter.limit("10/minute")
async def list_project_report_history_endpoint(
    request: Request,
    project_id: uuid.UUID,
    type_: list[ReportType] | None = Query(
        default=None,
        alias="type",
        description=(
            "Optional filter — one or more report_type values to include. "
            "Repeat the parameter (``?type=notice&type=sbom``) for multi-select. "
            "Omit for all four types."
        ),
    ),
    scan_id: uuid.UUID | None = Query(
        default=None,
        description=(
            "Optional filter — return only rows where ``scan_id`` matches. "
            "Pair with ``type=sbom`` etc. to find all artefacts produced for "
            "one scan."
        ),
    ),
    page: int = Query(default=1, ge=1, description="1-based page number."),
    page_size: int = Query(
        default=PAGE_SIZE_DEFAULT,
        ge=PAGE_SIZE_MIN,
        le=PAGE_SIZE_MAX,
        description=f"Rows per page (1..{PAGE_SIZE_MAX}, default {PAGE_SIZE_DEFAULT}).",
    ),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        result = await list_report_history(
            session,
            project_id=project_id,
            viewer=actor,
            # mypy: Query() with list[ReportType] hands us ``list[str] | None``
            # because Literal -> str is a covariant narrowing. The service
            # re-validates each entry against REPORT_TYPE_VALUES.
            type_filter=type_,
            scan_id_filter=scan_id,
            page=page,
            page_size=page_size,
        )
    except ReportHistoryNotFound as exc:
        return _problem_for_history_error(request, exc)
    except ReportHistoryError as exc:
        # 422 for bad page / page_size / type — the router's Query() will
        # already 422 most of these, but a future internal caller might bypass
        # the dependency, so the service-layer raise is the authoritative gate.
        return problem_response(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title=exc.title,
            detail=str(exc) or exc.title,
            instance=request.url.path,
        )

    return Response(
        content=result.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# slowapi's `@limiter.limit` wraps the endpoint with functools.wraps, which
# preserves __annotations__ but inherits slowapi's own module as the
# wrapper's __globals__. With ``from __future__ import annotations`` enabled,
# FastAPI calls ``typing.get_type_hints()`` on the wrapper to resolve string
# annotations and fails to find names like ``uuid`` and ``AsyncSession`` —
# Pydantic raises "TypeAdapter is not fully defined" and the endpoint returns
# 500 on every request. Mirror auth.py / obligations.py by patching the names
# the wrapper needs into its ``__globals__`` (the dict can be mutated
# in-place; reassigning the attribute itself is read-only).
for _name in (
    "uuid",
    "AsyncSession",
    "Request",
    "Response",
    "Depends",
    "Query",
    "CurrentUser",
    "ReportType",
):
    if _name in globals():
        list_project_report_history_endpoint.__globals__.setdefault(_name, globals()[_name])
del _name


__all__ = ["router"]
