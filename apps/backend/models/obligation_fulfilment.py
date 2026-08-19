# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Whether an obligation was actually met, for one project.

The portal can say a licence obliges you to publish a notice, and it can
generate the notice. It could not say whether anybody did it, so the answer to
"are we compliant" lived in a spreadsheet somebody kept beside the tool.

A table beside the obligation catalog rather than columns on it, because the
catalog is shared: one row describes what Apache-2.0 asks of everybody, and a
project's progress against that is not a property of the licence.

Nothing here changes what a notice says. The obligation text is the licence's
words; marking the work done records that somebody acted on them.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from . import Base

UUID_PK = UUID(as_uuid=True)
GEN_UUID = text("gen_random_uuid()")
NOW = text("now()")

#: Where a piece of obligation work has got to.
#:
#: ``not_applicable`` is a real answer and not a way of hiding one: an
#: obligation that binds a distributed binary does not bind an internal
#: service, and recording that judgement is more useful than leaving the row
#: untouched, which reads as nobody having looked.
OBLIGATION_FULFILMENT_STATUSES: tuple[str, ...] = (
    "not_started",
    "in_progress",
    "done",
    "not_applicable",
)


class ObligationFulfilment(Base):
    """One project's progress against one obligation."""

    __tablename__ = "obligation_fulfilments"

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, server_default=GEN_UUID)

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    obligation_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK, ForeignKey("obligations.id", ondelete="CASCADE"), nullable=False
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK, ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'not_started'")
    )
    assignee_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_PK, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    due_on: Mapped[date | None] = mapped_column(Date, nullable=True)

    # A note and a link rather than an upload: the evidence usually already
    # exists somewhere (a release page, a file in the repository, a ticket),
    # and copying it here would make the portal a second place it can be wrong.
    evidence_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_PK, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
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
            "status IN ('not_started', 'in_progress', 'done', 'not_applicable')",
            name="ck_obligation_fulfilments_status",
        ),
        # Marked done says somebody finished it, so it has to say when.
        CheckConstraint(
            "status <> 'done' OR completed_at IS NOT NULL",
            name="ck_obligation_fulfilments_completed_at",
        ),
        UniqueConstraint(
            "project_id",
            "obligation_id",
            name="uq_obligation_fulfilments_project_obligation",
        ),
        Index("ix_obligation_fulfilments_team_status", "team_id", "status"),
        Index(
            "ix_obligation_fulfilments_assignee",
            "assignee_user_id",
            postgresql_where=text("assignee_user_id IS NOT NULL"),
        ),
        Index(
            "ix_obligation_fulfilments_due",
            "due_on",
            postgresql_where=text(
                "due_on IS NOT NULL AND status <> 'done' AND status <> 'not_applicable'"
            ),
        ),
    )
