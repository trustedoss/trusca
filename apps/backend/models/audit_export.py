# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Where an audit export got to (N17).

Its own table, and that is the point. ``audit_logs`` is append-only and a
trigger enforces it, so an ``exported_at`` column there would mean either
dropping that trigger or carving an exception into it. The exception is the
property: a trail that some code may update is a trail somebody can argue
with. The position of an export is ordinary mutable bookkeeping and belongs
somewhere ordinary.

One row per destination. A deployment that repoints its collector starts a
new row rather than inheriting a position that describes what a different
collector already holds.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from . import Base

UUID_PK = UUID(as_uuid=True)
GEN_UUID = text("gen_random_uuid()")
NOW = text("now()")


class AuditExportCursor(Base):
    """How far one destination has been caught up."""

    __tablename__ = "audit_export_cursors"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK, primary_key=True, server_default=GEN_UUID
    )
    destination: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    #: The position, as the pair the export orders by. A timestamp alone
    #: cannot resume safely: audit rows share a millisecond often enough that
    #: "everything after this instant" either repeats the tail of a batch or
    #: skips it.
    last_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_id: Mapped[uuid.UUID | None] = mapped_column(UUID_PK, nullable=True)
    rows_exported: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )

    __table_args__ = (
        CheckConstraint(
            "(last_created_at IS NULL) = (last_id IS NULL)",
            name="ck_audit_export_cursors_position_is_whole",
        ),
    )
