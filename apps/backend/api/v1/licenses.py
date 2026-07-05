"""
Licenses API — Phase 3 PR #12 (Licenses tab + drawer).

Two read-only endpoints:

  GET /v1/projects/{project_id}/licenses     List per-license rows + distribution
  GET /v1/license_findings/{finding_id}      Drawer detail (license + ORT match)

Why read-only?
--------------
License findings carry no analyst workflow. ORT's ruleset is the authoritative
classifier — categories (allowed / conditional / forbidden / unknown) and
kinds (declared / concluded / detected) are produced by the scan pipeline and
are immutable once persisted. There is no PATCH counterpart in this PR.

Cross-team policy
-----------------
- List endpoint: 403 on cross-team. Existence of a project is not a secret
  across teams (PR #10 pattern; mirrors /vulnerabilities).
- Detail endpoint: 404 on cross-team. license_findings rows are keyed by an
  opaque UUID, so we existence-hide cross-team reads to avoid leaking that
  a given id is in use elsewhere (PR #11 pattern).

All 4xx/5xx responses are RFC 7807 ``application/problem+json``.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.errors import problem_response
from core.security import CurrentUser, require_role
from schemas.license_detail import (
    AffectedComponentByLicense,
    LicenseDetailResponse,
    LicenseDistribution,
    LicenseListItem,
    LicenseListResponse,
)
from services.license_service import (
    LicenseError,
    get_license_finding_detail,
    list_project_licenses,
)
from services.project_service import ProjectError
from services.scan_resolution import SnapshotScanNotFound

router = APIRouter(prefix="/v1", tags=["licenses"])
log = structlog.get_logger("licenses.api")


# ---------------------------------------------------------------------------
# Error translation helper
# ---------------------------------------------------------------------------


def _problem_for_license_error(
    request: Request,
    exc: ProjectError,
) -> Response:
    """Convert a license/project domain exception into a Problem Details response."""
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
# GET /v1/projects/{project_id}/licenses
# ---------------------------------------------------------------------------


@router.get(
    "/projects/{project_id}/licenses",
    response_model=LicenseListResponse,
    summary="Per-license rows + category distribution for the project's latest scan",
)
async def list_project_licenses_endpoint(
    request: Request,
    project_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    category: list[str] | None = Query(default=None),
    kind: list[str] | None = Query(default=None),
    search: str | None = Query(default=None, max_length=255),
    sort: str = Query(
        default="category",
        pattern=r"^(category|name|spdx_id|affected_count)$",
    ),
    order: str = Query(default="desc", pattern=r"^(asc|desc)$"),
    review_flag: str | None = Query(
        default=None,
        pattern=r"^(behavioral_use|non_commercial)$",
        description=(
            "Filter to licenses carrying an AI review flag (Phase D). "
            "behavioral_use = RAIL/Llama/Gemma/Falcon community licenses; "
            "non_commercial = CC-BY-NC…. Omit to list all licenses."
        ),
    ),
    scan_id: uuid.UUID | None = Query(
        default=None,
        description=(
            "Optional release-snapshot anchor (feature #28). When given, list "
            "license rows of this SPECIFIC succeeded scan instead of the project's "
            "latest succeeded scan. Must belong to this project and be succeeded, "
            "else 404. Omit for the default latest-succeeded behaviour."
        ),
    ),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        items, distribution, total = await list_project_licenses(
            session,
            project_id=project_id,
            actor=actor,
            limit=limit,
            offset=offset,
            categories=category,
            kinds=kind,
            search=search,
            sort=sort,
            order=order,
            review_flag=review_flag,
            snapshot_scan_id=scan_id,
        )
    except SnapshotScanNotFound:
        return _problem_for_snapshot_not_found(request)
    except (LicenseError, ProjectError) as exc:
        return _problem_for_license_error(request, exc)

    body = LicenseListResponse(
        items=[LicenseListItem.model_validate(item) for item in items],
        distribution=LicenseDistribution(**distribution),
        total=total,
    )
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# GET /v1/license_findings/{finding_id}
# ---------------------------------------------------------------------------


@router.get(
    "/license_findings/{finding_id}",
    response_model=LicenseDetailResponse,
    summary="License finding drawer payload (404 if invisible to caller)",
    responses={
        404: {
            "description": (
                "Finding does not exist, or exists in a team the caller cannot access. "
                "Returned in lieu of 403 to avoid leaking existence."
            ),
        },
    },
)
async def get_license_finding_endpoint(
    request: Request,
    finding_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        payload = await get_license_finding_detail(
            session,
            finding_id=finding_id,
            actor=actor,
        )
    except (LicenseError, ProjectError) as exc:
        return _problem_for_license_error(request, exc)

    body = LicenseDetailResponse(
        id=payload["id"],
        license_id=payload["license_id"],
        spdx_id=payload["spdx_id"],
        name=payload["name"],
        category=payload["category"],
        is_osi_approved=payload["is_osi_approved"],
        is_fsf_libre=payload["is_fsf_libre"],
        is_deprecated_license_id=payload["is_deprecated_license_id"],
        reference_url=payload["reference_url"],
        finding_kind=payload["finding_kind"],
        ort_match=payload["ort_match"],
        affected_components=[
            AffectedComponentByLicense.model_validate(c) for c in payload["affected_components"]
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


__all__ = ["router"]
