"""
Vulnerabilities API — Phase 3 PR #11 (Vulnerabilities tab + drawer).

Three endpoints:

  GET   /v1/projects/{project_id}/vulnerabilities          List CVE findings
  GET   /v1/vulnerability_findings/{finding_id}            Drawer detail
  PATCH /v1/vulnerability_findings/{finding_id}/status     Workflow transition

All routes require role >= developer; the `→ suppressed` transition is gated
inside the service layer to require role >= team_admin within the project's
team. Cross-team access (IDOR) is enforced inside the service: 403 for the
list endpoint (team-membership signal is not a secret here, mirrors PR #10
projects), 404 for detail / status (existence-hide cross-team rows).

All 4xx/5xx responses are RFC 7807 `application/problem+json`.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.errors import problem_response
from core.security import CurrentUser, require_role
from schemas.vulnerability_detail import (
    AffectedComponent,
    UpgradeRecommendation,
    VulnerabilityDetailResponse,
    VulnerabilityListItem,
    VulnerabilityListResponse,
    VulnerabilityStatusHistoryEntry,
    VulnerabilityStatusUpdate,
)
from services.project_service import ProjectError
from services.scan_resolution import SnapshotScanNotFound
from services.vulnerability_service import (
    VulnerabilityConflict,
    VulnerabilityError,
    VulnerabilityInvalidTransition,
    get_vulnerability_detail,
    list_project_vulnerabilities,
    update_vulnerability_status,
)

router = APIRouter(prefix="/v1", tags=["vulnerabilities"])
log = structlog.get_logger("vulnerabilities.api")


# ---------------------------------------------------------------------------
# Error translation helpers
# ---------------------------------------------------------------------------


def _problem_for_vulnerability_error(request: Request, exc: ProjectError) -> Response:
    """
    Convert a vulnerability/project domain exception into a Problem Details
    response. Keeps the per-exception switch small: VulnerabilityInvalidTransition
    and VulnerabilityConflict carry extension data; everything else uses the
    base envelope from `problem_response`.
    """
    if isinstance(exc, VulnerabilityInvalidTransition):
        # RFC 7807 §3.2 explicitly allows extension members. We surface the
        # legal target set so the UI can disable buttons for invalid moves.
        return problem_response(
            status_code=exc.status_code,
            title=exc.title,
            detail=str(exc) or exc.title,
            instance=request.url.path,
            allowed_to=list(exc.allowed_to),
        )
    return problem_response(
        status_code=exc.status_code,
        title=exc.title,
        detail=str(exc) or exc.title,
        instance=request.url.path,
    )


def _problem_for_snapshot_not_found(request: Request) -> Response:
    """RFC 7807 404 for an unresolvable ``?scan_id=`` snapshot anchor (feature #28).

    Existence-hide: the detail is uniform whether the scan is nonexistent,
    belongs to another project (IDOR probe), or is not succeeded — so the caller
    learns nothing about whether the id exists elsewhere.
    """
    return problem_response(
        status_code=status.HTTP_404_NOT_FOUND,
        title="Scan Snapshot Not Found",
        detail="No succeeded scan with that id exists for this project.",
        instance=request.url.path,
    )


# ---------------------------------------------------------------------------
# GET /v1/projects/{project_id}/vulnerabilities
# ---------------------------------------------------------------------------


@router.get(
    "/projects/{project_id}/vulnerabilities",
    response_model=VulnerabilityListResponse,
    summary="Paginated CVE findings for the project's latest scan",
)
async def list_project_vulnerabilities_endpoint(
    request: Request,
    project_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    search: str | None = Query(default=None, max_length=255),
    severity: list[str] | None = Query(default=None),
    finding_status: list[str] | None = Query(default=None, alias="status"),
    license_category: list[str] | None = Query(
        default=None,
        description=(
            "W2 #33 — License risk-axis filter. Repeatable; accepted values: "
            "``forbidden``, ``conditional``, ``allowed``, ``unknown`` (the cv "
            "had no license finding in this scan). Unknown values are dropped, "
            "so a query that filters ONLY by unknown values returns an empty "
            "page (not a 422). Omit to include all categories."
        ),
    ),
    min_epss: float | None = Query(
        default=None,
        ge=0,
        le=1,
        description=(
            "Keep only findings whose CVE has an EPSS exploit-probability >= this "
            "threshold, in [0, 1]. CVEs with no published EPSS score are excluded. "
            "Omit to disable EPSS filtering."
        ),
    ),
    reachable: str | None = Query(
        default=None,
        pattern=r"^(true|false|unknown)$",
        description=(
            "Tri-state reachability filter (v2.3). ``true`` → only findings whose "
            "vulnerable symbol is reachable on the call graph; ``false`` → only "
            "findings an analyser proved NOT reachable; ``unknown`` → only "
            "not-analysed findings (reachable IS NULL). Omit to disable the "
            "reachability filter."
        ),
    ),
    sort: str = Query(
        default="severity",
        pattern=r"^(severity|cvss|status|discovered_at|epss|reachable)$",
        description=(
            "Sort key. ``reachable`` ranks reachable findings first (then "
            "not-analysed, then proven-unreachable), tie-broken by severity desc."
        ),
    ),
    order: str = Query(default="desc", pattern=r"^(asc|desc)$"),
    scan_id: uuid.UUID | None = Query(
        default=None,
        description=(
            "Optional release-snapshot anchor (feature #28). When given, list CVE "
            "findings of this SPECIFIC succeeded scan instead of the project's "
            "latest succeeded scan. Must belong to this project and be succeeded, "
            "else 404. Omit for the default latest-succeeded behaviour."
        ),
    ),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        items, total = await list_project_vulnerabilities(
            session,
            project_id=project_id,
            actor=actor,
            limit=limit,
            offset=offset,
            search=search,
            severity=severity,
            status=finding_status,
            license_category=license_category,
            min_epss=min_epss,
            reachable=reachable,
            sort=sort,
            order=order,
            snapshot_scan_id=scan_id,
        )
    except SnapshotScanNotFound:
        return _problem_for_snapshot_not_found(request)
    except (VulnerabilityError, ProjectError) as exc:
        return _problem_for_vulnerability_error(request, exc)

    body = VulnerabilityListResponse(
        items=[VulnerabilityListItem.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# GET /v1/vulnerability_findings/{finding_id}
# ---------------------------------------------------------------------------


def _detail_response(payload: dict[str, Any]) -> Response:
    """Shared serializer for the two endpoints that return a detail payload."""
    body = VulnerabilityDetailResponse(
        id=payload["id"],
        project_id=payload["project_id"],
        scan_id=payload["scan_id"],
        cve_id=payload["cve_id"],
        severity=payload["severity"],
        cvss_score=payload["cvss_score"],
        epss_score=payload["epss_score"],
        epss_percentile=payload["epss_percentile"],
        cvss_vector=payload["cvss_vector"],
        summary=payload["summary"],
        details=payload["details"],
        references=payload["references"],
        published_at=payload["published_at"],
        status=payload["status"],
        analysis_state=payload["analysis_state"],
        analysis_justification=payload["analysis_justification"],
        analysis_source=payload["analysis_source"],
        vex_origin=payload["vex_origin"],
        analyst_user_id=payload["analyst_user_id"],
        analyzed_at=payload["analyzed_at"],
        reachable=payload["reachable"],
        reachability_source=payload["reachability_source"],
        reachability_analyzed_at=payload["reachability_analyzed_at"],
        affected_components=[
            AffectedComponent.model_validate(c) for c in payload["affected_components"]
        ],
        status_history=[
            VulnerabilityStatusHistoryEntry.model_validate(h) for h in payload["status_history"]
        ],
        upgrade_recommendation=(
            UpgradeRecommendation.model_validate(payload["upgrade_recommendation"])
            if payload.get("upgrade_recommendation") is not None
            else None
        ),
        created_at=payload["created_at"],
        updated_at=payload["updated_at"],
    )
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


@router.get(
    "/vulnerability_findings/{finding_id}",
    response_model=VulnerabilityDetailResponse,
    summary="Vulnerability finding drawer payload (404 if invisible to caller)",
)
async def get_vulnerability_finding_endpoint(
    request: Request,
    finding_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        payload = await get_vulnerability_detail(
            session,
            finding_id=finding_id,
            actor=actor,
        )
    except (VulnerabilityError, ProjectError) as exc:
        return _problem_for_vulnerability_error(request, exc)
    return _detail_response(payload)


# ---------------------------------------------------------------------------
# PATCH /v1/vulnerability_findings/{finding_id}/status
# ---------------------------------------------------------------------------


@router.patch(
    "/vulnerability_findings/{finding_id}/status",
    response_model=VulnerabilityDetailResponse,
    summary="Transition a vulnerability finding's VEX status (audit-logged)",
    responses={
        200: {"description": "Status transitioned. Body is the post-commit detail payload."},
        403: {
            "description": (
                "Caller's role is insufficient (e.g. developer attempting `→ suppressed`)."
            ),
        },
        404: {
            "description": (
                "Finding does not exist, or exists in a team the caller cannot access. "
                "Returned in lieu of 403 to avoid leaking existence."
            ),
        },
        409: {"description": "if_match snapshot did not match the current updated_at."},
        422: {
            "description": (
                "Transition is not allowed by the workflow matrix. The "
                "Problem Details body carries an `allowed_to` extension "
                "listing the legal next states from the current status."
            ),
        },
    },
)
async def update_vulnerability_status_endpoint(
    request: Request,
    finding_id: uuid.UUID,
    payload: VulnerabilityStatusUpdate,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        result = await update_vulnerability_status(
            session,
            finding_id=finding_id,
            actor=actor,
            target_status=payload.status,
            justification=payload.justification,
            if_match=payload.if_match,
        )
    except VulnerabilityConflict as exc:
        # 409 — distinct from 422 because it indicates concurrent modification,
        # not an invalid request shape.
        return _problem_for_vulnerability_error(request, exc)
    except (VulnerabilityError, ProjectError) as exc:
        return _problem_for_vulnerability_error(request, exc)
    return _detail_response(result)


__all__ = ["router"]
