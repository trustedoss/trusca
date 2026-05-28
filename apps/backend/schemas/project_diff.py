"""
Release-diff schemas — feature #28 Phase 2 (compare two release snapshots).

A "release snapshot" is one *succeeded* scan: each succeeded scan is an immutable
snapshot of a project's SCA posture (its ``scan_components`` /
``vulnerability_findings`` / ``license_findings`` keyed by ``scan_id``). Phase 1
(``GET /v1/projects/{id}/releases``) lets the user see and pin those snapshots;
this Phase 2 schema models the DIFF between two of them.

``GET /v1/projects/{id}/diff?base=<scan_id>&target=<scan_id>`` returns a
``ProjectDiff``: the two snapshot anchors, a side-by-side summary (risk score,
severity component-counts, build-gate verdict, component count), and three change
sets — components (added / removed / changed), vulnerabilities (introduced /
resolved), and licenses (per-category base/target deltas). Typical usage:
``base`` = older release (v0.1), ``target`` = newer release (v0.2).

Semantics (computed in ``services.project_diff_service``):
  - components identity = the ``component`` *package* (group/name/purl-without-
    version). added = package in target not in base; removed = package in base
    not in target; changed = same package, different ``component_version``.
  - vulnerabilities use the OPEN/active finding set (a finding suppressed/
    resolved via VEX — ``not_affected`` / ``false_positive`` / ``suppressed`` /
    ``fixed`` — counts as NOT open). introduced = open-in-target minus
    open-in-base, keyed by (cve_id, component_version); resolved = the converse.
  - summary reuses the SAME per-scan aggregations the Releases table / Overview
    tab use, so the diff can never disagree with them for the same pinned scan.

All field names are snake_case (CLAUDE.md §1.2 RFC 7807 / OpenAPI convention).
The schemas are registered in OpenAPI via the endpoint's ``response_model``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# The build-gate verdict for a snapshot. ``null`` means the gate was not
# evaluable for that scan (should not happen for a real succeeded snapshot, but
# the field stays optional so the contract is honest).
GateStatus = Literal["pass", "fail"]


class DiffSnapshotRef(BaseModel):
    """One side (base or target) of the comparison — which succeeded scan it is."""

    model_config = ConfigDict(from_attributes=True)

    scan_id: uuid.UUID = Field(
        description="The succeeded scan that IS this side of the diff."
    )
    release: str | None = Field(
        default=None,
        description=(
            "Optional release/version label from the scan's ``metadata.release`` "
            "(e.g. 'v0.1'). Non-unique and often absent (null)."
        ),
    )
    created_at: datetime = Field(
        description="When this scan was created."
    )


class DiffScalarDelta(BaseModel):
    """A single scalar metric compared across the two snapshots (base vs target).

    ``base`` / ``target`` may be null when the metric is not aggregable for that
    side (e.g. ``risk_score`` if a snapshot is somehow not aggregable — should not
    occur for a real succeeded scan).
    """

    model_config = ConfigDict(from_attributes=True)

    base: float | None = Field(default=None)
    target: float | None = Field(default=None)


class DiffIntDelta(BaseModel):
    """A single integer metric (a count) compared across the two snapshots."""

    model_config = ConfigDict(from_attributes=True)

    base: int = Field(default=0, ge=0)
    target: int = Field(default=0, ge=0)


class DiffGateDelta(BaseModel):
    """The build-gate verdict compared across the two snapshots."""

    model_config = ConfigDict(from_attributes=True)

    base: GateStatus | None = Field(default=None)
    target: GateStatus | None = Field(default=None)


class DiffSeverityDelta(BaseModel):
    """Worst-per-component vuln-severity counts compared across the two snapshots.

    Each bucket carries a ``{base, target}`` pair so the UI can render the delta.
    Counts are the number of component_versions whose WORST CVE finding lands in
    each bucket — the SAME worst-per-component bucketing the Releases table /
    Overview tab use, so the diff summary can never disagree with them. Only the
    four risk-bearing buckets are surfaced (``info`` / ``none`` are not
    actionable on a summary).
    """

    model_config = ConfigDict(from_attributes=True)

    critical: DiffIntDelta = Field(default_factory=DiffIntDelta)
    high: DiffIntDelta = Field(default_factory=DiffIntDelta)
    medium: DiffIntDelta = Field(default_factory=DiffIntDelta)
    low: DiffIntDelta = Field(default_factory=DiffIntDelta)


class DiffLicenseCategoryDelta(BaseModel):
    """Per-license-category component counts compared across the two snapshots.

    Each category carries a ``{base, target}`` pair so the UI can show how the
    license-risk distribution shifted between the two releases. Counts are the
    number of component_versions whose WORST license category lands in each
    bucket (same worst-per-component bucketing the license distribution uses).
    """

    model_config = ConfigDict(from_attributes=True)

    prohibited: DiffIntDelta = Field(
        default_factory=DiffIntDelta,
        description="Forbidden-category components (GPL/AGPL/SSPL/BUSL …).",
    )
    conditional: DiffIntDelta = Field(default_factory=DiffIntDelta)
    permissive: DiffIntDelta = Field(
        default_factory=DiffIntDelta,
        description="Allowed/permissive-category components (MIT/Apache-2.0 …).",
    )
    unknown: DiffIntDelta = Field(default_factory=DiffIntDelta)


class DiffSummary(BaseModel):
    """Side-by-side per-snapshot summary (base vs target) for the diff header."""

    model_config = ConfigDict(from_attributes=True)

    risk_score: DiffScalarDelta = Field(
        default_factory=DiffScalarDelta,
        description=(
            "Overall risk 0–100 (max of security/license axis) for each snapshot, "
            "using the SAME non-saturating scorer the Overview / Releases use."
        ),
    )
    severity: DiffSeverityDelta = Field(default_factory=DiffSeverityDelta)
    gate: DiffGateDelta = Field(default_factory=DiffGateDelta)
    component_count: DiffIntDelta = Field(
        default_factory=DiffIntDelta,
        description="Distinct component_versions observed in each snapshot.",
    )


class DiffComponentAdded(BaseModel):
    """A package present in target but NOT in base (newly introduced)."""

    model_config = ConfigDict(from_attributes=True)

    name: str
    namespace: str | None = Field(default=None)
    purl: str = Field(
        description="The component_version's full purl (purl-with-version) in target."
    )
    version: str


class DiffComponentRemoved(BaseModel):
    """A package present in base but NOT in target (removed, e.g. log4j dropped)."""

    model_config = ConfigDict(from_attributes=True)

    name: str
    namespace: str | None = Field(default=None)
    purl: str = Field(
        description="The component_version's full purl (purl-with-version) in base."
    )
    version: str


class DiffComponentChanged(BaseModel):
    """The same package present in BOTH snapshots at a DIFFERENT version (bump)."""

    model_config = ConfigDict(from_attributes=True)

    name: str
    namespace: str | None = Field(default=None)
    purl: str = Field(
        description=(
            "The component package's purl WITHOUT version (the stable package "
            "identity shared by base_version and target_version)."
        )
    )
    base_version: str
    target_version: str


class DiffComponents(BaseModel):
    """The three component change sets between base and target."""

    model_config = ConfigDict(from_attributes=True)

    added: list[DiffComponentAdded] = Field(default_factory=list)
    removed: list[DiffComponentRemoved] = Field(default_factory=list)
    changed: list[DiffComponentChanged] = Field(default_factory=list)


class DiffVulnerability(BaseModel):
    """One (cve, component_version) finding that changed open-status between snapshots."""

    model_config = ConfigDict(from_attributes=True)

    cve_id: str = Field(description="The CVE / advisory external id (e.g. 'CVE-2021-44228').")
    severity: str = Field(
        description="The CVE severity bucket (critical/high/medium/low/info/unknown)."
    )
    component_name: str
    component_version: str


class DiffVulnerabilities(BaseModel):
    """Vulnerabilities introduced / resolved between base and target (OPEN set)."""

    model_config = ConfigDict(from_attributes=True)

    introduced: list[DiffVulnerability] = Field(
        default_factory=list,
        description="Open in target, not open in base (newly introduced exposure).",
    )
    resolved: list[DiffVulnerability] = Field(
        default_factory=list,
        description="Was open in base, gone/closed in target (resolved exposure).",
    )


class DiffLicenses(BaseModel):
    """License change view — per-category base/target component-count deltas."""

    model_config = ConfigDict(from_attributes=True)

    category_delta: DiffLicenseCategoryDelta = Field(
        default_factory=DiffLicenseCategoryDelta
    )


class ProjectDiff(BaseModel):
    """The full diff between two succeeded-scan snapshots of one project."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "base": {
                    "scan_id": "3c15c82f-c409-4f5f-b7d9-92bca8cc1f7f",
                    "release": "v0.1",
                    "created_at": "2026-05-20T10:00:00Z",
                },
                "target": {
                    "scan_id": "50b3d477-2211-47a3-947b-69022dabb2b3",
                    "release": None,
                    "created_at": "2026-05-22T10:00:00Z",
                },
                "summary": {
                    "risk_score": {"base": 0.0, "target": 92.9},
                    "severity": {
                        "critical": {"base": 0, "target": 10},
                        "high": {"base": 0, "target": 8},
                        "medium": {"base": 0, "target": 20},
                        "low": {"base": 0, "target": 5},
                    },
                    "gate": {"base": "pass", "target": "fail"},
                    "component_count": {"base": 0, "target": 88},
                },
                "components": {
                    "added": [
                        {
                            "name": "log4j-core",
                            "namespace": "org.apache.logging.log4j",
                            "purl": "pkg:maven/org.apache.logging.log4j/log4j-core@2.14.1",
                            "version": "2.14.1",
                        }
                    ],
                    "removed": [],
                    "changed": [
                        {
                            "name": "lodash",
                            "namespace": None,
                            "purl": "pkg:npm/lodash",
                            "base_version": "4.17.20",
                            "target_version": "4.17.21",
                        }
                    ],
                },
                "vulnerabilities": {
                    "introduced": [
                        {
                            "cve_id": "CVE-2021-44228",
                            "severity": "critical",
                            "component_name": "log4j-core",
                            "component_version": "2.14.1",
                        }
                    ],
                    "resolved": [],
                },
                "licenses": {
                    "category_delta": {
                        "prohibited": {"base": 0, "target": 1},
                        "conditional": {"base": 0, "target": 2},
                        "permissive": {"base": 0, "target": 80},
                        "unknown": {"base": 0, "target": 5},
                    }
                },
                "truncated": False,
            }
        }
    )

    base: DiffSnapshotRef
    target: DiffSnapshotRef
    summary: DiffSummary = Field(default_factory=DiffSummary)
    components: DiffComponents = Field(default_factory=DiffComponents)
    vulnerabilities: DiffVulnerabilities = Field(default_factory=DiffVulnerabilities)
    licenses: DiffLicenses = Field(default_factory=DiffLicenses)
    truncated: bool = Field(
        default=False,
        description=(
            "True when any change-set list (components added/removed/changed, "
            "vulnerabilities introduced/resolved) was capped at the defensive "
            "per-list limit. The summary counts are always exact; only the "
            "enumerated lists are truncated."
        ),
    )


__all__ = [
    "DiffComponentAdded",
    "DiffComponentChanged",
    "DiffComponentRemoved",
    "DiffComponents",
    "DiffGateDelta",
    "DiffIntDelta",
    "DiffLicenseCategoryDelta",
    "DiffLicenses",
    "DiffScalarDelta",
    "DiffSeverityDelta",
    "DiffSnapshotRef",
    "DiffSummary",
    "DiffVulnerabilities",
    "DiffVulnerability",
    "GateStatus",
    "ProjectDiff",
]
