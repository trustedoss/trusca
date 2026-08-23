# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Global cross-project search — BomLens parity backlog H-2.

Backs ``GET /v1/search``. The endpoint fans out across every project the actor
can read, so team isolation is the dominant concern: BOTH sub-queries filter
through the single choke-point :func:`core.authz.team_scope_filter`, which
resolves to ``Project.team_id IN (actor.team_ids)`` for a member and to
``sa.true()`` for a super-admin. There is deliberately no local re-derivation
of the scope predicate here — a cross-team leak would be a P0.

Search safety: the user term is escaped with :func:`core.sql_safety.escape_like`
and matched with an explicit ``ESCAPE '\\'`` clause, so a literal ``%`` or ``_``
in the query is matched as a character and cannot collapse the ``ILIKE`` into
"match everything".

Which scan a hit comes from (concurrency-scaling plan Q2, 2026-08-22): both
categories resolve to each project's CURRENT-STATE scan
(:func:`services.scan_resolution.latest_succeeded_scan_select`), not every
scan the project has ever run. A component that only existed in an older,
since-superseded scan no longer surfaces here; :mod:`services.search_results_service`
carries the same rule for the full search page.

Shaping rules (contract the frontend depends on):
  - ``q`` is trimmed; shorter than :data:`MIN_QUERY_LEN` → empty results (no
    422, so the debounced palette can fire on every keystroke harmlessly).
  - ``kinds`` is a comma-separated subset of :data:`ALLOWED_KINDS`; unknown
    tokens are ignored, absent means both categories.
  - each category is capped at :data:`PER_CATEGORY_LIMIT` (20) rows.
  - results are de-duplicated (a component/CVE reached through more than one
    dependency path or finding within that current scan collapses to one row)
    and ordered deterministically.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from sqlalchemy import or_, select
from sqlalchemy.sql.elements import ColumnElement

from core.authz import team_scope_filter
from core.sql_safety import escape_like
from models import (
    Component,
    ComponentVersion,
    Project,
    Scan,
    ScanComponent,
    Vulnerability,
    VulnerabilityFinding,
)
from schemas.search import (
    ComponentSearchHit,
    GlobalSearchResults,
    VulnerabilitySearchHit,
)
from services.scan_resolution import latest_succeeded_scan_select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from core.security import CurrentUser

log = structlog.get_logger("search.service")

MIN_QUERY_LEN = 3
PER_CATEGORY_LIMIT = 20
COMPONENTS = "components"
VULNERABILITIES = "vulnerabilities"
ALLOWED_KINDS: frozenset[str] = frozenset({COMPONENTS, VULNERABILITIES})


def parse_kinds(kinds: str | None) -> set[str]:
    """Normalise the ``kinds`` CSV into the set of categories to search.

    ``None`` / empty → both categories. Unknown tokens are dropped (lenient,
    matching the existing query-param convention in the vulnerability list
    endpoint) rather than raising 422. If the CSV names ONLY unknown kinds the
    result is an empty set → the caller returns empty results.
    """
    if kinds is None:
        return set(ALLOWED_KINDS)
    requested = {token.strip().lower() for token in kinds.split(",") if token.strip()}
    if not requested:
        return set(ALLOWED_KINDS)
    return requested & ALLOWED_KINDS


async def _search_components(
    session: AsyncSession,
    *,
    scope: ColumnElement[bool],
    like: str,
) -> list[ComponentSearchHit]:
    """Components whose name or purl matches, within accessible projects.

    Concurrency-scaling plan Q2 (2026-08-22): joins through each project's
    CURRENT-STATE scan only
    (:func:`services.scan_resolution.latest_succeeded_scan_select`), not
    every scan the project has ever had. Before Q2 this joined
    ``scan_components → scans → projects``, which fanned out across a
    project's full retained history (up to 30 scans under the scan-series
    retention policy), so search cost grew with scan count rather than with
    catalog size. A component that only ever appeared in an older,
    since-superseded scan no longer surfaces here. That loss is intentional:
    "is this package anywhere in the organization RIGHT NOW" is the question
    the palette answers; "was it ever here" belongs to the scan detail and
    history screens.

    ``DISTINCT`` over the projected tuple still collapses the same
    (project, component-version) reached through more than one dependency
    path within that single scan.
    """
    current = latest_succeeded_scan_select(scope & Project.archived_at.is_(None))
    scan_ids = [row.scan_id for row in (await session.execute(current)).all()]
    if not scan_ids:
        return []

    stmt = (
        select(
            Project.id.label("project_id"),
            Project.name.label("project_name"),
            Project.slug.label("project_slug"),
            Component.name.label("component_name"),
            ComponentVersion.version.label("version"),
            Component.purl.label("purl"),
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
        .order_by(
            Project.name.asc(),
            Component.name.asc(),
            ComponentVersion.version.asc(),
        )
        .limit(PER_CATEGORY_LIMIT)
    )
    result = await session.execute(stmt)
    return [ComponentSearchHit.model_validate(dict(row)) for row in result.mappings().all()]


async def _search_vulnerabilities(
    session: AsyncSession,
    *,
    scope: ColumnElement[bool],
    like: str,
) -> list[VulnerabilitySearchHit]:
    """CVEs whose external id matches, within accessible projects.

    Joins ``vulnerability_findings → scans → projects`` (scope applied on
    ``projects``) and ``→ vulnerabilities``. ``DISTINCT`` collapses the same
    (project, CVE) surfaced by several component versions / scans to one row.
    """
    stmt = (
        select(
            Project.id.label("project_id"),
            Project.name.label("project_name"),
            Project.slug.label("project_slug"),
            Vulnerability.external_id.label("cve_id"),
            Vulnerability.severity.label("severity"),
        )
        .select_from(VulnerabilityFinding)
        .join(Scan, Scan.id == VulnerabilityFinding.scan_id)
        .join(Project, Project.id == Scan.project_id)
        .join(Vulnerability, Vulnerability.id == VulnerabilityFinding.vulnerability_id)
        .where(scope)
        .where(Project.archived_at.is_(None))
        .where(Vulnerability.external_id.ilike(like, escape="\\"))
        .distinct()
        .order_by(
            Project.name.asc(),
            Vulnerability.external_id.asc(),
        )
        .limit(PER_CATEGORY_LIMIT)
    )
    result = await session.execute(stmt)
    return [VulnerabilitySearchHit.model_validate(dict(row)) for row in result.mappings().all()]


async def global_search(
    session: AsyncSession,
    *,
    actor: CurrentUser,
    q: str,
    kinds: str | None = None,
) -> GlobalSearchResults:
    """Run the cross-project search for *actor*.

    Team isolation flows entirely through :func:`core.authz.team_scope_filter`;
    a non-super-admin with no memberships gets ``sa.false()`` → empty results.
    """
    query = (q or "").strip()
    if len(query) < MIN_QUERY_LEN:
        return GlobalSearchResults(query=query, components=[], vulnerabilities=[])

    selected = parse_kinds(kinds)
    scope = team_scope_filter(actor)
    like = f"%{escape_like(query)}%"

    components: list[ComponentSearchHit] = []
    vulnerabilities: list[VulnerabilitySearchHit] = []
    if COMPONENTS in selected:
        components = await _search_components(session, scope=scope, like=like)
    if VULNERABILITIES in selected:
        vulnerabilities = await _search_vulnerabilities(session, scope=scope, like=like)

    log.info(
        "search.global",
        actor_id=str(actor.id),
        query_len=len(query),
        kinds=sorted(selected),
        component_hits=len(components),
        vulnerability_hits=len(vulnerabilities),
    )
    return GlobalSearchResults(
        query=query,
        components=components,
        vulnerabilities=vulnerabilities,
    )


__all__ = [
    "ALLOWED_KINDS",
    "MIN_QUERY_LEN",
    "PER_CATEGORY_LIMIT",
    "global_search",
    "parse_kinds",
]
