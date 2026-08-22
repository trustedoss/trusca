# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Organization-configured boilerplate for the NOTICE attribution document (N21).

The NOTICE (``services.obligation_service.generate_notice``) has always taken
only the scan's own license/component/obligation data as input; there is no
row anywhere to hold a preface (an organization's letterhead, an internal
distribution notice) or a footer (a standard legal disclaimer). Both are
plain text an organization writes once, not markup: the document already
carries structure (headings, sections) that content injected here must not
be able to rearrange, and a template engine capable of that is exactly the
dependency the requirements doc rules out (conditionals/loops would let a
template omit an obligation, which no organization setting is allowed to do).

One row per (organization, format): the three renderers (text/markdown/html)
answer different questions about the same boilerplate — a markdown preface
that should read as a heading needs `#`, the html one needs no such marker —
so an organization writes each format's wording once rather than one blob
reused verbatim across all three.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from . import Base

UUID_PK = UUID(as_uuid=True)
GEN_UUID = text("gen_random_uuid()")
NOW = text("now()")

NOTICE_TEMPLATE_FORMAT_VALUES = ("text", "markdown", "html")


class NoticeTemplate(Base):
    """One organization's preface/footer boilerplate for one NOTICE format."""

    __tablename__ = "notice_templates"

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, server_default=GEN_UUID)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    format: Mapped[str] = mapped_column(String(16), nullable=False)

    # Plain text, not markup: rendered through the same escaping each format's
    # renderer already applies to every other untrusted string it prints, so
    # this can never inject structure the requirements doc reserves to the
    # scan data (headings, sections, an extra license block).
    preface: Mapped[str | None] = mapped_column(Text, nullable=True)
    footer: Mapped[str | None] = mapped_column(Text, nullable=True)

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

    __table_args__ = (
        UniqueConstraint("organization_id", "format", name="uq_notice_templates_org_format"),
        CheckConstraint(
            "format IN ('text', 'markdown', 'html')",
            name="ck_notice_templates_format",
        ),
    )


__all__ = ["NOTICE_TEMPLATE_FORMAT_VALUES", "NoticeTemplate"]
