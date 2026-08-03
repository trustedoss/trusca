# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""``/v1/dashboard/portfolio`` — the org-wide grid, grouped by team.

What this adds over the project list is not the numbers; it is the grouping.
A list answers "which project is worst"; a grid answers "which *team* is
carrying the risk", and that is a question only a portal with an org and team
model can ask at all.

Not a third copy of the numbers
-------------------------------
The severity counts come from ``services.project_list_enrichment`` — the same
two helpers the project list uses, called with the same arguments. That is
deliberate: a user who opens this grid and then the project list is comparing
the two, and a second implementation of "worst CVE per component, over the
latest succeeded scan" would eventually disagree with the first. Reusing the
helpers means there is nothing to keep in sync, which is the cheapest form of
CLAUDE.md hardening rule #2 — no duplicated vocabulary, so no parity test
needed to police it.

That choice also inherits the list's anchor: the latest succeeded scan that is
not superseded. It differs from ``dashboard_service._latest_succeeded_scan_ids``,
which ignores ``superseded_at``. The grid follows the project list rather than
the summary because those are the two screens a reader puts side by side.

Scoping
-------
``_accessible_project_ids`` from ``dashboard_service`` decides what the caller
may see, exactly as the summary, the action queue and the trend series do. The
team names are then read only for teams that own one of those projects, so a
team the caller does not belong to cannot be enumerated through this route
even as an empty row.

Truncation
----------
Caps are applied per team and across teams, and every cut is reported. A grid
that silently showed the first dozen projects would read as the whole
portfolio while being a sample of it, and the reader would draw exactly the
wrong conclusion about the ones not shown.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.security import CurrentUser
from models import Project, Scan
from models.auth import Team
from schemas.dashboard_portfolio import (
    DashboardPortfolio,
    PortfolioProject,
    PortfolioTeam,
)
from services.dashboard_service import _accessible_project_ids
from services.project_list_enrichment import (
    _latest_succeeded_scan_id_map,
    _severity_summary_map,
)

logger = structlog.get_logger("dashboard_portfolio.service")

# Cells per team row, and rows per grid. Both are display limits: past a dozen
# cells a row stops being scannable, and the reader should be on the project
# list with a filter instead. They bound the RESPONSE, not the work — every
# query above them is sized by the caller's whole portfolio, the same shape
# `/summary` has. What they must never do is hide a cut silently, so the
# response carries the full counts alongside what it actually shows.
PROJECTS_PER_TEAM = 12
MAX_TEAMS = 12


def _risk_key(project: PortfolioProject) -> tuple[int, int, int, int, str, str]:
    """Sort key: worst first, then by name, then by id.

    The tie-break is not cosmetic. Without one, two equally clean projects
    swap places between requests and whichever sits at the
    ``PROJECTS_PER_TEAM`` boundary changes at random — the grid would appear
    to reshuffle itself while nothing changed.

    The id is there because the name is not enough: ``projects.name`` carries
    no unique constraint (only ``(team_id, slug)`` does), so two projects in
    one team can share a name and four identical counts. The id makes the
    order total, which is what "deterministic" has to mean.
    """
    return (
        -project.critical,
        -project.high,
        -project.medium,
        -project.low,
        project.project_name,
        str(project.project_id),
    )


async def _project_rows(
    session: AsyncSession,
    *,
    project_ids: list[uuid.UUID],
) -> list[tuple[uuid.UUID, str, uuid.UUID]]:
    """``(project_id, name, team_id)`` for the accessible projects.

    ``team_id`` is NOT NULL on the table, so every project has an owning team
    and every row belongs on the grid.
    """
    if not project_ids:
        return []
    stmt = select(Project.id, Project.name, Project.team_id).where(
        Project.id.in_(project_ids)
    )
    return [(row[0], row[1], row[2]) for row in (await session.execute(stmt)).all()]


async def _team_names(
    session: AsyncSession,
    *,
    team_ids: list[uuid.UUID],
) -> dict[uuid.UUID, str]:
    """``{team_id: name}`` for teams that own a visible project.

    Only these ids are ever looked up: reading every team in the organisation
    and filtering afterwards would let a caller who belongs to one team learn
    the names of the others through an empty row.
    """
    if not team_ids:
        return {}
    stmt = select(Team.id, Team.name).where(Team.id.in_(team_ids))
    return {row[0]: row[1] for row in (await session.execute(stmt)).all()}


async def _last_scan_at(
    session: AsyncSession,
    *,
    scan_ids: list[uuid.UUID],
) -> dict[uuid.UUID, datetime]:
    """``{scan_id: created_at}`` for the anchor scans.

    ``created_at``, not ``completed_at``: it is the clock every other
    "latest succeeded scan" decision in the codebase orders by, and
    ``completed_at`` is nullable on older rows.
    """
    if not scan_ids:
        return {}
    stmt = select(Scan.id, Scan.created_at).where(Scan.id.in_(scan_ids))
    return {row[0]: row[1] for row in (await session.execute(stmt)).all()}


async def get_dashboard_portfolio(
    session: AsyncSession,
    *,
    actor: CurrentUser,
) -> DashboardPortfolio:
    """Build the team × project grid for ``actor``.

    Read-only, five batched queries, no per-project work. Returns an empty
    grid — not an error — for a caller with no accessible projects.
    """
    project_ids = await _accessible_project_ids(session, actor=actor)
    if not project_ids:
        logger.info("dashboard_portfolio.empty", actor_id=str(actor.id))
        return DashboardPortfolio(
            teams=[],
            team_count=0,
            shown_team_count=0,
            project_count=0,
            shown_project_count=0,
            truncated=False,
        )

    rows = await _project_rows(session, project_ids=project_ids)
    succeeded_by_project = await _latest_succeeded_scan_id_map(
        session, project_ids=project_ids
    )
    severity_by_project = await _severity_summary_map(
        session, succeeded_by_project=succeeded_by_project
    )
    scanned_at = await _last_scan_at(
        session, scan_ids=list(succeeded_by_project.values())
    )
    names = await _team_names(session, team_ids=[team_id for _, _, team_id in rows])

    by_team: dict[uuid.UUID, list[PortfolioProject]] = {}
    for project_id, project_name, team_id in rows:
        summary = severity_by_project.get(project_id)
        scan_id = succeeded_by_project.get(project_id)
        by_team.setdefault(team_id, []).append(
            PortfolioProject(
                project_id=project_id,
                project_name=project_name,
                critical=(summary or {}).get("critical", 0),
                high=(summary or {}).get("high", 0),
                medium=(summary or {}).get("medium", 0),
                low=(summary or {}).get("low", 0),
                # "Never scanned" is not "clean": the counts are zero either
                # way, so the flag is the only thing that separates a project
                # nobody has looked at from one that came back empty.
                scanned=summary is not None,
                last_scan_at=scanned_at.get(scan_id) if scan_id else None,
            )
        )

    # Rank each team from its FULL project list, before any cell is cut. The
    # obvious version ranks the rows after truncating them, and it is wrong in
    # a way that only shows at the boundary: a team's worst `high` can sit on a
    # project that sorts below the cut because its `critical` is lower, so the
    # team is ranked — and possibly dropped by MAX_TEAMS — on a sample of
    # itself.
    ranked: list[tuple[tuple[int, int, str], PortfolioTeam]] = []
    for team_id, projects in by_team.items():
        projects.sort(key=_risk_key)
        team_name = names.get(team_id, "")
        rank = (
            -max((p.critical for p in projects), default=0),
            -max((p.high for p in projects), default=0),
            team_name,
        )
        ranked.append(
            (
                rank,
                PortfolioTeam(
                    team_id=team_id,
                    team_name=team_name,
                    project_count=len(projects),
                    projects=projects[:PROJECTS_PER_TEAM],
                ),
            )
        )

    # Worst team first, by its worst project — the reason to group by team is
    # to see which one is carrying the risk.
    ranked.sort(key=lambda entry: entry[0])
    teams = [team for _, team in ranked]
    kept_teams = teams[:MAX_TEAMS]

    shown = sum(len(team.projects) for team in kept_teams)
    total_projects = sum(team.project_count for team in teams)
    truncated = shown < total_projects or len(kept_teams) < len(teams)

    logger.info(
        "dashboard_portfolio.built",
        actor_id=str(actor.id),
        team_count=len(teams),
        project_count=total_projects,
        shown=shown,
        truncated=truncated,
    )

    return DashboardPortfolio(
        teams=kept_teams,
        team_count=len(teams),
        shown_team_count=len(kept_teams),
        project_count=total_projects,
        shown_project_count=shown,
        truncated=truncated,
    )


__all__ = ["MAX_TEAMS", "PROJECTS_PER_TEAM", "get_dashboard_portfolio"]
