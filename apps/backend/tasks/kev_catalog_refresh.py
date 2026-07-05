"""
CISA KEV catalog refresh — daily Celery beat.

Pulls the public CISA KEV (Known Exploited Vulnerabilities) JSON feed via
:func:`integrations.kev_feed.fetch_kev_catalog` and reconciles the catalog
``vulnerabilities`` table's KEV columns (migration 0034):

  * A row whose ``external_id`` appears in the feed gets ``kev=true`` plus
    ``kev_date_added`` / ``kev_due_date`` from the feed entry — UPDATE only
    when something actually changed, so an unchanged catalog is a pure read.
  * A row currently ``kev=true`` whose CVE has DROPPED out of the feed is
    delisted: ``kev=false``, both dates NULLed. CISA does occasionally remove
    entries (withdrawn CVEs, vendor disputes), so the flag must track the
    feed in both directions.

Why full-set comparison, no batching knob:
  The CISA catalog is ~1,600 CVEs and our ``kev=true`` population is bounded
  by it (partial index ``ix_vulnerabilities_kev`` makes the delist SELECT a
  tiny index scan). Two bounded queries + in-Python diff per tick is simpler
  and cheaper than a cursor loop; there is nothing to tune, so no batch-size
  env var exists (contrast with the 600k-row sweep in
  ``vulnerability_catalog_refresh``).

Idempotency:
  Re-running against the same feed is a no-op — every write is guarded by a
  changed-value check, so the second run reports ``listed=0, delisted=0``.
  There is no watermark state; each tick reconciles from scratch.

Failure isolation:
  A feed failure (network, HTTP error, oversized/garbage body — all surfaced
  as :class:`integrations.kev_feed.KevFeedUnavailable`) logs a WARNING and
  returns a skipped summary; existing KEV flags are left untouched (a
  transient CISA outage must not mass-delist the catalog). The task never
  raises into the beat.

Air-gapped deployments:
  ``KEV_REFRESH_ENABLED=false`` short-circuits before any network attempt.
  ``KEV_FEED_URL`` can alternatively point at an internal mirror (same
  pattern as ``TRIVY_DB_REPOSITORY``).

Status row (Phase C — admin KEV panel):
  Every tick ends by UPSERTing the single ``kev_sync_state`` row (migration
  0035) with its outcome — the admin/health KEV panel's only durable source
  (``services.kev_health_service`` reads it). Writer contract lives in
  ``models/kev_sync_state.py``: a skipped tick touches only ``last_result``
  / ``skipped_reason`` / ``updated_at``; ``last_synced_at`` and the counters
  keep their last-good values. The write is best-effort — it runs after the
  reconcile session committed and a failure degrades to a WARNING.

CLAUDE.md compliance:
  - Core rule #3: the feed download sits behind a Celery task; no request
    path ever fetches it synchronously.
  - Core rule #11: URL / toggle / timeout are read via ``core.config``
    accessors at call time, never cached at module level.
  - §5 logging: structlog JSON, one summary event per tick; per-row changes
    are counted, not individually logged.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.config import kev_refresh_enabled
from integrations.kev_feed import KevEntry, KevFeedUnavailable, fetch_kev_catalog
from models import KevSyncState, Vulnerability
from tasks.celery_app import celery_app

log = structlog.get_logger("tasks.kev_catalog_refresh")

# Sanity floor on the parsed feed size before ANY write pass may run
# (security-reviewer MAJOR). A valid-JSON but gutted document —
# ``{"vulnerabilities": []}``, or one whose entries ALL failed the per-entry
# parse (``parsed == 0``) — sails through :func:`fetch_kev_catalog` as an
# empty/near-empty catalog, and the delist pass below would then read every
# ``kev=true`` row as "dropped from the feed" and mass-clear the whole flag
# population in one tick. The real CISA catalog has grown monotonically to
# ~1,600 entries since 2021 and CISA removes entries in ones and twos, never
# in bulk; 500 is far below any plausible legitimate catalog size yet far
# above what a truncated / corrupted document parses to. Below the floor the
# tick is treated exactly like a feed outage: WARNING + skip, flags left
# untouched, next daily tick retries.
_FEED_SANITY_FLOOR = 500


def _apply_listing(row: Vulnerability, entry: KevEntry) -> bool:
    """Set the KEV columns from a feed entry. Returns True when anything changed."""
    changed = False
    if row.kev is not True:
        row.kev = True
        changed = True
    if row.kev_date_added != entry.date_added:
        row.kev_date_added = entry.date_added
        changed = True
    if row.kev_due_date != entry.due_date:
        row.kev_due_date = entry.due_date
        changed = True
    return changed


def _apply_delisting(row: Vulnerability) -> bool:
    """Clear the KEV columns for a row no longer in the feed. Returns True when changed."""
    changed = False
    if row.kev is not False:
        row.kev = False
        changed = True
    if row.kev_date_added is not None:
        row.kev_date_added = None
        changed = True
    if row.kev_due_date is not None:
        row.kev_due_date = None
        changed = True
    return changed


def _sync_state_values(summary: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Map a tick summary onto the ``kev_sync_state`` UPSERT column set.

    Pure function (no DB) so the model's writer contract is directly
    unit-testable:

      * **synced** tick → full refresh: ``last_synced_at`` moves to ``now``,
        counters + ``duration_ms`` (float seconds → integer ms) are replaced,
        ``skipped_reason`` clears.
      * **skipped** tick → status-only touch: ``last_result`` /
        ``skipped_reason`` / ``updated_at``. ``last_synced_at`` and the
        counters are deliberately ABSENT from the mapping so the UPSERT's
        ``DO UPDATE SET`` never overwrites the last-good values (model
        docstring: the panel shows "last attempt" AND "last success").
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
        "feed_count": summary["feed_count"],
        "listed": summary["listed"],
        "delisted": summary["delisted"],
        "duration_ms": int(round(summary["duration_seconds"] * 1000)),
        "updated_at": now,
    }


def _persist_sync_state(summary: dict[str, Any]) -> None:
    """UPSERT the single ``kev_sync_state`` row from this tick's summary.

    Postgres ``INSERT .. ON CONFLICT (id) DO UPDATE`` (repo convention —
    same construct as ``services.obligation_service``) keeps the write one
    race-free round-trip against the BOOLEAN-PK singleton row; the first
    tick ever inserts, every later tick updates.

    Runs in its OWN session scope, AFTER the reconcile session has already
    committed — a failure here can therefore never roll back reconcile
    writes. Exceptions propagate to the caller, which treats them as
    best-effort (WARNING, never into the beat).
    """
    from core.db import sync_session_scope

    values = _sync_state_values(summary, datetime.now(tz=UTC))
    stmt = (
        pg_insert(KevSyncState)
        .values(values)
        .on_conflict_do_update(
            index_elements=[KevSyncState.id],
            set_={k: v for k, v in values.items() if k != "id"},
        )
    )
    with sync_session_scope() as session:
        session.execute(stmt)
        session.commit()


@celery_app.task(  # type: ignore[misc]
    name="trustedoss.kev_catalog_refresh",
    bind=True,
    # No autoretry: a transient feed failure is absorbed by the daily cadence
    # (the next tick reconciles from scratch); a persistent one should surface
    # as repeated WARNING logs, not a silent retry loop.
    max_retries=0,
)
def refresh_kev_catalog(self: Any) -> dict[str, Any]:
    """Reconcile ``vulnerabilities.kev*`` columns against the CISA KEV feed.

    Returns a summary dict::

        {
            "skipped": bool,           # True when disabled / feed unavailable
                                       # / feed below the sanity floor
            "skipped_reason": str|None,
            "feed_count": int,         # entries parsed from the feed
            "matched": int,            # catalog rows whose CVE is in the feed
            "listed": int,             # rows flagged / date-corrected this run
            "delisted": int,           # rows cleared (dropped out of the feed)
            "duration_seconds": float,
        }

    The function never raises — every failure mode degrades to a structured
    summary + WARNING so the beat tick stays clean (same contract as
    ``vulnerability_rematch``).
    """
    structlog.contextvars.bind_contextvars(
        task_name="kev_catalog_refresh",
        task_id=str(self.request.id) if self and self.request else None,
    )
    summary: dict[str, Any] = {
        "skipped": False,
        "skipped_reason": None,
        "feed_count": 0,
        "matched": 0,
        "listed": 0,
        "delisted": 0,
        "duration_seconds": 0.0,
    }
    started = time.monotonic()
    try:
        if not kev_refresh_enabled():
            summary["skipped"] = True
            summary["skipped_reason"] = "disabled"
            log.info("kev_catalog_refresh_disabled")
            return summary

        try:
            catalog = fetch_kev_catalog()
        except KevFeedUnavailable as exc:
            # Leave existing flags untouched — a transient CISA outage must
            # not mass-delist the catalog. Next daily tick retries.
            summary["skipped"] = True
            summary["skipped_reason"] = "feed_unavailable"
            log.warning(
                "kev_catalog_refresh_feed_unavailable",
                error=str(exc)[:300],
            )
            return summary

        summary["feed_count"] = len(catalog)

        if len(catalog) < _FEED_SANITY_FLOOR:
            # security-reviewer MAJOR — an empty / gutted (but valid-JSON)
            # document must never reach the delist pass; see the constant's
            # rationale above. Same operator posture as a feed outage.
            summary["skipped"] = True
            summary["skipped_reason"] = "feed_below_sanity_floor"
            log.warning(
                "kev_catalog_refresh_feed_below_sanity_floor",
                feed_count=len(catalog),
                floor=_FEED_SANITY_FLOOR,
            )
            return summary

        from core.db import sync_session_scope

        with sync_session_scope() as session:
            # Listing pass — rows whose external_id appears in the feed.
            # ~1,600 keys in one IN clause is well under Postgres' comfort
            # zone; upper() on both sides makes the match case-insensitive
            # (our catalog stores canonical upper-case CVE ids, but a GHSA-era
            # import may have drifted).
            listed_rows = (
                session.execute(
                    select(Vulnerability).where(
                        func.upper(Vulnerability.external_id).in_(list(catalog.keys()))
                    )
                )
                .scalars()
                .all()
            )
            summary["matched"] = len(listed_rows)
            for row in listed_rows:
                entry = catalog.get(row.external_id.upper())
                if entry is None:  # pragma: no cover — IN clause guarantees hit
                    continue
                if _apply_listing(row, entry):
                    summary["listed"] += 1

            # Delist pass — rows still flagged kev=true whose CVE dropped out
            # of the feed. Rides the partial index ix_vulnerabilities_kev, so
            # this SELECT touches at most ~catalog-size rows.
            flagged_rows = (
                session.execute(
                    select(Vulnerability).where(Vulnerability.kev.is_(True))
                )
                .scalars()
                .all()
            )
            for row in flagged_rows:
                if row.external_id.upper() in catalog:
                    continue
                if _apply_delisting(row):
                    summary["delisted"] += 1

            session.commit()

        summary["duration_seconds"] = time.monotonic() - started
        log.info("kev_catalog_refresh_complete", **summary)
        return summary
    except Exception as exc:  # noqa: BLE001 — task must not raise into the beat
        summary["skipped"] = True
        summary["skipped_reason"] = f"unexpected:{type(exc).__name__}"
        summary["duration_seconds"] = time.monotonic() - started
        log.warning(
            "kev_catalog_refresh_unexpected_error",
            error=str(exc)[:300],
        )
        return summary
    finally:
        # Status-row write covers EVERY exit path (synced, all skip flavours,
        # unexpected error) from one call site. Best-effort by contract: a
        # persist failure must neither undo the already-committed reconcile
        # (it runs in its own session) nor raise into the beat.
        try:
            _persist_sync_state(summary)
        except Exception as exc:  # noqa: BLE001 — best-effort status write
            log.warning(
                "kev_sync_state_persist_failed",
                error=str(exc)[:300],
            )
        structlog.contextvars.unbind_contextvars("task_name", "task_id")


__all__ = [
    "refresh_kev_catalog",
    # Exposed for unit tests.
    "_FEED_SANITY_FLOOR",
    "_apply_delisting",
    "_apply_listing",
    "_persist_sync_state",
    "_sync_state_values",
]
