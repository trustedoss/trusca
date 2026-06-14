"""
Received-SBOM conformance result model — model 3 (supplier-submitted SBOM ingest).

Table:
  - ``sbom_conformance`` — exactly one row per ingested ``Scan`` (``kind='sbom'``),
    holding the quality-scoring verdict computed by
    :mod:`services.sbom_conformance` from the uploaded document's ORIGINAL bytes
    (before any CycloneDX normalisation). The portal renders this as a
    pass / warn / fail badge plus a per-check table on the scan, and a supplier
    can be sent a rejection citing the failed mandatory checks.

Why a dedicated table (vs. ``scan_metadata`` JSONB):
  - The "Received SBOM" surface filters / sorts by ``result`` and coverage, and
    those are first-class queryable columns here rather than buried in the
    polymorphic scan metadata blob (which also has a 16 KiB ceiling the full
    per-check detail with capped ``missing[]`` lists can approach). The raw
    per-check array still lives in ``checks`` (JSONB) for the detail view.

Lifecycle:
  - INSERT (or REPLACE — the ingest task deletes any prior row for the scan
    before inserting, so a re-run under Celery ``acks_late`` stays idempotent
    against the ``uq_sbom_conformance_scan_id`` unique constraint). No UPDATE.
  - Hard delete only via ON DELETE CASCADE when the parent ``scans`` row (or,
    transitively, the project) is removed.

Conventions (CLAUDE.md core rules + neighbouring model files):
  - PostgreSQL only. UUID PK defaults to ``gen_random_uuid()`` (pgcrypto).
  - TIMESTAMPTZ ``created_at``; append-only, no ``updated_at`` (a re-run
    replaces the row, it does not mutate it in place).
  - ``result`` / ``source_format`` are short closed vocabularies but kept as
    VARCHAR (not native ENUM): the scorer in services/sbom_conformance.py owns
    the value set, the FE mirrors the check-id catalogue (a contract test keeps
    them in lockstep), and an ``ALTER TYPE ADD VALUE`` per tweak is more
    friction than value here.
  - No environment access at import time (CLAUDE.md core rule #11).

Cross-domain relationships:
  - FK columns reference ``scans.id`` / ``projects.id`` but this module adds no
    ORM ``relationship()`` edge back into ``scan.py`` (one-way dependency, same
    pattern as report_download.py). Callers query explicitly.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from . import Base

UUID_PK = PG_UUID(as_uuid=True)
GEN_UUID = text("gen_random_uuid()")
NOW = text("now()")
EMPTY_JSONB_ARR = text("'[]'::jsonb")

# Closed vocabularies owned by services.sbom_conformance (kept as VARCHAR, see
# module docstring). Mirrored here only for documentation / test reference.
RESULT_VALUES = ("pass", "warn", "fail")
SOURCE_FORMAT_VALUES = ("cyclonedx", "spdx-json", "spdx-tv", "unknown")


class SbomConformance(Base):
    """One conformance verdict for an ingested SBOM scan."""

    __tablename__ = "sbom_conformance"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK, primary_key=True, server_default=GEN_UUID
    )

    # One verdict per scan. UNIQUE so the ingest task's delete-then-insert
    # re-run path can rely on at-most-one row, and a stray double-insert is a
    # DB-level error rather than a silent duplicate.
    scan_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK,
        ForeignKey("scans.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    # Denormalised tenant/owner pointer (mirrors the scan's project) so the
    # "Received SBOM" list can filter without joining scans.
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Detected serialisation: cyclonedx | spdx-json | spdx-tv | unknown.
    source_format: Mapped[str] = mapped_column(String(16), nullable=False)

    # Overall verdict: pass | warn | fail. ``warn`` = all mandatory checks pass
    # but a recommended (license / hash coverage) check fell short.
    result: Mapped[str] = mapped_column(String(8), nullable=False)

    n_fail: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    n_warn: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    component_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )

    # Coverage percentages (0-100). NULL for SPDX Tag-Value, which is scored on
    # presence only (per-package coverage is not computed for Tag-Value).
    purl_coverage_pct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    license_coverage_pct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hash_coverage_pct: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Per-check detail array (each: id/label/required/status/detail/missing[]),
    # ``missing[]`` capped at 50 by the scorer. Drives the detail table.
    checks: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=EMPTY_JSONB_ARR
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )


__all__ = [
    "RESULT_VALUES",
    "SOURCE_FORMAT_VALUES",
    "SbomConformance",
]
