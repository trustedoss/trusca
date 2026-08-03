# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Response models for ``GET /v1/projects/{id}/governance``.

The band above the project tabs. Everything here already exists somewhere in
the product — the risk score on the Overview tab, the gate verdict in CI, the
KEV dates on the Vulnerabilities tab, the approval queue on its own page. What
the band adds is that they are visible *at once*, on the page where the work
happens, instead of one tab-switch apart each.
"""

from __future__ import annotations

import datetime
import uuid

from pydantic import BaseModel, Field


class GovernanceGate(BaseModel):
    """The build-gate verdict for the project's current snapshot."""

    status: str | None = Field(
        default=None,
        description=(
            "'pass' or 'fail'. Null when the project has no succeeded scan — "
            "which is not a pass. A gate that has never run and a gate that "
            "ran clean must not render alike."
        ),
    )
    critical_cve_count: int = Field(ge=0)
    forbidden_license_count: int = Field(
        ge=0,
        description=(
            "Components whose licence resolves to forbidden under the owning "
            "team's policy — which can differ from the category stored at "
            "scan time, and therefore from the licence axis of ``risk_score``."
        ),
    )
    epss_gate_count: int = Field(
        ge=0,
        description="Always zero when GATE_EPSS_THRESHOLD is unset, which disables that condition.",
    )
    scan_id: uuid.UUID | None = Field(
        default=None, description="The snapshot the verdict was computed from."
    )


class GovernanceKevSla(BaseModel):
    """Known-exploited findings against their CISA remediation date."""

    overdue: int = Field(ge=0)
    due_soon: int = Field(ge=0, description="Due within the next week — still actionable.")


class GovernanceTrendPoint(BaseModel):
    """One succeeded scan of this project, oldest first."""

    scan_id: uuid.UUID
    scanned_at: datetime.datetime
    critical: int = Field(
        ge=0,
        description=(
            "Components whose worst finding is critical in that scan — the "
            "Overview donut's population, which counts dismissed findings "
            "too. It is deliberately not the gate's blocking count: a team "
            "that triages five criticals away flips the gate to pass while "
            "this series holds its shape."
        ),
    )


class ProjectGovernance(BaseModel):
    """The governance band's whole payload."""

    project_id: uuid.UUID
    scanned: bool = Field(
        description=(
            "False when no scan has ever succeeded. Every number below is "
            "zero in that case, and none of them means the project is clean."
        )
    )
    risk_score: float = Field(
        ge=0,
        le=100,
        description="The Overview tab's overall score, from the same computation.",
    )
    gate: GovernanceGate
    kev_sla: GovernanceKevSla
    pending_approvals: int = Field(
        ge=0, description="Component approvals in pending or under_review."
    )
    trend: list[GovernanceTrendPoint] = Field(
        default_factory=list,
        description=(
            "The last few succeeded scans, oldest first, for a sparkline. "
            "Fewer than two points is not a trend and the UI should not draw "
            "one."
        ),
    )
