"""
endoflife.date catalog refresh — weekly Celery beat (Phase M, PR M-3).

Structural mirror of :mod:`tasks.kev_catalog_refresh` with one architectural
difference: the tick has TWO halves with different network postures.

  1. **Fetch half** (env-gated, default OFF — ``EOL_REFRESH_ENABLED``):
     pulls fresh per-product data from endoflife.date via
     :func:`integrations.eol_feed.fetch_eol_dataset`, applies a sanity floor
     (at least half the map's products must have fetched — a gutted sweep
     must never displace a good dataset), and persists the accepted snapshot
     into the ``eol_sync_state`` row (JSONB, a few KB).
  2. **Re-stamp half** (ALWAYS runs — pure local, no egress): resolves the
     effective dataset as the NEWER of (vendored/override snapshot, the
     sync-state row's fetched snapshot) and re-evaluates every catalog row
     that either matches a whitelist prefix or already carries a stamp.
     This is what makes an image upgrade (newer vendored snapshot) reach
     existing rows on air-gapped installs, and what clears stale stamps
     when the whitelist shrinks — the per-scan persist hook deliberately
     never clears (its job is enrichment, not reconciliation).

Staleness contract: the per-scan hook stamps from the vendored/override
snapshot only (no DB read on the persist path); a fetched-but-newer dataset
reaches rows on the next weekly tick, so persist-time staleness is bounded
by the beat cadence.

Failure isolation, idempotency, air-gap posture and the status-row write all
follow the KEV task line by line (never raises into the beat; changed-value
guards make re-runs no-ops; the ``finally`` UPSERT covers every exit path).
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime
from typing import Any

import structlog
from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.config import eol_enabled, eol_refresh_enabled
from integrations.eol_feed import EolFeedUnavailable, fetch_eol_dataset
from models import ComponentVersion, EolSyncState
from services.eol.eol_catalog import (
    EolDataset,
    load_dataset,
    load_rules,
    stamp_component_version,
)
from tasks.celery_app import celery_app

log = structlog.get_logger("tasks.eol_catalog_refresh")


def _snapshot_to_dataset(raw: Any) -> EolDataset | None:
    """Parse a stored sync-state snapshot dict into an EolDataset (or None)."""
    if not isinstance(raw, dict):
        return None
    snapshot = raw.get("_snapshot")
    if not isinstance(snapshot, str) or not snapshot:
        return None
    products = {
        key: value
        for key, value in raw.items()
        if not key.startswith("_") and isinstance(value, list)
    }
    if not products:
        return None
    return EolDataset(snapshot=snapshot, products=products)


def _newer_dataset(
    vendored: EolDataset | None, fetched: EolDataset | None
) -> EolDataset | None:
    """Prefer the dataset with the more recent ``_snapshot`` date.

    Tie goes to the FETCHED one (it reflects a live confirmation of the same
    day's data). Unparseable snapshot dates lose to parseable ones.
    """
    if vendored is None:
        return fetched
    if fetched is None:
        return vendored

    def _parse(dataset: EolDataset) -> date | None:
        try:
            return date.fromisoformat(dataset.snapshot)
        except ValueError:
            return None

    vendored_date, fetched_date = _parse(vendored), _parse(fetched)
    if fetched_date is None:
        return vendored
    if vendored_date is None:
        return fetched
    return vendored if vendored_date > fetched_date else fetched


def _clear_stamp(row: ComponentVersion) -> bool:
    """NULL every eol_* column for a row no rule covers any more."""
    changed = False
    for attr in ("eol_state", "eol_product", "eol_cycle", "eol_date", "eol_source"):
        if getattr(row, attr) is not None:
            setattr(row, attr, None)
            changed = True
    if changed:
        row.eol_evaluated_at = datetime.now(tz=UTC)
    return changed


def _sync_state_values(summary: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Map a tick summary onto the ``eol_sync_state`` UPSERT column set.

    Unlike the KEV mapping, the re-stamp counters (``stamped`` / ``cleared``)
    and ``duration_ms`` ride EVERY tick — the re-stamp half runs regardless
    of the fetch outcome. Only the fetch-derived fields (``last_synced_at``,
    ``snapshot``, ``snapshot_date``, ``products_*``) are withheld on a
    skipped fetch so the last-good snapshot survives.
    """
    values: dict[str, Any] = {
        "id": True,
        "stamped": summary["stamped"],
        "cleared": summary["cleared"],
        "duration_ms": int(round(summary["duration_seconds"] * 1000)),
        "updated_at": now,
    }
    if summary["skipped"]:
        values["last_result"] = "skipped"
        values["skipped_reason"] = summary["skipped_reason"]
        return values
    values.update(
        {
            "last_result": "synced",
            "skipped_reason": None,
            "last_synced_at": now,
            "snapshot": summary["snapshot"],
            "snapshot_date": summary["snapshot_date"],
            "products_ok": summary["products_ok"],
            "products_failed": summary["products_failed"],
        }
    )
    return values


def _persist_sync_state(summary: dict[str, Any]) -> None:
    """UPSERT the single ``eol_sync_state`` row (kev_catalog_refresh idiom)."""
    from core.db import sync_session_scope

    values = _sync_state_values(summary, datetime.now(tz=UTC))
    stmt = (
        pg_insert(EolSyncState)
        .values(values)
        .on_conflict_do_update(
            index_elements=[EolSyncState.id],
            set_={k: v for k, v in values.items() if k != "id"},
        )
    )
    with sync_session_scope() as session:
        session.execute(stmt)
        session.commit()


def _fetch_half(summary: dict[str, Any]) -> EolDataset | None:
    """Run the env-gated fetch; returns the accepted dataset or ``None``.

    Every skip flavour mirrors the KEV task's vocabulary; the sanity floor
    is proportional (≥ half the map's products) because the population is a
    10-product whitelist, not a 1,600-entry monotonic catalog.
    """
    if not eol_refresh_enabled():
        summary["skipped"] = True
        summary["skipped_reason"] = "refresh_disabled"
        return None

    products = [rule.product for rule in load_rules()]
    # Order-preserving dedup — several rules share a product (spring-boot).
    products = list(dict.fromkeys(products))
    if not products:
        summary["skipped"] = True
        summary["skipped_reason"] = "no_products_mapped"
        return None

    try:
        result = fetch_eol_dataset(products)
    except EolFeedUnavailable as exc:
        summary["skipped"] = True
        summary["skipped_reason"] = "feed_unavailable"
        log.warning("eol_catalog_refresh_feed_unavailable", error=str(exc)[:300])
        return None

    floor = (len(products) + 1) // 2
    if len(result.fetched) < floor:
        # A mostly-failed sweep must never displace the last good snapshot —
        # the missing products would all evaluate to "unknown" and a later
        # good sweep would churn them back. Same posture as an outage.
        summary["skipped"] = True
        summary["skipped_reason"] = "feed_below_sanity_floor"
        log.warning(
            "eol_catalog_refresh_feed_below_sanity_floor",
            fetched=len(result.fetched),
            floor=floor,
        )
        return None

    summary["products_ok"] = len(result.fetched)
    summary["products_failed"] = len(result.failed)
    summary["snapshot"] = result.dataset
    summary["snapshot_date"] = result.dataset.get("_snapshot")
    return _snapshot_to_dataset(result.dataset)


@celery_app.task(  # type: ignore[misc]
    name="trustedoss.eol_catalog_refresh",
    bind=True,
    # No autoretry — weekly cadence absorbs transient failures (KEV idiom).
    max_retries=0,
)
def refresh_eol_catalog(self: Any) -> dict[str, Any]:
    """Fetch (optional) + re-stamp the component catalog's EOL columns.

    Returns a summary dict; never raises into the beat::

        {
            "skipped": bool,            # the FETCH half's outcome
            "skipped_reason": str|None,
            "products_ok": int, "products_failed": int,
            "snapshot": dict|None,      # accepted dataset (persisted)
            "snapshot_date": str|None,
            "stamped": int,             # rows (re)stamped this tick
            "cleared": int,             # stale stamps cleared this tick
            "duration_seconds": float,
        }
    """
    structlog.contextvars.bind_contextvars(
        task_name="eol_catalog_refresh",
        task_id=str(self.request.id) if self and self.request else None,
    )
    summary: dict[str, Any] = {
        "skipped": False,
        "skipped_reason": None,
        "products_ok": 0,
        "products_failed": 0,
        "snapshot": None,
        "snapshot_date": None,
        "stamped": 0,
        "cleared": 0,
        "duration_seconds": 0.0,
    }
    started = time.monotonic()
    try:
        if not eol_enabled():
            # Feature fully off — no fetch, no re-stamp, and the status row
            # records the disabled tick.
            summary["skipped"] = True
            summary["skipped_reason"] = "disabled"
            log.info("eol_catalog_refresh_disabled")
            return summary

        fetched_dataset = _fetch_half(summary)

        from core.db import sync_session_scope

        with sync_session_scope() as session:
            # Effective dataset: newest of (vendored/override, fetched-now,
            # fetched-earlier from the status row). A fetch this tick already
            # IS the newest network state; otherwise fall back to whatever
            # the row stored on a previous tick.
            stored_dataset: EolDataset | None = None
            if fetched_dataset is None:
                state_row = session.get(EolSyncState, True)
                if state_row is not None:
                    stored_dataset = _snapshot_to_dataset(state_row.snapshot)
            effective = _newer_dataset(
                load_dataset(), fetched_dataset or stored_dataset
            )
            if effective is None:
                # No usable dataset anywhere — nothing to re-stamp against.
                if not summary["skipped"]:
                    summary["skipped"] = True
                    summary["skipped_reason"] = "no_dataset"
                log.warning("eol_catalog_refresh_no_dataset")
                return summary

            rules = load_rules()
            now = datetime.now(tz=UTC)
            today = now.date()
            # Bounded SELECT: rows matching any whitelist prefix (the stamp
            # set) OR already stamped (the clear set — covers a shrunk map).
            # %40-encoded npm scopes are matched by a second LIKE per rule
            # whose prefix contains '@' (cdxgen emits both spellings).
            like_clauses = []
            for rule in rules:
                like_clauses.append(
                    ComponentVersion.purl_with_version.like(rule.purl_prefix + "%")
                )
                if "@" in rule.purl_prefix:
                    like_clauses.append(
                        ComponentVersion.purl_with_version.like(
                            rule.purl_prefix.replace("@", "%40") + "%"
                        )
                    )
            like_clauses.append(ComponentVersion.eol_state.is_not(None))
            rows = (
                session.execute(
                    select(ComponentVersion).where(or_(*like_clauses))
                )
                .scalars()
                .all()
            )

            from services.eol.eol_catalog import evaluate

            for row in rows:
                verdict = evaluate(
                    row.purl_with_version,
                    row.version,
                    rules=rules,
                    dataset=effective,
                    today=today,
                )
                if verdict is None:
                    # No rule covers this purl (map shrank) — clear any
                    # stale stamp; an unstamped row is untouched.
                    if _clear_stamp(row):
                        summary["cleared"] += 1
                    continue
                if stamp_component_version(row, verdict, now):
                    summary["stamped"] += 1

            session.commit()

        summary["duration_seconds"] = time.monotonic() - started
        log.info(
            "eol_catalog_refresh_complete",
            **{k: v for k, v in summary.items() if k != "snapshot"},
        )
        return summary
    except Exception as exc:  # noqa: BLE001 — task must not raise into the beat
        summary["skipped"] = True
        summary["skipped_reason"] = f"unexpected:{type(exc).__name__}"
        summary["duration_seconds"] = time.monotonic() - started
        log.warning("eol_catalog_refresh_unexpected_error", error=str(exc)[:300])
        return summary
    finally:
        # Status-row write covers EVERY exit path from one call site
        # (kev_catalog_refresh idiom). Best-effort by contract.
        try:
            _persist_sync_state(summary)
        except Exception as exc:  # noqa: BLE001 — best-effort status write
            log.warning(
                "eol_sync_state_persist_failed",
                error=str(exc)[:300],
            )
        structlog.contextvars.unbind_contextvars("task_name", "task_id")


__all__ = [
    "refresh_eol_catalog",
    # Exposed for unit tests.
    "_clear_stamp",
    "_fetch_half",
    "_newer_dataset",
    "_persist_sync_state",
    "_snapshot_to_dataset",
    "_sync_state_values",
]
