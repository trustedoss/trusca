"""
KevSyncState — single-row status table for the CISA KEV catalog refresh.

Writer / reader contract:
  - **Writer**: ``tasks/kev_catalog_refresh`` UPSERTs the one row at the end
    of every tick (successful reconcile AND skip paths alike), persisting the
    summary it currently only logs (feed_count / listed / delisted /
    duration).
  - **Reader**: the admin/health KEV feed panel. The Trivy DB panel reads
    freshness off on-disk DB metadata, but the KEV feed leaves no file
    behind — the reconcile writes straight into ``vulnerabilities`` — so a
    DB status row is the only durable place for "when did the last sync run
    and what happened".

Why a single-row table (vs. an append-only sync-log table):
  - The panel needs exactly the LATEST outcome; history is already in the
    structlog stream (one ``kev_catalog_refresh_complete`` /
    ``..._feed_unavailable`` event per tick). A one-row UPSERT keeps the
    read a PK lookup and the table size constant.
  - There is no single-row-table precedent in this repo yet, so we enforce
    the invariant directly: the PK is a BOOLEAN defaulting to ``true`` with
    ``CHECK (id)`` — ``false`` is rejected by the CHECK and a second
    ``true`` row is rejected by the PK, so the table can never hold more
    than the one row ``id=true``.

Semantics of the nullable columns:
  - ``last_synced_at`` is the wall-clock time of the last SUCCESSFUL
    reconcile only; a skipped tick updates ``last_result`` /
    ``skipped_reason`` / ``updated_at`` but leaves ``last_synced_at`` (and
    the counters) at their last-good values, so the panel can show both
    "last attempt" (``updated_at``) and "last success" (``last_synced_at``).
  - ``last_result`` mirrors the task summary's outcome: ``"synced"`` or
    ``"skipped"`` (closed 2-value vocabulary owned by the task — VARCHAR,
    not native ENUM, same reasoning as sbom_conformance.py).
  - ``skipped_reason`` mirrors the task's ``skipped_reason``: ``disabled`` /
    ``feed_unavailable`` / ``feed_below_sanity_floor`` /
    ``unexpected:<ExceptionName>`` — 64 chars covers the longest form.
  - Everything except ``id`` / ``updated_at`` is nullable: before the first
    tick ever runs the row (if seeded) legitimately has no outcome, and a
    skip carries no counters.

No PII, no tenant scoping: system-global operational metadata, admin-only
read surface (Super Admin health panel).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from . import Base
from .scan import NOW


class KevSyncState(Base):
    """The one status row for the KEV catalog refresh beat.

    Columns:
        id: BOOLEAN PK, always ``true`` — combined with ``CHECK (id)`` this
            makes the table single-row by construction (see module
            docstring). The writer UPSERTs on this PK.
        last_synced_at: Timestamp of the last successful reconcile (not
            updated on skipped ticks).
        last_result: ``"synced"`` | ``"skipped"`` — outcome of the most
            recent tick.
        skipped_reason: Task's skip reason when ``last_result="skipped"``,
            else NULL.
        feed_count: Entries parsed from the CISA feed on the last successful
            reconcile.
        listed: Rows flagged / date-corrected on the last successful
            reconcile.
        delisted: Rows cleared (dropped out of the feed) on the last
            successful reconcile.
        duration_ms: Wall-clock duration of the last successful reconcile in
            milliseconds (the task measures seconds as float; the writer
            converts — integer ms is enough resolution for a panel).
        updated_at: Touched on every UPSERT (successful or skipped) — the
            panel's "last attempt" timestamp.
    """

    __tablename__ = "kev_sync_state"

    id: Mapped[bool] = mapped_column(Boolean, primary_key=True, server_default=text("true"))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_result: Mapped[str | None] = mapped_column(String(16), nullable=True)
    skipped_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    feed_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    listed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    delisted: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=NOW,
        onupdate=NOW,
    )

    __table_args__ = (
        # Single-row enforcement: PK uniqueness forbids a second ``true``
        # row, and this CHECK forbids the only other boolean value.
        CheckConstraint("id", name="ck_kev_sync_state_singleton"),
    )


__all__ = ["KevSyncState"]
