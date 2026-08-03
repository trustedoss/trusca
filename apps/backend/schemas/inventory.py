# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Organization-wide inventory wire shapes (S2).

The per-project Components tab answers "what is in this project". These shapes
answer the question every audited SCA tool surfaces and this one did not:
"where in the organization is this package, and what does it drag in with it".

Row grain
---------
``InventoryComponentRow`` is one **component** (a purl without a version), not
one component-version. A package used at three versions across nine projects is
one row that reports both spreads. That is the grain the question is asked at —
"do we use log4j anywhere" — and it is what makes the list short enough to scan.
Per-version detail is one click away on the reverse-lookup endpoints.

Field naming mirrors ``schemas/project_detail.py`` so the frontend's existing
severity / license-category unions carry over unchanged.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from schemas.project_detail import ComponentSeverity, LicenseCategoryName


class InventoryComponentRow(BaseModel):
    """One package in organization-wide use."""

    component_id: uuid.UUID
    name: str
    purl: str
    package_type: str

    # --- spread -----------------------------------------------------------
    project_count: int = Field(
        description="Distinct non-archived projects whose current scan contains this package."
    )
    version_count: int = Field(
        description="Distinct versions of this package in use across those projects."
    )
    versions: list[str] = Field(
        default_factory=list,
        description=(
            "Up to VERSION_SAMPLE_LIMIT versions in use, ascending. A truncated "
            "sample when version_count exceeds it — the row reports the true "
            "count separately so the list never implies it showed everything."
        ),
    )

    # --- risk rollup ------------------------------------------------------
    severity_max: ComponentSeverity = Field(
        description="Worst CVE severity observed on any in-use version of this package."
    )
    vulnerability_count: int = Field(
        description="Distinct CVE findings across every in-use version, de-duplicated."
    )
    license_category_max: LicenseCategoryName = Field(
        description="Most restrictive license category observed on any in-use version."
    )

    # --- lifecycle --------------------------------------------------------
    eol: bool = Field(description="True when any in-use version is end-of-life.")
    outdated: bool = Field(description="True when any in-use version is behind the latest.")


class InventoryComponentListResponse(BaseModel):
    """Paginated inventory page.

    ``limit`` / ``offset`` rather than ``page`` / ``size``: the surface is an
    infinite-scrolling virtualized table (the same shape the Components tab
    uses), and the frontend's ``useInfiniteQuery`` reads ``offset + len(items)``
    against ``total`` to decide whether another page exists.
    """

    items: list[InventoryComponentRow]
    total: int
    limit: int
    offset: int


class InventoryProjectUsage(BaseModel):
    """One project that uses a given component — reverse lookup row."""

    project_id: uuid.UUID
    project_name: str
    project_slug: str
    version: str
    direct: bool = Field(
        description="True when this project depends on the package directly."
    )
    scan_id: uuid.UUID = Field(
        description="The scan this reading came from — the project's current-state scan."
    )
    scanned_at: datetime


class InventoryProjectUsageListResponse(BaseModel):
    items: list[InventoryProjectUsage]
    total: int
    limit: int
    offset: int


class InventoryVulnerabilityImpact(BaseModel):
    """One project affected by a given CVE — reverse lookup row."""

    project_id: uuid.UUID
    project_name: str
    project_slug: str
    component_name: str
    purl: str
    version: str
    finding_id: uuid.UUID = Field(
        description="The finding row, so the caller can deep-link to its detail page."
    )
    status: str
    severity: ComponentSeverity


class InventoryVulnerabilityImpactResponse(BaseModel):
    """Impact of one CVE across the organization.

    ``severity`` is carried on the envelope as well as each row because the CVE
    has one severity; repeating it per row is for the table, and the envelope is
    what a header renders without reading into the list.
    """

    external_id: str
    severity: ComponentSeverity
    items: list[InventoryVulnerabilityImpact]
    total: int
    limit: int
    offset: int


__all__ = [
    "InventoryComponentListResponse",
    "InventoryComponentRow",
    "InventoryProjectUsage",
    "InventoryProjectUsageListResponse",
    "InventoryVulnerabilityImpact",
    "InventoryVulnerabilityImpactResponse",
]
