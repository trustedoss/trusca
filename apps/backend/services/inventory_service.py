# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Organization-wide component inventory (S2).

What this answers that the per-project surfaces cannot: "where in the
organization is this package, and which projects would a fix have to touch".
Every audited SCA tool ships some form of this view — an organization-wide
package index, searchable across projects — and it is the one shape this
product was missing.

Two rules govern every query here.

**Which scan counts as "in use".** A project's current posture is its latest
``status='succeeded'`` scan, resolved through
:func:`services.scan_resolution.latest_succeeded_scan_select`. Not
``Project.latest_scan_id`` — that pointer tracks the last *attempt*, so a
project whose newest run failed would report zero components (the verified bug
that made ``scan_resolution`` exist). Not "every scan ever", either: joining
across all scans would resurrect packages removed three releases ago and
report them as in use. The concurrency-scaling plan's Q2 (2026-08-22) applied
that same reasoning to ``/v1/search`` and ``/v1/search/results``, which now
share this same resolver rather than joining a project's full scan history.
The resolver is the same definition the per-project detail surfaces use, so a
component listed here is one the owning project's Components tab also shows.

**Team isolation.** Every query funnels through
:func:`core.authz.team_scope_filter`, the single choke-point, applied on
``Project``. A cross-team leak here is a P0: this endpoint's whole purpose is
to fan out across projects, so the scope predicate is the only thing standing
between an actor and the rest of the deployment. There is deliberately no local
re-derivation of the rule.

Aggregation shape
-----------------
Rows are per **component** (purl without version), collapsing every in-use
version across every accessible project. To get there without a cartesian
blow-up, CVE and licence facts are folded to one row per (scan, component
version) in subqueries *before* the component-level ``GROUP BY``; joining the
finding tables directly would multiply rows by CVEs × licences and inflate every
count. (``project_detail_service.list_components_for_project`` does join them
directly and its ``vuln_count`` is inflated by the licence fan-out as a result —
that is a per-project row so the error is bounded, but it must not be copied
into a surface that spans the whole organization.)

The distinct-CVE tally is the one fact that cannot be folded per version — a CVE
affecting two in-use versions is one CVE for the component — so it is a second
query over the page's component ids, the same follow-up-batch pattern the
per-project service uses for licence display names.
"""

from __future__ import annotations

import uuid

# `cast` is SQLAlchemy's SQL CAST throughout this module, so typing's is
# aliased — the two must never read as the same call.
from typing import TYPE_CHECKING, Any
from typing import cast as type_cast

import structlog
from sqlalchemy import String, cast, func, literal, or_, select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql.elements import ColumnElement

from core.authz import team_scope_filter
from core.sql_safety import escape_like
from models import (
    Component,
    ComponentVersion,
    License,
    LicenseFinding,
    Project,
    Scan,
    ScanComponent,
    Vulnerability,
    VulnerabilityFinding,
)
from schemas.inventory import (
    InventoryComponentListResponse,
    InventoryComponentRow,
    InventoryProjectUsage,
    InventoryProjectUsageListResponse,
    InventoryVulnerabilityImpact,
    InventoryVulnerabilityImpactResponse,
)
from schemas.project_detail import ComponentSeverity, LicenseCategoryName
from services.project_detail_service import (
    _LICENSE_CATEGORY_FROM_RANK,
    _LICENSE_CATEGORY_RANK,
    _SEVERITY_FROM_RANK,
    _SEVERITY_RANK,
    _license_rank_case,
    _normalize_license_filter,
    _normalize_severity_filter,
    _severity_rank_case,
)
from services.scan_resolution import latest_succeeded_scan_select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from core.security import CurrentUser

log = structlog.get_logger("inventory.service")

LIMIT_DEFAULT = 50
LIMIT_MAX = 200

#: How many in-use versions a row carries inline. The row also reports the true
#: ``version_count``, so a truncated sample never reads as the whole set.
VERSION_SAMPLE_LIMIT = 5

VALID_SORT_KEYS = frozenset({"name", "project_count", "severity", "license"})


class InventoryError(Exception):
    """Base class for inventory failures the router maps to a problem+json."""

    status_code = 400
    title = "Inventory error"


class InventoryComponentNotFound(InventoryError):
    """The component does not exist, or none of the actor's projects use it.

    The two conditions are deliberately conflated: an actor who probes a
    component id used only by another team must not be able to tell "exists but
    not yours" from "does not exist". Same existence-hiding contract the scan
    snapshot resolver applies.
    """

    status_code = 404
    title = "Component not found"


class InventoryVulnerabilityNotFound(InventoryError):
    """The CVE does not exist, or affects none of the actor's projects."""

    status_code = 404
    title = "Vulnerability not found"


def _severity_from_rank(rank: int | None) -> ComponentSeverity:
    """Rank → severity token, narrowed to the wire Literal.

    ``_SEVERITY_FROM_RANK`` is declared ``dict[int, str]`` in
    ``project_detail_service`` (its callers build untyped dicts, so nothing
    forced a narrower type). Every value in it IS one of the six tokens, so the
    cast is sound — doing it here once keeps the three row builders clean and
    gives one place to fix if that catalog ever grows a value the wire shape
    does not know.
    """
    return type_cast(
        ComponentSeverity, _SEVERITY_FROM_RANK.get(int(rank or 0), "none")
    )


def _license_from_rank(rank: int | None) -> LicenseCategoryName:
    """Rank → licence-category token, narrowed to the wire Literal."""
    return type_cast(
        LicenseCategoryName, _LICENSE_CATEGORY_FROM_RANK.get(int(rank or 0), "unknown")
    )


async def _current_scan_ids(
    session: AsyncSession, scope: ColumnElement[bool]
) -> list[uuid.UUID]:
    """The current-state scan of every non-archived project the actor can read.

    Resolved once and passed to the aggregates as an id list. All three shapes
    were measured against ~100k component versions with ~3k in-scope projects
    (super-admin, the widest possible scope), 15 samples each:

    ==============================  ========  ========
    shape                           p50       p95
    ==============================  ========  ========
    resolved id list + ``IN``       229 ms    262 ms
    inline subquery per reference   524 ms    562 ms
    CTE shared across references    586 ms    631 ms
    ==============================  ========  ========

    The id list wins and it is worth writing down why, because the other two
    look cheaper on paper. A plain subquery is inlined at every reference — the
    outer aggregate plus both rollups — so the ``DISTINCT ON`` runs three times
    per statement. A CTE fixes that but is an optimisation fence in Postgres:
    the planner materialises all ~3k rows before joining instead of pushing the
    scan-id predicate down into the index. Handing it a literal id list lets it
    use ``ix_scan_components_scan_id`` directly, which is what the aggregate
    actually needs.

    The selection rule still comes from the shared resolver; this module owns no
    second definition of "which scan is current".
    """
    stmt = latest_succeeded_scan_select(scope & Project.archived_at.is_(None))
    return [row.scan_id for row in (await session.execute(stmt)).all()]


#: Above this many resolved scan ids the predicate switches to an array
#: parameter. asyncpg refuses a statement carrying more than 32 767 arguments,
#: and ``list_inventory_components`` binds the id list at THREE references (the
#: outer aggregate and both rollups), so the real ceiling is a third of that,
#: around 10 900. This sits well under it and above every size the plan
#: recorded in :func:`_current_scan_ids` was measured at.
SCAN_ID_INLINE_LIMIT = 5_000


def _among_scans(
    column: InstrumentedAttribute[uuid.UUID], scan_ids: list[uuid.UUID]
) -> ColumnElement[bool]:
    """"This column is one of the current-state scans", at any scope size.

    Two shapes, because neither is right everywhere.

    Under :data:`SCAN_ID_INLINE_LIMIT` the id list goes in as it always has,
    one bind parameter per element. That is what the measurement in
    :func:`_current_scan_ids` describes and it stays the fast path: Postgres
    sees how many elements the ``IN`` carries and estimates the scan-id
    predicate from that.

    Over it, the list goes in as ONE array parameter. It has to: asyncpg
    refuses a statement past 32 767 arguments, so a super-admin whose scope
    covered enough projects got a 500 rather than a page.

        asyncpg.exceptions._base.InterfaceError:
        the number of query arguments cannot exceed 32767

    The array form is not free. On the fixture the measurement above describes,
    3 000 in-scope projects and 99 000 scan-component rows, 30 samples each:
    p50 206 ms / p95 221 ms for the id list against p50 287 ms / p95 354 ms for
    the array. The array hides the element count behind a placeholder, so the
    planner falls back to a default estimate and picks a worse join. Paying
    that on every deployment to fix a ceiling almost none of them will reach is
    the wrong trade; paying it only past the ceiling turns a hard failure into
    a slower answer. At 35 000 in-scope projects and 195 000 scan-component
    rows the id list raises the error above and the array form answers at p50
    1.1 s / p95 2.0 s.

    A join against ``unnest()`` was measured too, at p50 552 ms, and is not
    used.
    """
    if len(scan_ids) <= SCAN_ID_INLINE_LIMIT:
        return column.in_(scan_ids)
    return column == func.any(literal(scan_ids, ARRAY(PG_UUID(as_uuid=True))))


def _vuln_rollup(scan_ids: list[uuid.UUID]) -> Any:
    """Worst CVE severity per (scan, component version) — one row each.

    Folding here rather than joining the finding tables into the outer query is
    what keeps the component-level counts honest: a direct join multiplies each
    component version by its CVE count, and then by its licence count.

    """
    return (
        select(
            VulnerabilityFinding.scan_id.label("scan_id"),
            VulnerabilityFinding.component_version_id.label("cv_id"),
            func.max(_severity_rank_case()).label("sev_rank"),
        )
        .select_from(VulnerabilityFinding)
        .join(Vulnerability, Vulnerability.id == VulnerabilityFinding.vulnerability_id)
        .where(_among_scans(VulnerabilityFinding.scan_id, scan_ids))
        .group_by(
            VulnerabilityFinding.scan_id, VulnerabilityFinding.component_version_id
        )
        .subquery()
    )


def _license_rollup(scan_ids: list[uuid.UUID]) -> Any:
    """Most restrictive licence category per (scan, component version)."""
    return (
        select(
            LicenseFinding.scan_id.label("scan_id"),
            LicenseFinding.component_version_id.label("cv_id"),
            func.max(_license_rank_case()).label("lic_rank"),
        )
        .select_from(LicenseFinding)
        .join(License, License.id == LicenseFinding.license_id)
        .where(_among_scans(LicenseFinding.scan_id, scan_ids))
        .group_by(LicenseFinding.scan_id, LicenseFinding.component_version_id)
        .subquery()
    )


async def _distinct_cve_counts(
    session: AsyncSession,
    *,
    scan_ids: list[uuid.UUID],
    component_ids: list[uuid.UUID],
) -> dict[uuid.UUID, int]:
    """``{component_id: distinct CVE count}`` for the page's rows.

    A CVE that hits two in-use versions of the same package is one CVE for the
    package. That cannot be folded per version, so it is a second query over
    the ids the page actually returned — bounded work, and it keeps the main
    aggregate free of the finding-table fan-out.
    """
    if not component_ids:
        return {}
    stmt = (
        select(
            ComponentVersion.component_id.label("component_id"),
            func.count(func.distinct(VulnerabilityFinding.vulnerability_id)).label("n"),
        )
        .select_from(VulnerabilityFinding)
        .join(
            ComponentVersion,
            ComponentVersion.id == VulnerabilityFinding.component_version_id,
        )
        .where(_among_scans(VulnerabilityFinding.scan_id, scan_ids))
        .where(ComponentVersion.component_id.in_(component_ids))
        .group_by(ComponentVersion.component_id)
    )
    result = await session.execute(stmt)
    return {row.component_id: int(row.n) for row in result.all()}


async def _version_samples(
    session: AsyncSession,
    *,
    scan_ids: list[uuid.UUID],
    component_ids: list[uuid.UUID],
) -> dict[uuid.UUID, list[str]]:
    """``{component_id: [version, …]}`` capped at :data:`VERSION_SAMPLE_LIMIT`.

    Fetches the distinct (component, version) pairs for the page and slices in
    Python: the page is at most ``LIMIT_MAX`` rows and a package has few in-use
    versions, so the set is small and the readable form wins.
    """
    if not component_ids:
        return {}
    stmt = (
        select(
            ComponentVersion.component_id.label("component_id"),
            ComponentVersion.version.label("version"),
        )
        .select_from(ScanComponent)
        .join(
            ComponentVersion,
            ComponentVersion.id == ScanComponent.component_version_id,
        )
        .where(_among_scans(ScanComponent.scan_id, scan_ids))
        .where(ComponentVersion.component_id.in_(component_ids))
        .distinct()
        .order_by(ComponentVersion.component_id, ComponentVersion.version)
    )
    result = await session.execute(stmt)
    out: dict[uuid.UUID, list[str]] = {}
    for row in result.all():
        bucket = out.setdefault(row.component_id, [])
        if len(bucket) < VERSION_SAMPLE_LIMIT:
            bucket.append(row.version)
    return out


async def list_inventory_components(
    session: AsyncSession,
    *,
    actor: CurrentUser,
    limit: int = LIMIT_DEFAULT,
    offset: int = 0,
    q: str | None = None,
    package_type: list[str] | None = None,
    severity: list[str] | None = None,
    license_category: list[str] | None = None,
    eol: bool | None = None,
    outdated: bool | None = None,
    sort: str = "project_count",
    order: str = "desc",
) -> InventoryComponentListResponse:
    """One page of the organization-wide component inventory.

    An actor with no team memberships gets an empty page rather than an error:
    the scope predicate resolves to ``false``, the current-scan subquery is
    empty, and the aggregate returns nothing. No special case needed.
    """
    limit = max(1, min(limit, LIMIT_MAX))
    offset = max(0, offset)
    scope = team_scope_filter(actor)

    severity_filter = _normalize_severity_filter(severity)
    if severity_filter == []:
        return InventoryComponentListResponse(
            items=[], total=0, limit=limit, offset=offset
        )
    license_filter = _normalize_license_filter(license_category)
    if license_filter == []:
        return InventoryComponentListResponse(
            items=[], total=0, limit=limit, offset=offset
        )
    if package_type is not None:
        package_type = [p.strip() for p in package_type if p and p.strip()]
        if not package_type:
            return InventoryComponentListResponse(
                items=[], total=0, limit=limit, offset=offset
            )

    scan_ids = await _current_scan_ids(session, scope)
    if not scan_ids:
        return InventoryComponentListResponse(
            items=[], total=0, limit=limit, offset=offset
        )
    vuln_rollup = _vuln_rollup(scan_ids)
    license_rollup = _license_rollup(scan_ids)

    # One current scan per project, so distinct scans ARE distinct projects —
    # no join back to `scans` needed to count the spread.
    project_count = func.count(func.distinct(ScanComponent.scan_id))
    sev_rank = func.coalesce(func.max(vuln_rollup.c.sev_rank), 0)
    lic_rank = func.coalesce(func.max(license_rollup.c.lic_rank), 0)
    any_eol = func.bool_or(cast(ComponentVersion.eol_state, String) == "eol")
    any_outdated = func.bool_or(
        cast(ComponentVersion.currency_state, String) == "outdated"
    )

    base = (
        select(
            Component.id.label("component_id"),
            Component.name.label("name"),
            Component.purl.label("purl"),
            Component.package_type.label("package_type"),
            project_count.label("project_count"),
            func.count(func.distinct(ComponentVersion.id)).label("version_count"),
            sev_rank.label("sev_rank"),
            lic_rank.label("lic_rank"),
            any_eol.label("eol"),
            any_outdated.label("outdated"),
        )
        .select_from(ScanComponent)
        .join(
            ComponentVersion,
            ComponentVersion.id == ScanComponent.component_version_id,
        )
        .join(Component, Component.id == ComponentVersion.component_id)
        .outerjoin(
            vuln_rollup,
            (vuln_rollup.c.scan_id == ScanComponent.scan_id)
            & (vuln_rollup.c.cv_id == ScanComponent.component_version_id),
        )
        .outerjoin(
            license_rollup,
            (license_rollup.c.scan_id == ScanComponent.scan_id)
            & (license_rollup.c.cv_id == ScanComponent.component_version_id),
        )
        .where(_among_scans(ScanComponent.scan_id, scan_ids))
        .group_by(Component.id)
    )

    if q and q.strip():
        like = f"%{escape_like(q.strip())}%"
        base = base.where(
            or_(
                Component.name.ilike(like, escape="\\"),
                Component.purl.ilike(like, escape="\\"),
            )
        )
    if package_type:
        base = base.where(Component.package_type.in_(package_type))

    # Severity / licence / lifecycle narrow the ROLLUP, so they are HAVING
    # clauses over the component-level aggregate rather than row filters — a
    # WHERE on the per-version rank would drop the package's other versions and
    # silently change project_count with it.
    if severity_filter is not None:
        base = base.having(sev_rank.in_([_SEVERITY_RANK[s] for s in severity_filter]))
    if license_filter is not None:
        base = base.having(
            lic_rank.in_([_LICENSE_CATEGORY_RANK[c] for c in license_filter])
        )
    if eol is not None:
        base = base.having(any_eol.is_(eol))
    if outdated is not None:
        base = base.having(any_outdated.is_(outdated))

    direction = "asc" if str(order).lower() == "asc" else "desc"
    sort_key = sort if sort in VALID_SORT_KEYS else "project_count"
    sort_column = {
        "name": Component.name,
        "project_count": project_count,
        "severity": sev_rank,
        "license": lic_rank,
    }[sort_key]
    ordered = base.order_by(
        sort_column.asc() if direction == "asc" else sort_column.desc(),
        # Stable tie-break so paging can neither repeat nor skip a row.
        Component.name.asc(),
        Component.id.asc(),
    )

    count_stmt = select(func.count()).select_from(base.subquery())
    # Sequential, not asyncio.gather: an AsyncSession holds ONE asyncpg
    # connection and a connection cannot multiplex, so concurrent execute()
    # calls raise "another operation is in progress". There was no parallelism
    # to win — the two statements share the connection either way.
    total_result = await session.execute(count_stmt)
    rows_result = await session.execute(ordered.limit(limit).offset(offset))
    total = int(total_result.scalar_one())
    rows = rows_result.mappings().all()

    component_ids = [row["component_id"] for row in rows]
    versions_map = await _version_samples(
        session, scan_ids=scan_ids, component_ids=component_ids
    )
    cve_counts = await _distinct_cve_counts(
        session, scan_ids=scan_ids, component_ids=component_ids
    )

    items = [
        InventoryComponentRow(
            component_id=row["component_id"],
            name=row["name"],
            purl=row["purl"],
            package_type=row["package_type"],
            project_count=int(row["project_count"]),
            version_count=int(row["version_count"]),
            versions=versions_map.get(row["component_id"], []),
            severity_max=_severity_from_rank(row["sev_rank"]),
            vulnerability_count=cve_counts.get(row["component_id"], 0),
            license_category_max=_license_from_rank(row["lic_rank"]),
            eol=bool(row["eol"]),
            outdated=bool(row["outdated"]),
        )
        for row in rows
    ]

    log.info(
        "inventory.components",
        actor_id=str(actor.id),
        total=total,
        returned=len(items),
    )
    return InventoryComponentListResponse(
        items=items, total=total, limit=limit, offset=offset
    )


async def list_component_usage(
    session: AsyncSession,
    *,
    actor: CurrentUser,
    component_id: uuid.UUID,
    limit: int = LIMIT_DEFAULT,
    offset: int = 0,
) -> InventoryProjectUsageListResponse:
    """Which accessible projects use *component_id*, and at which version.

    Raises :class:`InventoryComponentNotFound` when the actor's projects use it
    nowhere — existence-hiding, so probing another team's package id teaches
    nothing about whether that id exists.
    """
    limit = max(1, min(limit, LIMIT_MAX))
    offset = max(0, offset)
    scope = team_scope_filter(actor)
    scan_ids = await _current_scan_ids(session, scope)
    if not scan_ids:
        raise InventoryComponentNotFound(str(component_id))

    base = (
        select(
            Project.id.label("project_id"),
            Project.name.label("project_name"),
            Project.slug.label("project_slug"),
            ComponentVersion.version.label("version"),
            func.bool_or(ScanComponent.direct).label("direct"),
            Scan.id.label("scan_id"),
            Scan.created_at.label("scanned_at"),
        )
        .select_from(ScanComponent)
        .join(Scan, Scan.id == ScanComponent.scan_id)
        .join(Project, Project.id == Scan.project_id)
        .join(
            ComponentVersion,
            ComponentVersion.id == ScanComponent.component_version_id,
        )
        .where(_among_scans(ScanComponent.scan_id, scan_ids))
        .where(ComponentVersion.component_id == component_id)
        # One project can carry the same version at several dependency paths;
        # collapse those and let `direct` be true when any path is direct.
        .group_by(
            Project.id,
            Project.name,
            Project.slug,
            ComponentVersion.version,
            Scan.id,
            Scan.created_at,
        )
    )

    count_stmt = select(func.count()).select_from(base.subquery())
    page_stmt = (
        base.order_by(Project.name.asc(), ComponentVersion.version.asc())
        .limit(limit)
        .offset(offset)
    )
    total_result = await session.execute(count_stmt)
    rows_result = await session.execute(page_stmt)
    total = int(total_result.scalar_one())
    if total == 0:
        raise InventoryComponentNotFound(str(component_id))

    items = [
        InventoryProjectUsage(
            project_id=row["project_id"],
            project_name=row["project_name"],
            project_slug=row["project_slug"],
            version=row["version"],
            direct=bool(row["direct"]),
            scan_id=row["scan_id"],
            scanned_at=row["scanned_at"],
        )
        for row in rows_result.mappings().all()
    ]
    return InventoryProjectUsageListResponse(
        items=items, total=total, limit=limit, offset=offset
    )


async def list_vulnerability_impact(
    session: AsyncSession,
    *,
    actor: CurrentUser,
    external_id: str,
    limit: int = LIMIT_DEFAULT,
    offset: int = 0,
) -> InventoryVulnerabilityImpactResponse:
    """Which accessible projects a CVE currently affects.

    Raises :class:`InventoryVulnerabilityNotFound` when it reaches none of
    them — the same existence-hiding contract as the component lookup.
    """
    limit = max(1, min(limit, LIMIT_MAX))
    offset = max(0, offset)
    scope = team_scope_filter(actor)
    scan_ids = await _current_scan_ids(session, scope)
    if not scan_ids:
        raise InventoryVulnerabilityNotFound(external_id)

    base = (
        select(
            Project.id.label("project_id"),
            Project.name.label("project_name"),
            Project.slug.label("project_slug"),
            Component.name.label("component_name"),
            Component.purl.label("purl"),
            ComponentVersion.version.label("version"),
            VulnerabilityFinding.id.label("finding_id"),
            cast(VulnerabilityFinding.status, String).label("status"),
            cast(Vulnerability.severity, String).label("severity"),
        )
        .select_from(VulnerabilityFinding)
        .join(Scan, Scan.id == VulnerabilityFinding.scan_id)
        .join(Project, Project.id == Scan.project_id)
        .join(Vulnerability, Vulnerability.id == VulnerabilityFinding.vulnerability_id)
        .join(
            ComponentVersion,
            ComponentVersion.id == VulnerabilityFinding.component_version_id,
        )
        .join(Component, Component.id == ComponentVersion.component_id)
        .where(_among_scans(VulnerabilityFinding.scan_id, scan_ids))
        .where(Vulnerability.external_id == external_id)
    )

    count_stmt = select(func.count()).select_from(base.subquery())
    page_stmt = (
        base.order_by(
            Project.name.asc(), Component.name.asc(), ComponentVersion.version.asc()
        )
        .limit(limit)
        .offset(offset)
    )
    total_result = await session.execute(count_stmt)
    rows_result = await session.execute(page_stmt)
    total = int(total_result.scalar_one())
    if total == 0:
        raise InventoryVulnerabilityNotFound(external_id)

    rows = rows_result.mappings().all()
    items = [
        InventoryVulnerabilityImpact(
            project_id=row["project_id"],
            project_name=row["project_name"],
            project_slug=row["project_slug"],
            component_name=row["component_name"],
            purl=row["purl"],
            version=row["version"],
            finding_id=row["finding_id"],
            status=row["status"],
            severity=_severity_from_rank(_SEVERITY_RANK.get(row["severity"], 0)),
        )
        for row in rows
    ]
    return InventoryVulnerabilityImpactResponse(
        external_id=external_id,
        severity=items[0].severity,
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


__all__ = [
    "LIMIT_DEFAULT",
    "LIMIT_MAX",
    "VALID_SORT_KEYS",
    "VERSION_SAMPLE_LIMIT",
    "InventoryComponentNotFound",
    "InventoryError",
    "InventoryVulnerabilityNotFound",
    "list_component_usage",
    "list_inventory_components",
    "list_vulnerability_impact",
]
