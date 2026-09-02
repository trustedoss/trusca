# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
MaliciousSyncState — single-row status table for the malicious-package beat.

Structural mirror of :mod:`models.eol_sync_state`, with one deliberate
omission: the fetched snapshot does NOT ride the row. EOL's dataset is a few
kilobytes and fits comfortably in JSONB; this one is 12.8 MB, and pulling it
through the ORM on every health-panel read (and every re-stamp tick) to save a
file read would be the wrong trade. The fetch half writes the file the
evaluator already loads, and this row records only what happened.

That difference has a consequence worth stating: a successful fetch is
durable on disk, not in the database, so a worker replaced between ticks falls
back to the snapshot shipped with the release. Air-gapped installs never fetch
at all and run on that snapshot permanently, which is the intended posture.

Writer / reader contract:
  - **Writer**: ``tasks/malicious_catalog_refresh`` UPSERTs the row at the end
    of every tick. Fetch fields (``last_synced_at`` / ``snapshot_date`` /
    ``purl_count`` / ``ecosystems_ok`` / ``ecosystems_failed``) update only on
    a successful fetch; re-stamp counters (``stamped`` / ``flagged``) update
    every tick, because the re-stamp runs locally even when the fetch is
    disabled or fails.
  - **Reader**: ``services.malicious_health_service`` (admin/health panel).

Semantics:
  - ``last_result`` is the FETCH outcome: ``"synced"`` | ``"skipped"``.
  - ``skipped_reason``: one of ``SYNC_SKIPPED_REASON_VALUES``, or an
    ``unexpected:<ExceptionName>`` value built by ``unexpected_reason()``.
    Both live in ``models.sync_state`` and are shared with the other
    sync-state tables and the refresh tasks; do not restate the members
    here, that is exactly how this list drifted from the code before.
  - ``snapshot_date`` is the date of the snapshot in effect after the tick,
    whether it came from a fetch or from the vendored file, so the panel's
    staleness maths reads one column.

No PII, no tenant scoping: system-global operational metadata, admin-only
read surface.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from . import Base
from .scan import NOW


class MaliciousSyncState(Base):
    """The one status row for the malicious-package refresh beat."""

    __tablename__ = "malicious_sync_state"

    id: Mapped[bool] = mapped_column(Boolean, primary_key=True, server_default=text("true"))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_result: Mapped[str | None] = mapped_column(String(16), nullable=True)
    skipped_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Date of the snapshot in effect after the tick (fetched or vendored).
    snapshot_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    purl_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ecosystems_ok: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ecosystems_failed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Catalog rows whose verdict CHANGED on the last re-stamp pass.
    stamped: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Catalog rows reading ``flagged`` after the pass — the number an
    #: operator reacts to, as opposed to the churn ``stamped`` measures.
    flagged: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Rows that went clear → flagged on this pass. Non-zero means the
    #: snapshot found something in stock that nobody had scanned since.
    newly_flagged: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=NOW,
        onupdate=NOW,
    )

    __table_args__ = (
        CheckConstraint("id", name="ck_malicious_sync_state_singleton"),
    )


__all__ = ["MaliciousSyncState"]
