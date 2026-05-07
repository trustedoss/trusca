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
  project.latest_scan_id``; existing index ``ix_scan_components_scan_id``
  covers the join.
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
    Project,
    Scan,
    ScanComponent,
    Vulnerability,
    VulnerabilityFinding,
)
from models import (
    License as LicenseModel,
)
from services.project_service import (
    ProjectError,
    ProjectForbidden,
    ProjectNotFound,
)

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

# Risk-score weights. Tunable via the formula in the schema docstring.
_RISK_WEIGHTS_SEVERITY = {"critical": 15, "high": 5, "medium": 1, "low": 0, "info": 0, "none": 0}
_RISK_WEIGHTS_LICENSE = {"forbidden": 30, "conditional": 5, "allowed": 0, "unknown": 0}
_RISK_SCORE_CAP = 100.0

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


def _compute_risk_score(
    severity_distribution: dict[str, int],
    license_distribution: dict[str, int],
) -> float:
    """Weighted sum capped at 100. See module docstring for the formula."""
    score = 0.0
    for key, weight in _RISK_WEIGHTS_SEVERITY.items():
        score += severity_distribution.get(key, 0) * weight
    for key, weight in _RISK_WEIGHTS_LICENSE.items():
        score += license_distribution.get(key, 0) * weight
    return min(_RISK_SCORE_CAP, float(score))


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
) -> dict[str, Any]:
    """
    Aggregate the Overview tab payload for ``project_id``.

    Returns a dict matching :class:`schemas.project_detail.ProjectOverviewResponse`.

    Raises :class:`ProjectNotFound` (404) if the project does not exist and
    :class:`ProjectForbidden` (403) if the caller is not on the owning team.
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
    recent: list[Scan] = []

    if project.latest_scan_id is not None:
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
            .where(ScanComponent.scan_id == project.latest_scan_id)
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
        recent_stmt = (
            select(Scan)
            .where(Scan.project_id == project_id)
            .order_by(Scan.created_at.desc(), Scan.id.desc())
            .limit(5)
        )

        agg_result, recent_result = await asyncio.gather(
            session.execute(agg_stmt),
            session.execute(recent_stmt),
        )

        for row in agg_result.all():
            sev_key = _SEVERITY_FROM_RANK.get(int(row.max_sev_rank), "none")
            lic_key = _LICENSE_CATEGORY_FROM_RANK.get(int(row.max_lic_rank), "unknown")
            count = int(row.n)
            severity_distribution[sev_key] = severity_distribution.get(sev_key, 0) + count
            license_distribution[lic_key] = license_distribution.get(lic_key, 0) + count
            total_components += count

        recent = list(recent_result.scalars().all())
        if recent:
            last_scan_at = recent[0].created_at

    risk_score = _compute_risk_score(severity_distribution, license_distribution)

    return {
        "project_id": project.id,
        "project_name": project.name,
        "total_components": total_components,
        "severity_distribution": severity_distribution,
        "license_distribution": license_distribution,
        "risk_score": risk_score,
        "recent_scans": recent,
        "last_scan_at": last_scan_at,
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
    sort: str = "name",
    order: str = "asc",
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

    if project.latest_scan_id is None:
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
        .where(ScanComponent.scan_id == project.latest_scan_id)
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
                (LicenseFinding.scan_id == project.latest_scan_id)
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
    # see. We pick the most recent project.latest_scan_id where the cv was
    # observed; that gives us the user's "current view" of the component.
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
        )
        .select_from(ComponentVersion)
        .join(Component, Component.id == ComponentVersion.component_id)
        .join(ScanComponent, ScanComponent.component_version_id == ComponentVersion.id)
        .join(Scan, Scan.id == ScanComponent.scan_id)
        .join(Project, Project.id == Scan.project_id)
        .where(ComponentVersion.id == component_version_id)
        .where(Project.latest_scan_id == Scan.id)
        .order_by(Scan.created_at.desc())
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
    vulns_stmt = (
        select(
            Vulnerability.external_id,
            Vulnerability.severity,
            Vulnerability.cvss_score,
            Vulnerability.summary,
            Vulnerability.details,
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

    sev_res, lic_res, vulns_res, lic_pick_res = await asyncio.gather(
        session.execute(sev_rank_stmt),
        session.execute(lic_rank_stmt),
        session.execute(vulns_stmt),
        session.execute(lic_pick_stmt),
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
                "title": vr.summary or vr.external_id,
                "description": vr.details,
                "fixed_version": None,
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
        "raw_data": dict(row.raw_data or {}),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


__all__ = [
    "ComponentNotFound",
    "get_component_detail",
    "get_project_overview",
    "list_components_for_project",
]
