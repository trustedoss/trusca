"""
Source-tree viewer HTTP surface — G3.2.

Two read endpoints over the per-scan source tarball preserved in G3.1:

  - GET /v1/projects/{project_id}/source-tree?path=<dir>&page=&size=&scan_id=
        → immediate children of ``path`` (lazy per-dir), paged.
  - GET /v1/projects/{project_id}/source-file?path=<file>&scan_id=
        → one file's bytes (capped) + per-line license matches.

Auth: every route requires ``require_role("developer")`` (role ≥ developer).
Team scoping + 404 existence-hide live in the service
(``services.source_tree_service``); a project / scan / tarball the caller cannot
see returns the same 404 as a missing one, so cross-team enumeration is closed
(mirrors ``api/v1/sbom.py``).

All 4xx / 5xx responses are RFC 7807 ``application/problem+json`` via
``core.errors.problem_response``; typed domain exceptions carry the status / title
/ type-URI so the translation is one mapping for every error.
"""

from __future__ import annotations

import re
import urllib.parse
import uuid

import structlog
from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.errors import problem_response
from core.security import CurrentUser, require_role
from schemas.source_tree import (
    LicenseMatch,
    SourceFileResponse,
    SourceTreeEntry,
    SourceTreePage,
)
from services.source_tree_service import (
    SourceTreeError,
    list_dir,
    read_file,
    read_file_raw,
)

router = APIRouter(prefix="/v1", tags=["source-tree"])
log = structlog.get_logger("source_tree.api")


def _problem(request: Request, exc: SourceTreeError) -> JSONResponse:
    """Translate a typed source-tree error to an RFC 7807 problem response."""
    return problem_response(
        status_code=exc.status_code,
        title=exc.title,
        detail=str(exc) or exc.title,
        instance=request.url.path,
        type_=exc.type_uri,
    )


# ---------------------------------------------------------------------------
# Content-Disposition filename helper (RFC 6266) — mirrors reports.py
# ---------------------------------------------------------------------------

# The member's base name flows into the Content-Disposition filename for the
# raw download. Strip everything outside ``[A-Za-z0-9._-]`` for the ASCII
# fallback so a filesystem can persist it (and CR/LF can never reach the
# header), then carry the original name percent-encoded in the UTF-8 extended
# parameter so the user still sees a readable download name.
_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_filename_token(name: str) -> str:
    token = _FILENAME_SAFE_RE.sub("-", name).strip("-")
    return token or "download"


def _format_content_disposition(filename: str) -> str:
    """Build an RFC 6266 ``Content-Disposition: attachment`` value.

    Emits both the ASCII ``filename=`` fallback and the UTF-8
    ``filename*=UTF-8''…`` extended parameter, exactly like the reports / NOTICE
    endpoints.
    """
    token = _safe_filename_token(filename)
    utf8_encoded = urllib.parse.quote(filename, safe="")
    return f'attachment; filename="{token}"; filename*=UTF-8\'\'{utf8_encoded}'


# ---------------------------------------------------------------------------
# GET /v1/projects/{project_id}/source-tree
# ---------------------------------------------------------------------------


@router.get(
    "/projects/{project_id}/source-tree",
    summary="List immediate children of a directory in a scan's preserved source",
    response_model=SourceTreePage,
    responses={
        400: {"description": "Malformed path selector"},
        401: {"description": "Authentication required"},
        404: {"description": "Project / scan / preserved source not available"},
    },
)
async def get_source_tree(
    request: Request,
    project_id: uuid.UUID,
    path: str = Query(
        default="",
        description="Directory whose immediate children to list. Empty = root.",
    ),
    page: int = Query(default=1, ge=1, description="1-based page index."),
    size: int = Query(default=100, ge=1, le=500, description="Page size (max 500)."),
    scan_id: uuid.UUID | None = Query(
        default=None,
        description="Scan to read; defaults to the project's latest scan.",
    ),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> JSONResponse | SourceTreePage:
    try:
        page_result = await list_dir(
            session,
            project_id=project_id,
            raw_path=path,
            scan_id=scan_id,
            actor=actor,
            page=page,
            size=size,
        )
    except SourceTreeError as exc:
        return _problem(request, exc)

    return SourceTreePage(
        scan_id=page_result.scan_id,
        path=page_result.path,
        entries=[
            SourceTreeEntry(
                name=e.name,
                path=e.path,
                is_dir=e.is_dir,
                byte_size=e.byte_size,
                license_spdx_ids=e.license_spdx_ids,
            )
            for e in page_result.entries
        ],
        total=page_result.total,
        page=page_result.page,
        size=page_result.size,
    )


# ---------------------------------------------------------------------------
# GET /v1/projects/{project_id}/source-file
# ---------------------------------------------------------------------------


@router.get(
    "/projects/{project_id}/source-file",
    summary="Read one file from a scan's preserved source + per-line license matches",
    response_model=SourceFileResponse,
    responses={
        200: {
            "description": (
                "Capped file JSON (default), or — with ``raw=true`` — the FULL "
                "member streamed as application/octet-stream."
            ),
            "content": {
                "application/json": {},
                "application/octet-stream": {},
            },
        },
        400: {"description": "Malformed path selector"},
        401: {"description": "Authentication required"},
        404: {"description": "Project / scan / file not available"},
        413: {"description": "Requested member is a directory / non-regular file"},
    },
)
async def get_source_file(
    request: Request,
    project_id: uuid.UUID,
    path: str = Query(description="File to read, relative to the source root."),
    scan_id: uuid.UUID | None = Query(
        default=None,
        description="Scan to read; defaults to the project's latest scan.",
    ),
    raw: bool = Query(
        default=False,
        description=(
            "When true, stream the FULL member as application/octet-stream "
            "(no per-file viewer cap) for download instead of the capped JSON "
            "preview. Same path-traversal / symlink defences apply."
        ),
    ),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> JSONResponse | SourceFileResponse | Response:
    if raw:
        # Every validation (path traversal, member lookup, dir / non-regular
        # refusal, team scoping, raw cap on the declared size) runs EAGERLY inside
        # read_file_raw and raises before any byte streams — so a rejection is an
        # RFC 7807 problem response, never a partial 200 body. The returned
        # ``chunks`` generator yields the member in 64 KiB slices and owns closing
        # the open tarball, so peak body memory is one chunk (not the whole, up to
        # 512 MiB, member). The cap is re-enforced WHILE streaming.
        try:
            raw_result = await read_file_raw(
                session,
                project_id=project_id,
                raw_path=path,
                scan_id=scan_id,
                actor=actor,
            )
        except SourceTreeError as exc:
            return _problem(request, exc)
        return StreamingResponse(
            raw_result.chunks,
            status_code=status.HTTP_200_OK,
            media_type="application/octet-stream",
            headers={
                "content-disposition": _format_content_disposition(
                    raw_result.filename
                ),
            },
        )

    try:
        file_result = await read_file(
            session,
            project_id=project_id,
            raw_path=path,
            scan_id=scan_id,
            actor=actor,
        )
    except SourceTreeError as exc:
        return _problem(request, exc)

    return SourceFileResponse(
        scan_id=file_result.scan_id,
        path=file_result.path,
        byte_size=file_result.byte_size,
        truncated=file_result.truncated,
        encoding=file_result.encoding,  # type: ignore[arg-type]
        content=file_result.content,
        license_matches=[
            LicenseMatch(
                spdx_id=m.spdx_id,
                start_line=m.start_line,
                end_line=m.end_line,
                score=m.score,
            )
            for m in file_result.license_matches
        ],
    )
