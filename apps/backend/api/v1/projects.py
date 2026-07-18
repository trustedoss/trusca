"""
Project CRUD + scan trigger API — Phase 2 PR #7.

Endpoints under `/v1/projects`:
  - POST   /v1/projects                    Create a project (role >= developer
                                            within target team).
  - GET    /v1/projects                    List projects visible to caller
                                            (paginated; team_id-clamped for
                                            non-super-admins).
  - GET    /v1/projects/{project_id}       Read one project (IDOR-safe).
  - PATCH  /v1/projects/{project_id}       Update mutable fields (role >=
                                            team_admin within project's team).
  - DELETE /v1/projects/{project_id}       Soft-delete (archive) the project.
  - POST   /v1/projects/{project_id}/scans Trigger a scan (skeleton — PR #7
                                            persists the row only; Celery
                                            enqueue lands in PR #8).

All 4xx/5xx responses are RFC 7807 problem+json. Domain exceptions raised by
the service layer (`services/project_service.py`,
`services/scan_service.py`) are translated to status codes here so the
service layer never leaks into the wire format.

Auth: every route requires a valid access token. The `require_role(...)`
dependency factory enforces minimum role (developer for read/create;
team_admin for update/archive). Cross-team data access (IDOR) is enforced
inside the service: this router does NOT decide who can read what — it only
decides who is authenticated.
"""

from __future__ import annotations

import uuid
from typing import cast

import structlog
from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.api_key_auth import require_role_or_api_key
from core.audit import bind_audit_team, get_audit_context, mask_sensitive_columns
from core.config import scan_trigger_rate_limit
from core.db import get_db
from core.errors import problem_response
from core.ratelimit import _authenticated_user_key, limiter
from core.security import CurrentUser, require_role
from models import AuditLog
from schemas.dependency_graph import ProjectDependencyGraph
from schemas.project_detail import (
    ComponentListResponse,
    ComponentSummary,
    ProjectOverviewResponse,
    ScanSummary,
)
from schemas.project_diff import ProjectDiff
from schemas.release_snapshot import ReleaseListResponse, ReleaseSnapshot
from schemas.scan import (
    LicenseCategorySummary,
    ProjectCreate,
    ProjectListResponse,
    ProjectPublic,
    ProjectUpdate,
    ScanCreate,
    ScanPublic,
    ScanStatus,
    SeveritySummary,
    SourceArchiveUploadResponse,
)
from services.dependency_graph_service import get_dependency_graph
from services.project_detail_service import (
    get_project_overview,
    list_components_for_project,
)
from services.project_diff_service import diff_release_snapshots
from services.project_list_enrichment import enrich_project_rows
from services.project_service import (
    ProjectError,
    archive_project,
    create_project,
    get_project,
    list_projects,
    update_project,
)
from services.release_snapshot_service import list_release_snapshots
from services.scan_resolution import SnapshotScanNotFound, resolve_snapshot_scan_id
from services.scan_service import (
    ConcurrentScanLimitExceeded,
    ScanError,
    ScanInProgressConflict,
    trigger_scan,
)
from services.source_archive_service import (
    ArchiveTooLarge,
    SourceArchiveError,
    save_uploaded_archive,
)
from services.source_archive_service import (
    _max_upload_bytes as _source_archive_max_upload_bytes,
)

router = APIRouter(prefix="/v1/projects", tags=["projects"])
log = structlog.get_logger("projects.api")


# ---------------------------------------------------------------------------
# Error translation helpers
# ---------------------------------------------------------------------------


def _problem_for_project_error(request: Request, exc: ProjectError) -> Response:
    return problem_response(
        status_code=exc.status_code,
        title=exc.title,
        detail=str(exc) or exc.title,
        instance=request.url.path,
    )


def _problem_for_snapshot_not_found(request: Request) -> Response:
    """RFC 7807 404 for an unresolvable ``?scan_id=`` snapshot anchor (feature #28).

    Existence-hide: the detail is deliberately uniform whether the scan is
    nonexistent, belongs to another project (an IDOR probe), or is not
    ``status='succeeded'`` — so a caller pinning another project's scan id learns
    nothing about whether that id exists elsewhere.
    """
    return problem_response(
        status_code=status.HTTP_404_NOT_FOUND,
        title="Scan Snapshot Not Found",
        detail="No succeeded scan with that id exists for this project.",
        instance=request.url.path,
    )


def _problem_for_scan_error(request: Request, exc: ScanError) -> Response:
    # B1: the per-team concurrency cap carries the RFC 7807 extension field
    # `limit`, a domain `type` URI, and a Retry-After header so callers can
    # back off intelligently. All other scan errors use the plain about:blank
    # envelope.
    #
    # M1 (security-reviewer): we deliberately do NOT include the team's live
    # `running_scans` count in the body — that would leak the team's real-time
    # active-scan count to every team developer on each request (an intra-team
    # side-channel). `limit` + `Retry-After` are enough for client back-off;
    # the count stays in the server-side log.warning only.
    if isinstance(exc, ConcurrentScanLimitExceeded):
        response = problem_response(
            status_code=exc.status_code,
            title=exc.title,
            detail=str(exc) or exc.title,
            instance=request.url.path,
            type_=exc.type_uri,
            limit=exc.limit,
        )
        response.headers["Retry-After"] = str(exc.retry_after_seconds)
        return response
    # P1 #10 — surface a machine-checkable extension on the per-project active-
    # scan conflict so the SPA can render a targeted notice ("a scan is already
    # running for this project") and recommend the in-progress drawer rather
    # than parsing the human-readable detail. The 409 envelope was always
    # returned; the boolean flag is the only addition.
    if isinstance(exc, ScanInProgressConflict):
        return problem_response(
            status_code=exc.status_code,
            title=exc.title,
            detail=str(exc) or exc.title,
            instance=request.url.path,
            scan_already_in_progress=True,
        )
    return problem_response(
        status_code=exc.status_code,
        title=exc.title,
        detail=str(exc) or exc.title,
        instance=request.url.path,
    )


def _problem_for_archive_error(request: Request, exc: SourceArchiveError) -> Response:
    return problem_response(
        status_code=exc.status_code,
        title=exc.title,
        detail=str(exc) or exc.title,
        instance=request.url.path,
        type_=exc.type_uri,
    )


def _declared_content_length(request: Request) -> int | None:
    """Parse the request's ``Content-Length`` header, or ``None`` if absent/bad.

    A multipart upload's Content-Length covers the whole envelope (part headers
    + boundaries), so it is always >= the file body — a safe over-estimate for
    an early-reject ceiling. A malformed value is treated as absent (the
    streamed-bytes guard in the service is the real cap).
    """
    raw = request.headers.get("content-length")
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


# ---------------------------------------------------------------------------
# POST /v1/projects
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=ProjectPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Create a project (auth required, role >= developer)",
)
async def create_project_endpoint(
    request: Request,
    payload: ProjectCreate,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        project = await create_project(session, payload=payload, actor=actor)
    except ProjectError as exc:
        return _problem_for_project_error(request, exc)

    body = ProjectPublic.model_validate(project)
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_201_CREATED,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# GET /v1/projects
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=ProjectListResponse,
    summary="List projects visible to the caller",
)
async def list_projects_endpoint(
    request: Request,
    team_id: uuid.UUID | None = Query(default=None),
    include_archived: bool = Query(default=False),
    q: str | None = Query(default=None, max_length=255),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        rows, total = await list_projects(
            session,
            actor=actor,
            team_id=team_id,
            include_archived=include_archived,
            q=q,
            page=page,
            size=size,
        )
    except ProjectError as exc:
        return _problem_for_project_error(request, exc)

    # #25 — enrich each page row with its latest scan status (status badge) and a
    # severity summary from its latest *succeeded* scan (risk indicator). W3 #30
    # adds three discoverability aggregates (scan_count / release_count /
    # last_scan_at). All three maps are computed in batched queries over the
    # page's project ids (no N+1); the page is already team-scoped by
    # ``list_projects`` above.
    (
        status_by_project,
        severity_by_project,
        counts_by_project,
        license_by_project,
        created_by_name,
    ) = await enrich_project_rows(session, projects=rows)

    items: list[ProjectPublic] = []
    for p in rows:
        item = ProjectPublic.model_validate(p)
        # The DB scan_status ENUM is exactly the ScanStatus Literal set; cast to
        # satisfy the typed field (the DB constraint guarantees membership).
        raw_status = status_by_project.get(p.id)
        item.latest_scan_status = cast(ScanStatus, raw_status) if raw_status else None
        sev = severity_by_project.get(p.id)
        item.severity_summary = SeveritySummary(**sev) if sev is not None else None
        lic = license_by_project.get(p.id)
        item.license_category_summary = (
            LicenseCategorySummary(**lic) if lic is not None else None
        )
        item.created_by_user_name = created_by_name.get(p.id)
        # W3 #30 — absent ⇒ project has no scans at all; keep schema defaults
        # (0 / 0 / null) instead of overwriting with explicit zeros.
        counts = counts_by_project.get(p.id)
        if counts is not None:
            item.scan_count = counts["scan_count"]
            item.release_count = counts["release_count"]
            item.last_scan_at = counts["last_scan_at"]
        items.append(item)

    body = ProjectListResponse(
        items=items,
        total=total,
        page=page,
        size=size,
    )
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# GET /v1/projects/{project_id}
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}",
    response_model=ProjectPublic,
    summary="Read one project (IDOR-safe; 403 if not a team member)",
)
async def get_project_endpoint(
    request: Request,
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        project = await get_project(session, project_id=project_id, actor=actor)
    except ProjectError as exc:
        return _problem_for_project_error(request, exc)

    body = ProjectPublic.model_validate(project)
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# PATCH /v1/projects/{project_id}
# ---------------------------------------------------------------------------


@router.patch(
    "/{project_id}",
    response_model=ProjectPublic,
    summary="Update mutable project fields (role >= team_admin)",
)
async def update_project_endpoint(
    request: Request,
    project_id: uuid.UUID,
    payload: ProjectUpdate,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("team_admin")),
) -> Response:
    try:
        project = await update_project(
            session,
            project_id=project_id,
            payload=payload,
            actor=actor,
        )
    except ProjectError as exc:
        return _problem_for_project_error(request, exc)

    body = ProjectPublic.model_validate(project)
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# DELETE /v1/projects/{project_id}  (soft-delete / archive)
# ---------------------------------------------------------------------------


@router.delete(
    "/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Archive (soft-delete) the project (developer and above)",
)
async def delete_project_endpoint(
    request: Request,
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    # M-10: archiving is a developer-level action (mirrors create). The
    # route gate only checks "authenticated developer+"; the service enforces
    # the project-team membership boundary.
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        await archive_project(session, project_id=project_id, actor=actor)
    except ProjectError as exc:
        return _problem_for_project_error(request, exc)

    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# POST /v1/projects/{project_id}/scans  (trigger scan — PR #7 skeleton)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# GET /v1/projects/{project_id}/overview  (Phase 3 PR #10 — task 3.1)
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/overview",
    response_model=ProjectOverviewResponse,
    summary="Aggregated risk / scan picture for the project (Phase 3 Overview tab)",
)
async def get_project_overview_endpoint(
    request: Request,
    project_id: uuid.UUID,
    scan_id: uuid.UUID | None = Query(
        default=None,
        description=(
            "Optional release-snapshot anchor (feature #28). When given, aggregate "
            "this SPECIFIC succeeded scan instead of the project's latest succeeded "
            "scan. Must belong to this project and be succeeded, else 404. Omit for "
            "the default latest-succeeded behaviour."
        ),
    ),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        payload = await get_project_overview(
            session,
            project_id=project_id,
            actor=actor,
            scan_id=scan_id,
        )
    except SnapshotScanNotFound:
        return _problem_for_snapshot_not_found(request)
    except ProjectError as exc:
        return _problem_for_project_error(request, exc)

    body = ProjectOverviewResponse(
        project_id=payload["project_id"],
        project_name=payload["project_name"],
        total_components=payload["total_components"],
        # Phase M — hand-built-response completeness (see
        # tests/unit/api/test_handbuilt_response_completeness.py: this
        # construction has silently dropped fields before).
        eol_count=payload["eol_count"],
        outdated_count=payload["outdated_count"],
        severity_distribution=payload["severity_distribution"],
        license_distribution=payload["license_distribution"],
        risk_score=payload["risk_score"],
        security_score=payload["security_score"],
        license_score=payload["license_score"],
        recent_scans=[ScanSummary.model_validate(s) for s in payload["recent_scans"]],
        last_scan_at=payload["last_scan_at"],
        last_succeeded_scan_at=payload["last_succeeded_scan_at"],
        vuln_data_available=payload["vuln_data_available"],
        has_git_credential=payload["has_git_credential"],
        current_user_role=payload["current_user_role"],
    )
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# GET /v1/projects/{project_id}/components  (Phase 3 PR #10 — task 3.3)
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/components",
    response_model=ComponentListResponse,
    summary="Paginated component list for the project's latest scan",
)
async def list_project_components_endpoint(
    request: Request,
    project_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    search: str | None = Query(default=None, max_length=255),
    severity: list[str] | None = Query(default=None),
    license_category: list[str] | None = Query(default=None),
    direct: bool | None = Query(
        default=None,
        description=(
            "W2 #31 — Direct/Transitive toggle. ``true`` keeps only direct "
            "deps (graph depth 1), ``false`` only transitive (or graph-less) "
            "deps. Omit to include both. BD-equivalent of the 'Dependency "
            "type' facet."
        ),
    ),
    dependency_scope: list[str] | None = Query(
        default=None,
        description=(
            "W2 #31 — BD-style 'Usage' facet. Repeatable; accepted values: "
            "``required``, ``optional``, ``unspecified`` (the NULL-scope "
            "bucket — common for SBOMs that don't encode scope). Unknown "
            "values are dropped, so a query that filters only by unknown "
            "values returns an empty page (not a 422). Omit to include all."
        ),
    ),
    eol: bool | None = Query(
        default=None,
        description=(
            "Phase M — end-of-life facet. ``true`` keeps only components "
            "whose release cycle is past its published end-of-life "
            "(endoflife.date); ``false`` keeps everything else, including "
            "untracked components. Omit to include both. Boolean mirrors "
            "the KEV filter UX."
        ),
    ),
    outdated: bool | None = Query(
        default=None,
        description=(
            "Version-currency facet (sibling of the EOL filter). ``true`` "
            "keeps only components behind the newest patch of their release "
            "line (endoflife.date); ``false`` keeps everything else, "
            "including current, unknown and untracked components. Omit to "
            "include both. Boolean mirrors the KEV filter UX."
        ),
    ),
    sort: str = Query(default="name", pattern=r"^(name|severity|license)$"),
    order: str = Query(default="asc", pattern=r"^(asc|desc)$"),
    scan_id: uuid.UUID | None = Query(
        default=None,
        description=(
            "Optional release-snapshot anchor (feature #28). When given, list "
            "components of this SPECIFIC succeeded scan instead of the project's "
            "latest succeeded scan. Must belong to this project and be succeeded, "
            "else 404. Omit for the default latest-succeeded behaviour."
        ),
    ),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        items, total = await list_components_for_project(
            session,
            project_id=project_id,
            actor=actor,
            limit=limit,
            offset=offset,
            search=search,
            severity=severity,
            license_category=license_category,
            direct=direct,
            dependency_scope=dependency_scope,
            eol=eol,
            outdated=outdated,
            sort=sort,
            order=order,
            scan_id=scan_id,
        )
    except SnapshotScanNotFound:
        return _problem_for_snapshot_not_found(request)
    except ProjectError as exc:
        return _problem_for_project_error(request, exc)

    body = ComponentListResponse(
        items=[ComponentSummary.model_validate(item) for item in items],
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
# GET /v1/projects/{project_id}/releases  (feature #28 — release snapshots)
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/releases",
    response_model=ReleaseListResponse,
    summary="List the project's release snapshots (succeeded scans, newest-first)",
    responses={
        403: {
            "description": (
                "Caller is not a member of the project's owning team (super_admin "
                "bypasses). RFC 7807 problem+json."
            ),
            "content": {"application/problem+json": {}},
        },
        404: {
            "description": "Project does not exist. RFC 7807 problem+json.",
            "content": {"application/problem+json": {}},
        },
    },
)
async def list_project_releases_endpoint(
    request: Request,
    project_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    # Each row is one succeeded scan = an immutable release snapshot. RBAC mirrors
    # the overview endpoint: ProjectNotFound (404) / ProjectForbidden (403). A
    # project with no succeeded scan returns an empty 200, never a 404.
    try:
        items, total = await list_release_snapshots(
            session,
            project_id=project_id,
            actor=actor,
            page=page,
            size=size,
        )
    except ProjectError as exc:
        return _problem_for_project_error(request, exc)

    body = ReleaseListResponse(
        items=[ReleaseSnapshot.model_validate(item) for item in items],
        total=total,
        page=page,
        size=size,
    )
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# GET /v1/projects/{project_id}/diff  (feature #28 Phase 2 — release diff)
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/diff",
    response_model=ProjectDiff,
    summary="Diff two release snapshots (succeeded scans) of the project",
    responses={
        403: {
            "description": (
                "Caller is not a member of the project's owning team (super_admin "
                "bypasses). RFC 7807 problem+json."
            ),
            "content": {"application/problem+json": {}},
        },
        404: {
            "description": (
                "Project does not exist, or one of `base`/`target` is not a "
                "succeeded scan of THIS project (existence-hidden: nonexistent / "
                "another project's scan / non-succeeded all collapse to the same "
                "404). RFC 7807 problem+json."
            ),
            "content": {"application/problem+json": {}},
        },
    },
)
async def diff_project_releases_endpoint(
    request: Request,
    project_id: uuid.UUID,
    base: uuid.UUID = Query(
        description=(
            "Base snapshot scan id (typically the OLDER release, e.g. v0.1). Must "
            "belong to this project and be succeeded, else 404 (existence-hide)."
        ),
    ),
    target: uuid.UUID = Query(
        description=(
            "Target snapshot scan id (typically the NEWER release, e.g. v0.2). "
            "Must belong to this project and be succeeded, else 404. `base == "
            "target` is allowed and yields an all-empty diff."
        ),
    ),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    # Validate BOTH anchors are succeeded snapshots of THIS project before running
    # any aggregation. resolve_snapshot_scan_id is the single source of truth for
    # the "belongs-to-project AND succeeded" rule and existence-hides the three
    # failure modes (nonexistent / cross-project / non-succeeded) behind one 404.
    # RBAC (team membership) is enforced inside diff_release_snapshots, mirroring
    # the other detail endpoints. We validate the anchors first so a cross-project
    # base/target probe is rejected uniformly regardless of membership outcome.
    try:
        resolved_base = await resolve_snapshot_scan_id(session, project_id, base)
        resolved_target = await resolve_snapshot_scan_id(session, project_id, target)
    except SnapshotScanNotFound:
        return _problem_for_snapshot_not_found(request)

    # resolve_snapshot_scan_id returns None only when the input id is None; both
    # inputs are required path/query params here, so a None result is impossible.
    # The assertion documents the invariant for the type checker.
    assert resolved_base is not None and resolved_target is not None

    try:
        payload = await diff_release_snapshots(
            session,
            project_id=project_id,
            actor=actor,
            base_scan_id=resolved_base,
            target_scan_id=resolved_target,
        )
    except ProjectError as exc:
        return _problem_for_project_error(request, exc)

    body = ProjectDiff.model_validate(payload)
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# GET /v1/projects/{project_id}/dependency-graph  (BomLens parity Phase H-1)
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/dependency-graph",
    response_model=ProjectDependencyGraph,
    summary="Resolved dependency graph (nodes + edges) of a scan snapshot",
    responses={
        404: {
            "description": (
                "Project does not exist, the caller is not a member of its owning "
                "team, or the pinned `scan_id` is not a succeeded scan of THIS "
                "project (nonexistent / another project's scan / non-succeeded — "
                "all existence-hidden behind one 404). Also returned when the "
                "project has no succeeded scan to read. RFC 7807 problem+json."
            ),
            "content": {"application/problem+json": {}},
        },
    },
)
async def get_dependency_graph_endpoint(
    request: Request,
    project_id: uuid.UUID,
    scan_id: uuid.UUID | None = Query(
        default=None,
        description=(
            "Optional release-snapshot anchor. When given, serialize this SPECIFIC "
            "succeeded scan's graph instead of the project's latest succeeded scan. "
            "Must belong to this project and be succeeded, else 404 (existence-hide). "
            "Omit for the default latest-succeeded behaviour."
        ),
    ),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    # Team membership + snapshot resolution are enforced INSIDE the service
    # (non-member and missing-project both existence-hide to 404; an invalid /
    # cross-project / non-succeeded scan_id, and "no succeeded scan", raise
    # SnapshotScanNotFound). The router only translates those to RFC 7807.
    try:
        payload = await get_dependency_graph(
            session,
            project_id=project_id,
            actor=actor,
            scan_id=scan_id,
        )
    except SnapshotScanNotFound:
        return _problem_for_snapshot_not_found(request)
    except ProjectError as exc:
        return _problem_for_project_error(request, exc)

    body = ProjectDependencyGraph.model_validate(payload)
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


@router.post(
    "/{project_id}/scans",
    response_model=ScanPublic,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger a scan for the project (queues a Celery task; returns 202 Accepted)",
    responses={
        429: {
            "description": (
                "Rate limited (too many triggers from this user) or the "
                "team's concurrent-scan cap is reached. RFC 7807 problem+json "
                "with a Retry-After header; the concurrency-cap variant adds a "
                "`limit` extension field. (The live per-team active-scan count "
                "is intentionally not exposed — see M1.)"
            ),
            "content": {"application/problem+json": {}},
        },
    },
)
# B1: per-USER scan-trigger rate limit (keyed by access-token sub, not IP, so
# NAT'd users / CI runners don't share a bucket). We pass the config accessor
# itself (not its result) so slowapi evaluates the limit string per request —
# os.getenv is read at call time (CLAUDE.md core rule #11), letting an operator
# retune SCAN_TRIGGER_RATE_LIMIT without a restart.
#
# L1 (security-reviewer): why `shared_limit` with a FIXED `scope`, not `@limit`.
# slowapi builds the per-request bucket key from the limit's key components.
# `@limiter.limit(...)` includes the matched route path in that key, so because
# this route's path template carries a per-call {project_id}, every project id
# would land in its OWN bucket — a single user could bypass the cap by spraying
# triggers across many projects. `@limiter.shared_limit(..., scope="scan_trigger")`
# builds the bucket key from `key_func(request)` (here the user id) plus the
# constant string `scope` and EXCLUDES the request URL/route path. That makes
# the bucket key effectively (user, "scan_trigger"), so the budget is shared
# across all of a user's projects. (There is no `key_style="url"` setting on our
# Limiter — the route-path-in-key behaviour is intrinsic to plain `@limit`.) The
# per-team concurrency cap below is a separate, complementary control enforced
# in the service.
@limiter.shared_limit(
    scan_trigger_rate_limit,
    scope="scan_trigger",
    key_func=_authenticated_user_key,
)
async def trigger_scan_endpoint(
    request: Request,
    project_id: uuid.UUID,
    payload: ScanCreate,
    session: AsyncSession = Depends(get_db),
    # CI scan-action authenticates with a tos_ API key — accept either that or
    # a JWT here (require_role alone is JWT-only and 401s the action).
    actor: CurrentUser = Depends(require_role_or_api_key("developer")),
) -> Response:
    # The service layer can raise:
    #   - ScanForbidden               (403) — caller not in the project's team
    #   - ProjectMissingForScan       (404) — project id does not exist
    #   - ScanArchiveMissing          (404) — upload scan, archive id not on disk
    #   - ScanSourceUnavailable       (422) — source scan with no git_url and no
    #                                          uploaded archive (silent-empty guard)
    #   - ConcurrentScanLimitExceeded (429) — team at its concurrent-scan cap
    #   - ScanInProgressConflict      (409) — partial unique index hit
    #   - ScanEnqueueFailed           (503) — Celery dispatch failed; scan row
    #                                          was marked 'failed' before this
    #                                          branch returns
    # _problem_for_scan_error reads exc.status_code so all map to the right
    # RFC 7807 envelope; the 429 concurrency variant additionally gets a
    # Retry-After header + a `limit` extension field (the live running_scans
    # count is logged server-side only — M1).
    try:
        scan = await trigger_scan(
            session,
            project_id=project_id,
            payload=payload,
            actor=actor,
        )
    except ScanError as exc:
        return _problem_for_scan_error(request, exc)

    body = ScanPublic.model_validate(scan)
    return Response(
        # `by_alias=True` so the response carries `metadata` (the API field
        # name) rather than `scan_metadata` (the ORM attribute name).
        content=body.model_dump_json(by_alias=True),
        status_code=status.HTTP_202_ACCEPTED,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# POST /v1/projects/{project_id}/source-archive
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/source-archive",
    response_model=SourceArchiveUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a .zip of local source for scanning (auth required, role >= developer)",
)
async def upload_source_archive_endpoint(
    request: Request,
    project_id: uuid.UUID,
    upload: UploadFile = File(..., description="A .zip archive of the project source tree."),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    # M2-fix (security review): reject before streaming a single body byte when
    # the declared Content-Length already exceeds the per-upload cap. The
    # service layer still enforces the cap on the *actual* streamed bytes (a
    # client can lie about / omit Content-Length), so this is a fast-fail
    # courtesy that avoids buffering an oversized multipart body — not the
    # authoritative guard. A genuine edge cap (Traefik) is a devops follow-up.
    declared = _declared_content_length(request)
    if declared is not None and declared > _source_archive_max_upload_bytes():
        return _problem_for_archive_error(
            request,
            ArchiveTooLarge(
                f"declared content-length {declared} exceeds the "
                f"{_source_archive_max_upload_bytes()}-byte upload limit"
            ),
        )

    # The service layer can raise:
    #   - ArchiveProjectNotFound   (404) — project missing OR in another team
    #                                       (existence-hide; never 403)
    #   - ArchiveUnsupportedType   (415) — bad extension / content-type / magic
    #   - ArchiveTooLarge          (413) — body exceeds SOURCE_ARCHIVE_MAX_BYTES
    #   - ArchiveQuotaExceeded     (507) — project archive storage budget full
    #   - ArchiveInvalid           (400) — empty / truncated / unwritable
    # All carry a `type_uri` so the RFC 7807 envelope gets a stable problem URI.
    try:
        saved = await save_uploaded_archive(
            session,
            project_id=project_id,
            upload=upload,
            actor=actor,
        )
    except SourceArchiveError as exc:
        return _problem_for_archive_error(request, exc)

    # The saved .zip is a filesystem side effect no DB row records, so the
    # automatic audit listener never sees the upload — write the row
    # explicitly (the later scan INSERT audits the scan, not this upload).
    # The service already authorized the actor against the project's team and
    # returns the byte count + team_id, so no re-query / stat() is needed.
    bind_audit_team(saved.team_id)
    ctx = get_audit_context()
    session.add(
        AuditLog(
            action="source_archive.uploaded",
            target_table="projects",
            target_id=str(project_id),
            actor_user_id=actor.id,
            team_id=saved.team_id,
            request_id=ctx.get("request_id"),
            ip=ctx.get("ip"),
            user_agent=ctx.get("user_agent"),
            # Explicit rows bypass the listener's masking — run the diff
            # through the same masker so a future sensitive key is caught.
            diff=mask_sensitive_columns(
                {
                    "archive_id": saved.archive_id,
                    "filename": upload.filename,
                    "bytes": saved.bytes_written,
                }
            ),
        )
    )
    await session.commit()

    body = SourceArchiveUploadResponse(archive_id=saved.archive_id)
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_201_CREATED,
        media_type="application/json",
    )


# slowapi's `@limiter.limit` wraps the endpoint with functools.wraps. The
# wrapper inherits slowapi's module as its `__globals__`, so under
# `from __future__ import annotations` FastAPI's `get_type_hints()` call on
# the wrapper cannot resolve our string annotations (ScanCreate, AsyncSession,
# ...) and misclassifies the body / dependencies. Mirror the fix used in
# auth.py and obligations.py: copy the names the wrapper needs into its
# `__globals__` (the dict is mutable even though the attribute is read-only).
for _name in (
    "uuid",
    "ScanCreate",
    "AsyncSession",
    "CurrentUser",
    "Request",
    "Response",
    "Depends",
):
    if _name in globals():
        trigger_scan_endpoint.__globals__.setdefault(_name, globals()[_name])
del _name
