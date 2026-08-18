# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
A request to move a finding into a status one person may not reach alone.

The row exists so that accepting risk leaves two names instead of one. Today a
finding can be closed as suppressed by whoever holds the grade, and the record
afterwards shows the outcome and the person who caused it; an organization that
wants the decision agreed by someone else has nowhere to record that agreement,
and an audit asking who signed off has only one name to find.

Only one request may be open per finding. Two people queueing different
outcomes for the same finding would leave an approver deciding a question they
cannot see the whole of, and whichever landed second would silently overwrite
the first. The partial unique index makes the second request fail rather than
race.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from . import Base

UUID_PK = UUID(as_uuid=True)
GEN_UUID = text("gen_random_uuid()")
NOW = text("now()")

#: The states a request moves through. Terminal states keep the row: the point
#: of the record is that it survives the decision.
TRANSITION_APPROVAL_STATES: tuple[str, ...] = ("pending", "approved", "rejected")


class TransitionApproval(Base):
    """One request, and its decision once someone else has made it."""

    __tablename__ = "transition_approvals"

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, server_default=GEN_UUID)

    finding_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK,
        ForeignKey("vulnerability_findings.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Denormalised from the finding's project so the review queue can be
    # scoped without joining three tables on every poll.
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK,
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
    )

    target_status: Mapped[str] = mapped_column(String(32), nullable=False)
    # Required, unlike the justification on a direct transition: the whole
    # reason a second person is involved is that they need something to judge.
    justification: Mapped[str] = mapped_column(Text, nullable=False)

    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_PK,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    state: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_PK,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # When the agreed change actually landed. An approval authorises exactly
    # one transition: an agreement that stayed valid for ever would let a
    # finding reopened after a suppression be re-suppressed on the strength of
    # the same, older agreement, with nobody asked a second time.
    applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )

    __table_args__ = (
        CheckConstraint(
            "state IN ('pending', 'approved', 'rejected')",
            name="ck_transition_approvals_state",
        ),
        Index(
            "uq_transition_approvals_open",
            "finding_id",
            unique=True,
            postgresql_where=text("state = 'pending'"),
        ),
        Index("ix_transition_approvals_team_state", "team_id", "state"),
    )
