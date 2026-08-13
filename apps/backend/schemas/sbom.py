# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
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


class ConformanceGuidance(BaseModel):
    """A fill-in fragment for an element the SBOM does not satisfy."""

    snippet: str = Field(description="CycloneDX fragment that would satisfy the element.")
    docUrl: str | None = Field(  # noqa: N815 — mirrors the vendored key name
        default=None, description="Link to the authoritative field documentation."
    )


class ConformanceReviewNote(BaseModel):
    """What a person has to establish for an element no scan can settle."""

    how: str = Field(description="English review note.")
    how_ko: str | None = Field(default=None, description="Korean review note.")


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
    # Also joined at read time (services/cisa_guidance.py), and only onto rows
    # that are not a pass.
    guidance: ConformanceGuidance | None = Field(
        default=None,
        description=(
            "The CycloneDX fragment that would satisfy this element, for an "
            "element the SBOM does not satisfy. Absent on a pass."
        ),
    )
    review: ConformanceReviewNote | None = Field(
        default=None,
        description=(
            "What a person has to establish for an element no scan can settle. "
            "Absent on a pass."
        ),
    )


AiVerdict = Literal["ok", "conditional", "review", "caution"]


class AiLicenseReason(BaseModel):
    """One declared license and what the registry says about it (gap #28)."""

    license: str = Field(description="The license string as the document spells it.")
    term_key: str | None = Field(
        default=None, description="Registry entry that matched, or null if none did."
    )
    term_name: str | None = None
    verdict: AiVerdict
    summary: str
    summary_ko: str
    conditions: list[str] = Field(
        default_factory=list,
        description=(
            "Condition ids that bind the selected scenario. Resolve against "
            "``condition_labels`` on the assessment."
        ),
    )
    source_url: str | None = None


class AiSubjectVerdict(BaseModel):
    """A model or dataset, its verdict, and the reasons behind it."""

    bom_ref: str
    name: str
    verdict: AiVerdict
    reasons: list[AiLicenseReason] = Field(default_factory=list)
    dataset_refs: list[str] = Field(
        default_factory=list,
        description="Datasets this model declares a dependency on (models only).",
    )
    dataset_verdict: AiVerdict | None = Field(
        default=None,
        description=(
            "Worst verdict across those datasets, or null when the model "
            "declares no dataset edges. Null is not a clean result: the links "
            "were absent, so they were not guessed at."
        ),
    )


class AiRiskAssessmentOut(BaseModel):
    """Usage-scenario verdicts for the models and datasets in one document.

    Advisory. Nothing here reaches a build gate or an approval workflow, and the
    disclaimer travels with the verdicts so a screen cannot show one without the
    other.
    """

    scenario: Literal["internal", "product", "redistribute", "outputs-only"] | None = Field(
        default=None,
        description=(
            "The project's ai_usage_context the verdicts were computed against. "
            "Null means the full license terms were applied."
        ),
    )
    verdict: AiVerdict = Field(description="Worst verdict across every subject.")
    models: list[AiSubjectVerdict] = Field(default_factory=list)
    datasets: list[AiSubjectVerdict] = Field(default_factory=list)
    condition_labels: dict[str, dict[str, str]] = Field(
        default_factory=dict, description="``{condition_id: {en, ko}}``."
    )
    disclaimer: str
    disclaimer_ko: str


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
    # Read-time computed (services.ai_risk_assessment) against the project's
    # ai_usage_context: None when the document carried no model component.
    ai_assessment: AiRiskAssessmentOut | None = None
    created_at: datetime


__all__ = [
    "AiLicenseReason",
    "AiRiskAssessmentOut",
    "AiSubjectVerdict",
    "CrosswalkElement",
    "CrosswalkFramework",
    "RegulationRef",
    "RegulatoryCrosswalk",
    "SbomConformanceCheck",
    "SbomConformanceRead",
]
