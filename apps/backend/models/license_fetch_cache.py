"""
LicenseFetchCache — TTL cache for the multi-ecosystem license fetcher.

Why a separate table (vs. piggy-backing on ``licenses`` /
``license_findings``):
  * The fetcher's lookup unit is ``purl`` (with version) — there is
    *no* persistent component_version row at the moment we issue the
    HTTP request, because the cache is consulted *before* we decide
    whether to upsert. Putting the cache on ``licenses`` would couple
    the freshness check to the licence row, which is incorrect: the
    same SPDX id can be the answer for many distinct purls.
  * Negative caching: 404 / unmapped responses are also cached for 24h
    so a hostile clone full of nonexistent packages cannot drive
    millions of repeat external-API hits during retries. We model
    that with ``is_negative=true`` and a nullable ``spdx_id``.
  * Forward-only migrations (CLAUDE.md §6) — the table is small,
    self-contained, and easy to drop if the fetcher is deprecated;
    intermixing with the live ``licenses`` table would have made that
    cleanup risky.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from . import Base
from .scan import NOW


class LicenseFetchCache(Base):
    """A TTL cache row produced by the license-fetcher dispatcher.

    A row keyed by the *versioned* PURL. The dispatcher reads the row
    first; if ``fetched_at`` is within the freshness window the row's
    answer (positive or negative) is reused without re-issuing the
    external HTTP request. Otherwise the fetcher runs and the row is
    updated in place (UPSERT keyed on the PRIMARY KEY).

    Columns:
        purl: Versioned PURL (``pkg:maven/...``, ``pkg:pypi/...``, ...).
            Primary key — the dispatcher always looks up the exact
            string the cdxgen SBOM produced.
        spdx_id: Resolved SPDX id, or NULL when ``is_negative=true``.
        reference_url: License-text URL provided by the registry, or
            NULL when the registry does not supply one.
        source: Short identifier of the registry that served the
            answer (``"maven_central"`` / ``"pypi"`` / ``"crates_io"``
            / ``"pkg_go_dev"``). For negative cache entries we still
            record the dispatch target so debug logs can show which
            ecosystem produced the miss.
        is_negative: True for "no licence found" answers (404 from the
            registry, license-block missing, free-text we cannot reduce
            to SPDX). Cached for the same TTL window so retries don't
            re-hit the external API.
        fetched_at: Timestamp of the most recent successful HTTP
            response (or the most recent confirmed miss).
    """

    __tablename__ = "license_fetch_cache"

    purl: Mapped[str] = mapped_column(Text, primary_key=True)
    spdx_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    reference_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    is_negative: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )

    __table_args__ = (
        # Background TTL sweepers (a future Celery Beat) will scan by
        # ``fetched_at``; an index makes that O(log n) instead of a
        # full scan on a ~hundreds-of-thousands-row cache.
        Index("ix_license_fetch_cache_fetched_at", "fetched_at"),
    )


__all__ = ["LicenseFetchCache"]
