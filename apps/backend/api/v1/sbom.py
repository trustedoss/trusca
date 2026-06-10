"""
SBOM export HTTP surface — Phase 3 (Step 4).

Endpoint:
  - GET /v1/projects/{project_id}/sbom?format=...

Auth: every route requires a valid access token (``require_role("developer")``).
IDOR is enforced inline — outsiders see 404 (existence-hide), exactly as for
component detail. We use 404-not-403 here because the SBOM endpoint is the
only one in the project surface that can leak structural details (component
names, versions) about a project; matching the behaviour of
``GET /v1/components/{id}`` keeps the IDOR-leak surface uniform.

All 4xx / 5xx responses are RFC 7807 problem+json; the success response is a
file download with ``Content-Disposition: attachment``.
"""

from __future__ import annotations

import uuid
from typing import Literal

import structlog
from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.authz import assert_team_access
from core.db import get_db
from core.errors import problem_response
from core.security import CurrentUser, require_role
from models import Project
from services.project_service import (
    ProjectError,
    ProjectForbidden,
    ProjectNotFound,
    get_project,
)
from services.report_download_service import record_report_download
from services.sbom_export import (
    SBOMExportError,
    SBOMUnsupportedFormat,
    export_sbom,
)
from services.sbom_signature import (
    KIND_ATTEST_CERT,
    KIND_ATTESTATION,
    KIND_CERTIFICATE,
    KIND_SIGNATURE,
    SBOMArtifactTooLarge,
    SignatureArtifact,
    build_signature_bundle,
    get_public_key,
    get_signature_artifact,
)
from services.scan_resolution import SnapshotScanNotFound, latest_succeeded_scan_id

router = APIRouter(prefix="/v1", tags=["sbom"])
log = structlog.get_logger("sbom.api")


# `format` is keyed both as a Pydantic Literal (so 422 fires for invalid values
# at the OpenAPI layer) and re-validated inside the service for defense in
# depth. The Literal mirrors ``services.sbom_export.SUPPORTED_FORMATS``.
SBOMFormat = Literal["cyclonedx-json", "cyclonedx-xml", "spdx-json", "spdx-tv"]


def _problem_for_project_error(request: Request, exc: ProjectError) -> Response:
    """Translate project-domain errors with existence-hide on forbidden.

    The SBOM endpoint hides existence: a non-team-member sees the same 404
    they'd see for an unknown project id. Inside the project domain a
    forbidden lookup raises :class:`ProjectForbidden`; we rewrite that to a
    404 envelope here. ProjectNotFound already has status_code=404.
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


def _problem_for_sbom_error(request: Request, exc: SBOMExportError) -> Response:
    return problem_response(
        status_code=exc.status_code,
        title=exc.title,
        detail=str(exc) or exc.title,
        instance=request.url.path,
    )


# ---------------------------------------------------------------------------
# GET /v1/projects/{project_id}/sbom
# ---------------------------------------------------------------------------


@router.get(
    "/projects/{project_id}/sbom",
    summary="Export SBOM for the project's latest succeeded scan",
    response_class=Response,
    responses={
        200: {
            "description": "SBOM document download",
            # Format-specific media types (M-24) — mirrors
            # ``services.sbom_export._FORMAT_CATALOG``.
            "content": {
                "application/vnd.cyclonedx+json": {},
                "application/vnd.cyclonedx+xml": {},
                "application/spdx+json": {},
                "text/spdx": {},
            },
        },
        401: {"description": "Authentication required"},
        404: {"description": "Project not found or not accessible"},
        422: {"description": "Unknown SBOM format"},
    },
)
async def export_project_sbom_endpoint(
    request: Request,
    project_id: uuid.UUID,
    fmt: SBOMFormat = Query(
        default="cyclonedx-json",
        alias="format",
        description="SBOM output format.",
    ),
    scan_id: uuid.UUID | None = Query(
        default=None,
        description=(
            "Optional release-snapshot anchor (feature #28). When given, export "
            "this SPECIFIC succeeded scan instead of the project's latest succeeded "
            "scan. Must belong to this project and be succeeded, else 404. Omit for "
            "the default latest-succeeded behaviour."
        ),
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
    # cross_team_attempt log entry is written for any unexpected gap. This
    # is belt-and-braces with `get_project`; cheap and consistent.
    assert_team_access(
        actor,
        project.team_id,
        log=log,
        resource="sbom_export",
        resource_id=str(project_id),
        deny=lambda: ProjectForbidden(f"actor is not a member of team {project.team_id}"),
    )

    try:
        body, content_type, filename = await export_sbom(
            session,
            project_id=project_id,
            fmt=fmt,
            scan_id=scan_id,
        )
    except SnapshotScanNotFound:
        # Existence-hide: a pinned scan_id that is cross-project / non-succeeded /
        # nonexistent surfaces the same 404 as an unknown project id.
        return problem_response(
            status_code=status.HTTP_404_NOT_FOUND,
            title="Scan Snapshot Not Found",
            detail="No succeeded scan with that id exists for this project.",
            instance=request.url.path,
        )
    except SBOMUnsupportedFormat as exc:
        return _problem_for_sbom_error(request, exc)
    except SBOMExportError as exc:  # pragma: no cover - defensive
        return _problem_for_sbom_error(request, exc)

    # Encode as UTF-8 explicitly — XML / SPDX-TV may carry non-ASCII names.
    # ``Content-Disposition: attachment`` makes browsers offer "save as".
    body_bytes = body.encode("utf-8")

    # Emit the Reports-center history row (W3 #32a). When the caller pinned a
    # specific scan via ``?scan_id=`` we already validated it inside
    # ``export_sbom``; otherwise we resolve the latest succeeded scan so the
    # history row carries the actual snapshot the SBOM was rendered against.
    # Best-effort: ANY DB error inside the helper is logged + swallowed.
    if scan_id is None:
        resolved_scan_id = await latest_succeeded_scan_id(session, project_id)
    else:
        resolved_scan_id = scan_id
    await record_report_download(
        session,
        project=project,
        scan_id=resolved_scan_id,
        user=actor,
        report_type="sbom",
        fmt=fmt,
        size_bytes=len(body_bytes),
        request=request,
    )

    headers = {
        "content-disposition": f'attachment; filename="{filename}"',
    }
    return Response(
        content=body_bytes,
        status_code=status.HTTP_200_OK,
        media_type=content_type,
        headers=headers,
    )


# ---------------------------------------------------------------------------
# SBOM signature download surface — v2.3-s3
#
# These endpoints let a consumer verify the project's signed SBOM *externally*
# with ``cosign verify-blob`` — they expose ONLY public material (the SBOM, the
# detached signature, the Fulcio certificate, the attestation, and the cosign
# PUBLIC key). The private key + password are never read or returned by the
# service layer (see ``services/sbom_signature.py``).
#
# Auth + IDOR are identical to the SBOM export endpoint above: every route needs
# a valid access token (``require_role("developer")``) and re-uses ``get_project``
# so an outsider sees a 404 (existence-hide), never a 403. A scan that was never
# signed (signing skipped) has no signature artifacts, so the locators return
# None and we surface a 404 with an actionable RFC 7807 envelope.
# ---------------------------------------------------------------------------


async def _resolve_project_or_problem(
    request: Request,
    *,
    project_id: uuid.UUID,
    session: AsyncSession,
    actor: CurrentUser,
    resource: str,
) -> Project | Response:
    """Shared auth + IDOR guard for the signature surface.

    Returns the project on success, or a :class:`Response` (RFC 7807 problem)
    that the caller should return verbatim. Mirrors the export endpoint:
    existence-hide on forbidden (404, never 403), plus a belt-and-braces
    ``assert_team_access`` so the cross-team audit log entry fires.
    """
    try:
        project = await get_project(session, project_id=project_id, actor=actor)
    except ProjectNotFound as exc:
        return _problem_for_project_error(request, exc)
    except ProjectForbidden as exc:
        return _problem_for_project_error(request, exc)
    except ProjectError as exc:  # pragma: no cover - defensive catch-all
        return _problem_for_project_error(request, exc)

    assert_team_access(
        actor,
        project.team_id,
        log=log,
        resource=resource,
        resource_id=str(project_id),
        deny=lambda: ProjectForbidden(f"actor is not a member of team {project.team_id}"),
    )
    return project


def _download_response(artifact: SignatureArtifact) -> Response:
    """Build the ``Content-Disposition: attachment`` download response.

    ``artifact.content`` is already bytes (read off disk / zipped in memory), so
    unlike the SBOM export path we do not re-encode.
    """
    headers = {"content-disposition": f'attachment; filename="{artifact.filename}"'}
    return Response(
        content=artifact.content,
        status_code=status.HTTP_200_OK,
        media_type=artifact.media_type,
        headers=headers,
    )


def _not_found_problem(request: Request, *, detail: str) -> Response:
    """RFC 7807 404 for a missing signing artifact.

    We do NOT use the project existence-hide title here — the project IS visible
    to this caller; what is missing is the *signing artifact* (the scan was never
    signed, or signing was skipped). The detail explains the actionable reason.
    """
    return problem_response(
        status_code=status.HTTP_404_NOT_FOUND,
        title="Signature Artifact Not Found",
        detail=detail,
        instance=request.url.path,
    )


def _too_large_problem(request: Request, exc: SBOMArtifactTooLarge) -> Response:
    """RFC 7807 413 when an artifact / bundle exceeds the configured size cap.

    A 413 here is a server-side DoS guard, not a client-fixable error — the
    detail intentionally avoids echoing the exact byte sizes (logged server-side)
    so we do not leak storage internals.
    """
    return problem_response(
        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        title=exc.title,
        detail=(
            "The requested SBOM signing artifact exceeds the maximum download size "
            "for this deployment and cannot be served."
        ),
        instance=request.url.path,
    )


@router.get(
    "/projects/{project_id}/sbom/signature",
    summary="Download the detached cosign signature for the latest SBOM",
    response_class=Response,
    responses={
        200: {
            "description": "Detached cosign signature (verify with cosign verify-blob)",
            "content": {"application/octet-stream": {}},
        },
        401: {"description": "Authentication required"},
        404: {"description": "Project not accessible, or the SBOM was not signed"},
    },
)
async def download_sbom_signature_endpoint(
    request: Request,
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    project = await _resolve_project_or_problem(
        request, project_id=project_id, session=session, actor=actor, resource="sbom_signature"
    )
    if isinstance(project, Response):
        return project

    try:
        artifact = await get_signature_artifact(session, project=project, kind=KIND_SIGNATURE)
    except SBOMArtifactTooLarge as exc:
        return _too_large_problem(request, exc)
    if artifact is None:
        return _not_found_problem(
            request,
            detail=(
                "No SBOM signature is available for this project's latest succeeded scan. "
                "The scan may not have been signed (cosign signing is best-effort)."
            ),
        )
    return _download_response(artifact)


@router.get(
    "/projects/{project_id}/sbom/certificate",
    summary="Download the Fulcio signing certificate (keyless signing only)",
    response_class=Response,
    responses={
        200: {
            "description": "Fulcio signing certificate (keyless verification)",
            "content": {"application/x-pem-file": {}},
        },
        401: {"description": "Authentication required"},
        404: {"description": "Project not accessible, or no keyless certificate exists"},
    },
)
async def download_sbom_certificate_endpoint(
    request: Request,
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    project = await _resolve_project_or_problem(
        request, project_id=project_id, session=session, actor=actor, resource="sbom_certificate"
    )
    if isinstance(project, Response):
        return project

    try:
        artifact = await get_signature_artifact(session, project=project, kind=KIND_CERTIFICATE)
    except SBOMArtifactTooLarge as exc:
        return _too_large_problem(request, exc)
    if artifact is None:
        return _not_found_problem(
            request,
            detail=(
                "No signing certificate is available. Certificates are emitted only by "
                "keyless (sigstore Fulcio) signing; key-based deployments verify with the "
                "public key from the public-key endpoint instead."
            ),
        )
    return _download_response(artifact)


@router.get(
    "/projects/{project_id}/sbom/attestation",
    summary="Download the in-toto / SLSA provenance attestation for the latest SBOM",
    response_class=Response,
    responses={
        200: {
            "description": "in-toto / DSSE SLSA provenance attestation",
            "content": {"application/octet-stream": {}},
        },
        401: {"description": "Authentication required"},
        404: {"description": "Project not accessible, or no attestation exists"},
    },
)
async def download_sbom_attestation_endpoint(
    request: Request,
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    project = await _resolve_project_or_problem(
        request, project_id=project_id, session=session, actor=actor, resource="sbom_attestation"
    )
    if isinstance(project, Response):
        return project

    try:
        artifact = await get_signature_artifact(session, project=project, kind=KIND_ATTESTATION)
    except SBOMArtifactTooLarge as exc:
        return _too_large_problem(request, exc)
    if artifact is None:
        return _not_found_problem(
            request,
            detail=(
                "No SBOM attestation is available for this project's latest succeeded scan. "
                "Attestation is best-effort and only runs after a successful signing."
            ),
        )
    return _download_response(artifact)


@router.get(
    "/projects/{project_id}/sbom/attestation-certificate",
    summary="Download the Fulcio certificate for the attestation (keyless signing only)",
    response_class=Response,
    responses={
        200: {
            "description": "Fulcio certificate for the in-toto attestation (keyless verification)",
            "content": {"application/x-pem-file": {}},
        },
        401: {"description": "Authentication required"},
        404: {"description": "Project not accessible, or no attestation certificate exists"},
    },
)
async def download_sbom_attestation_certificate_endpoint(
    request: Request,
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    # Keyless attestation emits its own Fulcio certificate (distinct from the
    # signature certificate). Exposing it individually — and in the bundle — lets
    # a keyless consumer run ``cosign verify-blob-attestation`` without contacting
    # the portal. Key-based deployments emit no certificate (404 with guidance).
    project = await _resolve_project_or_problem(
        request,
        project_id=project_id,
        session=session,
        actor=actor,
        resource="sbom_attestation_certificate",
    )
    if isinstance(project, Response):
        return project

    try:
        artifact = await get_signature_artifact(session, project=project, kind=KIND_ATTEST_CERT)
    except SBOMArtifactTooLarge as exc:
        return _too_large_problem(request, exc)
    if artifact is None:
        return _not_found_problem(
            request,
            detail=(
                "No attestation certificate is available. Attestation certificates are emitted "
                "only by keyless (sigstore Fulcio) signing; key-based deployments verify the "
                "attestation with the public key from the public-key endpoint instead."
            ),
        )
    return _download_response(artifact)


@router.get(
    "/projects/{project_id}/sbom/public-key",
    summary="Download the cosign public key for verifying SBOM signatures",
    response_class=Response,
    responses={
        200: {
            "description": "cosign PUBLIC key (verify with cosign verify-blob --key)",
            "content": {"application/x-pem-file": {}},
        },
        401: {"description": "Authentication required"},
        404: {"description": "Project not accessible, or no public key is configured"},
    },
)
async def download_sbom_public_key_endpoint(
    request: Request,
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    # The public key is deployment-global, but we still scope by project so the
    # auth/IDOR shape matches the rest of the signature surface (a caller must be
    # able to see the project to learn its verification material). The PRIVATE
    # key is never exposed — the service reads only the configured ``.pub``.
    project = await _resolve_project_or_problem(
        request, project_id=project_id, session=session, actor=actor, resource="sbom_public_key"
    )
    if isinstance(project, Response):
        return project

    artifact = get_public_key(project=project)
    if artifact is None:
        return _not_found_problem(
            request,
            detail=(
                "No cosign public key is configured for this deployment. "
                "If signing is keyless (sigstore Fulcio), verify using the certificate "
                "from the certificate endpoint instead of a public key."
            ),
        )
    return _download_response(artifact)


@router.get(
    "/projects/{project_id}/sbom/signature-bundle",
    summary="Download a zip bundle (SBOM + signature + cert/public-key + attestation + README)",
    response_class=Response,
    responses={
        200: {
            "description": "Zip bundle for external cosign verification",
            "content": {"application/zip": {}},
        },
        401: {"description": "Authentication required"},
        404: {"description": "Project not accessible, or the SBOM was not signed"},
    },
)
async def download_sbom_signature_bundle_endpoint(
    request: Request,
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    project = await _resolve_project_or_problem(
        request,
        project_id=project_id,
        session=session,
        actor=actor,
        resource="sbom_signature_bundle",
    )
    if isinstance(project, Response):
        return project

    try:
        artifact = await build_signature_bundle(session, project=project)
    except SBOMArtifactTooLarge as exc:
        return _too_large_problem(request, exc)
    if artifact is None:
        return _not_found_problem(
            request,
            detail=(
                "No signed SBOM is available for this project's latest succeeded scan, so a "
                "verification bundle cannot be assembled. The scan may not have been signed."
            ),
        )
    return _download_response(artifact)
