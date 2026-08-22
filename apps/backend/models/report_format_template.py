# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Organization-configured formatting for the vulnerability PDF/HTML report (N22).

Mirrors :mod:`models.notice_template` (N21): one row per organization, plain
text for the header and organization label (no markup engine — the same
requirements-doc constraint that rules out conditionals/loops for NOTICE
applies here, so nothing configured here can add, remove, or reorder a row of
report data). The one addition over N21 is column selection: an organization
may narrow which columns the vulnerabilities/components tables render, from a
fixed vocabulary, no computed or renamed columns.

Only the PDF/HTML report (``services.report_service``) is in scope. The Excel
report (``services.report_xlsx_service``) has its own, materially different
column set across three sheets; extending column selection to it is a
separate decision, not made here (recorded in the D11 tracker entry).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from . import Base

UUID_PK = UUID(as_uuid=True)
GEN_UUID = text("gen_random_uuid()")
NOW = text("now()")

# Canonical column vocabulary — the exact set/order ``build_report_html``
# already renders unconditionally today. A selection is always a subset of
# one of these tuples, always rendered in this order (no reordering, no
# renaming, no computed columns).
REPORT_VULNERABILITY_COLUMNS: tuple[str, ...] = ("cve", "cvss", "summary", "status")
REPORT_COMPONENT_COLUMNS: tuple[str, ...] = ("name", "version", "license", "severity", "vulns")


class ReportFormatTemplate(Base):
    """One organization's header/label/column-selection defaults for the report."""

    __tablename__ = "report_format_templates"

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, server_default=GEN_UUID)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    # Plain text, escaped exactly like every other untrusted string the report
    # builder already prints (``_esc``) — never interpreted as markup.
    header_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    org_label: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Each a JSON array of column names, a non-empty subset of
    # REPORT_VULNERABILITY_COLUMNS / REPORT_COMPONENT_COLUMNS respectively.
    # NULL means "no organization default" (request-time selection, if any,
    # still applies; absent both, every column renders — current behavior).
    vulnerability_columns: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    component_columns: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_PK,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )


__all__ = [
    "REPORT_COMPONENT_COLUMNS",
    "REPORT_VULNERABILITY_COLUMNS",
    "ReportFormatTemplate",
]
