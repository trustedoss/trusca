# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
A request to use a package, made before anything has been scanned.

Approvals today only exist after a scan finds something, which suits an
organization that reviews what its code already depends on. An organization
that asks first has nowhere to record the asking, and the answer arrives when
the dependency is already in the build.

The package is a purl string rather than a component id, because the whole
point is that no component row exists yet: nobody has pulled it in. When a scan
eventually finds it, the decision recorded here is carried onto the approval
that scan opens, so asking early is not asking twice.

The status column shares the ``approval_status`` type with those approvals. One
vocabulary on purpose: a request and an approval are the same question at
different times, and two enums with the same four names would drift with
nothing failing until something joined them.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from . import Base
from .component_approval import _approval_status_enum

UUID_PK = UUID(as_uuid=True)
GEN_UUID = text("gen_random_uuid()")
NOW = text("now()")


class ComponentIntakeRequest(Base):
    """One "may we use this?" and its answer."""

    __tablename__ = "component_intake_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, server_default=GEN_UUID)

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    # Denormalised from the project so the review queue can be scoped without
    # a join on every poll, the same way the transition approvals carry it.
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK, ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )

    purl: Mapped[str] = mapped_column(String(512), nullable=False)
    # Required. The reviewer is being asked about something that is not in the
    # codebase yet, so there is nothing else for them to look at.
    justification: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[str] = mapped_column(
        _approval_status_enum(), nullable=False, server_default=text("'pending'")
    )

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
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )

    __table_args__ = (
        CheckConstraint(
            "status NOT IN ('approved', 'rejected') OR decided_at IS NOT NULL",
            name="ck_component_intake_requests_decided_at",
        ),
        # One open request per package per project. Two people asking about the
        # same package would give a reviewer two questions that are one.
        Index(
            "ix_component_intake_requests_unique_open",
            "project_id",
            "purl",
            unique=True,
            postgresql_where=text("status IN ('pending', 'under_review')"),
        ),
        Index("ix_component_intake_requests_team_status", "team_id", "status"),
        Index("ix_component_intake_requests_project_purl", "project_id", "purl"),
    )
