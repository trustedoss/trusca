"""
Report-download history model — W3 #32a-1 (Reports center, persistence half).

Table:
  - ``report_downloads`` — one row per successful (or attempted) export from
    the Reports center: NOTICE / SBOM / vulnerability-PDF / VEX-export. Read-
    only mutations are NOT captured by the SQLAlchemy ``before_flush`` audit
    listener (which only fires on INSERT/UPDATE/DELETE), so the download
    endpoints emit an explicit row here. This is the SCHEMA half only —
    the actual emit call sites and the history-listing API land in the
    follow-up backend-developer PR.

Conventions (CLAUDE.md core rules + neighboring model files):
  - PostgreSQL only. UUID PKs default to ``gen_random_uuid()`` (pgcrypto).
  - TIMESTAMPTZ for ``created_at``; this row is **append-only** — there is
    deliberately no ``updated_at``. A correction is a new row, not an UPDATE.
  - Every FK column gets an explicit Index — Postgres does not auto-create
    them. The three compound indexes below also cover the FK-only equality
    paths via the leftmost-prefix rule.
  - Closed enum (``report_type``) uses a native Postgres ENUM type
    (``report_type_enum``); the migration owns CREATE TYPE so the model binds
    with ``create_type=False``.
  - No environment access at import time (CLAUDE.md core rule #11).

Cross-domain relationships:
  - FK columns reference ``projects.id``, ``scans.id``, ``teams.id``,
    ``users.id`` (auth + scan domains) but this module does NOT add ORM
    ``relationship()`` edges back into those modules. The dependency is
    one-way (report_download → projects/scans/teams/users) to avoid having
    to mutate any existing model file. Callers that need the parent row
    issue an explicit query.

Tenancy & visibility:
  - ``team_id`` is denormalised onto every row (mirrored from the parent
    project at insert time) so that history-list queries can filter by
    tenant without an extra join — CLAUDE.md §1.2 "compound indexes lead
    with the tenant column."
  - The compound ``(team_id, created_at DESC)`` index serves the admin /
    team-wide "what did this team download recently" view; the
    ``(project_id, created_at DESC)`` index serves the project-detail
    Reports tab.

Cascade policy:
  - ``project_id``    CASCADE   — a project delete cleans its history (the
                                  row is meaningless without the parent).
  - ``team_id``       CASCADE   — same rationale, plus mirrors Project's
                                  team_id cascade in scan.py:163.
  - ``scan_id``       SET NULL  — VEX export rows have NULL scan_id by
                                  design; for the other three types we
                                  preserve the history row when a scan is
                                  pruned (the "someone downloaded a NOTICE
                                  for build #42" record outlives scan
                                  garbage collection).
  - ``user_id``       SET NULL  — preserves the history when a user is
                                  deleted ("someone on the team got this
                                  file" is the audit-relevant fact).

PII note:
  - ``client_ip`` (INET) and ``user_agent`` (VARCHAR 512) are operational
    PII. The masking + retention policy is owned by the backend-developer
    PR at emit time — this module only declares the columns.

Format column:
  - ``format`` is a free VARCHAR(40), NOT an ENUM. New export formats
    (cyclonedx-json/xml, spdx-json/tv, cdx-vex, csaf, pdf, html, markdown,
    text, ...) appear on the timescale of feature work, and an
    ``ALTER TYPE ADD VALUE`` migration per new format is more friction
    than value. The writers are the single point that sets the value.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy.dialects.postgresql import INET, UUID
from sqlalchemy.orm import Mapped, mapped_column

from . import Base

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

UUID_PK = UUID(as_uuid=True)
GEN_UUID = text("gen_random_uuid()")
NOW = text("now()")

# Closed report-type set — encoded as a Postgres native ENUM. The migration
# (0025) owns ``CREATE TYPE``; here we bind with ``create_type=False`` so
# SQLAlchemy never emits its own.
REPORT_TYPE_VALUES = ("notice", "sbom", "vuln_pdf", "vex_export", "vuln_xlsx")


def _report_type_enum() -> PG_ENUM:
    return PG_ENUM(
        *REPORT_TYPE_VALUES,
        name="report_type_enum",
        create_type=False,
    )


# ---------------------------------------------------------------------------
# ReportDownload
# ---------------------------------------------------------------------------


class ReportDownload(Base):
    """One emitted report download — append-only history row.

    Lifecycle:
      - INSERT only. The download endpoint (backend-developer PR) emits a
        row after a successful (or attempted) export. ``size_bytes`` is
        NULL when the response was streamed and the final size is unknown
        at emit time, or when the emit happens before the bytes are
        materialised.
      - No UPDATE / DELETE in normal operation. A correction is a new row.
      - Hard delete only via ON DELETE CASCADE when the parent project /
        team is removed.

    Why a dedicated table (vs. piggy-backing on ``audit_logs``):
      - ``audit_logs`` is driven by the ``before_flush`` listener which
        only sees INSERT/UPDATE/DELETE events on tracked tables. Read-only
        download endpoints make no flush, so they would be silently absent
        from the audit log. A dedicated table with explicit emit is the
        forward-compatible path.
      - The Reports center UI (#32a) wants a per-project download history
        with type/format filters — that is a focused query against a
        purpose-built table, not a wide scan of the polymorphic audit log.
    """

    __tablename__ = "report_downloads"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK, primary_key=True, server_default=GEN_UUID
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Nullable: VEX export is not scoped to a specific scan (it summarises
    # the project's current finding state). For NOTICE / SBOM / vuln_pdf
    # this is the scan that produced the artefact, but we keep history if
    # the scan is later pruned — hence SET NULL, not CASCADE.
    scan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_PK,
        ForeignKey("scans.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Denormalised tenant pointer — mirrors ``projects.team_id`` at insert
    # time so the (team_id, created_at) index serves admin / team-wide
    # queries without a join to ``projects``.
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK,
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Nullable: preserve the row when the user account is deleted. The
    # history fact "someone on this team downloaded X" survives even if
    # the actor is gone.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_PK,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # ENUM('notice', 'sbom', 'vuln_pdf', 'vex_export'). The migration owns
    # CREATE TYPE; this binding has create_type=False.
    report_type: Mapped[str] = mapped_column(
        _report_type_enum(), nullable=False
    )

    # Free token — see module docstring "Format column". Examples:
    #   cyclonedx-json, cyclonedx-xml, spdx-json, spdx-tv,
    #   text, html, markdown, cdx-vex, csaf, pdf
    format: Mapped[str] = mapped_column(String(40), nullable=False)

    # NULL when the response was streamed and the size is unknown at emit
    # time, or when emit happens before bytes are materialised. BigInteger
    # so multi-GB SBOMs are representable.
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Operational PII (see module docstring). Mask/retention policy is the
    # backend-developer PR's responsibility at emit time.
    client_ip: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )

    __table_args__ = (
        # Project Reports tab: "downloads for this project, newest first".
        # Compound covers the FK equality path too (leftmost-prefix rule).
        Index(
            "ix_report_downloads_project_created_at",
            "project_id",
            "created_at",
            postgresql_ops={"created_at": "DESC"},
        ),
        # Admin / team-wide view: "downloads on my team, newest first".
        # Also covers plain team_id equality lookups.
        Index(
            "ix_report_downloads_team_created_at",
            "team_id",
            "created_at",
            postgresql_ops={"created_at": "DESC"},
        ),
        # "Which exports did this scan produce?" — covers the FK equality
        # path too. Nullable scan_id means the index has fewer entries
        # than the table (Postgres b-tree skips NULLs efficiently).
        Index("ix_report_downloads_scan_id", "scan_id"),
    )


__all__ = [
    "REPORT_TYPE_VALUES",
    "ReportDownload",
]
