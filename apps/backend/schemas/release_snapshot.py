"""
Release-snapshot schemas — feature #28 Phase 1 (release snapshot viewing).

A "release snapshot" is one *succeeded* scan: each succeeded scan is an immutable
snapshot of a project's SCA posture (its ``scan_components`` /
``vulnerability_findings`` / ``license_findings`` keyed by ``scan_id``). A scan
may carry an optional ``metadata.release`` label (e.g. "v1.2.3", non-unique,
often absent) the user attached when triggering it.

``GET /v1/projects/{id}/releases`` returns the project's succeeded scans
(most-recent first), one row per snapshot, each with a per-scan summary so the
frontend can render a Releases table and let the user pin a specific snapshot
(via the ``?scan_id=`` anchor the detail endpoints now accept).

Diff / compare between two releases is a LATER phase and is intentionally NOT
modelled here.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# The build-gate verdict for a snapshot. ``null`` means the gate was not
# evaluable for that scan (it never resolved a succeeded scan — should not happen
# for a row that IS a succeeded scan, but the field stays optional so the
# contract is honest if the gate ever returns no verdict).
GateStatus = Literal["pass", "fail"]


class ReleaseSeveritySummary(BaseModel):
    """Per-snapshot vulnerability-severity *component* counts.

    Counts are the number of component_versions whose WORST open CVE finding
    lands in each bucket, computed over THAT scan (``WHERE scan_id = <row>``) —
    the same worst-per-component bucketing the project-list badge / dashboard /
    overview use, so the Releases table can never disagree with the Overview tab
    for the same pinned scan. Only the four risk-bearing buckets are surfaced
    (``info`` / ``none`` are not actionable on a release row). All four keys are
    always present (zero when the snapshot carried no findings in that bucket).
    """

    model_config = ConfigDict(from_attributes=True)

    critical: int = Field(default=0, ge=0)
    high: int = Field(default=0, ge=0)
    medium: int = Field(default=0, ge=0)
    low: int = Field(default=0, ge=0)


class ReleaseSnapshot(BaseModel):
    """One succeeded scan, summarised as a release snapshot row."""

    model_config = ConfigDict(from_attributes=True)

    scan_id: uuid.UUID = Field(
        description="The succeeded scan that IS this snapshot. Use as the "
        "``?scan_id=`` anchor on the detail endpoints to pin this release."
    )
    release: str | None = Field(
        default=None,
        description=(
            "Optional release/version label from the scan's ``metadata.release`` "
            "(e.g. 'v1.2.3'). Non-unique and often absent (null)."
        ),
    )
    created_at: datetime = Field(
        description="When this scan was created (snapshots are ordered newest-first by this)."
    )
    risk_score: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description=(
            "Overall risk 0–100 for this snapshot = max(security, license) axis, "
            "using the SAME non-saturating scorer as the Overview tab "
            "(services.risk_score). Null only if the snapshot is not aggregable "
            "(should not occur for a succeeded scan)."
        ),
    )
    severity_summary: ReleaseSeveritySummary = Field(
        description="Worst-per-component vuln-severity counts for this snapshot."
    )
    gate_status: GateStatus | None = Field(
        default=None,
        description=(
            "The build-gate verdict for THIS snapshot (same evaluation the "
            "gate-result endpoint runs, pinned to this scan): 'pass' / 'fail', or "
            "null if not evaluable."
        ),
    )
    component_count: int = Field(
        ge=0,
        description="Distinct component_versions observed in this snapshot.",
    )


class ReleaseListResponse(BaseModel):
    """Paginated list of a project's release snapshots (succeeded scans)."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "items": [
                    {
                        "scan_id": "7822b62d-9156-423d-9df6-5e51f546fbe8",
                        "release": "v1.2.3",
                        "created_at": "2026-05-22T10:00:00Z",
                        "risk_score": 92.9,
                        "severity_summary": {
                            "critical": 10,
                            "high": 4,
                            "medium": 2,
                            "low": 1,
                        },
                        "gate_status": "fail",
                        "component_count": 42,
                    }
                ],
                "total": 1,
                "page": 1,
                "size": 20,
            }
        }
    )

    items: list[ReleaseSnapshot] = Field(default_factory=list)
    total: int = Field(ge=0, description="Total succeeded scans for the project (pre-pagination).")
    page: int = Field(ge=1)
    size: int = Field(ge=1, le=100)


__all__ = [
    "GateStatus",
    "ReleaseListResponse",
    "ReleaseSeveritySummary",
    "ReleaseSnapshot",
]
