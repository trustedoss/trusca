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
Projects search matches on the ``projects`` table directly and is not
scan-scoped at all: a project exists or it does not.

Components, vulnerabilities, and licences all resolve to each project's
CURRENT scan (:func:`services.scan_resolution.latest_succeeded_scan_select`).
Before the concurrency-scaling plan's Q2 (2026-08-22), components searched
across every scan a project had ever run, the same as the palette did. "Is
this package anywhere in our history" is a legitimate question, but it made
search cost grow with a project's retained scan count (up to 30 under the
scan-series retention policy) rather than with catalog size. Q2 narrowed both
surfaces to the current scan, matching the rule vulnerabilities and licences
already followed: a CVE that was fixed two releases ago is not something the
user wants back in a triage list, a licence finding from a superseded scan
would misreport today's obligation set, and a component removed a few
releases ago should not read as "in use". "Was it ever here" is now answered
by the scan detail and history screens, not by search.

Ranking
-------
Results order by ``similarity(column, query)`` descending — the pg_trgm score
the GIN indexes from migration 0043 already support. Plain alphabetical order
put "abc-lodash-shim" above "lodash" for the query "lodash", which is the wrong
answer to the question the user asked. Licences are the exception: the catalogue
is ~52 rows and has no trigram index, so they sort by name.

Counting a bounded window (Q3)
-------------------------------
Before the concurrency-scaling plan's Q3 (2026-08-24), ``total`` and every
facet bucket counted the FULL match set, every page, every keystroke: a
popular term (a common package name, a CVE affecting half the catalogue) paid
for a full scan/aggregate of everything it matched even to render page 1.
:func:`_windowed_total` and :func:`_windowed_facets` now wrap the match set in
``LIMIT RESULT_COUNT_CAP + 1`` before counting or grouping it, so the cost of
``total`` and ``facets`` is bounded regardless of how many rows actually
match. Within the cap, counts stay exact; a query that matches more sets
``counts_capped=True`` and reports ``total=RESULT_COUNT_CAP`` as a floor, not
the true count. See :data:`RESULT_COUNT_CAP` for why this number and not
another.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import Select, String, cast, func, or_, select
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

MIN_QUERY_LEN = 3
SIZE_DEFAULT = 25
SIZE_MAX = 100

#: Upper bound on how many matching rows ``total`` and each facet bucket will
#: actually count. Beyond this many rows, both report a floor
#: (``RESULT_COUNT_CAP``) instead of the true count and the page's
#: ``counts_capped`` flag turns on.
#:
#: 1,000 comfortably covers real pagination (``SIZE_MAX`` (100) times ten
#: pages is already more than anyone pages through by hand) while keeping the
#: COUNT/GROUP BY queries this module runs on every keystroke bounded: a plain
#: ``LIMIT RESULT_COUNT_CAP + 1`` wrapped around the match set lets Postgres
#: stop once it has the answer, rather than materializing and counting every
#: row a popular term matches. M3's load-test seed (200 projects × 20 scans ×
#: 500 components) put its "common name" query's match count at 206
#: (``concurrency-scaling-tracker.md`` §3-13), comfortably under this cap, so
#: ordinary searches keep an exact total and only pathologically common terms
#: (a package name shared by hundreds of projects, a CVE most of the catalogue
#: carries) hit the floor.
#:
#: A module-level name, not a default argument, so a test can
#: ``monkeypatch.setattr(search_results_service, "RESULT_COUNT_CAP", n)`` to
#: exercise the boundary without seeding a thousand rows.
RESULT_COUNT_CAP = 1000

PROJECTS = "projects"
COMPONENTS = "components"
VULNERABILITIES = "vulnerabilities"
LICENSES = "licenses"
ALLOWED_KINDS: frozenset[str] = frozenset({PROJECTS, COMPONENTS, VULNERABILITIES, LICENSES})


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


async def _windowed_total(session: AsyncSession, base: Select[Any]) -> tuple[int, bool]:
    """``(total, capped)`` for ``base``'s match set, bounded by
    :data:`RESULT_COUNT_CAP`.

    Wraps ``base`` (already team-scoped and filtered by its caller) in a
    ``LIMIT RESULT_COUNT_CAP + 1`` subquery and counts THAT instead of the
    unbounded match set. Counting to ``cap + 1`` rather than ``cap`` is what
    answers "does this exceed the cap" and "the exact count up to the cap" in
    the same query: a row count of ``cap + 1`` means the true total is
    larger, without needing to know by how much.

    Team isolation is not this function's concern: ``base`` arrives already
    filtered by :func:`core.authz.team_scope_filter`, and wrapping it in a
    bare ``LIMIT`` cannot drop or add to its ``WHERE`` clause.
    """
    windowed = base.limit(RESULT_COUNT_CAP + 1).subquery()
    raw = int((await session.execute(select(func.count()).select_from(windowed))).scalar_one())
    capped = raw > RESULT_COUNT_CAP
    return min(raw, RESULT_COUNT_CAP), capped


async def _windowed_facets(
    session: AsyncSession,
    base: Select[Any],
    facet_cols: dict[str, str],
) -> dict[str, list[SearchFacetBucket]]:
    """Facet buckets for ``base``'s match set, bounded by
    :data:`RESULT_COUNT_CAP` exactly like :func:`_windowed_total`: when the
    match set exceeds the cap, a bucket's ``count`` becomes a floor too, not
    the true per-value count.

    ``facet_cols`` maps a facet name (the key the wire response carries) to
    the label of the column on ``base`` to group by. Each facet issues its
    own ``LIMIT``-bounded subquery, mirroring how this module already ran
    one query per facet before Q3, rather than sharing a single windowed
    subquery with ``total``, so this module keeps issuing statements in the
    same order it always has (COUNT, then the page SELECT, then each facet)
    for the EXPLAIN-index tests in ``tests/_search_explain.py`` that pin that
    order; see the callers below, which run the page ``SELECT`` between
    :func:`_windowed_total` and this function.
    """
    facets: dict[str, list[SearchFacetBucket]] = {}
    for name, col_name in facet_cols.items():
        windowed = base.limit(RESULT_COUNT_CAP + 1).subquery()
        col = windowed.c[col_name]
        stmt = (
            select(col, func.count())
            .select_from(windowed)
            .group_by(col)
            .order_by(func.count().desc())
        )
        facets[name] = _facet_buckets((await session.execute(stmt)).all())
    return facets


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
    total, capped = await _windowed_total(session, base)
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
        total=total,
        counts_capped=capped,
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
    """Components whose name or purl matches, one row per (project, version).

    Concurrency-scaling plan Q2 (2026-08-22): restricted to each project's
    current scan, same as vulnerabilities and licences below (see the module
    docstring's "Which scan a hit comes from" section).
    """
    current = latest_succeeded_scan_select(scope & Project.archived_at.is_(None))
    scan_ids = [row.scan_id for row in (await session.execute(current)).all()]
    if not scan_ids:
        return _empty(COMPONENTS, q, page, size)

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
        .where(ScanComponent.scan_id.in_(scan_ids))
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
    total, capped = await _windowed_total(session, base)
    rows_result = await session.execute(page_stmt)
    facets = await _windowed_facets(session, base, facet_cols={"package_type": "package_type"})
    return SearchResultsPage(
        kind=COMPONENTS,
        query=q,
        items_components=[
            ComponentResult(**{k: v for k, v in row.items() if k != "rank"})
            for row in rows_result.mappings().all()
        ],
        total=total,
        counts_capped=capped,
        page=page,
        size=size,
        facets=facets,
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

    page_stmt = (
        base.order_by(
            func.similarity(Vulnerability.external_id, q).desc(),
            Vulnerability.external_id.asc(),
            Project.name.asc(),
        )
        .limit(size)
        .offset(offset)
    )
    total, capped = await _windowed_total(session, base)
    rows_result = await session.execute(page_stmt)
    facets = await _windowed_facets(
        session, base, facet_cols={"severity": "severity", "status": "status"}
    )
    return SearchResultsPage(
        kind=VULNERABILITIES,
        query=q,
        items_vulnerabilities=[
            VulnerabilityResult(**dict(row)) for row in rows_result.mappings().all()
        ],
        total=total,
        counts_capped=capped,
        page=page,
        size=size,
        facets=facets,
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
        wanted = [value.strip().lower() for value in license_category if value and value.strip()]
        if not wanted:
            return _empty(LICENSES, q, page, size)
        base = base.where(cast(License.category, String).in_(wanted))

    page_stmt = (
        base.order_by(License.name.asc(), Project.name.asc(), Component.name.asc())
        .limit(size)
        .offset(offset)
    )
    total, capped = await _windowed_total(session, base)
    rows_result = await session.execute(page_stmt)
    facets = await _windowed_facets(session, base, facet_cols={"license_category": "category"})
    return SearchResultsPage(
        kind=LICENSES,
        query=q,
        items_licenses=[LicenseResult(**dict(row)) for row in rows_result.mappings().all()],
        total=total,
        counts_capped=capped,
        page=page,
        size=size,
        facets=facets,
    )


__all__ = [
    "ALLOWED_KINDS",
    "MIN_QUERY_LEN",
    "RESULT_COUNT_CAP",
    "SIZE_DEFAULT",
    "SIZE_MAX",
    "SearchResultsError",
    "UnknownSearchKind",
    "search_results",
]
