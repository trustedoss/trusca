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


class RegulationRef(BaseModel):
    """One crosswalk reference joined onto a check (services.regulation_crosswalk).

    Informational only — which regulatory obligation the check's subject
    touches. Never a compliance determination (see the crosswalk disclaimer).
    """

    framework: str = Field(description="Framework id (e.g. 'bsi-tr-03183-2').")
    ref: str = Field(description="Section / article the check maps to.")
    basis: str = Field(description="Interpretive basis for the link, quoted from the crosswalk.")
    short: str = Field(description="Short framework display name (EN).")
    short_ko: str = Field(description="Short framework display name (KO).")


class CrosswalkElement(BaseModel):
    """A mapped check inside one framework's rollup row."""

    id: str
    label: str
    status: Literal["pass", "fail", "warn"]
    source: str | None = None
    detail: str
    refs: list[str] = Field(default_factory=list)


class CrosswalkFramework(BaseModel):
    """Per-framework rollup: how well this SBOM would answer that regulator."""

    id: str
    title: str
    title_ko: str
    short: str
    short_ko: str
    source: str
    total: int
    present: int = Field(description="Mapped checks that pass.")
    gap: int = Field(description="Mapped checks warning with an automated source.")
    review: int = Field(description="Mapped checks answerable only by a human (source 'na').")
    elements: list[CrosswalkElement] = Field(default_factory=list)


class RegulatoryCrosswalk(BaseModel):
    """The crosswalk summary block — documentation-preparation aid, not a verdict."""

    disclaimer: str
    disclaimer_ko: str
    frameworks: list[CrosswalkFramework] = Field(default_factory=list)


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
    # G7 AI-SBOM advisory extensions (services.g7_conformance) — present only
    # on the g7-* checks an ML-BOM ingest appends; None on the core checks.
    cluster: str | None = Field(
        default=None,
        description=(
            "G7 cluster id (metadata / slp / models / dp / infrastructure / "
            "sp / kpi); None for the core conformance checks."
        ),
    )
    source: str | None = Field(
        default=None,
        description=(
            "G7 registry satisfaction source: auto | inferred | declared | na."
        ),
    )
    role: str | None = Field(
        default=None,
        description=(
            "Party the G7 text names as the element's provider "
            "(informational, not a required/optional gate)."
        ),
    )
    evidence: list[str] | None = Field(
        default=None,
        description=(
            "Extracted values for a satisfied G7 element (e.g. model PURLs, "
            "license ids) — at most 8 items, each clamped to 200 chars."
        ),
    )
    # Joined at read time by the conformance endpoint (never persisted — the
    # crosswalk is a static vendored catalogue, storing it per row would be
    # denormalised noise). None when the join was not applied.
    regulations: list[RegulationRef] | None = Field(
        default=None,
        description=(
            "Regulatory references this check's subject maps to "
            "(services/regulation_crosswalk.json). Empty list = no mapping; "
            "informational only."
        ),
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
    # Read-time computed (services.regulation_crosswalk) — None when nothing
    # maps (unknown-format rows) so old consumers see no shape change.
    regulatory_crosswalk: RegulatoryCrosswalk | None = None
    created_at: datetime


__all__ = [
    "CrosswalkElement",
    "CrosswalkFramework",
    "RegulationRef",
    "RegulatoryCrosswalk",
    "SbomConformanceCheck",
    "SbomConformanceRead",
]
