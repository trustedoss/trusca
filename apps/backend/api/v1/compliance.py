"""
Compliance unified-grid API — W9-#58.

One read-only endpoint:

  GET /v1/projects/{project_id}/compliance     Unified license + obligation grid

Why read-only?
--------------
Both upstream surfaces (licenses, obligations) are read-only — categories and
obligation rows are produced by the scan pipeline and the catalog, not by an
analyst workflow. There is no PATCH counterpart.

Cross-team policy
-----------------
- 403 on cross-team. Existence of a project is not a secret across teams —
  mirrors :file:`api/v1/licenses.py`.

All 4xx / 5xx responses are RFC 7807 ``application/problem+json``.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.errors import problem_response
from core.security import CurrentUser, require_role
from schemas.compliance import ComplianceListResponse, ComplianceRow
from schemas.license_detail import LicenseDistribution
from services.compliance_service import ComplianceError, list_project_compliance
from services.project_service import ProjectError
from services.scan_resolution import SnapshotScanNotFound

router = APIRouter(prefix="/v1", tags=["compliance"])
log = structlog.get_logger("compliance.api")


def _problem_for_error(request: Request, exc: ProjectError) -> Response:
    """Convert a compliance/project domain exception into a Problem Details response."""
    return problem_response(
        status_code=exc.status_code,
        title=exc.title,
        detail=str(exc) or exc.title,
        instance=request.url.path,
    )


def _problem_for_snapshot_not_found(request: Request) -> Response:
    """RFC 7807 404 for an unresolvable ``?scan_id=`` snapshot anchor.

    Existence-hide: uniform detail whether the scan is nonexistent, in
    another project (IDOR probe), or not succeeded.
    """
    return problem_response(
        status_code=status.HTTP_404_NOT_FOUND,
        title="Scan Snapshot Not Found",
        detail="No succeeded scan with that id exists for this project.",
        instance=request.url.path,
    )


@router.get(
    "/projects/{project_id}/compliance",
    response_model=ComplianceListResponse,
    summary=(
        "Unified Compliance grid (licenses × obligations) for the project's "
        "latest succeeded scan"
    ),
)
async def list_project_compliance_endpoint(
    request: Request,
    project_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    category: list[str] | None = Query(
        default=None,
        description=(
            "Filter rows by license category. Repeat the parameter to OR-join "
            "multiple values (e.g. ?category=forbidden&category=conditional)."
        ),
    ),
    kind: list[str] | None = Query(
        default=None,
        description=(
            "Filter rows to licenses that carry at least one obligation of "
            "the given kind. Repeat to OR-join."
        ),
    ),
    search: str | None = Query(
        default=None,
        max_length=255,
        description=(
            "Substring match against SPDX id and license name. LIKE "
            "metacharacters are escaped server-side."
        ),
    ),
    has_obligations: bool | None = Query(
        default=None,
        description=(
            "When true, return only licenses that carry at least one "
            "obligation row. When false, return only licenses with NONE. "
            "Ignored when ``kind`` is also given."
        ),
    ),
    sort: str = Query(
        default="category",
        pattern=r"^(category|license_name|spdx_id|affected_count)$",
    ),
    order: str = Query(default="desc", pattern=r"^(asc|desc)$"),
    scan_id: uuid.UUID | None = Query(
        default=None,
        description=(
            "Optional release-snapshot anchor (feature #28). When given, the "
            "grid reflects this specific succeeded scan instead of the "
            "project's latest succeeded scan. Must belong to this project "
            "and be succeeded, else 404."
        ),
    ),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        items, distribution, total, generated_at = await list_project_compliance(
            session,
            project_id=project_id,
            actor=actor,
            limit=limit,
            offset=offset,
            categories=category,
            kinds=kind,
            search=search,
            has_obligations=has_obligations,
            sort=sort,
            order=order,
            snapshot_scan_id=scan_id,
        )
    except SnapshotScanNotFound:
        return _problem_for_snapshot_not_found(request)
    except (ComplianceError, ProjectError) as exc:
        return _problem_for_error(request, exc)

    body = ComplianceListResponse(
        items=[ComplianceRow.model_validate(item) for item in items],
        distribution=LicenseDistribution(**distribution),
        total=total,
        limit=limit,
        offset=offset,
        generated_at=generated_at,
    )
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


__all__ = ["router"]
