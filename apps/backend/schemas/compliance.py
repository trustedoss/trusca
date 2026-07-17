"""
Compliance unified grid schemas — W9-#58 (Compliance unified grid).

A single read endpoint backs the redesigned Compliance tab, which now shows
licenses and their obligations side-by-side in one grid instead of the two
sub-tab surfaces shipped by W4-C #20.

Read-only by design — the underlying data sources (licenses, obligations)
both carry no analyst workflow, so the unified surface is also a pure GET.

Authorization
-------------
- ``ProjectForbidden`` (403) on cross-team. Existence of a project is not a
  secret across teams — mirrors the Licenses tab list contract.

Why a new module
----------------
``schemas.license_detail`` already owns the "one row per license" shape, and
``schemas.obligation_detail`` already owns the "one row per (license, kind)"
shape. The Compliance grid is a JOIN — one row per license, with the
obligations as an embedded array. Keeping the join surface in its own module
mirrors the service split (``services.compliance_service``) and avoids
muddying the per-domain detail shapes.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from schemas.license_detail import LicenseCategory, LicenseDistribution, LicenseFindingKind

# ---------------------------------------------------------------------------
# Affected-component preview (per-row)
# ---------------------------------------------------------------------------


class ComplianceAffectedComponent(BaseModel):
    """A component_version that carries the row's license — preview shape.

    The unified grid embeds at most 5 affected components per row as a
    preview chip strip. The full list ships via the existing license drawer
    (``GET /v1/license_findings/{id}``) — the unified grid keys its drawer
    on the same ``license_finding_id`` so the existing drawer is reused
    verbatim.
    """

    model_config = ConfigDict(from_attributes=True)

    component_version_id: uuid.UUID
    name: str = Field(description="Component name (without version).")
    version: str
    purl: str | None = Field(
        default=None,
        description="Package URL including version, when known.",
    )


# ---------------------------------------------------------------------------
# Embedded obligation (per-row)
# ---------------------------------------------------------------------------


class ComplianceObligation(BaseModel):
    """One obligation attached to the row's license.

    The unified grid embeds the obligation summary inline so the user does
    not need to open a drawer to see "what does this license require?". The
    drawer still exists for the full text + reference URL — keyed by
    ``obligation_id`` so the existing ``GET /v1/projects/{id}/obligations/{id}``
    payload is reused verbatim.
    """

    model_config = ConfigDict(from_attributes=True)

    obligation_id: uuid.UUID
    kind: str = Field(
        max_length=64,
        description=(
            "Obligation kind — free-form catalog string (attribution, "
            "source-disclosure, copyleft, ...)."
        ),
    )
    summary: str = Field(
        description=(
            "Short human-readable summary of the obligation (English). Capped "
            "at 240 chars by the service so the grid row stays compact."
        ),
    )
    summary_ko: str | None = Field(
        default=None,
        description=(
            "Advisory Korean rendering of ``summary``, capped the same way. "
            "Null when the obligation did not come from the catalog — clients "
            "fall back to ``summary``. English remains authoritative."
        ),
    )


# ---------------------------------------------------------------------------
# Grid row
# ---------------------------------------------------------------------------


class ComplianceRow(BaseModel):
    """One row in the unified Compliance grid.

    A row aggregates "one license in the latest scan" with:
      - its category (allowed / conditional / forbidden / unknown);
      - the components affected by it (preview + total count);
      - the obligations attached to it (inline summaries);
      - whether the license requires a NOTICE entry.

    ``license_finding_id`` is the same opaque handle the existing License
    drawer uses — the grid's row-click → drawer cycle keys on this id so the
    drawer renders without a new endpoint.
    """

    model_config = ConfigDict(from_attributes=True)

    license_finding_id: uuid.UUID = Field(
        description=(
            "license_findings.id of a representative finding for this license "
            "in the latest scan. Same handle the existing License drawer "
            "(GET /v1/license_findings/{id}) accepts."
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
    license_name: str
    category: LicenseCategory
    category_source: str = Field(
        default="static",
        description=(
            "Where the row's category came from. ``static`` = catalog default "
            "(``licenses.category``). Reserved values ``policy``/``compound`` "
            "anticipate policy-override semantics (v2.5+); today the service "
            "always emits ``static``."
        ),
    )
    kind: LicenseFindingKind = Field(
        description=(
            "ORT classification kind on the representative finding "
            "(declared / concluded / detected)."
        ),
    )
    affected_component_count: int = Field(
        ge=0,
        description=(
            "Distinct component_versions in the latest scan that carry this "
            "license."
        ),
    )
    affected_components: list[ComplianceAffectedComponent] = Field(
        default_factory=list,
        description=(
            "Preview of up to 5 affected component_versions, ordered by name + "
            "version. Use ``affected_component_count`` for the true total — the "
            "preview is capped so the grid row stays compact. The License drawer "
            "renders the full list."
        ),
    )
    obligations: list[ComplianceObligation] = Field(
        default_factory=list,
        description=(
            "Obligations attached to this license, ordered by kind. Empty when "
            "the catalog records none for this license."
        ),
    )
    notice_required: bool = Field(
        default=False,
        description=(
            "True when this license carries an ``attribution`` or ``notice`` "
            "obligation — the project owes a NOTICE entry for it. Derived from "
            "``obligations`` so the grid can flag rows without a second trip."
        ),
    )
    category_override_source: str | None = Field(
        default=None,
        description=(
            "Reserved for v2.5+ policy-override surfacing. Today always "
            "``null``; clients should render an override badge only when the "
            "value is non-null."
        ),
    )


# ---------------------------------------------------------------------------
# List response
# ---------------------------------------------------------------------------


class ComplianceListResponse(BaseModel):
    """Page of compliance rows + the project-wide category distribution."""

    items: list[ComplianceRow]
    distribution: LicenseDistribution = Field(
        description=(
            "Unfiltered category counts for the underlying scan. Stable across "
            "pagination + filters so the chart axis does not jump."
        ),
    )
    total: int = Field(
        ge=0,
        description="Total rows matching the active filter, pre-pagination.",
    )
    limit: int = Field(ge=1, le=500)
    offset: int = Field(ge=0)
    generated_at: datetime = Field(
        description=(
            "Server clock when the response was assembled. Echoed so clients can "
            "tag a cached page."
        ),
    )


__all__ = [
    "ComplianceAffectedComponent",
    "ComplianceListResponse",
    "ComplianceObligation",
    "ComplianceRow",
]
