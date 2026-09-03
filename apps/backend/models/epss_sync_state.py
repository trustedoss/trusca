# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
EpssSyncState: single-row status table for the daily EPSS score sync.

Same shape and the same reasoning as ``models.kev_sync_state``: the sync
writes straight into ``vulnerabilities`` and leaves no file behind, so a
status row is the only durable place for "when did the last sync run and what
happened". The panel needs the latest outcome, not a history, so it is one row
UPSERTed on a BOOLEAN PK with ``CHECK (id)``, which makes a second row
impossible by construction.

Two columns are EPSS-specific and worth the space:

  - ``model_version`` / ``score_date`` come from the feed's own leading
    comment line (``#model_version:v2026.06.15,score_date:2026-09-02T...``).
    EPSS is a model whose scores move daily, so "which run produced what is in
    the catalog" is a real operational question, and unlike KEV's boolean
    listing there is no way to infer it from the values themselves.
  - ``matched`` / ``updated`` split what KEV calls listed/delisted. The sync
    reads the whole feed but only writes CVEs this deployment has actually
    seen, so ``matched`` (rows in the feed that are in our catalog) and
    ``updated`` (of those, how many actually changed) answer different
    questions: a low ``matched`` means the feed and the catalog disagree,
    while ``matched`` high and ``updated`` near zero is a normal quiet day.

No PII, no tenant scoping: system-global operational metadata on an
admin-only read surface.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from . import Base
from .scan import NOW


class EpssSyncState(Base):
    """The one status row for the EPSS score sync beat.

    Columns:
        id: BOOLEAN PK, always ``true``; with ``CHECK (id)`` this makes the
            table single-row. The writer UPSERTs on this PK.
        last_synced_at: Timestamp of the last successful sync (not updated on
            skipped ticks).
        last_result: ``"synced"`` | ``"skipped"``.
        skipped_reason: The task's skip reason when ``last_result="skipped"``,
            else NULL. Vocabulary lives in ``models.sync_state``.
        model_version: EPSS model version the feed declared, e.g.
            ``v2026.06.15``.
        score_date: Scoring timestamp the feed declared.
        feed_rows: Rows read from the feed document.
        matched: Feed rows whose CVE exists in this deployment's catalog.
        updated: Of those, how many carried a score or percentile different
            from what was already stored.
        duration_ms: Wall-clock duration of the last successful sync.
        updated_at: Touched on every UPSERT, successful or skipped; the
            panel's "last attempt" timestamp.
    """

    __tablename__ = "epss_sync_state"

    id: Mapped[bool] = mapped_column(Boolean, primary_key=True, server_default=text("true"))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_result: Mapped[str | None] = mapped_column(String(16), nullable=True)
    skipped_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    score_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    feed_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    matched: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=NOW,
        onupdate=NOW,
    )

    __table_args__ = (
        # Single-row enforcement: the PK forbids a second ``true`` row and
        # this CHECK forbids the only other boolean value.
        CheckConstraint("id", name="ck_epss_sync_state_singleton"),
    )


__all__ = ["EpssSyncState"]
