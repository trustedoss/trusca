"""
Unit tests for the kev_sync_state persist wiring — Phase C (C1 잔여).

Two contracts under test, both DB-free:

  1. ``_sync_state_values`` (pure mapping) implements the writer contract of
     ``models/kev_sync_state.py``: a synced tick replaces everything
     (float-seconds → integer-ms conversion included); a skipped tick maps
     ONLY ``last_result`` / ``skipped_reason`` / ``updated_at`` so the UPSERT
     can never claw back ``last_synced_at`` or the last-good counters.
  2. The persist call is best-effort: a blow-up inside ``_persist_sync_state``
     must not leak out of ``refresh_kev_catalog`` (the task's "never raises
     into the beat" contract) and must not alter the returned summary.

The actual UPSERT-against-Postgres behaviour (insert vs update, skip
preservation on a real row) lives in
``tests/integration/test_kev_catalog_refresh.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tasks.kev_catalog_refresh import (
    _persist_sync_state,
    _sync_state_values,
    refresh_kev_catalog,
)

NOW = datetime(2026, 7, 2, 1, 45, 3, tzinfo=UTC)


def _synced_summary() -> dict[str, object]:
    return {
        "skipped": False,
        "skipped_reason": None,
        "feed_count": 1602,
        "matched": 40,
        "listed": 4,
        "delisted": 1,
        "duration_seconds": 2.3138,
    }


# ---------------------------------------------------------------------------
# _sync_state_values — the writer contract as a pure mapping
# ---------------------------------------------------------------------------


def test_synced_values_replace_everything() -> None:
    values = _sync_state_values(_synced_summary(), NOW)
    assert values == {
        "id": True,
        "last_synced_at": NOW,
        "last_result": "synced",
        "skipped_reason": None,
        "feed_count": 1602,
        "listed": 4,
        "delisted": 1,
        "duration_ms": 2314,  # 2.3138 s → rounded integer ms
        "updated_at": NOW,
    }


def test_duration_seconds_to_ms_rounding() -> None:
    summary = _synced_summary()
    summary["duration_seconds"] = 0.0004  # sub-ms run rounds to 0, not negative
    assert _sync_state_values(summary, NOW)["duration_ms"] == 0
    summary["duration_seconds"] = 59.9996
    assert _sync_state_values(summary, NOW)["duration_ms"] == 60000


@pytest.mark.parametrize(
    "reason",
    [
        "disabled",
        "feed_unavailable",
        "feed_below_sanity_floor",
        "unexpected:RuntimeError",
    ],
)
def test_skipped_values_touch_status_columns_only(reason: str) -> None:
    """The mapping must OMIT last_synced_at and the counters entirely — their
    absence from the DO UPDATE SET is what preserves the last-good values."""
    summary: dict[str, object] = {
        "skipped": True,
        "skipped_reason": reason,
        "feed_count": 3,  # e.g. below-floor tick still parsed a few entries
        "matched": 0,
        "listed": 0,
        "delisted": 0,
        "duration_seconds": 0.01,
    }
    values = _sync_state_values(summary, NOW)
    assert values == {
        "id": True,
        "last_result": "skipped",
        "skipped_reason": reason,
        "updated_at": NOW,
    }
    for forbidden in ("last_synced_at", "feed_count", "listed", "delisted", "duration_ms"):
        assert forbidden not in values


def test_skipped_reason_fits_column_width() -> None:
    """skipped_reason is VARCHAR(64) — the longest task-produced form
    (``unexpected:<ExceptionName>``) must fit for realistic exception names."""
    summary: dict[str, object] = {
        "skipped": True,
        "skipped_reason": "unexpected:" + "SQLAlchemyOperationalError",
        "feed_count": 0,
        "matched": 0,
        "listed": 0,
        "delisted": 0,
        "duration_seconds": 0.0,
    }
    reason = _sync_state_values(summary, NOW)["skipped_reason"]
    assert isinstance(reason, str) and len(reason) <= 64


# ---------------------------------------------------------------------------
# Best-effort persist — never raises into the beat
# ---------------------------------------------------------------------------


def test_persist_failure_does_not_leak_out_of_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The finally-block persist is wrapped: a DB outage at status-write time
    degrades to a WARNING, the tick's summary still returns."""
    from tasks import kev_catalog_refresh as task_module

    monkeypatch.setenv("KEV_REFRESH_ENABLED", "false")  # cheapest exit path

    def _boom(summary: dict[str, object]) -> None:
        raise RuntimeError("status table unavailable")

    monkeypatch.setattr(task_module, "_persist_sync_state", _boom)

    summary = refresh_kev_catalog.run()
    assert summary["skipped"] is True
    assert summary["skipped_reason"] == "disabled"


def test_persist_uses_its_own_session_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    """_persist_sync_state opens core.db.sync_session_scope itself (separate
    from the reconcile session) — verified by intercepting the scope and
    asserting the UPSERT statement lands on it."""
    import core.db as core_db

    executed: list[object] = []

    class _Session:
        def execute(self, stmt: object) -> None:
            executed.append(stmt)

        def commit(self) -> None:
            executed.append("commit")

    from contextlib import contextmanager

    @contextmanager
    def _fake_scope():
        yield _Session()

    monkeypatch.setattr(core_db, "sync_session_scope", _fake_scope)

    _persist_sync_state(_synced_summary())

    assert executed[-1] == "commit"
    # The one non-commit entry is the ON CONFLICT upsert against the
    # singleton table (compile with the pg dialect — ON CONFLICT is
    # Postgres-only and str() would use the default dialect).
    from typing import Any, cast

    from sqlalchemy.dialects import postgresql

    stmt_sql = str(cast(Any, executed[0]).compile(dialect=postgresql.dialect()))
    assert "kev_sync_state" in stmt_sql
    assert "ON CONFLICT" in stmt_sql
