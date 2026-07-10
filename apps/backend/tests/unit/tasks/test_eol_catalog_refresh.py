"""Unit tests — endoflife.date refresh task internals (Phase M, PR M-3).

DB-free units over the task's pure helpers plus the fetch-half gating,
mirroring ``test_kev_sync_state_persist.py``'s scope split: the real
UPSERT-against-Postgres behaviour belongs to an integration test; here we
pin the contracts that make the tick safe —

  * ``_sync_state_values``: the re-stamp counters ride EVERY tick (the
    re-stamp half runs even on a skipped fetch — unlike KEV), while the
    fetch-derived fields are withheld on a skip so the last-good snapshot
    survives the UPSERT;
  * ``_newer_dataset`` / ``_snapshot_to_dataset``: effective-dataset
    resolution (vendored vs fetched, tie → fetched, garbage loses);
  * ``_fetch_half``: default-OFF gate, feed-outage skip, and the
    proportional sanity floor (≥ half the mapped products) that stops a
    gutted sweep from displacing a good snapshot;
  * ``_clear_stamp``: NULLs every eol_* column exactly once (idempotent).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest

from services.eol.eol_catalog import EolDataset
from tasks.eol_catalog_refresh import (
    _clear_stamp,
    _fetch_half,
    _newer_dataset,
    _snapshot_to_dataset,
    _sync_state_values,
)

NOW = datetime(2026, 7, 12, 2, 15, 3, tzinfo=UTC)


def _base_summary(**overrides: Any) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "skipped": False,
        "skipped_reason": None,
        "products_ok": 10,
        "products_failed": 0,
        "snapshot": {"_snapshot": "2026-07-12", "express": []},
        "snapshot_date": "2026-07-12",
        "stamped": 7,
        "cleared": 2,
        "duration_seconds": 1.2345,
    }
    summary.update(overrides)
    return summary


# ---------------------------------------------------------------------------
# _sync_state_values
# ---------------------------------------------------------------------------


def test_synced_values_carry_everything() -> None:
    values = _sync_state_values(_base_summary(), NOW)
    assert values == {
        "id": True,
        "last_result": "synced",
        "skipped_reason": None,
        "last_synced_at": NOW,
        "snapshot": {"_snapshot": "2026-07-12", "express": []},
        "snapshot_date": "2026-07-12",
        "products_ok": 10,
        "products_failed": 0,
        "stamped": 7,
        "cleared": 2,
        "duration_ms": 1234,  # 1.2345s → rounded integer ms
        "updated_at": NOW,
    }


def test_skipped_values_keep_restamp_counters_but_withhold_fetch_fields() -> None:
    values = _sync_state_values(
        _base_summary(skipped=True, skipped_reason="refresh_disabled"),
        NOW,
    )
    # Re-stamp half ran → its counters persist even on a skipped fetch.
    assert values["stamped"] == 7
    assert values["cleared"] == 2
    assert values["last_result"] == "skipped"
    assert values["skipped_reason"] == "refresh_disabled"
    # Fetch-derived fields ABSENT so the UPSERT preserves last-good values.
    for withheld in (
        "last_synced_at",
        "snapshot",
        "snapshot_date",
        "products_ok",
        "products_failed",
    ):
        assert withheld not in values


# ---------------------------------------------------------------------------
# _snapshot_to_dataset / _newer_dataset
# ---------------------------------------------------------------------------


def test_snapshot_to_dataset_roundtrip_and_garbage() -> None:
    good = {"_snapshot": "2026-07-01", "express": [{"cycle": "4", "eol": False}]}
    dataset = _snapshot_to_dataset(good)
    assert dataset is not None
    assert dataset.snapshot == "2026-07-01"
    assert dataset.cycles("express")

    assert _snapshot_to_dataset(None) is None
    assert _snapshot_to_dataset("garbage") is None
    assert _snapshot_to_dataset({"express": []}) is None  # no _snapshot
    assert _snapshot_to_dataset({"_snapshot": "2026-07-01"}) is None  # no products


@pytest.mark.parametrize(
    ("vendored_date", "fetched_date", "winner"),
    [
        ("2026-07-01", "2026-07-08", "fetched"),
        ("2026-07-08", "2026-07-01", "vendored"),
        ("2026-07-08", "2026-07-08", "fetched"),  # tie → fetched
        ("garbage", "2026-07-01", "fetched"),  # unparseable loses
        ("2026-07-01", "garbage", "vendored"),
    ],
)
def test_newer_dataset_prefers_the_fresher_snapshot(
    vendored_date: str, fetched_date: str, winner: str
) -> None:
    vendored = EolDataset(snapshot=vendored_date, products={"a": []})
    fetched = EolDataset(snapshot=fetched_date, products={"b": []})
    result = _newer_dataset(vendored, fetched)
    assert result is (fetched if winner == "fetched" else vendored)


def test_newer_dataset_handles_missing_sides() -> None:
    only = EolDataset(snapshot="2026-07-01", products={"a": []})
    assert _newer_dataset(None, only) is only
    assert _newer_dataset(only, None) is only
    assert _newer_dataset(None, None) is None


# ---------------------------------------------------------------------------
# _fetch_half — gating + sanity floor
# ---------------------------------------------------------------------------


def test_fetch_half_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EOL_REFRESH_ENABLED", raising=False)
    summary = _base_summary(products_ok=0, snapshot=None)
    assert _fetch_half(summary) is None
    assert summary["skipped"] is True
    assert summary["skipped_reason"] == "refresh_disabled"


def test_fetch_half_feed_unavailable_skips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from integrations.eol_feed import EolFeedUnavailable

    monkeypatch.setenv("EOL_REFRESH_ENABLED", "true")

    def _boom(products: list[str]) -> Any:
        raise EolFeedUnavailable("all down")

    monkeypatch.setattr("tasks.eol_catalog_refresh.fetch_eol_dataset", _boom)
    summary = _base_summary()
    assert _fetch_half(summary) is None
    assert summary["skipped_reason"] == "feed_unavailable"


def test_fetch_half_sanity_floor_rejects_gutted_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from integrations.eol_feed import EolFetchResult

    monkeypatch.setenv("EOL_REFRESH_ENABLED", "true")

    def _mostly_failed(products: list[str]) -> EolFetchResult:
        return EolFetchResult(
            dataset={"_snapshot": "2026-07-12", products[0]: []},
            fetched=[products[0]],  # 1 of 10 — far below the floor
            failed=products[1:],
        )

    monkeypatch.setattr(
        "tasks.eol_catalog_refresh.fetch_eol_dataset", _mostly_failed
    )
    summary = _base_summary(snapshot=None)
    assert _fetch_half(summary) is None
    assert summary["skipped_reason"] == "feed_below_sanity_floor"


def test_fetch_half_accepts_a_healthy_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from integrations.eol_feed import EolFetchResult

    monkeypatch.setenv("EOL_REFRESH_ENABLED", "true")

    def _healthy(products: list[str]) -> EolFetchResult:
        dataset: dict[str, Any] = {"_snapshot": "2026-07-12"}
        for product in products:
            dataset[product] = [{"cycle": "1", "eol": False}]
        return EolFetchResult(dataset=dataset, fetched=list(products), failed=[])

    monkeypatch.setattr("tasks.eol_catalog_refresh.fetch_eol_dataset", _healthy)
    summary = _base_summary(snapshot=None, products_ok=0)
    dataset = _fetch_half(summary)
    assert dataset is not None
    assert summary["skipped"] is False
    assert summary["products_ok"] > 0
    assert summary["snapshot_date"] == "2026-07-12"


# ---------------------------------------------------------------------------
# _clear_stamp
# ---------------------------------------------------------------------------


class _FakeComponentVersion:
    eol_state: str | None = "eol"
    eol_product: str | None = "express"
    eol_cycle: str | None = "3"
    eol_date: date | None = date(2020, 1, 1)
    eol_source: str | None = "endoflife.date@2026-01-01"
    eol_evaluated_at: datetime | None = None


def test_clear_stamp_nulls_everything_once() -> None:
    row = _FakeComponentVersion()
    assert _clear_stamp(row) is True  # type: ignore[arg-type]
    assert row.eol_state is None
    assert row.eol_product is None
    assert row.eol_cycle is None
    assert row.eol_date is None
    assert row.eol_source is None
    assert row.eol_evaluated_at is not None

    stamp_time = row.eol_evaluated_at
    assert _clear_stamp(row) is False  # type: ignore[arg-type]
    assert row.eol_evaluated_at == stamp_time  # idempotent — not re-dirtied
