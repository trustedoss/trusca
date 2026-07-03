"""
License detail schemas — Phase 3 PR #12 (Licenses tab + drawer).

These schemas back two new read-only endpoints:

- GET /v1/projects/{id}/licenses             → LicenseListResponse
- GET /v1/license_findings/{id}              → LicenseDetailResponse

Why "read-only"
---------------
Unlike vulnerability findings, license findings carry no analyst workflow
(no status transitions, no audit log). The ORT ruleset declares the
authoritative `category` (allowed / conditional / forbidden / unknown) and the
`kind` (declared / concluded / detected) — both are produced by the scan
pipeline and are immutable once persisted. This module therefore exposes only
GET shapes; there is no PATCH counterpart.

Closed enum mirrors
-------------------
We mirror the closed Postgres ENUMs as Pydantic Literals so OpenAPI advertises
a precise enum (rather than a free string). The values are sourced from
``models.scan.LICENSE_CATEGORY_VALUES`` / ``LICENSE_FINDING_KIND_VALUES`` —
keep in lock-step with the DB layer.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

LicenseCategory = Literal["allowed", "conditional", "forbidden", "unknown"]
LicenseFindingKind = Literal["declared", "concluded", "detected"]

# AI-relevant restriction class a human must review (Phase D). Mirrors
# ``services.license_flags.REVIEW_FLAG_VALUES`` — the two are reconciled in
# ``tests/unit/test_catalog_contracts.py`` (testing standard §2: same vocabulary
# in ≥2 places ⇒ contract test). ``None`` on the wire means "not in scope".
ReviewFlag = Literal["behavioral_use", "non_commercial"]


# ---------------------------------------------------------------------------
# List endpoint
# ---------------------------------------------------------------------------


class LicenseListItem(BaseModel):
    """One row in the Licenses tab table.

    A row represents a single license observed in the project's latest scan,
    aggregated across all component_versions that carry it. ``id`` carries the
    license_findings row id of a representative finding (usually the
    lexicographically first by source_path) so the drawer endpoint has a
    stable handle to dereference.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(
        description=(
            "license_findings.id of a representative finding for this license "
            "in the latest scan. Used as the drawer's primary key."
        ),
    )
    license_id: uuid.UUID = Field(description="licenses.id (catalog row).")
    spdx_id: str | None = Field(
        default=None,
        description=(
            "SPDX short identifier (MIT, Apache-2.0, GPL-3.0-only, ...). "
            "Null for ORT custom licenses (LicenseRef-*)."
        ),
    )
    name: str
    category: LicenseCategory
    kind: LicenseFindingKind = Field(
        description=(
            "ORT classification kind on the representative finding "
            "(declared / concluded / detected)."
        ),
    )
    affected_count: int = Field(
        ge=1,
        description="Distinct component_versions in the latest scan that carry this license.",
    )
    is_osi_approved: bool = False
    is_fsf_libre: bool = False
    review_flag: ReviewFlag | None = Field(
        default=None,
        description=(
            "AI-relevant restriction class requiring human review "
            "(behavioral_use = RAIL/Llama/Gemma/Falcon community licenses; "
            "non_commercial = CC-BY-NC…). Null for ordinary OSS licenses. The "
            "portal only surfaces the class; applicability is a human/legal "
            "judgement."
        ),
    )
    sample_finding_id: uuid.UUID = Field(
        description=(
            "Finding row to pass to GET /v1/license_findings/{id} when the "
            "user opens the drawer. Echoes ``id`` today; a separate field is "
            "kept so frontends can stay forward-compatible if the list shape "
            "ever needs to advertise multiple sample findings."
        ),
    )


class LicenseDistribution(BaseModel):
    """Counts of distinct component_versions per category in the latest scan.

    Always returns all four buckets (zero if absent) so the bar chart renders
    a stable axis. This is the single source of truth shared with the Overview
    tab's ``license_distribution`` — no duplicate aggregator endpoint.
    """

    forbidden: int = Field(ge=0, default=0)
    conditional: int = Field(ge=0, default=0)
    allowed: int = Field(ge=0, default=0)
    unknown: int = Field(ge=0, default=0)


class LicenseListResponse(BaseModel):
    """Page of licenses for the project's latest scan."""

    items: list[LicenseListItem]
    distribution: LicenseDistribution
    total: int = Field(ge=0)


# ---------------------------------------------------------------------------
# Detail / drawer endpoint
# ---------------------------------------------------------------------------


class AffectedComponentByLicense(BaseModel):
    """A component_version that carries the license shown in the drawer."""

    model_config = ConfigDict(from_attributes=True)

    component_version_id: uuid.UUID
    component_name: str
    version: str
    kind: LicenseFindingKind
    source_path: str | None = Field(
        default=None,
        description=(
            "Repo-relative path of the file that produced the finding "
            "(e.g. LICENSE, package.json). Null when ORT did not record one."
        ),
    )


class LicenseDetailResponse(BaseModel):
    """Full drawer payload for a single license_findings row."""

    id: uuid.UUID = Field(description="license_findings.id (the row the URL points at).")
    license_id: uuid.UUID
    spdx_id: str | None = None
    name: str
    category: LicenseCategory
    is_osi_approved: bool = False
    is_fsf_libre: bool = False
    is_deprecated_license_id: bool = False
    review_flag: ReviewFlag | None = Field(
        default=None,
        description=(
            "AI-relevant restriction class requiring human review "
            "(behavioral_use = RAIL/Llama/Gemma/Falcon community licenses; "
            "non_commercial = CC-BY-NC…). Null for ordinary OSS licenses. "
            "Exposed on the detail payload too so the drawer renders the flag "
            "on a direct load / deep link without depending on the list page."
        ),
    )
    reference_url: str | None = None
    finding_kind: LicenseFindingKind = Field(
        description="ORT classification kind on this specific finding row.",
    )
    ort_match: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Best-effort pass-through of license_findings.raw_data. The shape "
            "is not contractual: ORT may include matched-text excerpts, "
            "license-detector confidence, copyright statements, and similar. "
            "Frontends should render this defensively. ``null`` when the "
            "scan pipeline did not emit any raw data for the finding."
        ),
    )
    affected_components: list[AffectedComponentByLicense] = Field(
        default_factory=list,
        description=(
            "All component_versions in the same scan that carry this license, "
            "across every kind (declared / concluded / detected). "
            "Capped at 500 rows — see ``affected_components_truncated``."
        ),
    )
    affected_components_truncated: bool = Field(
        default=False,
        description=(
            "True when the server truncated ``affected_components`` to its "
            "500-row cap. Clients should display a notice and optionally "
            "fall back to the components tab for the full list."
        ),
    )
    affected_components_total: int = Field(
        default=0,
        ge=0,
        description=(
            "Total number of distinct component_versions associated with "
            "this license in the scan, before the response cap is applied."
        ),
    )
    created_at: datetime
    updated_at: datetime


__all__ = [
    "AffectedComponentByLicense",
    "LicenseCategory",
    "LicenseDetailResponse",
    "LicenseDistribution",
    "LicenseFindingKind",
    "LicenseListItem",
    "LicenseListResponse",
]
