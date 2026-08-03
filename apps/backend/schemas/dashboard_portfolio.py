# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Response models for ``GET /v1/dashboard/portfolio``.

The org-wide view: every project the caller can see, grouped by the team that
owns it, each carrying the severity counts its project-list row shows.

Truncation is part of the contract rather than a silent detail. A deployment
with hundreds of projects cannot render a cell per project, and a view that
quietly showed the first dozen would read as "this is your portfolio" while
being a sample of it. Every level that can be cut says how many it dropped.
"""

from __future__ import annotations

import datetime
import uuid

from pydantic import BaseModel, Field


class PortfolioProject(BaseModel):
    """One project cell."""

    project_id: uuid.UUID
    project_name: str
    critical: int = Field(ge=0)
    high: int = Field(ge=0)
    medium: int = Field(ge=0)
    low: int = Field(ge=0)
    scanned: bool = Field(
        description=(
            "False when no scan has ever succeeded for this project. The "
            "counts are all zero in that case, which is not the same as a "
            "clean project and must not render as one."
        )
    )
    last_scan_at: datetime.datetime | None = Field(
        default=None,
        description="When the latest succeeded scan started. Null when never scanned.",
    )


class PortfolioTeam(BaseModel):
    """One team's row of the grid."""

    team_id: uuid.UUID
    team_name: str
    project_count: int = Field(
        ge=0, description="Projects this team owns that the caller can see."
    )
    projects: list[PortfolioProject] = Field(
        default_factory=list,
        description=(
            "Worst first. Capped per team — compare the length against "
            "project_count to know whether the row is complete."
        ),
    )


class DashboardPortfolio(BaseModel):
    """Teams and their projects, scoped to what the caller may read."""

    teams: list[PortfolioTeam] = Field(default_factory=list)
    team_count: int = Field(
        ge=0, description="Teams with at least one visible project, before any cap."
    )
    shown_team_count: int = Field(
        ge=0,
        description=(
            "Team rows actually included. Reported separately from "
            "``team_count`` because a dropped team leaves no trace in "
            "``teams`` at all — its per-row caption cannot fire, so without "
            "this number a reader counting rows against ``team_count`` "
            "concludes the shown projects are spread across every team."
        ),
    )
    project_count: int = Field(ge=0, description="Visible projects, before any cap.")
    shown_project_count: int = Field(
        ge=0, description="Projects actually included in ``teams``."
    )
    truncated: bool = Field(
        description=(
            "True when any team row or the team list itself was cut. The UI "
            "must say so — a grid that silently shows a subset invites the "
            "reader to conclude the rest is clean."
        )
    )
