"""
Project detail (Overview / Components) schemas — Phase 3 PR #10.

These schemas underpin three new endpoints under `api/v1/projects.py`:

- GET /v1/projects/{id}/overview            → ProjectOverviewResponse
- GET /v1/projects/{id}/components          → ComponentListResponse
- GET /v1/components/{id}                   → ComponentDetailResponse

Design notes
------------
The data model in `models/scan.py` does NOT carry severity/license directly on
the `components` table. Instead, components are reachable via the project's
**latest scan**:

    project → latest_scan_id → scan
    scan → scan_components → component_version → component
    scan → vulnerability_findings → vulnerability  (severity)
    scan → license_findings → license             (license category)

Severity per component is therefore the *maximum* severity across all CVE
findings for that component_version *within the latest scan*. License
category is the worst (most restrictive) category across all license findings
for that component_version within the latest scan. "No findings" maps to
`severity_max='none'` and `license_category='unknown'`.

Risk score (Phase 3.1 §1):
    min(100, critical*15 + high*5 + medium*1 + forbidden*30 + conditional*5)

The maximum is intentionally clamped — a project with hundreds of criticals
and a clean license profile shouldn't read as a higher risk than one that
overflows the bar. Phase 3+ may swap to a logarithmic / weighted formula;
the response schema does not change.

`components_total` reflects the count of distinct (component_version) rows in
the latest scan (deduplicated across multiple dependency_paths). When a
project has never been scanned, the response is well-formed but every
distribution map is empty.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# Severity / license enum values mirror models.scan but we re-declare them as
# Literals here so OpenAPI receives a precise enum (rather than a free string).
ComponentSeverity = Literal["critical", "high", "medium", "low", "info", "none"]
LicenseCategoryName = Literal["forbidden", "conditional", "allowed", "unknown"]

# The actor's effective role *within the project's owning team*. This is NOT
# the global `role` from JWT/`/auth/me` (which only ever yields `super_admin`
# or developer): a user who is `team_admin` of this project's team must see
# `team_admin` here so the frontend can enable team-scoped actions such as
# vulnerability suppression (BUG-005). See `current_user_role` below.
TeamScopedRole = Literal["super_admin", "team_admin", "developer"]


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------


class ScanSummary(BaseModel):
    """Compact scan record used by the project overview's recent-scans list."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    status: str
    progress_percent: int
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime


class ProjectOverviewResponse(BaseModel):
    """Aggregated risk / scan picture for the project detail Overview tab."""

    project_id: uuid.UUID
    project_name: str
    total_components: int
    severity_distribution: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Count of components per severity bucket. Keys are a subset of "
            "{critical, high, medium, low, info, none}. Buckets with zero "
            "components are still included so frontends can render an empty bar."
        ),
    )
    license_distribution: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Count of components per license category. Keys are a subset of "
            "{forbidden, conditional, allowed, unknown}."
        ),
    )
    risk_score: float = Field(
        ge=0.0,
        le=100.0,
        description=(
            "Composite risk 0–100 derived from severity + license distribution. "
            "Formula: min(100, critical*15 + high*5 + medium*1 + forbidden*30 "
            "+ conditional*5)."
        ),
    )
    recent_scans: list[ScanSummary] = Field(default_factory=list)
    last_scan_at: datetime | None = None
    current_user_role: TeamScopedRole = Field(
        description=(
            "The requesting user's effective role *within this project's owning "
            "team*: 'super_admin' for platform superusers, otherwise the user's "
            "membership role on the project's team ('team_admin' / 'developer'). "
            "Users who can read the project via org-wide visibility but hold no "
            "membership default to the least-privileged 'developer'. The "
            "frontend uses this (not the global JWT role) to gate team-scoped "
            "actions such as vulnerability suppression (BUG-005)."
        ),
    )


# ---------------------------------------------------------------------------
# Component list
# ---------------------------------------------------------------------------


class ComponentSummary(BaseModel):
    """One row in the components tab list. Optimized for table rendering."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(description="component_version id (the scan-bound row)")
    component_id: uuid.UUID
    name: str
    version: str
    purl: str | None = None
    license: str | None = Field(
        default=None,
        description="SPDX id of the worst-category license, or its name if no SPDX id.",
    )
    license_category: LicenseCategoryName
    severity_max: ComponentSeverity
    vulnerability_count: int = Field(ge=0)


class ComponentListResponse(BaseModel):
    """Page of components for a project, derived from its latest scan."""

    items: list[ComponentSummary]
    total: int
    limit: int
    offset: int


# ---------------------------------------------------------------------------
# Component detail (drawer)
# ---------------------------------------------------------------------------


class VulnerabilityRef(BaseModel):
    """Compact CVE reference attached to a component detail."""

    model_config = ConfigDict(from_attributes=True)

    cve_id: str = Field(description="DT/NVD external id (e.g. CVE-2024-1234, GHSA-...).")
    severity: str
    cvss: float | None = None
    epss_score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "EPSS exploit-probability for the CVE, in [0, 1]. ``null`` when no "
            "EPSS publication exists for this CVE."
        ),
    )
    epss_percentile: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="EPSS percentile rank for the CVE, in [0, 1]. ``null`` when unset.",
    )
    title: str
    description: str | None = None
    fixed_version: str | None = None


class ComponentDetailResponse(BaseModel):
    """Drawer payload for a single component in a project's latest scan."""

    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    version: str
    purl: str | None = None
    license: str | None = None
    license_category: LicenseCategoryName
    severity_max: ComponentSeverity
    vulnerabilities: list[VulnerabilityRef] = Field(default_factory=list)
    raw_data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


__all__ = [
    "ComponentDetailResponse",
    "ComponentListResponse",
    "ComponentSeverity",
    "ComponentSummary",
    "LicenseCategoryName",
    "ProjectOverviewResponse",
    "ScanSummary",
    "TeamScopedRole",
    "VulnerabilityRef",
]
