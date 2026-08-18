# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
One organization-wide ruling on a component.

A component that thirty projects depend on is reviewed thirty times today, and
the answer is usually the same each time because the question is about the
component and not about the project using it. This row is where an
organization records that answer once.

It does not replace the per-project approval and does not constrain it. A team
may keep its own review open while the organization rules, and where a project
has an answer of its own that answer wins. The two only meet at read time, in
:func:`services.organization_verdict_service.resolve_for_project`.

Deliberately a separate table from ``component_approvals`` rather than a
nullable ``project_id`` there: widening that column would leave the existing
partial unique index not covering the new rows, since SQL treats NULLs as
distinct, and "one open approval per component and project" would go on
reading as if it still held.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from . import Base
from .component_approval import _approval_status_enum

UUID_PK = UUID(as_uuid=True)
GEN_UUID = text("gen_random_uuid()")
NOW = text("now()")


class OrganizationComponentVerdict(Base):
    """An organization's answer about one component, and how it got there."""

    __tablename__ = "organization_component_verdicts"

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, server_default=GEN_UUID)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    component_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK,
        ForeignKey("components.id", ondelete="CASCADE"),
        nullable=False,
    )

    # The same native ENUM the per-project approvals use, so the two surfaces
    # speak one vocabulary and a report can join them without a translation.
    status: Mapped[str] = mapped_column(
        _approval_status_enum(), nullable=False, server_default=text("'pending'")
    )

    # Required, unlike the per-project note. A ruling that reaches every
    # project in the organization is the one people ask about later, and "why"
    # is the whole of what they are asking.
    justification: Mapped[str] = mapped_column(Text, nullable=False)

    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_PK, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_PK, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Mirrors component_approvals.version: handed out as an ETag and demanded
    # back, so two administrators deciding at once get a 412 and not a lost
    # write.
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )

    __table_args__ = (
        # A decided row without a timestamp sorts last and would lose to every
        # other decision, so the organization's real answer would go unread.
        CheckConstraint(
            "status NOT IN ('approved', 'rejected') OR decided_at IS NOT NULL",
            name="ck_org_component_verdicts_decided_at",
        ),
        # One open ruling per organization and component, the same shape as the
        # per-project constraint. A decided row is terminal, so an organization
        # changes its mind by ruling again rather than by editing the record of
        # what it used to think.
        Index(
            "ix_org_component_verdicts_unique_open",
            "organization_id",
            "component_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'under_review')"),
        ),
        Index("ix_org_component_verdicts_org_status", "organization_id", "status"),
        Index("ix_org_component_verdicts_component", "component_id"),
    )
