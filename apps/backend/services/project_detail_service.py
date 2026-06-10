"""
Project detail (Overview / Components) services — Phase 3 PR #10.

Three top-level entry points, each invoked from the matching router endpoint:

- :func:`get_project_overview`
- :func:`list_components_for_project`
- :func:`get_component_detail`

Why a new module?
-----------------
`services/project_service.py` already owns project CRUD; loading the latest
scan, joining vulnerability + license findings, and building distributions is
a different concern (read-only aggregation across multiple tables). Keeping
them apart keeps the CRUD module small and lets us evolve the read shape
without touching write paths.

Authorization
-------------
Every entry point re-uses the project-service guard
(`ProjectForbidden` if the actor is not a member of the owning team;
`ProjectNotFound` if the project / component is missing). super_admin
bypasses team membership exactly as elsewhere.

For component detail (`/v1/components/{id}`) we resolve the parent
component_version → scans → projects to locate the owning team. A component
that has *never* been seen by any scan we can read raises 404 (rather than
403) — leaking existence of unrelated components is undesirable.

Performance
-----------
- Overview emits 3 SQL statements: project lookup, distribution aggregation
  (single GROUP BY over scan_components ⨝ findings ⨝ licenses), recent scans.
- Component list emits 2 statements (count + items) executed concurrently
  via ``asyncio.gather`` so the round-trip cost is one RTT, not two.
- The aggregation queries always anchor on ``scan_components.scan_id =
  <latest succeeded scan>`` (resolved once per request via
  :func:`services.scan_resolution.latest_succeeded_scan_id` — NOT the
  ``project.latest_scan_id`` pointer, which tracks the last *attempted* scan and
  would otherwise blank the whole tab whenever the most recent attempt failed,
  contradicting the build gate). Existing index ``ix_scan_components_scan_id``
  covers the join; ``ix_scans_project_created_at`` covers the resolver.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import structlog
from sqlalchemy import String, case, cast, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.security import CurrentUser
from models import (
    Component,
    ComponentVersion,
    LicenseFinding,
    Membership,
    Obligation,
    Project,
    Scan,
    ScanComponent,
    Vulnerability,
    VulnerabilityFinding,
)
from models import (
    License as LicenseModel,
)
from services import risk_score
from services.project_service import (
    ProjectError,
    ProjectForbidden,
    ProjectNotFound,
)
from services.scan_resolution import resolve_snapshot_scan_id

log = structlog.get_logger("project_detail.service")


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class ComponentNotFound(ProjectError):
    status_code = 404
    title = "Component Not Found"


# ---------------------------------------------------------------------------
# Constants — severity / license ranking
# ---------------------------------------------------------------------------

# Higher rank = "worse". We pick the *highest* rank per component when
# multiple findings exist. The DB `vuln_severity` enum carries 'unknown' as a
# valid value, but the API normalises that to 'info' for display: a CVE we
# don't know the severity of should never be shown as a green ribbon.
_SEVERITY_RANK: dict[str, int] = {
    "none": 0,
    "info": 1,
    "unknown": 1,
    "low": 2,
    "medium": 3,
    "high": 4,
    "critical": 5,
}

_SEVERITY_FROM_RANK: dict[int, str] = {
    0: "none",
    1: "info",
    2: "low",
    3: "medium",
    4: "high",
    5: "critical",
}

_LICENSE_CATEGORY_RANK: dict[str, int] = {
    "unknown": 0,
    "allowed": 1,
    "conditional": 2,
    "forbidden": 3,
}

# W2 #31 — BD-style "Usage" rank for ``ScanComponent.dependency_scope``
# (populated from cdxgen SBOM ``component.scope``, a CycloneDX 1.6 field).
# Higher rank = "more required". The same component_version can appear at
# several paths with different scopes (diamond deps); we surface the
# *most-required* one, mirroring how depth picks the *shallowest* path —
# both report the dependency at its "strongest" claim on the project.
#
# Rank 0 is reserved for "no scope observed" (the column is NULL on every
# path). cdxgen leaves scope NULL for most ecosystems where the SBOM does
# not encode the field; we map that to ``dependency_scope=None`` in the
# response (UI label: "—").
_SCOPE_RANK: dict[str, int] = {
    "optional": 1,
    "required": 2,
}

_SCOPE_FROM_RANK: dict[int, str | None] = {
    0: None,
    1: "optional",
    2: "required",
}

# Accepted ``?dependency_scope=`` filter values. "unspecified" matches the
# (very common) NULL rows so callers can isolate the "scope unknown" bucket
# without conflating it with optional/required deps.
_SCOPE_FILTER_VALUES: set[str] = {"required", "optional", "unspecified"}
_SCOPE_FILTER_RANK: dict[str, int] = {
    "unspecified": 0,
    "optional": 1,
    "required": 2,
}

_LICENSE_CATEGORY_FROM_RANK: dict[int, str] = {
    0: "unknown",
    1: "allowed",
    2: "conditional",
    3: "forbidden",
}

# All component-severity keys returned in `severity_distribution`. We always
# emit each bucket (even with zero) so frontends can render a stable bar/donut.
_ALL_SEVERITY_KEYS = ("critical", "high", "medium", "low", "info", "none")
_ALL_LICENSE_KEYS = ("forbidden", "conditional", "allowed", "unknown")

# Risk scoring (security / license axes + overall) lives in `services.risk_score`
# — the single source of truth shared with release snapshots and project diff.

# Component list pagination + sort caps.
_LIST_LIMIT_DEFAULT = 50
_LIST_LIMIT_MAX = 500
_VALID_SORT_KEYS = frozenset({"name", "severity", "license"})
_VALID_ORDER = frozenset({"asc", "desc"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


from core.authz import assert_team_access  # noqa: E402

# All cross-team guards in this module flow through `assert_team_access`
# (chore PR #5) so the `authz.cross_team_attempt` log shape is centralized.


async def _load_project(session: AsyncSession, project_id: uuid.UUID) -> Project:
    """Project lookup that surfaces ProjectNotFound (404) on miss."""
    result = await session.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if project is None:
        raise ProjectNotFound(f"project {project_id} not found")
    return project


async def _resolve_team_scoped_role(
    session: AsyncSession,
    *,
    actor: CurrentUser,
    team_id: uuid.UUID,
) -> str:
    """The actor's effective role *within the project's owning team* (BUG-005).

    The global ``CurrentUser.role`` / JWT role only distinguishes super_admin
    from "everyone else" — a membership-based ``team_admin`` is invisible to
    the frontend, which then wrongly disables team-scoped actions such as
    vulnerability suppression. The frontend needs the per-team role, so we
    resolve it here:

    - super-users are ``super_admin`` (they bypass team membership everywhere);
    - otherwise we read the actor's membership row for *this* team and return
      its role (``team_admin`` / ``developer``);
    - a reader who reaches the project via org-wide visibility but holds no
      membership defaults to the least-privileged ``developer`` (fail-closed).

    We query ``memberships`` directly (team_id + user_id, both covered by an
    index) rather than trusting the JWT-derived ``actor.team_roles`` so the
    value is authoritative even if the token predates a membership change.
    """
    if actor.is_superuser:
        return "super_admin"

    role = await session.scalar(
        select(Membership.role).where(
            (Membership.team_id == team_id) & (Membership.user_id == actor.id)
        )
    )
    # No membership row → org-wide reader. Fail closed to the minimum role.
    return role or "developer"


def _severity_rank_case() -> Any:
    """SQLAlchemy CASE that maps a vuln_severity ENUM value to its rank int.

    Note: Postgres ENUM ↔ varchar comparison requires explicit cast — without
    it asyncpg fails with `operator does not exist: vuln_severity = character
    varying`. Casting the column to text on the LHS lets the dict-key string
    literals compare cleanly.
    """
    return case(
        {
            literal("critical"): 5,
            literal("high"): 4,
            literal("medium"): 3,
            literal("low"): 2,
            literal("info"): 1,
            literal("unknown"): 1,
        },
        value=cast(Vulnerability.severity, String),
        else_=0,
    )


def _license_rank_case() -> Any:
    """SQLAlchemy CASE that maps a license_category ENUM value to its rank int.

    Same enum-cast rationale as `_severity_rank_case()` above.
    """
    return case(
        {
            literal("forbidden"): 3,
            literal("conditional"): 2,
            literal("allowed"): 1,
        },
        value=cast(LicenseModel.category, String),
        else_=0,
    )


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------


async def get_project_overview(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    actor: CurrentUser,
    scan_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """
    Aggregate the Overview tab payload for ``project_id``.

    Returns a dict matching :class:`schemas.project_detail.ProjectOverviewResponse`.

    Raises :class:`ProjectNotFound` (404) if the project does not exist and
    :class:`ProjectForbidden` (403) if the caller is not on the owning team.

    ``scan_id`` (feature #28) optionally pins the aggregation to a SPECIFIC
    succeeded snapshot instead of the latest succeeded scan. When provided it is
    validated by :func:`services.scan_resolution.resolve_snapshot_scan_id`
    (must belong to THIS project and be ``status='succeeded'``); an invalid /
    cross-project / non-succeeded id raises :class:`SnapshotScanNotFound`, which
    the router maps to a 404 (existence-hide). Omitting it preserves the
    unchanged latest-succeeded default.
    """
    project = await _load_project(session, project_id)
    assert_team_access(
        actor,
        project.team_id,
        log=log,
        resource="project_overview",
        resource_id=str(project_id),
        deny=lambda: ProjectForbidden(
            f"actor is not a member of team {project.team_id}",
        ),
    )

    severity_distribution = dict.fromkeys(_ALL_SEVERITY_KEYS, 0)
    license_distribution = dict.fromkeys(_ALL_LICENSE_KEYS, 0)
    total_components = 0
    last_scan_at: Any = None
    last_succeeded_scan_at: Any = None
    # #35 Surface B — tri-state: True/False once we know the DT vuln-DB size at
    # scan time, None when unknown (no succeeded scan, or a scan predating the
    # capture). None means "no caveat" — we never cry wolf on missing data.
    vuln_data_available: bool | None = None
    recent: list[Scan] = []

    # Anchor the current-state aggregation on the resolved snapshot scan: the
    # pinned ``scan_id`` when given (feature #28), else the latest SUCCEEDED scan
    # — NOT ``project.latest_scan_id`` (the last *attempted* scan). Otherwise a
    # project whose newest attempt failed shows "NO RISK / 0 components" while
    # the build gate (which already uses the succeeded scan) reads "blocked". See
    # ``services.scan_resolution`` for the full rationale.
    aggregate_scan_id = await resolve_snapshot_scan_id(session, project_id, scan_id)

    # Recent scans are PROJECT-wide (filtered only by project_id) and must NOT be
    # gated on a resolved snapshot: a project whose only scan is still
    # queued/running — or whose every attempt failed — has no succeeded snapshot
    # (``aggregate_scan_id is None``) yet absolutely has scans to track. Gating
    # this on the snapshot blanked the recent-scans table for a freshly-triggered
    # first scan, stranding the user with no way to re-open the live progress
    # drawer (#29). So the list always runs; only the distribution aggregation
    # below depends on a succeeded snapshot existing.
    recent_stmt = (
        select(Scan)
        .where(Scan.project_id == project_id)
        .order_by(Scan.created_at.desc(), Scan.id.desc())
        .limit(5)
    )

    if aggregate_scan_id is not None:
        # Per-component-version aggregation. We aggregate inside one CTE
        # rather than emitting two GROUP BYs over scan_components, because
        # the same (cv) row can have several findings (multiple CVEs, multi-
        # license declarations). For the distribution we collapse to the
        # *worst* finding per cv by taking MAX(rank).
        sev_rank = _severity_rank_case()
        lic_rank = _license_rank_case()

        per_cv_subq = (
            select(
                ScanComponent.component_version_id.label("cv_id"),
                func.coalesce(func.max(sev_rank), 0).label("max_sev_rank"),
                func.coalesce(func.max(lic_rank), 0).label("max_lic_rank"),
            )
            .select_from(ScanComponent)
            .outerjoin(
                VulnerabilityFinding,
                (VulnerabilityFinding.scan_id == ScanComponent.scan_id)
                & (
                    VulnerabilityFinding.component_version_id
                    == ScanComponent.component_version_id
                ),
            )
            .outerjoin(
                Vulnerability,
                Vulnerability.id == VulnerabilityFinding.vulnerability_id,
            )
            .outerjoin(
                LicenseFinding,
                (LicenseFinding.scan_id == ScanComponent.scan_id)
                & (
                    LicenseFinding.component_version_id
                    == ScanComponent.component_version_id
                ),
            )
            .outerjoin(
                LicenseModel,
                LicenseModel.id == LicenseFinding.license_id,
            )
            .where(ScanComponent.scan_id == aggregate_scan_id)
            .group_by(ScanComponent.component_version_id)
            .subquery()
        )

        agg_stmt = select(
            per_cv_subq.c.max_sev_rank,
            per_cv_subq.c.max_lic_rank,
            func.count().label("n"),
        ).group_by(per_cv_subq.c.max_sev_rank, per_cv_subq.c.max_lic_rank)

        # Run aggregation + recent-scans concurrently. We deliberately gather
        # to keep the overview within p95 < 200ms (DoD §3.1).

        # #25 / SBOM-label fix — the latest *succeeded* scan's created_at. The
        # SBOM tab labels its download with this (not last_scan_at, the last
        # *attempt*) so the timestamp matches what sbom_export actually exports.
        # The succeeded scan may be OLDER than the 5 most-recent attempts in
        # `recent`, so we resolve its created_at directly rather than reading it
        # off `recent[0]`.
        # Also read the anchor scan's metadata: it carries the DT vulnerability-DB
        # size captured AT scan time (#35 Surface B). A succeeded scan that ran
        # while the DB was empty produces 0 CVEs that mean "no data", not "safe" —
        # the overview surfaces this so a developer doesn't read an empty Security
        # axis as a clean bill of health.
        succeeded_meta_stmt = select(Scan.created_at, Scan.scan_metadata).where(
            Scan.id == aggregate_scan_id
        )

        agg_result, recent_result, succeeded_meta_result = await asyncio.gather(
            session.execute(agg_stmt),
            session.execute(recent_stmt),
            session.execute(succeeded_meta_stmt),
        )

        succeeded_row = succeeded_meta_result.one_or_none()
        if succeeded_row is not None:
            last_succeeded_scan_at = succeeded_row.created_at
            dt_vuln_count = (succeeded_row.scan_metadata or {}).get(
                "dt_vulnerability_count"
            )
            # Absent key (scan predates the capture) → leave None (no caveat).
            if dt_vuln_count is not None:
                vuln_data_available = int(dt_vuln_count) > 0

        for row in agg_result.all():
            sev_key = _SEVERITY_FROM_RANK.get(int(row.max_sev_rank), "none")
            lic_key = _LICENSE_CATEGORY_FROM_RANK.get(int(row.max_lic_rank), "unknown")
            count = int(row.n)
            severity_distribution[sev_key] = severity_distribution.get(sev_key, 0) + count
            license_distribution[lic_key] = license_distribution.get(lic_key, 0) + count
            total_components += count

        recent = list(recent_result.scalars().all())
    else:
        # No succeeded snapshot yet — distributions stay zeroed (nothing to
        # aggregate), but the project may still have queued/running/failed
        # attempts. Always surface them so the user can track / re-open an
        # in-flight scan (#29). ``last_succeeded_scan_at`` stays None.
        recent_result = await session.execute(recent_stmt)
        recent = list(recent_result.scalars().all())

    if recent:
        last_scan_at = recent[0].created_at

    security_score = risk_score.security_score(severity_distribution)
    license_score = risk_score.license_score(license_distribution)
    overall_risk = risk_score.overall_risk_score(security_score, license_score)

    current_user_role = await _resolve_team_scoped_role(
        session, actor=actor, team_id=project.team_id
    )

    return {
        "project_id": project.id,
        "project_name": project.name,
        "total_components": total_components,
        "severity_distribution": severity_distribution,
        "license_distribution": license_distribution,
        "risk_score": overall_risk,
        "security_score": security_score,
        "license_score": license_score,
        "recent_scans": recent,
        "last_scan_at": last_scan_at,
        "last_succeeded_scan_at": last_succeeded_scan_at,
        "vuln_data_available": vuln_data_available,
        # Feature #18 Part B — read-only "credential configured?" flag. Never the
        # plaintext / ciphertext, only the boolean derived from the model property.
        "has_git_credential": project.has_git_credential,
        "current_user_role": current_user_role,
    }


# ---------------------------------------------------------------------------
# Component list
# ---------------------------------------------------------------------------


def _normalize_severity_filter(
    raw: list[str] | None,
) -> list[str] | None:
    if raw is None:
        return None
    cleaned = [s for s in raw if s in _SEVERITY_RANK]
    if not cleaned:
        # Caller passed only invalid values — return an empty list to signal
        # "no rows match" without raising a 422. Validation lives in the
        # router layer (Pydantic Query enum) for the API surface.
        return []
    return cleaned


def _normalize_license_filter(
    raw: list[str] | None,
) -> list[str] | None:
    if raw is None:
        return None
    cleaned = [c for c in raw if c in _LICENSE_CATEGORY_RANK]
    if not cleaned:
        return []
    return cleaned


def _normalize_scope_filter(
    raw: list[str] | None,
) -> list[str] | None:
    """W2 #31 — accept ``required``/``optional``/``unspecified``, drop others.

    Returns ``None`` (no filter), ``[]`` (caller passed only invalid values →
    "no rows match" without a 422), or the cleaned subset. Validation lives
    in the router for the API surface; this guards the service contract.
    """
    if raw is None:
        return None
    cleaned = [s for s in raw if s in _SCOPE_FILTER_VALUES]
    if not cleaned:
        return []
    return cleaned


async def list_components_for_project(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    actor: CurrentUser,
    limit: int = _LIST_LIMIT_DEFAULT,
    offset: int = 0,
    search: str | None = None,
    severity: list[str] | None = None,
    license_category: list[str] | None = None,
    direct: bool | None = None,
    dependency_scope: list[str] | None = None,
    sort: str = "name",
    order: str = "asc",
    scan_id: uuid.UUID | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """
    Page of components for the project's latest scan.

    Returns ``(items, total)`` where each item is a plain dict shaped to
    :class:`schemas.project_detail.ComponentSummary`. We return dicts (not
    ORM rows) because the row is synthesized from a JOIN + per-cv aggregates
    that don't fit cleanly onto a single ORM mapping.

    Pagination is offset-based for Phase 3 (DoD: 1万 row cap is comfortable
    for OFFSET). Phase 3+ may swap to keyset; the response shape would not
    change because frontends consume ``items`` opaquely.
    """
    if sort not in _VALID_SORT_KEYS:
        raise ProjectError(f"unsupported sort key: {sort!r}")
    if order not in _VALID_ORDER:
        raise ProjectError(f"unsupported order: {order!r}")

    limit = max(min(int(limit), _LIST_LIMIT_MAX), 1)
    offset = max(int(offset), 0)

    project = await _load_project(session, project_id)
    assert_team_access(
        actor,
        project.team_id,
        log=log,
        resource="project_components",
        resource_id=str(project_id),
        deny=lambda: ProjectForbidden(
            f"actor is not a member of team {project.team_id}",
        ),
    )

    # Anchor on the resolved snapshot scan (see ``services.scan_resolution``):
    # the pinned ``scan_id`` when given (feature #28), else the latest SUCCEEDED
    # scan — the same scan the build gate / overview use, not the last attempted
    # scan (which may have failed and carry no rows). An invalid pinned id raises
    # SnapshotScanNotFound → 404 at the router.
    aggregate_scan_id = await resolve_snapshot_scan_id(session, project_id, scan_id)
    if aggregate_scan_id is None:
        return [], 0

    sev_rank = _severity_rank_case()
    lic_rank = _license_rank_case()

    # CTE — one row per (component_version) in the latest scan with the
    # worst severity / license rank and the count of vuln findings + the
    # license name to display.
    per_cv_subq = (
        select(
            ScanComponent.component_version_id.label("cv_id"),
            func.coalesce(func.max(sev_rank), 0).label("max_sev_rank"),
            func.coalesce(func.max(lic_rank), 0).label("max_lic_rank"),
            func.count(VulnerabilityFinding.id).label("vuln_count"),
            # v2.2 2.2-a2 — the same cv can appear at several dependency paths
            # (diamond deps, monorepos), each a ScanComponent row with its own
            # depth. We surface the SHALLOWEST path (MIN) — the most "direct"
            # way the project reaches this component — and OR the direct flags.
            # MIN over a set that includes NULLs ignores the NULLs, so a cv with
            # at least one graph-derived depth reports that; a cv with only
            # NULL depths reports NULL ("graph not available").
            func.min(ScanComponent.depth).label("min_depth"),
            func.bool_or(ScanComponent.direct).label("is_direct"),
            # W2 #31 — pick the *most-required* scope across this cv's paths
            # (required > optional). Rank 0 means every path had NULL scope,
            # which maps back to ``dependency_scope=None`` in the response.
            # CASE is portable; ``case`` is already imported.
            func.coalesce(
                func.max(
                    case(
                        (ScanComponent.dependency_scope == "required", 2),
                        (ScanComponent.dependency_scope == "optional", 1),
                        else_=0,
                    )
                ),
                0,
            ).label("max_scope_rank"),
        )
        .select_from(ScanComponent)
        .outerjoin(
            VulnerabilityFinding,
            (VulnerabilityFinding.scan_id == ScanComponent.scan_id)
            & (
                VulnerabilityFinding.component_version_id
                == ScanComponent.component_version_id
            ),
        )
        .outerjoin(
            Vulnerability,
            Vulnerability.id == VulnerabilityFinding.vulnerability_id,
        )
        .outerjoin(
            LicenseFinding,
            (LicenseFinding.scan_id == ScanComponent.scan_id)
            & (
                LicenseFinding.component_version_id
                == ScanComponent.component_version_id
            ),
        )
        .outerjoin(
            LicenseModel,
            LicenseModel.id == LicenseFinding.license_id,
        )
        .where(ScanComponent.scan_id == aggregate_scan_id)
        .group_by(ScanComponent.component_version_id)
        .subquery()
    )

    # Main statement: join per_cv → component_versions → components and pick a
    # representative license string for the row. We pick the license whose
    # category matches the worst rank (deterministic-ish: GROUP BY collapses
    # to one row per cv anyway, then the outer JOIN picks one). For Phase 3
    # we accept the first matching license name; UI shows full list in drawer.
    base = (
        select(
            ComponentVersion.id.label("cv_id"),
            ComponentVersion.version.label("version"),
            ComponentVersion.purl_with_version.label("purl"),
            Component.id.label("component_id"),
            Component.name.label("name"),
            per_cv_subq.c.max_sev_rank.label("max_sev_rank"),
            per_cv_subq.c.max_lic_rank.label("max_lic_rank"),
            per_cv_subq.c.vuln_count.label("vuln_count"),
            per_cv_subq.c.min_depth.label("min_depth"),
            per_cv_subq.c.is_direct.label("is_direct"),
            per_cv_subq.c.max_scope_rank.label("max_scope_rank"),
        )
        .select_from(per_cv_subq)
        .join(ComponentVersion, ComponentVersion.id == per_cv_subq.c.cv_id)
        .join(Component, Component.id == ComponentVersion.component_id)
    )

    # Search: ILIKE on component name OR namespace — cheap because of
    # ix_components_type_name (covers prefix); for substring we accept a
    # full scan over the per-scan working set.
    if search:
        like = f"%{search.strip()}%"
        base = base.where(or_(Component.name.ilike(like), Component.namespace.ilike(like)))

    # Severity filter (rank-based so we don't string-compare ENUM names).
    severity_filter = _normalize_severity_filter(severity)
    if severity_filter is not None:
        if not severity_filter:
            return [], 0
        ranks = [_SEVERITY_RANK[s] for s in severity_filter]
        base = base.where(per_cv_subq.c.max_sev_rank.in_(ranks))

    license_filter = _normalize_license_filter(license_category)
    if license_filter is not None:
        if not license_filter:
            return [], 0
        ranks = [_LICENSE_CATEGORY_RANK[c] for c in license_filter]
        base = base.where(per_cv_subq.c.max_lic_rank.in_(ranks))

    # W2 #31 — Direct/Transitive toggle. ``direct=True`` keeps only graph-
    # roots (depth==1); ``direct=False`` keeps every non-root, including the
    # cvs whose scan carried no graph (``is_direct`` defaults False there).
    # Skipped entirely when the caller did not pass the param.
    if direct is not None:
        base = base.where(per_cv_subq.c.is_direct.is_(direct))

    # W2 #31 — BD-style "Usage" filter. The cv-level rank is compared to the
    # caller's requested ranks; an "unspecified" request matches the (very
    # common) NULL-scope bucket without conflating it with optional/required.
    scope_filter = _normalize_scope_filter(dependency_scope)
    if scope_filter is not None:
        if not scope_filter:
            return [], 0
        scope_ranks = [_SCOPE_FILTER_RANK[s] for s in scope_filter]
        base = base.where(per_cv_subq.c.max_scope_rank.in_(scope_ranks))

    # Sorting. We pick the primary column then call .asc()/.desc() inline so
    # mypy --strict doesn't complain about an untyped lambda factory.
    primary: Any
    if sort == "name":
        primary = Component.name
    elif sort == "severity":
        primary = per_cv_subq.c.max_sev_rank
    else:  # sort == "license"
        primary = per_cv_subq.c.max_lic_rank
    primary_clause = primary.desc() if order == "desc" else primary.asc()

    if sort == "name":
        # Name is not unique; tiebreak by version + cv_id so pagination is
        # stable across pages. The cv_id guarantees a strict total order.
        order_clauses = [primary_clause, ComponentVersion.version, ComponentVersion.id]
    else:
        order_clauses = [primary_clause, Component.name, ComponentVersion.id]

    items_stmt = base.order_by(*order_clauses).limit(limit).offset(offset)

    # Count uses the same WHERE/JOIN graph; SQLAlchemy 2.0 lets us wrap the
    # statement and count over its rows.
    count_stmt = select(func.count()).select_from(base.subquery())

    items_result, count_result = await asyncio.gather(
        session.execute(items_stmt),
        session.execute(count_stmt),
    )

    total = int(count_result.scalar_one())

    # Build a per-cv license display string in a single follow-up query —
    # cheap because we already have the page's cv_ids.
    rows = list(items_result.all())
    cv_ids = [r.cv_id for r in rows]
    license_display: dict[uuid.UUID, str | None] = {cid: None for cid in cv_ids}
    if cv_ids:
        # Pick the highest-ranked license name per cv (matches what determined
        # license_category). Rank ties resolve deterministically by spdx_id.
        lic_stmt = (
            select(
                LicenseFinding.component_version_id.label("cv_id"),
                LicenseModel.spdx_id.label("spdx_id"),
                LicenseModel.name.label("name"),
                _license_rank_case().label("rank"),
            )
            .join(LicenseModel, LicenseModel.id == LicenseFinding.license_id)
            .where(
                (LicenseFinding.scan_id == aggregate_scan_id)
                & LicenseFinding.component_version_id.in_(cv_ids)
            )
        )
        lic_result = await session.execute(lic_stmt)
        # Bucket by cv_id, keeping the highest-ranked license seen.
        best: dict[uuid.UUID, tuple[int, str]] = {}
        for r in lic_result.all():
            current = best.get(r.cv_id)
            display = r.spdx_id or r.name
            if current is None or r.rank > current[0]:
                best[r.cv_id] = (r.rank, display)
        license_display.update({cv: name for cv, (_, name) in best.items()})

    items: list[dict[str, Any]] = []
    for r in rows:
        items.append(
            {
                "id": r.cv_id,
                "component_id": r.component_id,
                "name": r.name,
                "version": r.version,
                "purl": r.purl,
                "license": license_display.get(r.cv_id),
                "license_category": _LICENSE_CATEGORY_FROM_RANK.get(
                    int(r.max_lic_rank), "unknown"
                ),
                "severity_max": _SEVERITY_FROM_RANK.get(int(r.max_sev_rank), "none"),
                "vulnerability_count": int(r.vuln_count),
                # v2.2 2.2-a2 — graph depth (1 = direct, 2+ = transitive) and a
                # convenience ``direct`` flag. NULL depth when the scan carried
                # no dependency graph; ``direct`` is then False (we never claim
                # directness we cannot prove).
                "depth": int(r.min_depth) if r.min_depth is not None else None,
                "direct": bool(r.is_direct),
                # W2 #31 — BD-style "Usage" mapped back from the per-cv rank.
                # ``None`` (rank 0) means every path had NULL scope (the cdxgen
                # SBOM didn't encode one); the UI renders that as "—" rather
                # than guessing.
                "dependency_scope": _SCOPE_FROM_RANK[int(r.max_scope_rank)],
            }
        )

    return items, total


# ---------------------------------------------------------------------------
# Component detail
# ---------------------------------------------------------------------------


async def get_component_detail(
    session: AsyncSession,
    *,
    component_version_id: uuid.UUID,
    actor: CurrentUser,
) -> dict[str, Any]:
    """
    Return the drawer payload for a single component_version.

    The component_version is anchored on the *latest scan* of any project
    where it appears that the actor can access. If no such scan exists (the
    component only appears in projects the actor cannot read), we raise
    :class:`ComponentNotFound` (404) so we don't leak existence.
    """
    # Find a scan_components row for this cv inside a project the actor can
    # see. We pick the most recent SUCCEEDED scan where the cv was observed;
    # that gives us the user's "current view" of the component — consistent with
    # the overview / components-list / vuln-list, which all anchor on the latest
    # succeeded scan rather than ``project.latest_scan_id`` (the last *attempted*
    # scan, which may have failed). See ``services.scan_resolution``.
    cv_stmt = (
        select(
            ComponentVersion.id,
            ComponentVersion.version,
            ComponentVersion.purl_with_version,
            ComponentVersion.created_at,
            ComponentVersion.updated_at,
            Component.id.label("component_id"),
            Component.name.label("component_name"),
            Project.id.label("project_id"),
            Project.team_id.label("team_id"),
            Scan.id.label("scan_id"),
            ScanComponent.raw_data.label("raw_data"),
            ScanComponent.depth.label("depth"),
            ScanComponent.direct.label("direct"),
            # W2 #31 — Usage at the chosen (shallowest) path. The drawer
            # surfaces the row's own scope, not an aggregate across paths,
            # because the depth/direct fields already pin one path. ``None``
            # when cdxgen left the field unset on this row.
            ScanComponent.dependency_scope.label("dependency_scope"),
        )
        .select_from(ComponentVersion)
        .join(Component, Component.id == ComponentVersion.component_id)
        .join(ScanComponent, ScanComponent.component_version_id == ComponentVersion.id)
        .join(Scan, Scan.id == ScanComponent.scan_id)
        .join(Project, Project.id == Scan.project_id)
        .where(ComponentVersion.id == component_version_id)
        # Anchor on SUCCEEDED scans only (not ``project.latest_scan_id``); the
        # most recent one containing this cv is the project's current view of it.
        .where(cast(Scan.status, String) == "succeeded")
        # Most recent (succeeded) scan first; within that, the SHALLOWEST path
        # (v2.2 2.2-a2) — ``depth ASC NULLS LAST`` — so the drawer reports the
        # most-direct way the project reaches this component when the same cv
        # sits at several dependency paths in one scan (diamond deps). The
        # ``scan_id`` tiebreak keeps selection deterministic across two scans
        # sharing a ``created_at``.
        .order_by(
            Scan.created_at.desc(),
            Scan.id.desc(),
            ScanComponent.depth.asc().nulls_last(),
        )
        .limit(1)
    )
    cv_result = await session.execute(cv_stmt)
    row = cv_result.first()
    if row is None:
        raise ComponentNotFound(f"component {component_version_id} not found")

    # Hide existence: 404 rather than 403. Components are global rows;
    # leaking that one exists across teams is undesirable.
    assert_team_access(
        actor,
        row.team_id,
        log=log,
        resource="component_detail",
        resource_id=str(component_version_id),
        deny=lambda: ComponentNotFound(
            f"component {component_version_id} not found"
        ),
    )

    # Worst severity + worst license category for the cv inside this scan.
    sev_rank_stmt = (
        select(func.coalesce(func.max(_severity_rank_case()), 0))
        .select_from(VulnerabilityFinding)
        .join(Vulnerability, Vulnerability.id == VulnerabilityFinding.vulnerability_id)
        .where(VulnerabilityFinding.scan_id == row.scan_id)
        .where(VulnerabilityFinding.component_version_id == component_version_id)
    )
    lic_rank_stmt = (
        select(func.coalesce(func.max(_license_rank_case()), 0))
        .select_from(LicenseFinding)
        .join(LicenseModel, LicenseModel.id == LicenseFinding.license_id)
        .where(LicenseFinding.scan_id == row.scan_id)
        .where(LicenseFinding.component_version_id == component_version_id)
    )

    # Vulnerability list — de-duplicated by external_id; many findings can
    # reference the same CVE if multiple paths hit the same component.
    # ``fixed_version`` comes from the per-finding column (v2.2 2.2-a1): this
    # component_version is fixed, so each CVE row carries the fix version that
    # remediates THIS package for THAT CVE. NULL when the pipeline found none.
    vulns_stmt = (
        select(
            Vulnerability.external_id,
            Vulnerability.severity,
            Vulnerability.cvss_score,
            Vulnerability.epss_score,
            Vulnerability.epss_percentile,
            Vulnerability.summary,
            Vulnerability.details,
            VulnerabilityFinding.fixed_version.label("fixed_version"),
        )
        .join(
            VulnerabilityFinding,
            VulnerabilityFinding.vulnerability_id == Vulnerability.id,
        )
        .where(VulnerabilityFinding.scan_id == row.scan_id)
        .where(VulnerabilityFinding.component_version_id == component_version_id)
        .order_by(Vulnerability.severity.desc(), Vulnerability.external_id.asc())
    )

    # License row to display.
    lic_pick_stmt = (
        select(LicenseModel.spdx_id, LicenseModel.name, _license_rank_case().label("rank"))
        .join(LicenseFinding, LicenseFinding.license_id == LicenseModel.id)
        .where(LicenseFinding.scan_id == row.scan_id)
        .where(LicenseFinding.component_version_id == component_version_id)
    )

    # M-20 — obligations carried by *every* license observed for this cv in
    # the anchoring scan (not just the displayed "best" one): a dual-licensed
    # component owes the duties of each license it ships under. One IN-subquery
    # statement total (no per-license round-trips); the subquery naturally
    # de-duplicates repeat findings of the same license (multiple kinds /
    # source paths). Empty result when the cv has no license findings or the
    # catalog defines no obligations — never an error. Ordering is pinned to
    # (kind, license, id) so the drawer payload is deterministic.
    obligations_stmt = (
        select(
            Obligation.id,
            Obligation.kind,
            Obligation.text,
            Obligation.link,
            LicenseModel.spdx_id,
            LicenseModel.name,
        )
        .join(LicenseModel, LicenseModel.id == Obligation.license_id)
        .where(
            Obligation.license_id.in_(
                select(LicenseFinding.license_id)
                .where(LicenseFinding.scan_id == row.scan_id)
                .where(LicenseFinding.component_version_id == component_version_id)
            )
        )
        .order_by(
            Obligation.kind.asc(),
            LicenseModel.spdx_id.asc().nulls_last(),
            Obligation.id.asc(),
        )
    )

    sev_res, lic_res, vulns_res, lic_pick_res, obligations_res = await asyncio.gather(
        session.execute(sev_rank_stmt),
        session.execute(lic_rank_stmt),
        session.execute(vulns_stmt),
        session.execute(lic_pick_stmt),
        session.execute(obligations_stmt),
    )

    sev_rank_val = int(sev_res.scalar_one() or 0)
    lic_rank_val = int(lic_res.scalar_one() or 0)

    # Best license display for the cv (mirrors the list endpoint logic).
    best: tuple[int, str] | None = None
    for lr in lic_pick_res.all():
        display = lr.spdx_id or lr.name
        if best is None or lr.rank > best[0]:
            best = (lr.rank, display)
    license_display = best[1] if best else None

    # M-20 — obligation refs, already (kind, license, id)-ordered by the
    # query. ``license`` mirrors the display convention used everywhere else
    # in this module: SPDX id with a name fallback for LicenseRef customs.
    obligations: list[dict[str, Any]] = [
        {
            "id": ob.id,
            "kind": ob.kind,
            "text": ob.text,
            "link": ob.link,
            "license": ob.spdx_id or ob.name,
        }
        for ob in obligations_res.all()
    ]

    # Deduplicate CVEs.
    seen_cves: set[str] = set()
    vulns: list[dict[str, Any]] = []
    for vr in vulns_res.all():
        if vr.external_id in seen_cves:
            continue
        seen_cves.add(vr.external_id)
        vulns.append(
            {
                "cve_id": vr.external_id,
                "severity": vr.severity,
                "cvss": float(vr.cvss_score) if vr.cvss_score is not None else None,
                "epss_score": float(vr.epss_score) if vr.epss_score is not None else None,
                "epss_percentile": (
                    float(vr.epss_percentile) if vr.epss_percentile is not None else None
                ),
                "title": vr.summary or vr.external_id,
                "description": vr.details,
                "fixed_version": vr.fixed_version,
            }
        )

    return {
        "id": row.id,
        "project_id": row.project_id,
        "name": row.component_name,
        "version": row.version,
        "purl": row.purl_with_version,
        "license": license_display,
        "license_category": _LICENSE_CATEGORY_FROM_RANK.get(lic_rank_val, "unknown"),
        "severity_max": _SEVERITY_FROM_RANK.get(sev_rank_val, "none"),
        "vulnerabilities": vulns,
        # M-20 — duties carried by the component's license(s); see
        # ``obligations_stmt`` above for sourcing/ordering guarantees.
        "obligations": obligations,
        "raw_data": dict(row.raw_data or {}),
        # v2.2 2.2-a2 — graph depth + direct flag for the chosen (shallowest)
        # path. NULL depth when the scan carried no dependency graph.
        "depth": int(row.depth) if row.depth is not None else None,
        "direct": bool(row.direct),
        # W2 #31 — BD-style "Usage" for the chosen path. Only ``required`` /
        # ``optional`` / ``None`` reach the response: ``ScanComponent`` rows
        # written by cdxgen carry exactly the CycloneDX value (no normalisation),
        # so any other string would be a data bug we want to surface as "—".
        "dependency_scope": (
            row.dependency_scope
            if row.dependency_scope in ("required", "optional")
            else None
        ),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


__all__ = [
    "ComponentNotFound",
    "get_component_detail",
    "get_project_overview",
    "list_components_for_project",
]
