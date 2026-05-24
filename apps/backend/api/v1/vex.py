"""
VEX export + import HTTP surface — v2.1 Track A (A1 export, A2 import).

Endpoints:
  - GET  /v1/projects/{project_id}/vex?format=openvex|cyclonedx   (A1 export)
  - POST /v1/projects/{project_id}/vex/import                     (A2 import)

Auth: every route requires a valid access token.
  - Export (GET) requires ``require_role("developer")`` — a read.
  - Import (POST) additionally requires *team_admin within the project's team*.
    Import is a bulk-triage privileged action: a single upload can transition
    many findings (including into ``suppressed``, which the manual PATCH path
    already gates at team_admin). The role check is enforced in this router
    against the *project's* team (not the actor's highest role) to avoid
    cross-team escalation.

IDOR is enforced inline — outsiders see 404 (existence-hide), exactly as for the
SBOM export and component detail. We use 404-not-403 here because the VEX
endpoint leaks structural details (component purls, CVE ids, triage state) about
a project; matching the SBOM endpoint's behaviour keeps the IDOR-leak surface
uniform. A same-team developer who lacks team_admin sees 403 (the project's
existence is already known to a team member).

All 4xx / 5xx responses are RFC 7807 problem+json; the export success response
is a file download, the import success response is a JSON summary.
"""

from __future__ import annotations

import uuid
from typing import Literal

import structlog
from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.authz import assert_team_access
from core.db import get_db
from core.errors import problem_response
from core.security import CurrentUser, require_role
from schemas.vex_import import VEXImportSummary
from services.project_service import (
    ProjectError,
    ProjectForbidden,
    ProjectNotFound,
    get_project,
)
from services.vex_export import (
    VEXExportError,
    VEXUnsupportedFormat,
    export_vex,
)
from services.vex_import import (
    VEXImportError,
    import_vex,
)
from services.vulnerability_service import _has_team_admin

router = APIRouter(prefix="/v1", tags=["vex"])
log = structlog.get_logger("vex.api")


def _declared_content_length(request: Request) -> int | None:
    """Parse the request's ``Content-Length`` header, or ``None`` if absent/bad.

    A multipart upload's Content-Length covers the whole envelope (part headers
    + boundaries), so it is always >= the file body — a safe over-estimate for
    an early-reject ceiling. A malformed value is treated as absent (the
    decoded-bytes guard in the service is the real cap).
    """
    raw = request.headers.get("content-length")
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


async def _read_bounded(upload: UploadFile, *, max_bytes: int) -> bytes:
    """Read an uploaded body in chunks, aborting once it exceeds ``max_bytes``.

    The declared Content-Length is only a courtesy fast-fail — a client can omit
    or understate it. This streams the body and raises
    :class:`~services.vex_import.VEXImportTooLarge` the moment the accumulated
    size crosses the cap, so a body with a missing/false Content-Length still
    yields a 413 without buffering the whole (possibly huge) payload.

    We read one chunk past the cap (cap + 1 byte) to detect the overflow, then
    stop — at most ``max_bytes`` + one chunk is ever held in memory.
    """
    from services.vex_import import VEXImportTooLarge

    chunk_size = 64 * 1024
    buf = bytearray()
    while True:
        chunk = await upload.read(chunk_size)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > max_bytes:
            raise VEXImportTooLarge(
                f"VEX document exceeds the {max_bytes}-byte limit",
            )
    return bytes(buf)


# `format` is keyed both as a Pydantic Literal (so 422 fires for invalid values
# at the OpenAPI layer) and re-validated inside the service for defense in
# depth. The Literal mirrors ``services.vex_export.SUPPORTED_FORMATS``.
VEXFormat = Literal["openvex", "cyclonedx"]


def _problem_for_project_error(request: Request, exc: ProjectError) -> Response:
    """Translate project-domain errors with existence-hide on forbidden.

    The VEX endpoint hides existence: a non-team-member sees the same 404 they'd
    see for an unknown project id. Inside the project domain a forbidden lookup
    raises :class:`ProjectForbidden`; we rewrite that to a 404 envelope here.
    ProjectNotFound already has status_code=404.
    """
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


def _problem_for_vex_error(request: Request, exc: VEXExportError) -> Response:
    return problem_response(
        status_code=exc.status_code,
        title=exc.title,
        detail=str(exc) or exc.title,
        instance=request.url.path,
    )


def _problem_for_import_error(request: Request, exc: VEXImportError) -> Response:
    return problem_response(
        status_code=exc.status_code,
        title=exc.title,
        detail=str(exc) or exc.title,
        instance=request.url.path,
    )


# ---------------------------------------------------------------------------
# GET /v1/projects/{project_id}/vex
# ---------------------------------------------------------------------------


@router.get(
    "/projects/{project_id}/vex",
    summary="Export a VEX document from the project's current finding triage",
    response_class=Response,
    responses={
        200: {
            "description": "VEX document download",
            "content": {
                "application/json": {},
            },
        },
        401: {"description": "Authentication required"},
        404: {"description": "Project not found or not accessible"},
        422: {"description": "Unknown VEX format"},
    },
)
async def export_project_vex_endpoint(
    request: Request,
    project_id: uuid.UUID,
    fmt: VEXFormat = Query(
        default="openvex",
        alias="format",
        description="VEX output format.",
    ),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    # IDOR guard — re-use ``get_project`` so the "is the actor allowed to see
    # this project?" decision lives in exactly one place. Existence-hide: any
    # ProjectForbidden here surfaces as 404 to outsiders (see helper above).
    try:
        project = await get_project(session, project_id=project_id, actor=actor)
    except ProjectNotFound as exc:
        return _problem_for_project_error(request, exc)
    except ProjectForbidden as exc:
        return _problem_for_project_error(request, exc)
    except ProjectError as exc:  # pragma: no cover - defensive catch-all
        return _problem_for_project_error(request, exc)

    # Re-assert team membership through the central audit helper so the
    # cross_team_attempt log entry is written for any unexpected gap. This is
    # belt-and-braces with `get_project`; cheap and consistent.
    assert_team_access(
        actor,
        project.team_id,
        log=log,
        resource="vex_export",
        resource_id=str(project_id),
        deny=lambda: ProjectForbidden(f"actor is not a member of team {project.team_id}"),
    )

    try:
        body, content_type, filename = await export_vex(
            session,
            project_id=project_id,
            fmt=fmt,
        )
    except VEXUnsupportedFormat as exc:
        return _problem_for_vex_error(request, exc)
    except VEXExportError as exc:  # pragma: no cover - defensive
        return _problem_for_vex_error(request, exc)

    # ``Content-Disposition: attachment`` makes browsers offer "save as".
    headers = {
        "content-disposition": f'attachment; filename="{filename}"',
    }
    return Response(
        content=body.encode("utf-8"),
        status_code=status.HTTP_200_OK,
        media_type=content_type,
        headers=headers,
    )


# ---------------------------------------------------------------------------
# POST /v1/projects/{project_id}/vex/import
# ---------------------------------------------------------------------------


@router.post(
    "/projects/{project_id}/vex/import",
    summary="Import a VEX document, auto-transitioning matching findings (team_admin)",
    response_model=VEXImportSummary,
    responses={
        200: {"description": "Import summary (matched/applied/skipped/errors)"},
        401: {"description": "Authentication required"},
        403: {"description": "Requires team_admin within the project's team"},
        404: {"description": "Project not found or not accessible"},
        413: {"description": "VEX document too large"},
        422: {"description": "Malformed or unsupported VEX document"},
    },
)
async def import_project_vex_endpoint(
    request: Request,
    project_id: uuid.UUID,
    upload: UploadFile = File(
        ...,
        description="An OpenVEX or CycloneDX VEX JSON document (format auto-detected).",
    ),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    # IDOR guard — reuse ``get_project`` so the "can the actor see this
    # project?" decision lives in one place. Existence-hide: a non-member sees
    # the same 404 as for an unknown project id (see helper above).
    try:
        project = await get_project(session, project_id=project_id, actor=actor)
    except ProjectNotFound as exc:
        return _problem_for_project_error(request, exc)
    except ProjectForbidden as exc:
        # Cross-team → 404 existence-hide (the helper rewrites Forbidden→404).
        return _problem_for_project_error(request, exc)
    except ProjectError as exc:  # pragma: no cover - defensive catch-all
        return _problem_for_project_error(request, exc)

    # Team-admin gate, evaluated against the PROJECT's team (not the actor's
    # highest role) to block cross-team escalation. A same-team developer who
    # is visible-as-a-member but lacks team_admin gets a 403 here — the 404
    # existence-hide above already protected the cross-team case.
    if not _has_team_admin(actor, project.team_id):
        log.warning(
            "vex_import.forbidden",
            actor_id=str(actor.id),
            project_id=str(project_id),
            team_id=str(project.team_id),
        )
        return problem_response(
            status_code=status.HTTP_403_FORBIDDEN,
            title="Forbidden",
            detail="VEX import requires role >= team_admin within the project's team.",
            instance=request.url.path,
        )

    # Fast-fail on an oversized declared Content-Length before buffering the
    # body. The service re-checks the decoded size (a client can lie about /
    # omit Content-Length), so this is a courtesy ceiling, not the authority.
    from services.vex_import import VEXImportTooLarge, _max_document_bytes

    max_bytes = _max_document_bytes()
    declared = _declared_content_length(request)
    if declared is not None and declared > max_bytes:
        return problem_response(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            title="VEX Document Too Large",
            detail=(
                f"declared content-length {declared} exceeds the "
                f"{max_bytes}-byte VEX import limit"
            ),
            instance=request.url.path,
        )

    # Bounded read: accumulate chunks and abort the moment we exceed the cap,
    # rather than buffering the entire (possibly multi-GB) body up-front. A
    # client that omits or lies about Content-Length cannot bypass the limit by
    # streaming — we stop reading as soon as the cap is crossed.
    try:
        raw = await _read_bounded(upload, max_bytes=max_bytes)
    except VEXImportTooLarge as exc:
        return _problem_for_import_error(request, exc)

    try:
        summary = await import_vex(
            session,
            project=project,
            raw=raw,
            actor=actor,
        )
    except VEXImportError as exc:
        return _problem_for_import_error(request, exc)

    body = VEXImportSummary.model_validate(summary)
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )
