"""
Report-download history schemas — W3 #32a-2 (Reports center, emit + list half).

These shapes back the new ``GET /v1/projects/{project_id}/reports/history``
surface and underpin the four download endpoints (NOTICE / SBOM / vulnerability
PDF / VEX export) that emit one row apiece.

Design notes
------------
- The wire enum ``ReportType`` mirrors ``models.report_download.REPORT_TYPE_VALUES``
  (the closed Postgres ENUM ``report_type_enum``). Redeclaring it as a Literal
  here gives OpenAPI a clean ``enum`` rather than a free string; the model side
  remains the single source of persistence truth.
- The list response uses the same ``items / total / page / page_size`` shape as
  ``NotificationListResponse`` and ``AuditLogListPage`` so frontends share one
  pagination harness.
- ``client_ip`` and ``user_agent`` columns deliberately do NOT appear here —
  CLAUDE.md §5 keeps operational PII out of API responses (the row carries them
  for forensic queries only). The emit helper also runs the UA through
  :func:`core.pii_mask.mask_pii` before it reaches the DB.
- ``user`` is a small inline summary (id + email) rather than a foreign-id +
  separate ``user_email`` pair, matching the per-row "actor at this moment"
  surface the Reports tab needs. ``None`` means the actor row was deleted (the
  FK is ``ON DELETE SET NULL``) — the history fact survives the user.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Mirrors models.report_download.REPORT_TYPE_VALUES. Kept as a Literal here so
# OpenAPI emits a clean enum and FastAPI Query() validation rejects unknown
# tokens with a 422 RFC 7807 envelope before the service runs.
ReportType = Literal["notice", "sbom", "vuln_pdf", "vex_export"]


class ReportDownloadUserSummary(BaseModel):
    """Small per-row actor summary for the history list response.

    Mirrors the ``actor_email`` / ``actor_user_id`` shape used by the admin
    audit log search response (``schemas.admin_ops.AuditLogItem``) but packed
    into one nested object so the frontend can render the cell with a single
    truthiness check.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str


class ReportDownloadEntry(BaseModel):
    """One row in the Reports history list response.

    Frozen contract — the SPA's Reports tab depends on every field name. Add
    new optional fields as nullable; do not rename. ``size_bytes`` is None
    when the emit happened before the bytes were materialised (e.g. a future
    streaming surface) — current emit sites always know the size.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    scan_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "The scan that produced this artefact. NULL for ``vex_export`` rows "
            "by design (VEX summarises the project's current finding state and "
            "is not scan-bound), and NULL when the originating scan was later "
            "pruned (the FK is ``ON DELETE SET NULL``)."
        ),
    )
    team_id: uuid.UUID = Field(
        description=(
            "Denormalised tenant pointer — mirrored from the parent project at "
            "emit time so admin / team-wide queries do not require a join."
        ),
    )
    user: ReportDownloadUserSummary | None = Field(
        default=None,
        description=(
            "The actor who triggered the download. NULL when the user account "
            "was deleted (FK is ``ON DELETE SET NULL``) — the history fact "
            "'someone on this team got this file' survives the actor."
        ),
    )
    report_type: ReportType
    format: str = Field(
        description=(
            "Free token (cyclonedx-json / spdx-tv / pdf / text / cdx-vex / …). "
            "Not an enum because new export formats appear on the timescale of "
            "feature work; the writer is the single source of values."
        ),
    )
    size_bytes: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Body length in bytes when known at emit time, else NULL."
        ),
    )
    created_at: datetime


class ReportHistoryResponse(BaseModel):
    """Paginated list response for the project Reports tab.

    Pagination mirrors ``NotificationListResponse`` and ``AuditLogListPage`` so
    a single virtualised-list harness drives all three surfaces.
    """

    items: list[ReportDownloadEntry]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)


__all__ = [
    "ReportDownloadEntry",
    "ReportDownloadUserSummary",
    "ReportHistoryResponse",
    "ReportType",
]
