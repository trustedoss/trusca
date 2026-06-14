"""
Pydantic schemas for the received-SBOM surface — model 3.

``SbomConformanceRead`` is the wire shape of a :class:`models.SbomConformance`
row: the quality verdict the ingest pipeline computed for an uploaded SBOM. The
portal renders ``result`` as a pass / warn / fail badge and ``checks`` as the
per-requirement detail table. The check-id set mirrors
``services.sbom_conformance.CHECK_IDS`` (a contract test keeps the FE mirror in
lockstep).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SbomConformanceCheck(BaseModel):
    """One requirement's verdict within a conformance result."""

    id: str = Field(description="Stable check id (see sbom_conformance.CHECK_IDS).")
    label: str = Field(description="Human-readable requirement label.")
    required: bool = Field(
        description="True for a mandatory check, False for a recommended (warn-only) one."
    )
    status: Literal["pass", "fail", "warn"]
    detail: str = Field(description="Short evidence string (e.g. '96% (32/33)').")
    missing: list[str] = Field(
        default_factory=list,
        description="Offending item names for a failed check, capped at 50.",
    )


class SbomConformanceRead(BaseModel):
    """The conformance verdict for an ingested SBOM scan."""

    model_config = ConfigDict(from_attributes=True)

    scan_id: uuid.UUID
    project_id: uuid.UUID
    source_format: Literal["cyclonedx", "spdx-json", "spdx-tv", "unknown"]
    result: Literal["pass", "warn", "fail"]
    n_fail: int
    n_warn: int
    component_count: int
    # NULL for SPDX Tag-Value (scored on presence; per-package coverage absent).
    purl_coverage_pct: int | None = None
    license_coverage_pct: int | None = None
    hash_coverage_pct: int | None = None
    checks: list[SbomConformanceCheck] = Field(default_factory=list)
    created_at: datetime


__all__ = [
    "SbomConformanceCheck",
    "SbomConformanceRead",
]
