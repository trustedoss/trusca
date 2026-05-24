"""
Policy gate + SCA PR-comment HTTP surface — Phase 5 PR #17.

Endpoints:

  - GET  /v1/projects/{project_id}/gate-result
        Return the build-blocking verdict for the project's most recent
        successful scan. Accepts either a JWT (web UI) or an API key (CI
        runners) bearer token.

  - POST /v1/scans/{scan_id}/post-pr-comment
        Render the SCA Markdown report and (optionally) post it as a
        comment on a GitHub PR. ``dry_run=true`` returns the body without
        touching api.github.com — that is the default contract used by CI
        rehearsal flows and by the integration tests.

All 4xx / 5xx responses are RFC 7807 ``application/problem+json``. Cross-team
attempts are existence-hidden as 404 (matching the pattern PR #11 introduced
for vulnerabilities and license findings) so probing the gate surface cannot
enumerate other teams' projects.

Auth dispatch
-------------
Both endpoints accept JWT *or* API key bearer tokens via
:func:`_principal_from_jwt_or_api_key`. The dispatcher resolves the
Authorization header by prefix — ``tos_*`` tokens go through
:func:`get_api_key_principal`, JWT-shaped tokens through
:func:`get_optional_current_user`. Either path returns a ``CurrentUser`` with
identical downstream contract; neither path elevates privilege beyond what
the bearer's owner has at request time.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import String, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.api_key_auth import get_api_key_principal
from core.authz import assert_team_access
from core.db import get_db
from core.errors import problem_response
from core.security import CurrentUser, get_optional_current_user
from models import (
    Component,
    ComponentVersion,
    LicenseFinding,
    Project,
    Scan,
    ScanComponent,
    VulnerabilityFinding,
)
from models import (
    License as LicenseModel,
)
from models import (
    Vulnerability as VulnerabilityModel,
)
from schemas.policy_gate import (
    GateResultResponse,
    PostPRCommentRequest,
    PostPRCommentResponse,
)
from services.policy_gate import GateResult, evaluate_gate
from services.project_service import ProjectError, ProjectNotFound
from services.sca_comment import (
    CommentSummary,
    RecommendedUpgrade,
    SCACommentError,
    post_pr_comment,
)
from services.upgrade_recommendation import (
    FindingSignal,
    priority_rank,
    recommend_for_component,
)

# Severity / license category buckets we always emit in the SCA-comment
# summary. Mirrors ``services.project_detail_service._ALL_SEVERITY_KEYS`` /
# ``_ALL_LICENSE_KEYS`` — duplicated locally rather than imported through a
# private symbol to keep the module boundary clean.
_SEVERITY_KEYS = ("critical", "high", "medium", "low", "info", "none")
_LICENSE_KEYS = ("forbidden", "conditional", "allowed", "unknown")

# Cap on how many "upgrade to X" rows the PR comment surfaces (v2.2 2.2-a3) so
# the comment stays scannable even for a large dependency tree. The highest-
# priority (direct, high-severity, high-EPSS) upgrades win the slots.
_MAX_COMMENT_RECOMMENDATIONS = 10
# Cap on how many CVE ids we list per recommended upgrade row.
_MAX_CVES_PER_RECOMMENDATION = 5

# Finding statuses that are NOT open work — mirrors
# services.policy_gate._CLOSED_FINDING_STATUSES so the comment's upgrade
# recommendations reflect exactly the findings the gate still considers open.
_CLOSED_FINDING_STATUSES = ("not_affected", "fixed", "false_positive")

router = APIRouter(prefix="/v1", tags=["policy-gate"])
log = structlog.get_logger("policy_gate.api")


# ---------------------------------------------------------------------------
# JWT-or-API-Key auth dispatcher
# ---------------------------------------------------------------------------


async def _principal_from_jwt_or_api_key(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> CurrentUser:
    """Resolve the bearer token to a :class:`CurrentUser`.

    Tries API-key auth first (cheap prefix check + bcrypt verify), falls
    back to JWT. Raises 401 with an RFC 7807 envelope when neither path
    yields an active principal.

    The two helpers we delegate to (``get_api_key_principal`` /
    ``get_optional_current_user``) each carry their own session dependency,
    but FastAPI deduplicates Depends-of-the-same-callable within a request,
    so the actual DB hits are: 1 authenticate_api_key (if it looks like a
    key) + 1 user lookup. JWT path only does the user lookup.
    """
    # First, try API key — the helper returns None if the token is not a
    # tos_-shaped key, so JWTs fall straight through to the next branch.
    principal = await get_api_key_principal(request, session)
    if principal is not None and principal.is_active:
        return principal

    principal = await get_optional_current_user(request, session)
    if principal is not None and principal.is_active:
        return principal

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _problem_for_project_error(request: Request, exc: ProjectError) -> Response:
    return problem_response(
        status_code=exc.status_code,
        title=exc.title,
        detail=str(exc) or exc.title,
        instance=request.url.path,
    )


def _problem_for_sca_comment_error(request: Request, exc: SCACommentError) -> Response:
    return problem_response(
        status_code=exc.status_code,
        title=exc.title,
        detail=str(exc) or exc.title,
        instance=request.url.path,
    )


async def _load_project_for_gate(
    session: AsyncSession,
    project_id: uuid.UUID,
    actor: CurrentUser,
) -> Project:
    """Project lookup with **existence-hide** cross-team behaviour.

    Unlike ``services.project_service.get_project`` (which raises 403 for
    cross-team callers because the rest of the project surface treats team
    membership as non-secret), the gate result endpoint is reachable by API
    keys whose owners may not even know the project exists. We hide
    existence with a 404 so a stolen key cannot enumerate every project_id
    the platform has ever issued.
    """
    result = await session.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if project is None:
        raise ProjectNotFound(f"project {project_id} not found")

    assert_team_access(
        actor,
        project.team_id,
        log=log,
        resource="policy_gate",
        resource_id=str(project_id),
        deny=lambda: ProjectNotFound(f"project {project_id} not found"),
    )
    return project


def _build_response_body(result: GateResult) -> GateResultResponse:
    return GateResultResponse(
        gate=result.gate,
        reason=result.reason,
        critical_cve_count=result.critical_cve_count,
        forbidden_license_count=result.forbidden_license_count,
        epss_gate_count=result.epss_gate_count,
        epss_threshold=result.epss_threshold,
        project_id=result.project_id,
        scan_id=result.scan_id,
        evaluated_at=result.evaluated_at,
    )


# ---------------------------------------------------------------------------
# GET /v1/projects/{project_id}/gate-result
# ---------------------------------------------------------------------------


@router.get(
    "/projects/{project_id}/gate-result",
    response_model=GateResultResponse,
    summary="Evaluate the build-gate verdict for the project's latest succeeded scan",
)
async def get_gate_result_endpoint(
    request: Request,
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(_principal_from_jwt_or_api_key),
) -> Response:
    try:
        await _load_project_for_gate(session, project_id, actor)
    except ProjectError as exc:
        return _problem_for_project_error(request, exc)

    gate_result = await evaluate_gate(session, project_id)
    body = _build_response_body(gate_result)
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# POST /v1/scans/{scan_id}/post-pr-comment
# ---------------------------------------------------------------------------


def _project_view_url(project_id: uuid.UUID) -> str | None:
    """Return the human-facing project URL for the comment, if configured.

    ``PORTAL_PUBLIC_URL`` is read at request time per CLAUDE.md core rule
    #11. If unset, the comment skips the trailing "View full report" link.
    """
    base = os.getenv("PORTAL_PUBLIC_URL")
    if not base:
        return None
    return f"{base.rstrip('/')}/projects/{project_id}"


def _resolve_github_token() -> str | None:
    """Read the GitHub bearer token from the environment at request time."""
    return os.getenv("GITHUB_TOKEN") or os.getenv("TRUSTEDOSS_GITHUB_TOKEN")


async def _build_recommended_upgrades(
    session: AsyncSession,
    *,
    scan_id: uuid.UUID,
) -> tuple[RecommendedUpgrade, ...]:
    """Compute the scan's highest-priority "upgrade to X" rows (v2.2 2.2-a3).

    One query pulls every open finding on the scan with its component identity
    (name + current version), the per-finding ``fixed_version``, severity, EPSS,
    and the ``direct`` graph signal. We group by ``component_version`` and run
    the upgrade engine per component, keep only the components with an
    actionable recommendation, sort by :func:`priority_rank` (direct → severity
    → EPSS), and cap the list. Pure stored-data read — never touches DT.
    """
    rows = (
        await session.execute(
            select(
                VulnerabilityFinding.component_version_id.label("cv_id"),
                Component.name.label("component_name"),
                ComponentVersion.version.label("current_version"),
                VulnerabilityFinding.fixed_version.label("fixed_version"),
                cast(VulnerabilityModel.severity, String).label("severity"),
                VulnerabilityModel.epss_score.label("epss_score"),
                VulnerabilityModel.external_id.label("cve_id"),
                func.coalesce(ScanComponent.direct, False).label("direct"),
                ScanComponent.depth.label("depth"),
            )
            .select_from(VulnerabilityFinding)
            .join(
                VulnerabilityModel,
                VulnerabilityModel.id == VulnerabilityFinding.vulnerability_id,
            )
            .join(
                ComponentVersion,
                ComponentVersion.id == VulnerabilityFinding.component_version_id,
            )
            .join(Component, Component.id == ComponentVersion.component_id)
            .outerjoin(
                ScanComponent,
                (ScanComponent.scan_id == VulnerabilityFinding.scan_id)
                & (
                    ScanComponent.component_version_id
                    == VulnerabilityFinding.component_version_id
                ),
            )
            .where(VulnerabilityFinding.scan_id == scan_id)
            .where(
                cast(VulnerabilityFinding.status, String).notin_(_CLOSED_FINDING_STATUSES)
            )
        )
    ).all()

    # Group by component_version. A diamond dep can produce multiple
    # ScanComponent rows (different paths), so dedupe (cv_id, cve_id) for the
    # signal list and OR the direct flag.
    grouped: dict[uuid.UUID, dict[str, Any]] = {}
    for row in rows:
        bucket = grouped.setdefault(
            row.cv_id,
            {
                "name": row.component_name,
                "current_version": row.current_version,
                "direct": False,
                "min_depth": None,
                "signals": {},  # cve_id -> FindingSignal
            },
        )
        if bool(row.direct):
            bucket["direct"] = True
        if row.depth is not None:
            prev = bucket["min_depth"]
            bucket["min_depth"] = row.depth if prev is None else min(prev, row.depth)
        bucket["signals"][row.cve_id] = FindingSignal(
            fixed_version=row.fixed_version,
            severity=str(row.severity),
            epss_score=float(row.epss_score) if row.epss_score is not None else None,
        )

    scored: list[tuple[tuple[int, int, float], RecommendedUpgrade]] = []
    for bucket in grouped.values():
        signals = list(bucket["signals"].values())
        is_direct = bool(bucket["direct"]) or (
            bucket["min_depth"] is not None and int(bucket["min_depth"]) == 1
        )
        rec = recommend_for_component(signals, direct=is_direct)
        if rec.recommended_version is None:
            continue  # only surface actionable upgrades in the comment.
        cve_ids = tuple(sorted(bucket["signals"].keys()))[:_MAX_CVES_PER_RECOMMENDATION]
        scored.append(
            (
                priority_rank(rec),
                RecommendedUpgrade(
                    component_name=str(bucket["name"]),
                    current_version=str(bucket["current_version"]),
                    recommended_version=rec.recommended_version,
                    max_severity=rec.max_severity or "unknown",
                    direct=rec.direct,
                    cve_ids=cve_ids,
                ),
            )
        )

    # Most urgent first; stable tie-break on component name keeps output
    # deterministic across runs.
    scored.sort(key=lambda item: (item[0], item[1].component_name), reverse=True)
    return tuple(rec for _, rec in scored[:_MAX_COMMENT_RECOMMENDATIONS])


async def _build_summary_for_scan(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    scan_id: uuid.UUID | None,
) -> CommentSummary:
    """Compose the per-scan numbers the Markdown body shows.

    The comment must reflect the SAME scan the gate verdict was computed
    against — using ``Project.latest_scan_id`` (which the Overview tab
    relies on) would produce a comment whose component count disagrees
    with the gate decision when the most recent scan is failed/queued.
    Aggregating directly against ``scan_id`` keeps the two in lock-step.

    When ``scan_id`` is ``None`` (project never had a successful scan), we
    return zero buckets and a zero component count — the comment then
    surfaces a clean "no signal" report rather than crashing.
    """
    sev: dict[str, int] = dict.fromkeys(_SEVERITY_KEYS, 0)
    lic: dict[str, int] = dict.fromkeys(_LICENSE_KEYS, 0)
    components_count = 0

    if scan_id is not None:
        # Component count for this scan.
        components_stmt = (
            select(func.count())
            .select_from(ScanComponent)
            .where(ScanComponent.scan_id == scan_id)
        )
        components_count = int((await session.execute(components_stmt)).scalar_one())

        # Severity buckets — count vulnerability_findings grouped by the
        # joined Vulnerability.severity. Status filtering matches the gate
        # logic so suppressed/open findings are counted but
        # not_affected/fixed/false_positive are not.
        sev_stmt = (
            select(
                cast(VulnerabilityModel.severity, String).label("severity"),
                func.count().label("n"),
            )
            .select_from(VulnerabilityFinding)
            .join(
                VulnerabilityModel,
                VulnerabilityModel.id == VulnerabilityFinding.vulnerability_id,
            )
            .where(VulnerabilityFinding.scan_id == scan_id)
            .where(
                cast(VulnerabilityFinding.status, String).notin_(
                    ("not_affected", "fixed", "false_positive"),
                ),
            )
            .group_by(cast(VulnerabilityModel.severity, String))
        )
        for row in (await session.execute(sev_stmt)).all():
            key = str(row.severity)
            if key == "unknown":
                key = "info"
            if key in sev:
                sev[key] = int(row.n)

        # License buckets — DISTINCT component_versions per category so the
        # numbers match the Overview tab's bucketing convention.
        lic_stmt = (
            select(
                cast(LicenseModel.category, String).label("category"),
                func.count(func.distinct(LicenseFinding.component_version_id)).label("n"),
            )
            .select_from(LicenseFinding)
            .join(LicenseModel, LicenseModel.id == LicenseFinding.license_id)
            .where(LicenseFinding.scan_id == scan_id)
            .group_by(cast(LicenseModel.category, String))
        )
        for row in (await session.execute(lic_stmt)).all():
            key = str(row.category)
            if key in lic:
                lic[key] = int(row.n)

    recommended_upgrades: tuple[RecommendedUpgrade, ...] = ()
    if scan_id is not None:
        recommended_upgrades = await _build_recommended_upgrades(
            session, scan_id=scan_id
        )

    return CommentSummary(
        components_count=components_count,
        severity_distribution=sev,
        license_distribution=lic,
        project_url=_project_view_url(project_id),
        recommended_upgrades=recommended_upgrades,
    )


@router.post(
    "/scans/{scan_id}/post-pr-comment",
    response_model=PostPRCommentResponse,
    summary="Render an SCA Markdown report and (optionally) post it to a GitHub PR",
)
async def post_pr_comment_endpoint(
    request: Request,
    scan_id: uuid.UUID,
    payload: PostPRCommentRequest,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(_principal_from_jwt_or_api_key),
) -> Response:
    # Resolve the scan -> project so we authorize against the project's
    # owning team, not against the scan id directly. A scan_id that the
    # caller cannot read is existence-hidden as 404.
    scan_row = (
        await session.execute(select(Scan).where(Scan.id == scan_id))
    ).scalar_one_or_none()
    if scan_row is None:
        return problem_response(
            status_code=status.HTTP_404_NOT_FOUND,
            title="Scan Not Found",
            detail=f"scan {scan_id} not found",
            instance=request.url.path,
        )

    try:
        await _load_project_for_gate(session, scan_row.project_id, actor)
    except ProjectError as exc:
        return _problem_for_project_error(request, exc)

    # Always evaluate the gate against the project's most recent succeeded
    # scan, not the scan_id in the URL — this is the build-gate semantic CI
    # tools expect. The scan_id in the URL only authorizes the caller to
    # the project; the gate verdict reflects the latest signal.
    gate_result = await evaluate_gate(session, scan_row.project_id)
    summary = await _build_summary_for_scan(
        session,
        project_id=scan_row.project_id,
        scan_id=gate_result.scan_id,
    )

    github_token = None if payload.dry_run else _resolve_github_token()
    try:
        posted = await post_pr_comment(
            repo_full_name=payload.repo_full_name,
            pr_number=payload.pr_number,
            gate_result=gate_result,
            summary=summary,
            github_token=github_token,
            dry_run=payload.dry_run,
        )
    except SCACommentError as exc:
        return _problem_for_sca_comment_error(request, exc)

    body = PostPRCommentResponse(
        status=posted.status,
        comment_id=posted.comment_id,
        comment_url=posted.comment_url,
        body_preview=posted.body_preview,
        gate=gate_result.gate,
    )
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# Re-export so the test suite can monkey-patch a stable symbol without
# reaching into a ``_``-prefixed helper.
resolve_github_token = _resolve_github_token


__all__ = ["router"]
