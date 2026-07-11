"""
EolSyncState — single-row status table for the endoflife.date refresh beat.

Structural mirror of :mod:`models.kev_sync_state` (the single-row BOOLEAN-PK
``CHECK (id)`` construction, the writer/reader contract, the skip semantics)
with one addition the KEV table does not need: the **fetched snapshot itself**
rides the row (``snapshot`` JSONB, a few KB). The KEV reconcile writes
straight into ``vulnerabilities`` and needs no dataset store; the EOL refresh
must keep the freshest dataset somewhere durable so the weekly re-stamp pass
can prefer it over the (potentially older) snapshot vendored with the release
— without any network call on air-gapped installs.

Writer / reader contract:
  - **Writer**: ``tasks/eol_catalog_refresh`` UPSERTs the one row at the end
    of every tick. The fetch half updates ``last_synced_at`` / ``snapshot`` /
    ``snapshot_date`` / ``products_ok`` / ``products_failed`` only on a
    SUCCESSFUL fetch; the re-stamp half's counters (``stamped`` /
    ``cleared``) update on every tick — the re-stamp runs even when the
    fetch is disabled or fails (pure-local pass).
  - **Reader**: ``services.eol_health_service`` (admin/health EOL panel) and
    the refresh task itself (effective-dataset resolution).

Semantics:
  - ``last_result`` is the FETCH outcome: ``"synced"`` | ``"skipped"``
    (closed task-owned vocabulary, VARCHAR not native ENUM — the
    kev_sync_state reasoning).
  - ``skipped_reason``: ``disabled`` / ``refresh_disabled`` /
    ``feed_unavailable`` / ``feed_below_sanity_floor`` /
    ``unexpected:<ExceptionName>``.
  - ``snapshot_date`` mirrors the stored ``snapshot["_snapshot"]`` as a DATE
    so the health panel's staleness maths never parses JSONB.

No PII, no tenant scoping: system-global operational metadata, admin-only
read surface.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from . import Base
from .scan import NOW


class EolSyncState(Base):
    """The one status row for the endoflife.date refresh beat (0039)."""

    __tablename__ = "eol_sync_state"

    id: Mapped[bool] = mapped_column(Boolean, primary_key=True, server_default=text("true"))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_result: Mapped[str | None] = mapped_column(String(16), nullable=True)
    skipped_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # The fetched compact dataset (same shape as the vendored
    # eol_snapshot.json, a few KB). NULL until the first successful fetch.
    snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    snapshot_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    products_ok: Mapped[int | None] = mapped_column(Integer, nullable=True)
    products_failed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stamped: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cleared: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=NOW,
        onupdate=NOW,
    )

    __table_args__ = (
        CheckConstraint("id", name="ck_eol_sync_state_singleton"),
    )


__all__ = ["EolSyncState"]
