# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Paged, faceted search for the full search page (S3).

Relationship to ``search_service``
----------------------------------
``search_service`` backs the ⌘K palette: a fixed 20 hits per category, no
counts, no paging, and a wire contract the palette's tests pin exactly. This
module backs the full page: one kind at a time, paged, counted, faceted. They
are deliberately separate endpoints. Folding paging and facets into the palette
endpoint would have made its response shape depend on which parameters were
sent — and the plan's own goal was that a palette call keeps answering exactly
what it answers today.

They do share the rules that must not diverge: team isolation through the
single :func:`core.authz.team_scope_filter` choke-point, and the same LIKE
escaping so a literal ``%`` stays a percent sign.

Which scan a hit comes from
---------------------------
Projects and components search across every scan a project has ever had, the
same as the palette — "is this package anywhere in our history" is a legitimate
question and the palette has always answered it that way.

Vulnerabilities and licences resolve to each project's CURRENT scan
(:func:`services.scan_resolution.latest_succeeded_scan_select`). A CVE that was
fixed two releases ago is not something the user wants back in a triage list,
and a licence finding from a superseded scan would misreport today's obligation
set. The asymmetry is deliberate and is the reason it is written down here.

Ranking
-------
Results order by ``similarity(column, query)`` descending — the pg_trgm score
the GIN indexes from migration 0043 already support. Plain alphabetical order
put "abc-lodash-shim" above "lodash" for the query "lodash", which is the wrong
answer to the question the user asked. Licences are the exception: the catalogue
is ~52 rows and has no trigram index, so they sort by name.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import String, cast, func, or_, select
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
from schemas.search_results import (
    ComponentResult,
    LicenseResult,
    ProjectResult,
    SearchFacetBucket,
    SearchResultsPage,
    VulnerabilityResult,
)
from services.scan_resolution import latest_succeeded_scan_select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from core.security import CurrentUser

log = structlog.get_logger("search.results")

MIN_QUERY_LEN = 2
SIZE_DEFAULT = 25
SIZE_MAX = 100

PROJECTS = "projects"
COMPONENTS = "components"
VULNERABILITIES = "vulnerabilities"
LICENSES = "licenses"
ALLOWED_KINDS: frozenset[str] = frozenset(
    {PROJECTS, COMPONENTS, VULNERABILITIES, LICENSES}
)


class SearchResultsError(Exception):
    """Base class the router maps to a problem+json."""

    status_code = 400
    title = "Search error"


class UnknownSearchKind(SearchResultsError):
    """The requested kind is not one this endpoint serves."""

    status_code = 422
    title = "Unknown search kind"


def _empty(kind: str, query: str, page: int, size: int) -> SearchResultsPage:
    return SearchResultsPage(kind=kind, query=query, total=0, page=page, size=size)


def _facet_buckets(rows: Any) -> list[SearchFacetBucket]:
    """Rows of ``(value, count)`` → buckets, dropping NULL values."""
    return [
        SearchFacetBucket(value=str(value), count=int(count))
        for value, count in rows
        if value is not None
    ]


async def search_results(
    session: AsyncSession,
    *,
    actor: CurrentUser,
    kind: str,
    q: str,
    page: int = 1,
    size: int = SIZE_DEFAULT,
    severity: list[str] | None = None,
    status: list[str] | None = None,
    package_type: list[str] | None = None,
    license_category: list[str] | None = None,
) -> SearchResultsPage:
    """One page of results for one kind.

    A query shorter than :data:`MIN_QUERY_LEN` returns an empty page with a 200
    rather than a 422 — the page fires this as the user types, and an error
    banner that appears after the first keystroke is noise, not information.
    """
    if kind not in ALLOWED_KINDS:
        raise UnknownSearchKind(kind)

    query = (q or "").strip()
    page = max(1, page)
    size = max(1, min(size, SIZE_MAX))
    if len(query) < MIN_QUERY_LEN:
        return _empty(kind, query, page, size)

    scope = team_scope_filter(actor)
    like = f"%{escape_like(query)}%"
    offset = (page - 1) * size

    if kind == PROJECTS:
        result = await _projects(
            session,
            scope=scope,
            q=query,
            like=like,
            page=page,
            size=size,
            offset=offset,
        )
    elif kind == COMPONENTS:
        result = await _components(
            session,
            scope=scope,
            q=query,
            like=like,
            page=page,
            size=size,
            offset=offset,
            package_type=package_type,
        )
    elif kind == VULNERABILITIES:
        result = await _vulnerabilities(
            session,
            scope=scope,
            q=query,
            like=like,
            page=page,
            size=size,
            offset=offset,
            severity=severity,
            status=status,
        )
    else:
        result = await _licenses(
            session,
            scope=scope,
            q=query,
            like=like,
            page=page,
            size=size,
            offset=offset,
            license_category=license_category,
        )

    log.info(
        "search.results",
        actor_id=str(actor.id),
        kind=kind,
        query_len=len(query),
        total=result.total,
        page=page,
    )
    return result


async def _projects(
    session: AsyncSession,
    *,
    scope: ColumnElement[bool],
    q: str,
    like: str,
    page: int,
    size: int,
    offset: int,
) -> SearchResultsPage:
    """Projects whose name, slug, or clone URL matches.

    Archived projects are included here, unlike everywhere else — someone
    searching by name is often looking for the archived one specifically, and
    the row says so rather than hiding it.
    """
    base = (
        select(Project)
        .where(scope)
        .where(
            or_(
                Project.name.ilike(like, escape="\\"),
                Project.slug.ilike(like, escape="\\"),
                Project.git_url.ilike(like, escape="\\"),
            )
        )
    )
    count_stmt = select(func.count()).select_from(base.subquery())
    page_stmt = (
        base.order_by(
            func.similarity(Project.name, q).desc(),
            Project.name.asc(),
            Project.id.asc(),
        )
        .limit(size)
        .offset(offset)
    )
    # Sequential, not asyncio.gather: an AsyncSession holds ONE asyncpg
    # connection and a connection cannot multiplex, so concurrent execute()
    # calls raise "another operation is in progress". Gathering here bought no
    # parallelism to begin with — there is nothing to overlap.
    total_result = await session.execute(count_stmt)
    rows_result = await session.execute(page_stmt)
    rows = list(rows_result.scalars().all())
    return SearchResultsPage(
        kind=PROJECTS,
        query=q,
        items_projects=[
            ProjectResult(
                project_id=row.id,
                project_name=row.name,
                project_slug=row.slug,
                git_url=row.git_url,
                archived=row.archived_at is not None,
            )
            for row in rows
        ],
        total=int(total_result.scalar_one()),
        page=page,
        size=size,
        facets={},
    )


async def _components(
    session: AsyncSession,
    *,
    scope: ColumnElement[bool],
    q: str,
    like: str,
    page: int,
    size: int,
    offset: int,
    package_type: list[str] | None,
) -> SearchResultsPage:
    """Components whose name or purl matches, one row per (project, version)."""
    # One Label object, projected AND sorted on. Building two separate
    # `func.similarity(...)` expressions renders two different bind parameters,
    # and Postgres compares SELECT DISTINCT's ORDER BY against the select list
    # textually — so the second one reads as an expression that is not there.
    rank = func.similarity(Component.name, q).label("rank")
    base = (
        select(
            Project.id.label("project_id"),
            Project.name.label("project_name"),
            Project.slug.label("project_slug"),
            Component.id.label("component_id"),
            Component.name.label("component_name"),
            Component.purl.label("purl"),
            Component.package_type.label("package_type"),
            ComponentVersion.version.label("version"),
            # Projected, not just sorted on: Postgres rejects a SELECT
            # DISTINCT whose ORDER BY names an expression absent from the
            # select list. Stripped again before the rows reach the wire shape.
            rank,
        )
        .select_from(ScanComponent)
        .join(Scan, Scan.id == ScanComponent.scan_id)
        .join(Project, Project.id == Scan.project_id)
        .join(ComponentVersion, ComponentVersion.id == ScanComponent.component_version_id)
        .join(Component, Component.id == ComponentVersion.component_id)
        .where(scope)
        .where(Project.archived_at.is_(None))
        .where(
            or_(
                Component.name.ilike(like, escape="\\"),
                Component.purl.ilike(like, escape="\\"),
            )
        )
        .distinct()
    )
    if package_type:
        wanted = [value.strip() for value in package_type if value and value.strip()]
        if not wanted:
            return _empty(COMPONENTS, q, page, size)
        base = base.where(Component.package_type.in_(wanted))

    count_stmt = select(func.count()).select_from(base.subquery())
    page_stmt = (
        base.order_by(
            rank.desc(),
            Component.name.asc(),
            Project.name.asc(),
            ComponentVersion.version.asc(),
        )
        .limit(size)
        .offset(offset)
    )
    facet_src = base.subquery()
    facet_stmt = (
        select(facet_src.c.package_type, func.count())
        .group_by(facet_src.c.package_type)
        .order_by(func.count().desc())
    )
    total_result = await session.execute(count_stmt)
    rows_result = await session.execute(page_stmt)
    facet_result = await session.execute(facet_stmt)
    return SearchResultsPage(
        kind=COMPONENTS,
        query=q,
        items_components=[
            ComponentResult(**{k: v for k, v in row.items() if k != "rank"})
            for row in rows_result.mappings().all()
        ],
        total=int(total_result.scalar_one()),
        page=page,
        size=size,
        facets={"package_type": _facet_buckets(facet_result.all())},
    )


async def _vulnerabilities(
    session: AsyncSession,
    *,
    scope: ColumnElement[bool],
    q: str,
    like: str,
    page: int,
    size: int,
    offset: int,
    severity: list[str] | None,
    status: list[str] | None,
) -> SearchResultsPage:
    """CVEs matching id or summary, restricted to each project's current scan.

    Current-scan only: a finding from a scan two releases old is not something
    a triager wants surfacing in a search for work to do.
    """
    current = latest_succeeded_scan_select(scope & Project.archived_at.is_(None))
    scan_ids = [row.scan_id for row in (await session.execute(current)).all()]
    if not scan_ids:
        return _empty(VULNERABILITIES, q, page, size)

    base = (
        select(
            Project.id.label("project_id"),
            Project.name.label("project_name"),
            Project.slug.label("project_slug"),
            VulnerabilityFinding.id.label("finding_id"),
            Vulnerability.external_id.label("cve_id"),
            cast(Vulnerability.severity, String).label("severity"),
            cast(VulnerabilityFinding.status, String).label("status"),
            Component.name.label("component_name"),
            ComponentVersion.version.label("version"),
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
        .where(VulnerabilityFinding.scan_id.in_(scan_ids))
        .where(
            or_(
                Vulnerability.external_id.ilike(like, escape="\\"),
                Vulnerability.summary.ilike(like, escape="\\"),
            )
        )
    )
    if severity:
        wanted = [value.strip().lower() for value in severity if value and value.strip()]
        if not wanted:
            return _empty(VULNERABILITIES, q, page, size)
        base = base.where(cast(Vulnerability.severity, String).in_(wanted))
    if status:
        wanted_status = [value.strip().lower() for value in status if value and value.strip()]
        if not wanted_status:
            return _empty(VULNERABILITIES, q, page, size)
        base = base.where(cast(VulnerabilityFinding.status, String).in_(wanted_status))

    count_stmt = select(func.count()).select_from(base.subquery())
    page_stmt = (
        base.order_by(
            func.similarity(Vulnerability.external_id, q).desc(),
            Vulnerability.external_id.asc(),
            Project.name.asc(),
        )
        .limit(size)
        .offset(offset)
    )
    facet_src = base.subquery()
    severity_stmt = (
        select(facet_src.c.severity, func.count())
        .group_by(facet_src.c.severity)
        .order_by(func.count().desc())
    )
    status_stmt = (
        select(facet_src.c.status, func.count())
        .group_by(facet_src.c.status)
        .order_by(func.count().desc())
    )
    total_result = await session.execute(count_stmt)
    rows_result = await session.execute(page_stmt)
    sev_result = await session.execute(severity_stmt)
    status_result = await session.execute(status_stmt)
    return SearchResultsPage(
        kind=VULNERABILITIES,
        query=q,
        items_vulnerabilities=[
            VulnerabilityResult(**dict(row)) for row in rows_result.mappings().all()
        ],
        total=int(total_result.scalar_one()),
        page=page,
        size=size,
        facets={
            "severity": _facet_buckets(sev_result.all()),
            "status": _facet_buckets(status_result.all()),
        },
    )


async def _licenses(
    session: AsyncSession,
    *,
    scope: ColumnElement[bool],
    q: str,
    like: str,
    page: int,
    size: int,
    offset: int,
    license_category: list[str] | None,
) -> SearchResultsPage:
    """Licences matching SPDX id or name, restricted to each project's current scan.

    Ordered by name rather than trigram similarity: the catalogue is ~52 rows
    and carries no trigram index, so a similarity sort would buy nothing and
    cost a sequential scan on every page.
    """
    current = latest_succeeded_scan_select(scope & Project.archived_at.is_(None))
    scan_ids = [row.scan_id for row in (await session.execute(current)).all()]
    if not scan_ids:
        return _empty(LICENSES, q, page, size)

    base = (
        select(
            Project.id.label("project_id"),
            Project.name.label("project_name"),
            Project.slug.label("project_slug"),
            License.id.label("license_id"),
            License.spdx_id.label("spdx_id"),
            License.name.label("license_name"),
            cast(License.category, String).label("category"),
            Component.name.label("component_name"),
            ComponentVersion.version.label("version"),
        )
        .select_from(LicenseFinding)
        .join(Scan, Scan.id == LicenseFinding.scan_id)
        .join(Project, Project.id == Scan.project_id)
        .join(License, License.id == LicenseFinding.license_id)
        .join(
            ComponentVersion,
            ComponentVersion.id == LicenseFinding.component_version_id,
        )
        .join(Component, Component.id == ComponentVersion.component_id)
        .where(LicenseFinding.scan_id.in_(scan_ids))
        .where(
            or_(
                License.spdx_id.ilike(like, escape="\\"),
                License.name.ilike(like, escape="\\"),
            )
        )
        .distinct()
    )
    if license_category:
        wanted = [
            value.strip().lower() for value in license_category if value and value.strip()
        ]
        if not wanted:
            return _empty(LICENSES, q, page, size)
        base = base.where(cast(License.category, String).in_(wanted))

    count_stmt = select(func.count()).select_from(base.subquery())
    page_stmt = (
        base.order_by(
            License.name.asc(), Project.name.asc(), Component.name.asc()
        )
        .limit(size)
        .offset(offset)
    )
    facet_src = base.subquery()
    facet_stmt = (
        select(facet_src.c.category, func.count())
        .group_by(facet_src.c.category)
        .order_by(func.count().desc())
    )
    total_result = await session.execute(count_stmt)
    rows_result = await session.execute(page_stmt)
    facet_result = await session.execute(facet_stmt)
    return SearchResultsPage(
        kind=LICENSES,
        query=q,
        items_licenses=[
            LicenseResult(**dict(row)) for row in rows_result.mappings().all()
        ],
        total=int(total_result.scalar_one()),
        page=page,
        size=size,
        facets={"license_category": _facet_buckets(facet_result.all())},
    )


__all__ = [
    "ALLOWED_KINDS",
    "MIN_QUERY_LEN",
    "SIZE_DEFAULT",
    "SIZE_MAX",
    "SearchResultsError",
    "UnknownSearchKind",
    "search_results",
]
