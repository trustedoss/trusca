# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Internal usage lookup for an exact, versionless PURL.

Backs the "already used internally?" panel on the external package lookup
(deps.dev) page. Deliberately separate from ``integrations.depsdev``: this
is a pure internal-DB read, not an outbound egress call, and the two
concerns should not share a module.

Matches on ``Component.purl == purl`` (equality), never ``ILIKE``.
``search_results_service._components`` searches by substring
(``%{escape_like(q)}%``), which is right for a person typing a fragment of a
name but wrong here: wrapped in wildcards, ``pkg:npm/lodash`` also matches
``pkg:npm/lodash-es`` and any other package whose purl happens to contain it.
The caller already has the exact purl deps.dev resolved, so equality is both
correct and simpler.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.authz import team_scope_filter
from core.security import CurrentUser
from models import Component, ComponentVersion, Project, Scan, ScanComponent
from schemas.external_packages import InternalProjectUsage
from services.scan_resolution import latest_succeeded_scan_select


async def internal_usage_by_purl(
    session: AsyncSession, *, actor: CurrentUser, purl: str
) -> list[InternalProjectUsage]:
    """Every project (within the actor's team scope) whose current scan
    carries a component with exactly this purl.

    Empty list means "not used internally, as far as this actor can see",
    not necessarily "not used anywhere" for a non-super-admin actor, since
    team scoping still applies.
    """
    scope = team_scope_filter(actor)
    current = latest_succeeded_scan_select(scope & Project.archived_at.is_(None))
    scan_ids = [row.scan_id for row in (await session.execute(current)).all()]
    if not scan_ids:
        return []

    stmt = (
        select(
            Project.id.label("project_id"),
            Project.name.label("project_name"),
            Project.slug.label("project_slug"),
            ComponentVersion.version.label("version"),
        )
        .select_from(ScanComponent)
        .join(Scan, Scan.id == ScanComponent.scan_id)
        .join(Project, Project.id == Scan.project_id)
        .join(ComponentVersion, ComponentVersion.id == ScanComponent.component_version_id)
        .join(Component, Component.id == ComponentVersion.component_id)
        .where(ScanComponent.scan_id.in_(scan_ids))
        .where(Component.purl == purl)
        .distinct()
        .order_by(Project.name)
    )
    rows = (await session.execute(stmt)).all()
    return [
        InternalProjectUsage(
            project_id=row.project_id,
            project_name=row.project_name,
            project_slug=row.project_slug,
            version=row.version,
        )
        for row in rows
    ]
