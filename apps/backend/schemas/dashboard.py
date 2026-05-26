"""
Dashboard summary schema — portfolio overview aggregate.

Backs a single new endpoint::

    GET /v1/dashboard/summary  → DashboardSummary

The app root ``/`` previously just redirected to ``/projects``; this is the
data contract for a real portfolio-overview page. Every aggregate is scoped to
the caller's *accessible projects only* (see ``services.dashboard_service`` for
the BOLA/IDOR-sensitive scoping logic) — the schema itself carries no scoping
intelligence, it only describes the shape.

Enum / literal alignment
------------------------
The bucket keys here mirror the persisted enum vocabularies exactly, so the
frontend never has to translate:

- severity buckets  → ``models.scan.VULN_SEVERITY_VALUES`` collapsed to the
  five display buckets (``unknown`` folds into ``info`` — a CVE whose severity
  we don't know should never read as a green ribbon, matching
  ``services.project_detail_service``).
- license buckets   → ``models.scan.LICENSE_CATEGORY_VALUES``
  (``prohibited`` is this product's UI label for the persisted ``forbidden``
  category; ``conditional`` / ``permissive`` map to ``conditional`` /
  ``allowed``; ``unknown`` stays ``unknown``).
- scan-status buckets → ``models.scan.SCAN_STATUS_VALUES`` (we surface the four
  operationally interesting states; ``cancelled`` is intentionally omitted from
  the headline counts — a cancelled scan is neither in-flight nor a result).

Every bucket is always present (zero-filled) so the frontend can render stable
gauges/donuts without null-guarding individual keys.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Bucket sub-models (fixed-key count maps)
# ---------------------------------------------------------------------------


class ScanStatusCounts(BaseModel):
    """Scan counts by lifecycle state over the caller's accessible projects.

    ``cancelled`` scans are deliberately excluded — they are neither in-flight
    nor a result, so they do not belong in a portfolio headline.
    """

    queued: int = Field(default=0, ge=0)
    running: int = Field(default=0, ge=0)
    succeeded: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)


class VulnerabilitySeverityCounts(BaseModel):
    """Component findings by worst severity, over each project's latest succeeded scan.

    A finding's persisted severity of ``unknown`` is folded into ``info``.
    """

    critical: int = Field(default=0, ge=0)
    high: int = Field(default=0, ge=0)
    medium: int = Field(default=0, ge=0)
    low: int = Field(default=0, ge=0)
    info: int = Field(default=0, ge=0)


class LicenseCategoryCounts(BaseModel):
    """Component license verdicts over each project's latest succeeded scan.

    ``prohibited`` is the UI label for the persisted ``forbidden`` category;
    ``permissive`` is the UI label for ``allowed``. ``conditional`` and
    ``unknown`` keep their persisted names.
    """

    prohibited: int = Field(default=0, ge=0)
    conditional: int = Field(default=0, ge=0)
    permissive: int = Field(default=0, ge=0)
    unknown: int = Field(default=0, ge=0)


# ---------------------------------------------------------------------------
# Recent-scan row
# ---------------------------------------------------------------------------


class RecentScan(BaseModel):
    """One row in the dashboard's recent-scans feed (newest first)."""

    model_config = ConfigDict(from_attributes=True)

    scan_id: uuid.UUID
    project_id: uuid.UUID
    project_name: str
    status: str = Field(
        description="Scan status enum value (queued|running|succeeded|failed|cancelled).",
    )
    kind: str = Field(description="Scan kind enum value (source|container).")
    finished_at: datetime | None = Field(
        default=None,
        description=(
            "When the scan reached a terminal state (``scans.completed_at``). "
            "``null`` for a scan that is still queued or running."
        ),
    )
    release: str | None = Field(
        default=None,
        description=(
            "Feature #18 Part A — the optional release/version label the scan was "
            "triggered with (e.g. \"v1.2.3\"), read from ``scans.metadata.release``. "
            "``null`` when the scan carried no release label."
        ),
    )


# ---------------------------------------------------------------------------
# Top-level summary
# ---------------------------------------------------------------------------


class DashboardSummary(BaseModel):
    """Portfolio overview for the caller's accessible projects.

    All aggregates are scoped to projects the caller may read: super-admins see
    every project; everyone else sees only projects whose ``team_id`` is one of
    their team memberships. A user must never see counts that include another
    team's projects.
    """

    project_count: int = Field(
        ge=0,
        description="Accessible, non-archived projects.",
    )
    scan_status_counts: ScanStatusCounts = Field(default_factory=ScanStatusCounts)
    vulnerability_severity_counts: VulnerabilitySeverityCounts = Field(
        default_factory=VulnerabilitySeverityCounts
    )
    license_category_counts: LicenseCategoryCounts = Field(
        default_factory=LicenseCategoryCounts
    )
    pending_approvals_count: int = Field(
        ge=0,
        description=(
            "Open component approvals (status in {'pending','under_review'}) "
            "for accessible projects."
        ),
    )
    recent_scans: list[RecentScan] = Field(
        default_factory=list,
        description="The 10 most recent scans across accessible projects, newest first.",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "project_count": 7,
                "scan_status_counts": {
                    "queued": 1,
                    "running": 0,
                    "succeeded": 12,
                    "failed": 2,
                },
                "vulnerability_severity_counts": {
                    "critical": 3,
                    "high": 9,
                    "medium": 22,
                    "low": 41,
                    "info": 5,
                },
                "license_category_counts": {
                    "prohibited": 1,
                    "conditional": 4,
                    "permissive": 180,
                    "unknown": 12,
                },
                "pending_approvals_count": 2,
                "recent_scans": [
                    {
                        "scan_id": "3f1d8c2a-0000-0000-0000-000000000001",
                        "project_id": "9a2b7e10-0000-0000-0000-000000000002",
                        "project_name": "payments-api",
                        "status": "succeeded",
                        "kind": "source",
                        "finished_at": "2026-05-25T09:14:00Z",
                    }
                ],
            }
        }
    )


__all__ = [
    "DashboardSummary",
    "LicenseCategoryCounts",
    "RecentScan",
    "ScanStatusCounts",
    "VulnerabilitySeverityCounts",
]
