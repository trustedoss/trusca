# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Response models for ``GET /v1/dashboard/action-queue``.

Every field answers "what should someone do next", so each bucket carries
enough to act without a second request: a project id to link to, a name to
show, and the number that makes it urgent.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class GateBlockedProject(BaseModel):
    """A project whose latest succeeded scan trips the build gate."""

    project_id: uuid.UUID
    project_name: str
    scan_id: uuid.UUID = Field(
        description="The scan the verdict was computed from — the latest succeeded one."
    )
    critical_cve_count: int = Field(
        ge=0, description="Open critical findings. Zero when only licences block."
    )
    forbidden_license_count: int = Field(
        ge=0,
        description=(
            "Components whose licence resolves to forbidden under the owning "
            "team's policy, or the catalogue category when no policy applies."
        ),
    )
    epss_gate_count: int = Field(
        default=0,
        ge=0,
        description=(
            "Open findings at or above the EPSS gate threshold. Always zero "
            "when GATE_EPSS_THRESHOLD is unset, which disables that condition."
        ),
    )


class KevSlaBucket(BaseModel):
    """Known-exploited findings measured against their CISA remediation date."""

    overdue: int = Field(ge=0, description="Open KEV findings past their due date.")
    due_soon: int = Field(
        ge=0,
        description="Open KEV findings due within the next week — still actionable.",
    )


class StaleProject(BaseModel):
    """A project nothing has successfully scanned recently."""

    project_id: uuid.UUID
    project_name: str
    last_succeeded_at: datetime | None = Field(
        default=None,
        description="Null when no scan has ever succeeded — registered and never run.",
    )


class ActionQueue(BaseModel):
    """The work waiting on a person, across every project the caller can see."""

    pending_approvals: int = Field(
        ge=0, description="Component approvals in pending or under_review."
    )
    kev_sla: KevSlaBucket
    gate_blocked: list[GateBlockedProject] = Field(default_factory=list)
    stale_projects: list[StaleProject] = Field(default_factory=list)
