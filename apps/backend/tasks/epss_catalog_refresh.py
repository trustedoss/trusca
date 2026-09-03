# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Daily EPSS score sync onto the vulnerability catalog.

``vulnerabilities.epss_score`` and ``epss_percentile`` have existed since v2.4
and nothing wrote them. The scanner does not emit EPSS on either the SBOM or
the image path, measured across 88 and 107 findings with zero EPSS keys, so
the Vulnerabilities tab's EPSS column, the ``min_epss`` filter, the
``sort=priority`` ranking, the reports and the optional
``GATE_EPSS_THRESHOLD`` build gate were all reading columns that were always
NULL. A gate that can never fire is worse than an absent one, because the
build passes and the reason it passed is invisible. This task fills them from
FIRST's daily CSV; see ``integrations/epss_feed`` for the source, the
attribution, and why the bulk file rather than the API.

Shape of a tick
---------------
1. Read the deployment's own CVE ids out of ``vulnerabilities``. Feed rows
   outside that set are dropped as they stream past, so peak memory tracks
   this catalog (hundreds to thousands of rows) rather than the feed
   (367k and growing).
2. Fetch and parse the feed for exactly those ids.
3. Compare each matched score against what is stored and UPDATE only the rows
   that actually changed.

Why the whole feed is not stored
--------------------------------
A CVE nobody has scanned has no row to attach a score to, and creating one
would put 367k catalog rows in front of a deployment that has seen a hundred
packages. When a new scan first sees a CVE the row is created by the matching
layer, and the next daily tick scores it, so the steady state is the same
without the bulk.

Audit-log cost
--------------
None. Worker sessions deliberately run without the audit listeners (see
``core/db.py``): auditing every scan-artifact write would balloon
``audit_logs``, and the two persist paths that DO need an evidence chain emit
their rows explicitly. A catalog score refresh is not a user action and gets
no audit rows, which is what makes a daily pass over thousands of rows
affordable.

Idempotency and safety
----------------------
Re-running a tick against an unchanged feed updates nothing: the comparison
is against the stored value, quantised the same way the column stores it. A
feed that parses to implausibly few rows is refused before any write, exactly
like the KEV reconcile, so a truncated or gutted document cannot blank the
catalog's scores. Nothing is ever cleared: a CVE that drops out of the feed
keeps its last known score rather than reverting to "unknown", because EPSS
withdrawing a score is not evidence that the old one was wrong.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from core.config import epss_refresh_enabled
from core.db import sync_session_scope
from integrations.epss_feed import EpssFeed, EpssFeedUnavailable, fetch_epss_scores
from models import EpssSyncState, Vulnerability
from models.sync_state import (
    SYNC_SKIPPED_DISABLED,
    SYNC_SKIPPED_FEED_BELOW_SANITY_FLOOR,
    SYNC_SKIPPED_FEED_UNAVAILABLE,
    unexpected_reason,
)
from tasks.celery_app import celery_app

log = structlog.get_logger("tasks.epss_catalog_refresh")

# Sanity floor on rows READ from the feed document, checked before any write.
# The published file carries ~367,000 rows and grows with the CVE corpus, so a
# document that parses to fewer than 100,000 is truncated, mirrored wrong, or
# a placeholder, and writing from it would replace good scores with whatever
# survived the corruption. Deliberately measured on rows read rather than rows
# matched: how many match is a property of THIS deployment's catalog, and a
# small install legitimately matches a handful.
_FEED_SANITY_FLOOR = 100_000

# Rows per UPDATE batch. The catalog is small enough that this rarely matters,
# but a deployment with a large scan history should not hold one transaction
# open across every row it owns.
_BATCH_SIZE = 1_000


def _catalog_cve_ids(session: Session) -> set[str]:
    """Every CVE id this deployment knows, upper-cased for feed comparison.

    ``external_id`` is stored as the scanner emitted it. EPSS publishes upper
    case, and the feed parser upper-cases its side, so this side is normalised
    too rather than trusting the two to agree.
    """
    return {
        row.upper()
        for row in session.execute(select(Vulnerability.external_id)).scalars().all()
        if isinstance(row, str) and row
    }


def _apply_scores(session: Session, feed: EpssFeed) -> int:
    """Write the changed scores. Returns how many rows actually changed.

    Only a row whose stored score or percentile differs from the feed is
    touched. That keeps a quiet day's transaction empty rather than rewriting
    every row with the value it already has, and it makes the ``updated``
    count on the status row mean something an operator can act on.
    """
    updated = 0
    pending = 0
    for row in session.execute(select(Vulnerability)).scalars():
        entry = feed.scores.get((row.external_id or "").upper())
        if entry is None:
            continue
        if row.epss_score == entry.score and row.epss_percentile == entry.percentile:
            continue
        row.epss_score = entry.score
        row.epss_percentile = entry.percentile
        updated += 1
        pending += 1
        if pending >= _BATCH_SIZE:
            session.commit()
            pending = 0
    if pending:
        session.commit()
    return updated


def _sync_state_values(summary: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Column values for the status row, for both outcomes.

    A skipped tick leaves ``last_synced_at`` and the counters at their
    last-good values, so the panel can show "last attempt" and "last success"
    separately; only ``updated_at``, ``last_result`` and ``skipped_reason``
    move.
    """
    if summary["skipped"]:
        return {
            "id": True,
            "last_result": "skipped",
            "skipped_reason": summary["skipped_reason"],
            "updated_at": now,
        }
    return {
        "id": True,
        "last_synced_at": now,
        "last_result": "synced",
        "skipped_reason": None,
        "model_version": summary["model_version"],
        "score_date": summary["score_date"],
        "feed_rows": summary["feed_rows"],
        "matched": summary["matched"],
        "updated": summary["updated"],
        "duration_ms": int(round(summary["duration_seconds"] * 1000)),
        "updated_at": now,
    }


def _persist_sync_state(summary: dict[str, Any]) -> None:
    """UPSERT the single ``epss_sync_state`` row from this tick's summary.

    Its own session scope, after the sync session has already committed, so a
    failure writing status can never roll back scores. Exceptions propagate to
    the caller, which treats this as best effort.
    """
    values = _sync_state_values(summary, datetime.now(tz=UTC))
    stmt = (
        pg_insert(EpssSyncState)
        .values(values)
        .on_conflict_do_update(
            index_elements=[EpssSyncState.id],
            set_={k: v for k, v in values.items() if k != "id"},
        )
    )
    with sync_session_scope() as session:
        session.execute(stmt)
        session.commit()


def _new_summary() -> dict[str, Any]:
    return {
        "skipped": False,
        "skipped_reason": None,
        "feed_rows": 0,
        "matched": 0,
        "updated": 0,
        "catalog_size": 0,
        "model_version": None,
        "score_date": None,
        "duration_seconds": 0.0,
    }


@celery_app.task(  # type: ignore[misc]
    name="trustedoss.epss_catalog_refresh",
    bind=True,
    # No autoretry: a transient feed failure is absorbed by the daily cadence,
    # and a persistent one should surface as repeated warnings rather than a
    # silent retry loop against somebody else's server.
    max_retries=0,
)
def refresh_epss_scores(self: Any) -> dict[str, Any]:
    """Sync EPSS scores onto the vulnerability catalog. One tick, one pass.

    Returns a summary dict::

        {
            "skipped": bool,
            "skipped_reason": str | None,
            "feed_rows": int,        # rows read from the feed document
            "matched": int,          # feed rows present in this catalog
            "updated": int,          # of those, rows whose value changed
            "catalog_size": int,     # CVEs this deployment knows
            "model_version": str | None,
            "score_date": datetime | None,
            "duration_seconds": float,
        }
    """
    structlog.contextvars.bind_contextvars(
        task_name="epss_catalog_refresh",
        task_id=getattr(self.request, "id", None),
    )
    summary = _new_summary()
    started = time.monotonic()
    try:
        if not epss_refresh_enabled():
            # Default off. An air-gapped deployment must not reach the public
            # internet because it was installed; the operator turns this on,
            # or points EPSS_FEED_URL at an internal mirror.
            summary["skipped"] = True
            summary["skipped_reason"] = SYNC_SKIPPED_DISABLED
            log.info("epss_catalog_refresh_disabled")
            return _finish(summary, started)

        log.info("epss_catalog_refresh_started")
        with sync_session_scope() as session:
            wanted = _catalog_cve_ids(session)
        summary["catalog_size"] = len(wanted)
        if not wanted:
            # Nothing has been scanned yet, so there is nothing to score. Not
            # an error and not worth a feed download.
            summary["skipped"] = True
            summary["skipped_reason"] = "empty_catalog"
            log.info("epss_catalog_refresh_empty_catalog")
            return _finish(summary, started)

        try:
            feed = fetch_epss_scores(wanted=wanted)
        except EpssFeedUnavailable as exc:
            summary["skipped"] = True
            summary["skipped_reason"] = SYNC_SKIPPED_FEED_UNAVAILABLE
            log.warning("epss_catalog_refresh_feed_unavailable", error=str(exc)[:300])
            return _finish(summary, started)

        summary["feed_rows"] = feed.rows_read
        summary["matched"] = len(feed.scores)
        summary["model_version"] = feed.model_version
        summary["score_date"] = feed.score_date

        if feed.rows_read < _FEED_SANITY_FLOOR:
            # Refused before any write: a document this short is truncated or
            # a placeholder, and scoring from it would overwrite good values
            # with the remains of a bad publish.
            summary["skipped"] = True
            summary["skipped_reason"] = SYNC_SKIPPED_FEED_BELOW_SANITY_FLOOR
            log.warning(
                "epss_catalog_refresh_feed_below_floor",
                rows_read=feed.rows_read,
                floor=_FEED_SANITY_FLOOR,
            )
            return _finish(summary, started)

        with sync_session_scope() as session:
            summary["updated"] = _apply_scores(session, feed)
        return _finish(summary, started)
    except Exception as exc:  # noqa: BLE001 - the beat must survive anything
        summary["skipped"] = True
        summary["skipped_reason"] = unexpected_reason(exc)
        log.exception("epss_catalog_refresh_unexpected_error")
        return _finish(summary, started)
    finally:
        structlog.contextvars.unbind_contextvars("task_name", "task_id")


def _finish(summary: dict[str, Any], started: float) -> dict[str, Any]:
    """Stamp the duration, log the outcome, and persist the status row."""
    summary["duration_seconds"] = time.monotonic() - started
    log.info(
        "epss_catalog_refresh_complete",
        skipped=summary["skipped"],
        skipped_reason=summary["skipped_reason"],
        feed_rows=summary["feed_rows"],
        matched=summary["matched"],
        updated=summary["updated"],
        catalog_size=summary["catalog_size"],
        model_version=summary["model_version"],
        duration_seconds=round(summary["duration_seconds"], 2),
    )
    try:
        _persist_sync_state(summary)
    except Exception:  # noqa: BLE001 - status is best effort, never the beat
        log.warning("epss_sync_state_persist_failed", exc_info=True)
    return summary


__all__ = ["refresh_epss_scores"]
