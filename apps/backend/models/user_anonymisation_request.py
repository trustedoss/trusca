# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
UserAnonymisationRequest: a two-person gate in front of an irreversible erasure.

Anonymising a user strips their name, address and OAuth links, revokes their
sessions, and scrubs the client details from their audit rows. None of it can
be undone, and it is asked for by one person about another. A single
super-admin acting alone is one mis-click away from destroying data nobody
asked to lose, so the request is separated from the decision: one super-admin
opens it, a different one approves, and only then does it run.

Shaped after ``models.transition_approval``, which guards a smaller
irreversible act the same way, down to the partial unique index that allows at
most one open request per subject.

Why the FKs are RESTRICT and not SET NULL
-----------------------------------------
Every other user reference in this schema is ``ON DELETE SET NULL`` so a
deleted account does not take business records with it. Here that would be
exactly wrong: the rows exist to record who authorised an erasure, and a NULL
approver turns "these two people decided" into "somebody decided", which is
the state this table exists to prevent. RESTRICT instead, so the record cannot
be hollowed out by removing one of its participants.

``subject_user_id`` is RESTRICT for a different reason: after anonymisation the
subject's row survives, stripped, and this request is the only account of what
happened to it.

No PII of its own
-----------------
The table stores three user ids and timestamps, nothing else. It deliberately
does not copy the subject's email or name, not even to say what was erased: a
request row naming the address it removed would keep that address in the
database forever, which would make the whole operation pointless.
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
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from . import Base

# Defined here rather than imported from ``.scan``: the import order in
# ``models/__init__`` puts this module first, and pulling them across makes
# mypy unable to resolve their types. ``transition_approval`` does the same.
UUID_PK = UUID(as_uuid=True)
GEN_UUID = text("gen_random_uuid()")
NOW = text("now()")

#: A request nobody has decided on yet.
ANONYMISATION_PENDING = "pending"
#: A second super-admin approved it; the erasure has not run yet.
ANONYMISATION_APPROVED = "approved"
#: The erasure ran to completion.
ANONYMISATION_EXECUTED = "executed"
#: The requester withdrew it before a decision.
ANONYMISATION_CANCELLED = "cancelled"
#: Nobody decided within the window, so it can no longer be approved.
ANONYMISATION_EXPIRED = "expired"

ANONYMISATION_STATES: tuple[str, ...] = (
    ANONYMISATION_PENDING,
    ANONYMISATION_APPROVED,
    ANONYMISATION_EXECUTED,
    ANONYMISATION_CANCELLED,
    ANONYMISATION_EXPIRED,
)


class UserAnonymisationRequest(Base):
    """One request to anonymise one user, and who agreed to it.

    Columns:
        subject_user_id: Whose data is erased. RESTRICT, see module docstring.
        requested_by_user_id: The super-admin who opened the request.
        approved_by_user_id: The one who approved it. NULL until then, and
            enforced different from the requester and from the subject by
            ``ck_user_anonymisation_requests_distinct_parties``.
        state: One of ``ANONYMISATION_STATES``.
        expires_at: When an undecided request stops being approvable. Set by
            the service to a fixed window rather than a configurable one: a
            deployment that widened it would be widening the window in which
            an irreversible act sits half-authorised.
        executed_at: When the operator command ran. NULL while a request is
            approved and waiting, which is the state
            ``list_awaiting_execution`` surfaces: approval happens in the
            product, execution happens on a server, and nothing looks wrong
            in between.
    """

    __tablename__ = "user_anonymisation_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, server_default=GEN_UUID)
    subject_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    requested_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_PK,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=ANONYMISATION_PENDING
    )
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    executed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "state IN ('pending', 'approved', 'executed', 'cancelled', 'expired')",
            name="ck_user_anonymisation_requests_state",
        ),
        # The whole point of the table, enforced in the schema rather than only
        # in the service: the approver is a different person from the requester,
        # and neither of them is the subject. A service-only check is one
        # refactor away from being skipped by a second call site.
        CheckConstraint(
            "approved_by_user_id IS NULL OR ("
            "approved_by_user_id <> requested_by_user_id "
            "AND approved_by_user_id <> subject_user_id)",
            name="ck_user_anonymisation_requests_distinct_parties",
        ),
        CheckConstraint(
            "requested_by_user_id <> subject_user_id",
            name="ck_user_anonymisation_requests_not_self",
        ),
        # At most one live request per subject. Two open requests would let two
        # requesters each find their own approver and reach the same erasure
        # twice, and the second run would have nothing left to erase but would
        # still record that it happened.
        Index(
            "uq_user_anonymisation_requests_open",
            "subject_user_id",
            unique=True,
            postgresql_where=text("state IN ('pending', 'approved')"),
        ),
        Index("ix_user_anonymisation_requests_state", "state"),
    )


__all__ = [
    "ANONYMISATION_APPROVED",
    "ANONYMISATION_CANCELLED",
    "ANONYMISATION_EXECUTED",
    "ANONYMISATION_EXPIRED",
    "ANONYMISATION_PENDING",
    "ANONYMISATION_STATES",
    "UserAnonymisationRequest",
]
