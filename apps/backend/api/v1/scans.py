"""
Scan read API — Phase 2 PR #7 + Step 4 (cross-project listing).

Endpoints under `/v1`:
  - GET /v1/scans                            List scans across every project
                                              the actor can see (Step 4).
  - GET /v1/scans/{scan_id}                  Read one scan (IDOR-safe via
                                              team membership on the parent
                                              project).
  - GET /v1/projects/{project_id}/scans      List scans for a project.

The scan trigger (POST) lives in `api/v1/projects.py` because it is naturally
a sub-resource of a project. The read endpoints sit here because clients
fetch them by scan id (notification deep links, audit log entries) without
necessarily knowing the parent project up front.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import structlog
from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import workspace_root
from core.db import get_db
from core.errors import problem_response
from core.security import CurrentUser, require_role
from schemas.scan import ScanListResponse, ScanPublic
from services.admin_scan_service import (
    AdminScanError,
    cancel_scan_for_actor,
)
from services.scan_service import (
    ScanError,
    get_scan,
    list_scans_for_actor,
    list_scans_for_project,
)

router = APIRouter(prefix="/v1", tags=["scans"])
log = structlog.get_logger("scans.api")

# Streaming chunk size for the scan log download. 64 KiB is comfortably above
# the default OS page boundary and keeps multi-MB logs out of resident memory.
_LOG_DOWNLOAD_CHUNK_BYTES = 64 * 1024


def _problem_for_scan_error(request: Request, exc: ScanError) -> Response:
    return problem_response(
        status_code=exc.status_code,
        title=exc.title,
        detail=str(exc) or exc.title,
        instance=request.url.path,
    )


def _problem_for_admin_scan_error(request: Request, exc: AdminScanError) -> Response:
    """Translate an AdminScanError (shared cancel core) into RFC 7807.

    Mirrors the admin endpoint's translator so the user-facing cancel returns
    the identical envelope (incl. ``scan_already_cancelled`` extension) on a
    409 — the only difference between the two surfaces is the team-access gate.
    """
    extensions: dict[str, object] = dict(exc.extensions)
    return problem_response(
        status_code=exc.status_code,
        title=exc.title,
        detail=str(exc) or exc.title,
        instance=request.url.path,
        type_=exc.type_uri,
        **extensions,
    )


# ---------------------------------------------------------------------------
# GET /v1/scans  (cross-project list — Step 4)
# ---------------------------------------------------------------------------


@router.get(
    "/scans",
    response_model=ScanListResponse,
    summary="List scans across every project accessible to the caller",
)
async def list_my_scans_endpoint(
    request: Request,
    status_filter: str | None = Query(
        default=None,
        alias="status",
        # Mirror SCAN_STATUS_VALUES from models.scan. Pydantic emits 422 for
        # any other value with an RFC 7807 envelope (the validation handler
        # in core.errors).
        pattern=r"^(queued|running|succeeded|failed|cancelled)$",
        description="Filter by scan status.",
    ),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    rows, total = await list_scans_for_actor(
        session,
        actor=actor,
        status_filter=status_filter,
        page=page,
        size=size,
    )
    body = ScanListResponse(
        items=[ScanPublic.from_scan(s) for s in rows],
        total=total,
        page=page,
        size=size,
    )
    return Response(
        content=body.model_dump_json(by_alias=True),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# GET /v1/scans/{scan_id}
# ---------------------------------------------------------------------------


@router.get(
    "/scans/{scan_id}",
    response_model=ScanPublic,
    summary="Read one scan (IDOR-safe via project team membership)",
)
async def get_scan_endpoint(
    request: Request,
    scan_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        scan = await get_scan(session, scan_id=scan_id, actor=actor)
    except ScanError as exc:
        return _problem_for_scan_error(request, exc)

    body = ScanPublic.model_validate(scan)
    return Response(
        content=body.model_dump_json(by_alias=True),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# GET /v1/scans/{scan_id}/log  (download the persisted tool log)
# ---------------------------------------------------------------------------


async def _stream_log_file(path: Path) -> AsyncIterator[bytes]:
    """Yield the log file in fixed-size chunks for ``StreamingResponse``.

    We open with a plain blocking ``open()`` because:
      - The file already lives on the same worker host (workspace volume).
      - FastAPI runs route handlers in a worker thread pool when needed; for
        a small async generator that yields cooperatively after each chunk
        the blocking read is negligible vs the network send.
    """
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(_LOG_DOWNLOAD_CHUNK_BYTES)
            if not chunk:
                return
            yield chunk


@router.get(
    "/scans/{scan_id}/log",
    summary="Download the persisted tool log for one scan",
    responses={
        200: {
            "content": {"text/plain": {}},
            "description": "Plain-text scan log, streamed.",
        },
        404: {
            "content": {"application/problem+json": {}},
            "description": (
                "Scan not found, the caller has no access to it, or the log "
                "file is not on disk yet (very early-stage scan or persistence "
                "disabled)."
            ),
        },
    },
)
async def download_scan_log_endpoint(
    request: Request,
    scan_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    """Stream the per-scan ``scan.log`` written by ``tasks._progress.publish_log``.

    Authorization: same gate as ``GET /v1/scans/{scan_id}`` — reuses
    ``services.scan_service.get_scan`` so team-membership / super-admin rules
    stay in lock-step with the metadata endpoint. A non-member sees the same
    404 as a non-existent scan id (existence-hide) so a developer cannot probe
    scan ids belonging to other teams via this endpoint.

    Lifecycle: the file is written incrementally by the worker as the scan
    runs. While the scan is still running the response returns whatever has
    been flushed so far (the publisher uses a line-buffered handle, so each
    completed line is on disk by the time it is on the WebSocket). After the
    scan terminates the file stays on disk until ``workspace_cleaner`` reaps
    the parent workspace directory (current default: per
    ``WORKSPACE_ORPHAN_MAX_AGE_SECONDS``).
    """
    # Auth + existence check first. ScanNotFound (404) and ScanForbidden (403)
    # both collapse to 404 here — the existence-hide contract for this endpoint
    # never leaks "this scan exists but you cannot see it". We deliberately do
    # not call _problem_for_scan_error for ScanForbidden so the 403 envelope
    # cannot leak through.
    try:
        await get_scan(session, scan_id=scan_id, actor=actor)
    except ScanError:
        return problem_response(
            status_code=status.HTTP_404_NOT_FOUND,
            title="Scan Log Not Found",
            detail="No scan log is available for this id.",
            instance=request.url.path,
        )

    # Path is constructed from {workspace_root}/{scan_id}/scan.log. scan_id is
    # already a parsed UUID at the route signature (FastAPI rejects malformed
    # strings with 422), so traversal is impossible by construction. The
    # is_relative_to() check below is defense-in-depth in case workspace_root
    # ever changes semantics (symlink, env-var injection of "..", etc.).
    root = Path(workspace_root()).resolve()
    log_path = (Path(workspace_root()) / str(scan_id) / "scan.log").resolve()

    if not log_path.is_relative_to(root):
        log.warning(
            "scan_log_path_escape_blocked",
            scan_id=str(scan_id),
            resolved=str(log_path),
            root=str(root),
        )
        return problem_response(
            status_code=status.HTTP_404_NOT_FOUND,
            title="Scan Log Not Found",
            detail="No scan log is available for this id.",
            instance=request.url.path,
        )

    if not log_path.is_file():
        # The scan exists and the caller has access, but no log was written.
        # Common cases: very early-stage scan that crashed before any tool
        # emitted a line, persistence disabled via SCAN_LOG_PERSIST_ENABLED=false,
        # or the workspace cleaner already reaped the parent dir. Return 404
        # with a Problem Details body distinct from the "no access" 404 detail
        # text so an internal operator can tell them apart in logs.
        return problem_response(
            status_code=status.HTTP_404_NOT_FOUND,
            title="Scan Log Not Available",
            detail="Scan log not available yet.",
            instance=request.url.path,
            type_="urn:trustedoss:problem:scan_log_unavailable",
        )

    return StreamingResponse(
        _stream_log_file(log_path),
        status_code=status.HTTP_200_OK,
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="scan-{scan_id}.log"',
            # X-Content-Type-Options to defang any "the file is HTML in disguise"
            # gambit a hostile tool might play by emitting markup to stdout.
            "X-Content-Type-Options": "nosniff",
        },
    )


# ---------------------------------------------------------------------------
# POST /v1/scans/{scan_id}/cancel  (user-facing — own-team scans only)
# ---------------------------------------------------------------------------


@router.post(
    "/scans/{scan_id}/cancel",
    summary="Cancel a queued / running scan owned by the caller's team",
)
async def cancel_scan_endpoint(
    request: Request,
    scan_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    """Cancel one of the caller's own team's scans.

    PR-A1 (scan stability). Auth: any authenticated team member
    (``developer`` or higher). The owning-team check lives in the service
    (``cancel_scan_for_actor``) which existence-hides other teams' scans as
    404 — so a developer cannot probe scan ids belonging to other teams.

    Admin force-cancel (``POST /v1/admin/scans/{id}/cancel``) remains separate
    and cross-team; the two share the same revoke + status-mutation core.
    """
    try:
        item = await cancel_scan_for_actor(session, actor=actor, scan_id=scan_id)
    except AdminScanError as exc:
        return _problem_for_admin_scan_error(request, exc)

    log.warning(
        "scan.cancel",
        actor_id=str(actor.id),
        scan_id=str(scan_id),
    )
    return Response(
        content=item.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# GET /v1/projects/{project_id}/scans
# ---------------------------------------------------------------------------


@router.get(
    "/projects/{project_id}/scans",
    response_model=ScanListResponse,
    summary="List scans for a project (most recent first)",
)
async def list_scans_endpoint(
    request: Request,
    project_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        rows, total = await list_scans_for_project(
            session,
            project_id=project_id,
            actor=actor,
            page=page,
            size=size,
        )
    except ScanError as exc:
        return _problem_for_scan_error(request, exc)

    body = ScanListResponse(
        items=[ScanPublic.from_scan(s) for s in rows],
        total=total,
        page=page,
        size=size,
    )
    return Response(
        content=body.model_dump_json(by_alias=True),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )
