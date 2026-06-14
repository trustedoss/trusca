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
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.api_key_auth import require_role_or_api_key
from core.authz import assert_team_access
from core.config import sbom_ingest_max_bytes, scan_trigger_rate_limit
from core.db import get_db
from core.errors import problem_response
from core.ratelimit import _authenticated_user_key, limiter
from core.security import CurrentUser, require_role
from models import Project, SbomConformance
from schemas.sbom import SbomConformanceRead
from schemas.scan import ScanPublic
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
from services.sbom_ingest_service import (
    SbomIngestError,
    SbomIngestTooLarge,
    ingest_sbom,
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
from services.scan_service import (
    ConcurrentScanLimitExceeded,
    ScanError,
    ScanInProgressConflict,
)

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


def _problem_for_sbom_ingest_error(request: Request, exc: SbomIngestError) -> Response:
    """Translate an SBOM-ingest validation error into an RFC 7807 envelope.

    Each ``SbomIngestError`` subclass carries its own ``status_code`` (413 / 415 /
    422) and a stable ``type_uri`` problem URI.
    """
    return problem_response(
        status_code=exc.status_code,
        title=exc.title,
        detail=str(exc) or exc.title,
        instance=request.url.path,
        type_=exc.type_uri,
    )


def _problem_for_scan_error(request: Request, exc: ScanError) -> Response:
    """Translate a scan-domain error (raised by the shared scan guards) to 7807.

    The SBOM-ingest path reuses ``services.scan_service`` guards, so it can raise
    ``ScanForbidden`` (403), ``ProjectMissingForScan`` (404),
    ``ScanArchivedConflict`` (409), ``ConcurrentScanLimitExceeded`` (429),
    ``ScanInProgressConflict`` (409), or ``ScanEnqueueFailed`` (503). The mapping
    here mirrors ``api/v1/projects.py::_problem_for_scan_error`` exactly so the
    two scan-creating surfaces return identical envelopes.
    """
    # B1: the per-team concurrency cap carries the `limit` extension, a domain
    # `type` URI, and a Retry-After header. M1 (security-reviewer): the live
    # running_scans count is deliberately NOT exposed (intra-team side-channel).
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
    # P1 #10 — machine-checkable extension on the per-project active-scan conflict.
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


def _declared_content_length(request: Request) -> int | None:
    """Parse the request's ``Content-Length`` header, or ``None`` if absent/bad.

    Mirrors ``api/v1/projects.py``: a multipart upload's Content-Length covers the
    whole envelope, so it is a safe over-estimate for an early-reject ceiling. A
    malformed value is treated as absent (the streamed-bytes cap in the service is
    the authoritative guard).
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


# ---------------------------------------------------------------------------
# POST /v1/projects/{project_id}/sbom-ingest — external CycloneDX SBOM ingest
# ---------------------------------------------------------------------------


@router.post(
    "/projects/{project_id}/sbom-ingest",
    response_model=ScanPublic,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ingest an external CycloneDX SBOM (queues a Celery task; returns 202 Accepted)",
    responses={
        202: {
            "description": "SBOM accepted; a queued scan row is returned.",
            "content": {"application/json": {}},
        },
        401: {"description": "Authentication required"},
        403: {
            "description": (
                "Caller is not a member of the project's owning team, or a "
                "project-scoped API key targets a different project. RFC 7807."
            ),
            "content": {"application/problem+json": {}},
        },
        404: {
            "description": "Project not found (existence-hidden). RFC 7807.",
            "content": {"application/problem+json": {}},
        },
        409: {
            "description": (
                "A scan is already queued/running for this project, or the project "
                "is archived. RFC 7807."
            ),
            "content": {"application/problem+json": {}},
        },
        413: {
            "description": "SBOM exceeds the ingest size cap. RFC 7807.",
            "content": {"application/problem+json": {}},
        },
        415: {
            "description": "Upload is not a CycloneDX JSON media type. RFC 7807.",
            "content": {"application/problem+json": {}},
        },
        422: {
            "description": (
                "Upload is not a valid / supported CycloneDX document (not JSON, "
                "wrong bomFormat, unsupported specVersion, too many components). "
                "RFC 7807."
            ),
            "content": {"application/problem+json": {}},
        },
        429: {
            "description": (
                "Rate limited (too many scan creations from this user) or the "
                "team's concurrent-scan cap is reached. RFC 7807 + Retry-After."
            ),
            "content": {"application/problem+json": {}},
        },
    },
)
# B1: share the SAME rate-limit bucket as the scan-trigger endpoint
# (``scope="scan_trigger"``, keyed by the authenticated user) — ingesting an SBOM
# is a scan-creating action, so it draws from the same per-user budget rather than
# opening a parallel lane. See ``api/v1/projects.py::trigger_scan_endpoint`` for
# why ``shared_limit`` (route-path-independent bucket key) is required over plain
# ``@limit`` (which would bucket per {project_id} and let a user spray uploads
# across projects to bypass the cap).
@limiter.shared_limit(
    scan_trigger_rate_limit,
    scope="scan_trigger",
    key_func=_authenticated_user_key,
)
async def ingest_sbom_endpoint(
    request: Request,
    project_id: uuid.UUID,
    sbom: UploadFile = File(
        ...,
        description="A CycloneDX JSON SBOM document (.json / .cdx.json).",
    ),
    ref: str | None = Form(
        default=None,
        description=(
            "Optional git ref this SBOM was produced from (e.g. refs/heads/main, "
            "a tag, or a bare branch name). Normalized into a retention key."
        ),
    ),
    release: str | None = Form(
        default=None,
        description="Optional release/version label for the resulting snapshot.",
    ),
    session: AsyncSession = Depends(get_db),
    # Accept a JWT or a tos_ API key — CI pipelines push SBOMs with the key, the
    # SPA with a JWT. A project-scoped key targeting a different project is 403'd
    # inside the shared scan guard (existence-hide is not applied to the key-scope
    # mismatch, matching the scan-trigger endpoint).
    actor: CurrentUser = Depends(require_role_or_api_key("developer")),
) -> Response:
    # NOTE: this is NOT the Dependency-Track ``/api/v1/bom`` + ``X-Api-Key`` BOM
    # upload surface — it is a first-party, RBAC-scoped portal endpoint that
    # returns a queued scan row (202) rather than a DT token.

    # M2-style fast-fail: reject before reading a single body byte when the
    # declared Content-Length already exceeds the ingest cap. The service still
    # enforces the cap on the ACTUAL streamed bytes (a client can lie about /
    # omit Content-Length), so this is a courtesy short-circuit, not the guard.
    declared = _declared_content_length(request)
    if declared is not None and declared > sbom_ingest_max_bytes():
        return _problem_for_sbom_ingest_error(
            request,
            SbomIngestTooLarge(
                f"declared content-length {declared} exceeds the "
                f"{sbom_ingest_max_bytes()}-byte SBOM ingest limit"
            ),
        )

    # Guard order is enforced inside ``ingest_sbom``: authz/existence (404/403) +
    # api-key scope (403) FIRST, then request validation (413/415/422), then the
    # 409 active-scan conflict at flush — so a non-member never learns a project's
    # state. Scan-domain guard failures map via ``_problem_for_scan_error``; SBOM
    # validation failures via ``_problem_for_sbom_ingest_error``.
    try:
        scan = await ingest_sbom(
            session,
            project_id=project_id,
            upload=sbom,
            actor=actor,
            ref=ref,
            release=release,
        )
    except SbomIngestError as exc:
        return _problem_for_sbom_ingest_error(request, exc)
    except ScanError as exc:
        return _problem_for_scan_error(request, exc)

    body = ScanPublic.model_validate(scan)
    return Response(
        # ``by_alias=True`` so the response carries ``metadata`` (the API field
        # name) rather than ``scan_metadata`` (the ORM attribute name) — matches
        # the scan-trigger endpoint's ScanPublic serialization.
        content=body.model_dump_json(by_alias=True),
        status_code=status.HTTP_202_ACCEPTED,
        media_type="application/json",
    )


@router.get(
    "/projects/{project_id}/scans/{scan_id}/conformance",
    response_model=SbomConformanceRead,
    summary="Get the conformance verdict for an ingested SBOM scan",
    responses={
        200: {
            "description": "The SBOM conformance verdict (pass/warn/fail + per-check detail).",
            "content": {"application/json": {}},
        },
        401: {"description": "Authentication required"},
        404: {
            "description": (
                "Project not accessible (existence-hidden), or no conformance verdict "
                "exists for this scan (not an ingested SBOM scan, or still queued). "
                "RFC 7807."
            ),
            "content": {"application/problem+json": {}},
        },
    },
)
async def get_sbom_conformance_endpoint(
    request: Request,
    project_id: uuid.UUID,
    scan_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role_or_api_key("developer")),
) -> Response:
    # Same auth + IDOR guard as the rest of the SBOM surface: an outsider sees
    # 404 (existence-hide), and the cross-team audit entry fires.
    project = await _resolve_project_or_problem(
        request,
        project_id=project_id,
        session=session,
        actor=actor,
        resource="sbom_conformance",
    )
    if isinstance(project, Response):
        return project

    # The verdict row carries the denormalised project_id, so the scan-belongs-
    # to-project check is a single predicate (a cross-project scan_id yields no
    # row → the same 404 as an unknown scan). No conformance row also means the
    # scan is not an ingested SBOM scan, or its ingest task has not reached the
    # conformance stage yet.
    row = (
        await session.execute(
            select(SbomConformance).where(
                SbomConformance.scan_id == scan_id,
                SbomConformance.project_id == project_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return problem_response(
            status_code=status.HTTP_404_NOT_FOUND,
            title="Conformance Verdict Not Found",
            detail=(
                "No SBOM conformance verdict exists for this scan. The scan may not be "
                "an ingested SBOM scan, or its ingest may still be in progress."
            ),
            instance=request.url.path,
        )

    body = SbomConformanceRead.model_validate(row)
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# slowapi's ``@limiter.shared_limit`` wraps the endpoint with functools.wraps,
# whose ``__globals__`` is slowapi's module. Under ``from __future__ import
# annotations`` FastAPI's ``get_type_hints()`` on the wrapper cannot resolve our
# string annotations, so it misclassifies the body / dependency params. Mirror the
# fix used in projects.py / auth.py: copy the names the wrapper needs into its
# ``__globals__`` (the dict is mutable even though the attribute is read-only).
for _name in (
    "uuid",
    "UploadFile",
    "AsyncSession",
    "CurrentUser",
    "Request",
    "Response",
    "Depends",
    "File",
    "Form",
):
    if _name in globals():
        ingest_sbom_endpoint.__globals__.setdefault(_name, globals()[_name])
del _name
