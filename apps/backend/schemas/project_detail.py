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
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    release: str | None = Field(
        default=None,
        description=(
            "Optional release/version label sourced from "
            "``Scan.scan_metadata['release']`` (same field the Versions tab "
            "renders). ``null`` when the scan was run without a release label."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _hoist_release_from_scan_metadata(cls, data: Any) -> Any:
        """Lift ``scan_metadata['release']`` onto the top-level ``release`` field.

        ``ProjectOverviewResponse`` populates ``recent_scans`` directly from
        ORM :class:`Scan` rows (``from_attributes=True``). Pydantic can read the
        scalar columns by attribute, but the release label lives one level deep
        in the JSONB ``scan_metadata`` dict. We intercept the raw ORM object,
        copy the seven scalar fields, and hoist the metadata-nested label so
        the wire schema stays flat.

        Plain ``dict`` / already-validated inputs (unit tests, JSON requests)
        pass through unchanged.
        """
        if isinstance(data, dict):
            return data
        if not hasattr(data, "scan_metadata"):
            return data
        meta = getattr(data, "scan_metadata", None) or {}
        raw = meta.get("release") if isinstance(meta, dict) else None
        release: str | None = None
        if isinstance(raw, str):
            stripped = raw.strip()
            release = stripped or None
        return {
            "id": data.id,
            "kind": data.kind,
            "status": data.status,
            "progress_percent": data.progress_percent,
            "started_at": data.started_at,
            "completed_at": data.completed_at,
            "created_at": data.created_at,
            "release": release,
        }


class ProjectOverviewResponse(BaseModel):
    """Aggregated risk / scan picture for the project detail Overview tab."""

    project_id: uuid.UUID
    project_name: str
    total_components: int
    eol_count: int = Field(
        default=0,
        description=(
            "Phase M — count of distinct components in the anchored scan "
            "whose release cycle is past its published end-of-life "
            "(endoflife.date). A supply-chain risk axis distinct from CVEs: "
            "an EOL runtime gets no upstream fixes."
        ),
    )
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
            "Overall project risk 0–100 = max(security_score, license_score) — "
            "the worse of the two axes. Non-saturating (band-by-worst-severity, "
            "see security_score/license_score). Kept for back-compat and for "
            "'riskiest project' sorting / release trends."
        ),
    )
    security_score: float = Field(
        ge=0.0,
        le=100.0,
        description=(
            "Security risk 0–100 driven by the worst CVE severity present: "
            "critical→75–100, high→50–74, medium→25–49, low→1–24, none→0. The "
            "count of that severity sets the position within the band (n/(n+4)), "
            "so the score rises with count without saturating at a hard cap."
        ),
    )
    license_score: float = Field(
        ge=0.0,
        le=100.0,
        description=(
            "License risk 0–100 driven by the worst license category present: "
            "forbidden→75–100 (build-blocking), conditional→25–49 (review; never "
            "Critical on its own), unknown→1–24, allowed→0. Same n/(n+4) "
            "within-band scaling as security_score."
        ),
    )
    recent_scans: list[ScanSummary] = Field(default_factory=list)
    last_scan_at: datetime | None = Field(
        default=None,
        description=(
            "Timestamp (`created_at`) of the project's latest scan *attempt* "
            "regardless of status — the attempt timeline. May be a failed scan. "
            "`null` when the project has never been scanned."
        ),
    )
    last_succeeded_scan_at: datetime | None = Field(
        default=None,
        description=(
            "Timestamp (`created_at`) of the project's latest *succeeded* scan — "
            "the scan whose findings this overview (and the SBOM export) actually "
            "reflect, resolved via the same anchor as the build gate. `null` when "
            "the project has no succeeded scan. The SBOM tab labels its download "
            "with THIS field (not `last_scan_at`) so the timestamp matches what is "
            "downloaded; the two differ whenever the latest attempt failed."
        ),
    )
    vuln_data_available: bool | None = Field(
        default=None,
        description=(
            "#35 Surface B — whether the DT vulnerability database held any data "
            "WHEN the anchored scan ran (captured in scan_metadata at scan time). "
            "True = the DB was populated, so an empty Security axis is a real "
            "clean result. False = the DB was empty, so 0 CVEs means 'no data', "
            "NOT 'safe' — the UI shows a caveat prompting a rescan once the NVD "
            "mirror finishes. None = unknown (no succeeded scan, or a scan that "
            "predates this capture); the UI shows no caveat (never cry wolf)."
        ),
    )
    has_git_credential: bool = Field(
        default=False,
        description=(
            "Feature #18 Part B — True when a private-repo git credential is "
            "configured for this project. Read-only; the plaintext and ciphertext "
            "are NEVER returned. The UI uses this to show a 'credential configured' "
            "badge and to drive the set/rotate/clear control."
        ),
    )
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
    depth: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Shortest-path distance from a dependency-graph root (v2.2): 1 = "
            "direct, 2+ = transitive. The shallowest path when the component "
            "appears at several. ``null`` when the scan carried no dependency "
            "graph (older scans / flat-list ecosystems)."
        ),
    )
    direct: bool = Field(
        default=False,
        description=(
            "True when this component is a direct dependency of the scanned "
            "project (graph depth 1). False for transitive deps and when the "
            "graph was unavailable."
        ),
    )
    dependency_scope: Literal["required", "optional"] | None = Field(
        default=None,
        description=(
            "W2 #31 — BD-style 'Usage' for the component, derived from the "
            "CycloneDX ``component.scope`` field cdxgen emits. The value is "
            "aggregated across the same cv's dependency paths (``required`` "
            "wins over ``optional`` — a component used at runtime from any "
            "path is reported as ``required``). ``null`` when every path "
            "left scope unset (the common case for ecosystems whose SBOMs "
            "do not encode scope) — the UI renders that as '—' rather than "
            "guessing."
        ),
    )
    eol_state: Literal["eol", "supported", "unknown"] | None = Field(
        default=None,
        description=(
            "Phase M — endoflife.date verdict for this component version. "
            "``eol`` = release cycle past its published end-of-life; "
            "``supported`` = tracked and still supported; ``unknown`` = "
            "tracked product but the cycle could not be decided; ``null`` = "
            "not a tracked product (closed whitelist — never guessed). The "
            "UI renders a badge only for ``eol`` (absence is the signal)."
        ),
    )
    eol_date: date | None = Field(
        default=None,
        description=(
            "Published end-of-life date, when the feed carries one (dated "
            "cycles). ``null`` for boolean-only feeds and untracked rows."
        ),
    )


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
    fixed_version: str | None = Field(
        default=None,
        description=(
            "Version that remediates this CVE for this component, when the scan "
            "pipeline could determine one from DT findings (v2.2). ``null`` when "
            "DT reported no fix version, or for findings scanned before v2.2."
        ),
    )


class ObligationRef(BaseModel):
    """Compact license-obligation reference attached to a component detail.

    M-20 — the Components drawer renders the duties carried by the
    component's license(s) without a second request. This is a deliberately
    lean projection of the obligations catalog: the full drawer shape
    (affected components, truncation flags, …) stays on
    :class:`schemas.obligation_detail.ObligationDetailResponse`, reachable
    from the Obligations tab.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(description="obligations.id (catalog row).")
    kind: str = Field(
        max_length=64,
        description=(
            "Obligation kind — free-form catalog string (e.g. attribution, "
            "source-disclosure, copyleft)."
        ),
    )
    text: str = Field(description="Human-readable obligation text.")
    link: str | None = Field(
        default=None,
        description=(
            "Optional URL with further explanation. Frontends MUST scheme-"
            "filter to http/https before rendering as a clickable link."
        ),
    )
    license: str = Field(
        description=(
            "Display identifier of the parent license: its SPDX short id, "
            "falling back to the license name for ORT custom licenses "
            "(LicenseRef-*) that carry no SPDX id."
        ),
    )


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
    obligations: list[ObligationRef] = Field(
        default_factory=list,
        description=(
            "M-20 — duties carried by every license observed for this "
            "component in the anchoring scan, ordered by (kind, license, id) "
            "for a deterministic response. Empty when the component has no "
            "license, the license is not in the catalog, or the catalog "
            "defines no obligations for it."
        ),
    )
    raw_data: dict[str, Any] = Field(default_factory=dict)
    depth: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Shortest-path distance from a dependency-graph root (v2.2): 1 = "
            "direct, 2+ = transitive. ``null`` when the scan carried no "
            "dependency graph."
        ),
    )
    direct: bool = Field(
        default=False,
        description="True when this is a direct dependency (graph depth 1).",
    )
    dependency_scope: Literal["required", "optional"] | None = Field(
        default=None,
        description=(
            "W2 #31 — BD-style 'Usage' for the chosen (shallowest) path. "
            "``null`` when cdxgen left the field unset on that path. Drawers "
            "surface the row's own scope rather than an aggregate, because "
            "depth/direct already pin one path."
        ),
    )
    eol_state: Literal["eol", "supported", "unknown"] | None = Field(
        default=None,
        description=(
            "Phase M — endoflife.date verdict (see ComponentSummary.eol_state "
            "for the vocabulary). ``null`` = not a tracked product."
        ),
    )
    eol_product: str | None = Field(
        default=None,
        description="endoflife.date product slug the component mapped to.",
    )
    eol_cycle: str | None = Field(
        default=None,
        description="Release cycle derived from the version (e.g. '3.2').",
    )
    eol_date: date | None = Field(
        default=None,
        description="Published end-of-life date, when the feed carries one.",
    )
    eol_source: str | None = Field(
        default=None,
        description="Snapshot provenance, e.g. 'endoflife.date@2026-07-11'.",
    )
    created_at: datetime
    updated_at: datetime


__all__ = [
    "ComponentDetailResponse",
    "ComponentListResponse",
    "ComponentSeverity",
    "ComponentSummary",
    "LicenseCategoryName",
    "ObligationRef",
    "ProjectOverviewResponse",
    "ScanSummary",
    "TeamScopedRole",
    "VulnerabilityRef",
]
