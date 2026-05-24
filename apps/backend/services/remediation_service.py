"""
Remediation service — v2.2 2.2-b2 (npm manifest dry-run).

Bridges the a3 upgrade-recommendation engine and the b2 npm edit adapter to
produce a DRY-RUN: "if we bumped your vulnerable npm dependencies to their
minimum-safe versions, here is the edited ``package.json`` and the diff." It does
NOT open a GitHub PR and does NOT persist anything — that is b3. This is a pure
read + compute over already-stored scan data plus an in-memory text edit.

Pipeline
--------
1. Resolve the project (team-scoped, 404 existence-hide — same shape as
   :mod:`services.source_tree_service`). RBAC: any team member (developer+).
2. Compute the project's npm upgrade recommendations from its LATEST scan, by
   running the a3 engine per npm component over its open findings (the exact same
   aggregation the build-gate PR comment uses, restricted to ``pkg:npm`` and
   keyed by the purl-decoded package NAME — so a scoped ``@scope/pkg`` is matched
   correctly against the manifest key, which ``Component.name`` alone would lose).
3. Obtain the manifest text: prefer the caller-supplied ``manifest_override``
   (an uploaded ``package.json``); otherwise best-effort read ``package.json``
   from the scan's preserved source tarball (G3.1). If neither is available we
   return a structured "no manifest" result, not a 500.
4. Run :func:`integrations.remediation.edit_npm_manifest` and return the proposed
   diff + per-package before/after + warnings.

CLAUDE.md compliance:
  * Core rule #11: byte limits read via ``os.getenv`` at call time (the adapter
    owns its size cap accessor).
  * Core rule #12: the only caller (the router) requires JWT; the service
    re-asserts team access for defence in depth.
  * §4: failures raise typed domain exceptions carrying an HTTP status / title /
    type-URI; the router maps them to RFC 7807 ``application/problem+json``.
  * §5: structlog JSON, one event per line; never logs manifest contents.
  * Core rule #2: NO schema change — everything is computed from existing rows
    and an in-memory edit; persisting the attempt is b3's migration.
"""

from __future__ import annotations

import tarfile
import uuid
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import unquote

import structlog
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import String, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.authz import assert_team_access
from core.security import CurrentUser
from integrations.remediation import (
    ManifestParseError,
    VersionBump,
    edit_npm_manifest,
)
from integrations.remediation.base import DependencyChange, ManifestWarning
from models import (
    Component,
    ComponentVersion,
    Project,
    ScanComponent,
    VulnerabilityFinding,
)
from models import (
    Vulnerability as VulnerabilityModel,
)
from services.policy_gate import _CLOSED_FINDING_STATUSES
from services.source_preservation_service import scan_source_tarball_path
from services.upgrade_recommendation import (
    FindingSignal,
    compare_versions,
    parse_version,
    recommend_for_component,
)

log = structlog.get_logger("remediation.service")

# Where the manifest came from — mirrors ``schemas.remediation.ManifestSource``.
ManifestSource = Literal["override", "preserved_source", "none"]

# Cap how many tar members we scan when hunting for package.json so a pathological
# tarball can't drive an unbounded walk. The viewer already bounds the tree; this
# is a second belt for THIS read path.
_MAX_TAR_MEMBERS_SCANNED = 50_000


# ---------------------------------------------------------------------------
# Domain exceptions (RFC 7807 — status_code / title / type_uri per §4)
# ---------------------------------------------------------------------------


class RemediationError(Exception):
    """Base class for remediation errors. Each carries an HTTP status."""

    status_code: int = 400
    title: str = "Remediation Error"
    type_uri: str = "https://docs.trustedoss.io/errors/remediation"


class ProjectNotAccessible(RemediationError):
    """The project is unknown or in another team (404 existence-hide)."""

    status_code = 404
    title = "Project Not Found"
    type_uri = "https://docs.trustedoss.io/errors/remediation-project-not-found"


class ManifestRejected(RemediationError):
    """The supplied / fetched manifest is malformed and cannot be edited."""

    status_code = 422
    title = "Manifest Rejected"
    type_uri = "https://docs.trustedoss.io/errors/remediation-manifest-rejected"


# ---------------------------------------------------------------------------
# Result containers (service-internal; the router maps to schemas)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DryRunRecommendation:
    """One npm component the dry-run wants to bump (advisory context)."""

    package: str
    current_version: str
    recommended_version: str


@dataclass(frozen=True)
class DryRunResult:
    """The computed npm remediation dry-run for a project."""

    project_id: uuid.UUID
    scan_id: uuid.UUID | None
    ecosystem: str
    manifest_source: ManifestSource
    manifest_found: bool
    changed: bool
    edited_manifest: str | None
    recommendations: tuple[DryRunRecommendation, ...] = ()
    changes: tuple[DependencyChange, ...] = ()
    warnings: tuple[ManifestWarning, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# npm name from purl
# ---------------------------------------------------------------------------


def decode_npm_package_name(purl: str | None) -> str | None:
    """Extract the npm package name (with scope) from a ``pkg:npm/...`` purl.

    ``pkg:npm/lodash@4.17.21``        → ``lodash``
    ``pkg:npm/%40scope%2Fpkg@1.0.0``  → ``@scope/pkg``
    ``pkg:npm/@scope/pkg@1.0.0``      → ``@scope/pkg`` (cdxgen may leave the
                                        scope un-encoded)

    Returns ``None`` for a non-npm or malformed purl. NEVER raises — the purl is
    DT/cdxgen-derived (untrusted).
    """
    if not isinstance(purl, str) or not purl.startswith("pkg:npm/"):
        return None
    body = purl[len("pkg:npm/") :]
    body = body.split("?", 1)[0].split("#", 1)[0]
    at = body.rfind("@")
    if at > 0:  # at == 0 is the scope marker, not a version separator
        body = body[:at]
    if not body:
        return None
    try:
        name = unquote(body)
    except (ValueError, UnicodeDecodeError):  # pragma: no cover — unquote is lenient
        return None
    return name or None


# ---------------------------------------------------------------------------
# Recommendation aggregation (npm only) over the project's latest scan
# ---------------------------------------------------------------------------


async def _resolve_accessible_project(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    actor: CurrentUser,
) -> Project:
    """Load the project, enforcing team access with 404 existence-hide."""
    project = (
        await session.execute(select(Project).where(Project.id == project_id))
    ).scalar_one_or_none()
    if project is None:
        raise ProjectNotAccessible(f"project {project_id} not found")
    assert_team_access(
        actor,
        project.team_id,
        log=log,
        resource="remediation",
        resource_id=str(project_id),
        deny=lambda: ProjectNotAccessible(f"project {project_id} not found"),
    )
    return project


@dataclass
class _ComponentBucket:
    """Mutable accumulator for one component's findings during aggregation."""

    name: str
    current_version: str
    direct: bool = False
    min_depth: int | None = None
    signals: dict[str, FindingSignal] = field(default_factory=dict)


async def _npm_recommendations_for_scan(
    session: AsyncSession,
    *,
    scan_id: uuid.UUID,
) -> dict[str, DryRunRecommendation]:
    """Return ``{npm_package_name → DryRunRecommendation}`` for a scan.

    Mirrors ``api/v1/policy_gate._build_recommended_upgrades`` but restricted to
    ``pkg:npm`` components and keyed by the purl-decoded package NAME so a scoped
    package is matched against the manifest correctly. Only components with an
    ACTIONABLE recommendation (``recommended_version is not None``) are returned.
    Pure stored-data read — never touches DT.
    """
    rows = (
        await session.execute(
            select(
                VulnerabilityFinding.component_version_id.label("cv_id"),
                Component.purl.label("purl"),
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
                & (ScanComponent.component_version_id == VulnerabilityFinding.component_version_id),
            )
            .where(VulnerabilityFinding.scan_id == scan_id)
            .where(Component.package_type == "npm")
            .where(cast(VulnerabilityFinding.status, String).notin_(_CLOSED_FINDING_STATUSES))
        )
    ).all()

    # Group by component_version (a diamond dep yields multiple ScanComponent
    # rows; dedupe (cv_id, cve_id) for the signal list and OR the direct flag).
    grouped: dict[uuid.UUID, _ComponentBucket] = {}
    for row in rows:
        name = decode_npm_package_name(row.purl)
        if name is None:
            continue
        bucket = grouped.get(row.cv_id)
        if bucket is None:
            bucket = _ComponentBucket(name=name, current_version=str(row.current_version))
            grouped[row.cv_id] = bucket
        if bool(row.direct):
            bucket.direct = True
        if row.depth is not None:
            depth = int(row.depth)
            bucket.min_depth = depth if bucket.min_depth is None else min(bucket.min_depth, depth)
        bucket.signals[row.cve_id] = FindingSignal(
            fixed_version=row.fixed_version,
            severity=str(row.severity),
            epss_score=float(row.epss_score) if row.epss_score is not None else None,
        )

    recs: dict[str, DryRunRecommendation] = {}
    for bucket in grouped.values():
        signals = list(bucket.signals.values())
        is_direct = bucket.direct or (bucket.min_depth is not None and bucket.min_depth == 1)
        rec = recommend_for_component(signals, direct=is_direct)
        if rec.recommended_version is None:
            continue  # only actionable upgrades drive a manifest edit.
        candidate = DryRunRecommendation(
            package=bucket.name,
            current_version=bucket.current_version,
            recommended_version=rec.recommended_version,
        )
        # If the same package name appears under two component_versions, keep the
        # higher recommended version (the one that fixes everything).
        existing = recs.get(bucket.name)
        if existing is None:
            recs[bucket.name] = candidate
        else:
            a = parse_version(candidate.recommended_version)
            b = parse_version(existing.recommended_version)
            if a is not None and (b is None or compare_versions(a, b) > 0):
                recs[bucket.name] = candidate
    return recs


# ---------------------------------------------------------------------------
# Manifest retrieval (preserved-source best-effort)
# ---------------------------------------------------------------------------


def _read_package_json_from_tarball(
    project_id: uuid.UUID, scan_id: uuid.UUID, *, max_bytes: int
) -> str | None:
    """Best-effort read of the repo-root ``package.json`` from the preserved tar.

    Returns the file text, or ``None`` if the tarball / member is absent or
    unreadable. NEVER raises into the caller (a degraded read is "no manifest",
    not a 500). Reuses the SAME UUID-only path the source-tree viewer uses, so
    there is no path-traversal surface (the member is looked up by exact name).
    """
    path = scan_source_tarball_path(project_id, scan_id)
    if not path.is_file():
        return None
    try:
        tar = tarfile.open(path, mode="r:gz")
    except (tarfile.TarError, OSError):
        log.warning(
            "remediation_tarball_unreadable",
            project_id=str(project_id),
            scan_id=str(scan_id),
        )
        return None
    try:
        target_member: tarfile.TarInfo | None = None
        # Prefer the shallowest package.json (repo root) over a nested one.
        best_depth = None
        scanned = 0
        for member in tar:
            scanned += 1
            if scanned > _MAX_TAR_MEMBERS_SCANNED:
                break
            if not member.isreg():
                continue
            # Normalise the arcname; we only accept an EXACT basename match so a
            # crafted member like ``evil/package.json.sh`` cannot pose as one.
            name = member.name.replace("\\", "/").lstrip("./")
            if name.rsplit("/", 1)[-1] != "package.json":
                continue
            depth = name.count("/")
            if best_depth is None or depth < best_depth:
                best_depth = depth
                target_member = member
                if depth == 0:
                    break  # repo-root package.json — cannot do better
        if target_member is None:
            return None
        if int(target_member.size) > max_bytes:
            log.warning(
                "remediation_manifest_oversized",
                project_id=str(project_id),
                scan_id=str(scan_id),
                byte_size=int(target_member.size),
            )
            return None
        extracted = tar.extractfile(target_member)
        if extracted is None:
            return None
        try:
            data = extracted.read(max_bytes + 1)
        finally:
            extracted.close()
        if len(data) > max_bytes:
            return None
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return None
    finally:
        tar.close()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def compute_npm_dry_run(
    session: AsyncSession,
    actor: CurrentUser,
    project_id: uuid.UUID,
    *,
    manifest_override: str | None = None,
) -> DryRunResult:
    """Compute the npm manifest-remediation dry-run for a project.

    Resolves the project (team-scoped, 404 existence-hide), derives the npm
    upgrade recommendations from the latest scan, obtains the manifest (override
    or preserved source), runs the adapter, and returns the proposed diff +
    per-package before/after + warnings.
    """
    project = await _resolve_accessible_project(session, project_id=project_id, actor=actor)
    scan_id = project.latest_scan_id

    notes: list[str] = []

    if scan_id is None:
        recommendations: dict[str, DryRunRecommendation] = {}
        notes.append("project has no completed scan; no recommendations available")
    else:
        recommendations = await _npm_recommendations_for_scan(session, scan_id=scan_id)

    # Obtain the manifest: override wins; else best-effort preserved source.
    from integrations.remediation.npm import npm_manifest_max_bytes

    max_bytes = npm_manifest_max_bytes()
    manifest_text: str | None = None
    manifest_source: ManifestSource = "none"
    if manifest_override is not None:
        manifest_text = manifest_override
        manifest_source = "override"
    elif scan_id is not None:
        manifest_text = await run_in_threadpool(
            _read_package_json_from_tarball,
            project_id,
            scan_id,
            max_bytes=max_bytes,
        )
        if manifest_text is not None:
            manifest_source = "preserved_source"

    sorted_recs = tuple(sorted(recommendations.values(), key=lambda r: r.package))

    if manifest_text is None:
        notes.append(
            "no package.json available (no upload and none preserved from the "
            "latest scan); supply one in the request body to preview the edit"
        )
        log.info(
            "remediation_dry_run_no_manifest",
            project_id=str(project_id),
            scan_id=str(scan_id) if scan_id else None,
            recommendation_count=len(sorted_recs),
        )
        return DryRunResult(
            project_id=project_id,
            scan_id=scan_id,
            ecosystem="npm",
            manifest_source="none",
            manifest_found=False,
            changed=False,
            edited_manifest=None,
            recommendations=sorted_recs,
            notes=tuple(notes),
        )

    bumps = [
        VersionBump(
            package=rec.package,
            target=rec.recommended_version,
            current=rec.current_version,
        )
        for rec in sorted_recs
    ]

    try:
        result = edit_npm_manifest(manifest_text, bumps)
    except ManifestParseError as exc:
        log.warning(
            "remediation_manifest_rejected",
            project_id=str(project_id),
            reason=exc.reason,
            source=manifest_source,
        )
        raise ManifestRejected(f"package.json could not be edited: {exc.detail}") from exc

    log.info(
        "remediation_dry_run_computed",
        project_id=str(project_id),
        scan_id=str(scan_id) if scan_id else None,
        source=manifest_source,
        recommendation_count=len(sorted_recs),
        changed=result.changed,
        change_count=len(result.changes),
        warning_count=len(result.warnings),
    )

    return DryRunResult(
        project_id=project_id,
        scan_id=scan_id,
        ecosystem="npm",
        manifest_source=manifest_source,
        manifest_found=True,
        changed=result.changed,
        edited_manifest=result.edited_text if result.changed else None,
        recommendations=sorted_recs,
        changes=result.changes,
        warnings=result.warnings,
        notes=tuple(notes),
    )


__all__ = [
    "DryRunRecommendation",
    "DryRunResult",
    "ManifestRejected",
    "ProjectNotAccessible",
    "RemediationError",
    "compute_npm_dry_run",
    "decode_npm_package_name",
]
