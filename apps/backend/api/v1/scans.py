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
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from core.api_key_auth import require_role_or_api_key
from core.config import api_read_rate_limit, workspace_root
from core.db import get_db
from core.errors import problem_response
from core.ratelimit import _authenticated_user_key, limiter
from core.security import CurrentUser, require_role
from schemas.scan import ScanListResponse, ScanPublic
from services.admin_scan_service import (
    AdminScanError,
    cancel_scan_for_actor,
)
from services.scan_service import (
    ScanError,
    delete_scan,
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
    # M-30: pass any machine-readable extensions (e.g. ScanDeleteConflict's
    # scan_active / scan_release_protected). The base ScanError defaults to an
    # empty mapping, so this is a no-op for errors that carry none.
    extensions: dict[str, object] = dict(exc.extensions)
    return problem_response(
        status_code=exc.status_code,
        title=exc.title,
        detail=str(exc) or exc.title,
        instance=request.url.path,
        **extensions,  # type: ignore[arg-type]
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
# F-1 (RED-team, Low): this GET accepts a tos_ API key, but had no limiter — and
# the limiter is decorator-opt-in (default_limits=[]), so an undecorated route
# is fully unthrottled. Every api-key miss runs a dummy bcrypt (~50-100ms CPU);
# an unbounded Bearer-tos_ flood here saturates workers. shared_limit with a
# FIXED scope buckets by (actor, "api_read") and EXCLUDES the {scan_id} path so
# one actor can't bypass the cap by spraying different scan ids. Keyed by
# _authenticated_user_key → apikey:<prefix> pre-auth, so the cap fires before
# bcrypt on the hot path. Accessor (not its result) → re-read per request.
@limiter.shared_limit(
    api_read_rate_limit,
    scope="api_read",
    key_func=_authenticated_user_key,
)
async def get_scan_endpoint(
    request: Request,
    scan_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    # CI scan-action polls this endpoint with a tos_ API key while waiting for
    # the scan to reach a terminal state — accept either the key or a JWT.
    actor: CurrentUser = Depends(require_role_or_api_key("developer")),
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


def _scan_log_not_found_response(
    request: Request, *, scan_id_for_log: uuid.UUID, reason: str
) -> JSONResponse:
    """Return the BYTE-IDENTICAL 404 Problem Details body for every miss path.

    Security (HIGH, security-reviewer follow-up on ea75d1f): the original
    endpoint emitted two distinct Problem Details bodies for the two 404
    branches ("scan not found / forbidden" vs "scan exists but file not
    written"). A scripted attacker enumerating UUIDs across teams could use
    the envelope difference to perfectly distinguish "valid scan id in another
    team" from "non-existent scan id" — defeating the existence-hide gate this
    endpoint was built to enforce.

    We collapse every miss path to the same body (same ``title``, ``detail``,
    no ``type_`` urn — the urn itself was the differentiator that leaked
    information). The operator-distinguishing reason ("not_found_or_forbidden",
    "file_not_present", "path_escape_blocked", …) is recorded ONLY in a
    structlog event so on-call can still tell branches apart in logs.
    """
    log.info(
        "scan_log_not_found",
        scan_id=str(scan_id_for_log),
        reason=reason,
    )
    # Canonical instance string — using the literal request path here would
    # echo the scan_id back, defeating the existence-hide contract that
    # test_404_envelope_is_byte_identical_across_miss_paths gates. The route
    # pattern is the same across (a) cross-team, (b) file-missing, and (c)
    # random-uuid miss paths.
    return problem_response(
        status_code=status.HTTP_404_NOT_FOUND,
        title="Scan log not found",
        detail="No scan log is available for this scan id.",
        instance="/v1/scans/{scan_id}/log",
    )


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
                "No scan log is available for this scan id. The body is "
                "deliberately the same for every miss path (scan not found, "
                "caller has no access, log file not yet on disk, or path "
                "traversal defense triggered) so the response envelope cannot "
                "be used to enumerate scan ids across teams. Operators can "
                "distinguish branches via the ``scan_log_not_found`` "
                "structlog event's ``reason`` field."
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
    # never leaks "this scan exists but you cannot see it". All 404 paths below
    # share one byte-identical Problem Details body via
    # ``_scan_log_not_found_response`` so the response envelope cannot be used
    # to distinguish branches (HIGH finding, security-reviewer follow-up).
    try:
        await get_scan(session, scan_id=scan_id, actor=actor)
    except ScanError:
        return _scan_log_not_found_response(
            request, scan_id_for_log=scan_id, reason="not_found_or_forbidden"
        )

    # Cache the resolved workspace root once: ``workspace_root()`` re-reads
    # ``os.getenv`` per call (CLAUDE.md core rule #11) which means a SIGHUP
    # reload between the two existing reads could in principle disagree on the
    # path used for traversal defense vs the path opened. Reading once removes
    # that race window (MEDIUM #2).
    root_str = workspace_root()
    root = Path(root_str).resolve()
    log_path = (Path(root_str) / str(scan_id) / "scan.log").resolve()

    if not log_path.is_relative_to(root):
        log.warning(
            "scan_log_path_escape_blocked",
            scan_id=str(scan_id),
            resolved=str(log_path),
            root=str(root),
        )
        return _scan_log_not_found_response(
            request, scan_id_for_log=scan_id, reason="path_escape_blocked"
        )

    if not log_path.is_file():
        # The scan exists and the caller has access, but no log was written.
        # Common cases: very early-stage scan that crashed before any tool
        # emitted a line, persistence disabled via SCAN_LOG_PERSIST_ENABLED=false,
        # or the workspace cleaner already reaped the parent dir. We return the
        # SAME Problem Details body as the not-found / forbidden branch above
        # (the operator-distinguishing reason goes into structlog only).
        return _scan_log_not_found_response(
            request, scan_id_for_log=scan_id, reason="file_not_present"
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
# DELETE /v1/scans/{scan_id}  (scan-retention Layer 3 — manual reclaim)
# ---------------------------------------------------------------------------


@router.delete(
    "/scans/{scan_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a terminal scan and its findings (own-team scans only)",
    responses={
        204: {"description": "Scan deleted (cascade removed its findings)."},
        404: {"description": "Scan not found, or not visible to the caller."},
        409: {
            "description": (
                "Scan is active (cancel it first) or carries a release label "
                "(pass ``force=true`` to delete)."
            )
        },
    },
)
async def delete_scan_endpoint(
    request: Request,
    scan_id: uuid.UUID,
    force: bool = Query(
        default=False,
        description=(
            "Delete even when the scan carries an explicit metadata.release "
            "label. Release-labelled snapshots are immutable by default."
        ),
    ),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    """Hard-delete a terminal scan and (via cascade) its findings / components.

    DT-style retention reclaims most stale scans automatically; this is the
    manual escape hatch. Auth: any team member (``developer``+). The owning-team
    check lives in the service (``delete_scan``), which existence-hides other
    teams' scans as 404. Active scans (queued/running) return 409 — cancel
    first. A release-labelled scan returns 409 unless ``force=true``.
    """
    try:
        await delete_scan(session, scan_id=scan_id, actor=actor, force=force)
    except ScanError as exc:
        return _problem_for_scan_error(request, exc)

    log.warning(
        "scan.delete",
        actor_id=str(actor.id),
        scan_id=str(scan_id),
        forced=bool(force),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
