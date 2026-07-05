"""
Service-layer tests for ``services.kev_health_service`` — Phase C (C2).

Coverage targets:
  - The three row states: absent (never ran) / synced / skipped — field
    mapping incl. ``last_attempt_at = kev_sync_state.updated_at``.
  - ``next_refresh_at`` derived from the LIVE Celery beat crontab (before /
    after the daily fire — deterministic via ``remaining_delta``).
  - Config propagation: ``KEV_REFRESH_ENABLED`` toggle, ``KEV_FEED_URL``
    host-only extraction (mirror URLs keep their path/credentials private).
  - Graceful degrade: a DB read failure or an out-of-vocabulary
    ``last_result`` value returns the config-only payload, never raises.
  - ``KevFeedStatusOut`` schema: JSON round-trip and the closed
    ``last_result`` vocabulary.

Pure unit tests — no database. The service takes an ``AsyncSession`` but only
calls ``session.get`` / ``session.execute``, so a duck-typed fake drives all
branches (repo convention: _FakeSession stand-ins, see mypy test overrides).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from models import KevSyncState
from schemas.admin_ops import KevFeedStatusOut
from services.kev_health_service import compute_next_refresh_at, get_kev_feed_health

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, value: int) -> None:
        self._value = value

    def scalar_one(self) -> int:
        return self._value


class _FakeSession:
    """Duck-typed AsyncSession: ``get`` serves the singleton row, ``execute``
    serves the partial-index count."""

    def __init__(self, row: KevSyncState | None, flagged: int = 0) -> None:
        self._row = row
        self._flagged = flagged

    async def get(self, model: type, pk: object) -> KevSyncState | None:
        assert model is KevSyncState
        assert pk is True  # BOOLEAN singleton PK
        return self._row

    async def execute(self, stmt: object) -> _FakeResult:
        return _FakeResult(self._flagged)


class _BoomSession:
    async def get(self, model: type, pk: object) -> None:
        raise RuntimeError("connection yanked")

    async def execute(self, stmt: object) -> None:
        raise RuntimeError("connection yanked")


def _synced_row() -> KevSyncState:
    return KevSyncState(
        id=True,
        last_synced_at=datetime(2026, 7, 1, 1, 45, 3, tzinfo=UTC),
        last_result="synced",
        skipped_reason=None,
        feed_count=1602,
        listed=4,
        delisted=1,
        duration_ms=2314,
        updated_at=datetime(2026, 7, 1, 1, 45, 3, tzinfo=UTC),
    )


def _skipped_row() -> KevSyncState:
    """A skip AFTER an earlier success — last-good values preserved."""
    return KevSyncState(
        id=True,
        last_synced_at=datetime(2026, 7, 1, 1, 45, 3, tzinfo=UTC),
        last_result="skipped",
        skipped_reason="feed_unavailable",
        feed_count=1602,
        listed=4,
        delisted=1,
        duration_ms=2314,
        updated_at=datetime(2026, 7, 2, 1, 45, 1, tzinfo=UTC),
    )


# ---------------------------------------------------------------------------
# Row states
# ---------------------------------------------------------------------------


async def test_row_absent_returns_never_ran_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No kev_sync_state row = the refresh never ran: every sync-derived
    field None, config-derived fields still populated."""
    monkeypatch.delenv("KEV_REFRESH_ENABLED", raising=False)
    monkeypatch.delenv("KEV_FEED_URL", raising=False)

    out = await get_kev_feed_health(_FakeSession(row=None, flagged=0))

    assert isinstance(out, KevFeedStatusOut)
    assert out.enabled is True  # default
    assert out.last_synced_at is None
    assert out.last_attempt_at is None
    assert out.last_result is None
    assert out.skipped_reason is None
    assert out.feed_count is None
    assert out.listed is None
    assert out.delisted is None
    assert out.duration_ms is None
    # The live count is still valid without a status row.
    assert out.kev_flagged_total == 0
    assert out.next_refresh_at is not None
    assert out.feed_host == "www.cisa.gov"


async def test_synced_row_maps_all_fields() -> None:
    out = await get_kev_feed_health(_FakeSession(row=_synced_row(), flagged=137))

    assert out.last_result == "synced"
    assert out.skipped_reason is None
    assert out.last_synced_at == datetime(2026, 7, 1, 1, 45, 3, tzinfo=UTC)
    assert out.last_attempt_at == datetime(2026, 7, 1, 1, 45, 3, tzinfo=UTC)
    assert out.feed_count == 1602
    assert out.listed == 4
    assert out.delisted == 1
    assert out.duration_ms == 2314
    assert out.kev_flagged_total == 137


async def test_skipped_row_keeps_last_good_success_fields() -> None:
    """Skip after success: last_attempt_at moves, last_synced_at + counters
    stay at last-good — the FE distinguishes 'attempting and failing'."""
    out = await get_kev_feed_health(_FakeSession(row=_skipped_row(), flagged=137))

    assert out.last_result == "skipped"
    assert out.skipped_reason == "feed_unavailable"
    assert out.last_attempt_at == datetime(2026, 7, 2, 1, 45, 1, tzinfo=UTC)
    assert out.last_synced_at == datetime(2026, 7, 1, 1, 45, 3, tzinfo=UTC)
    assert out.last_attempt_at is not None and out.last_synced_at is not None
    assert out.last_attempt_at > out.last_synced_at
    assert out.feed_count == 1602  # preserved, not zeroed by the skip


# ---------------------------------------------------------------------------
# next_refresh_at — derived from the live beat schedule
# ---------------------------------------------------------------------------


def test_next_refresh_before_daily_fire_is_same_day() -> None:
    """00:00 UTC → next fire is that day's 01:45 UTC (current beat spec)."""
    now = datetime(2026, 7, 2, 0, 0, tzinfo=UTC)
    assert compute_next_refresh_at(now=now) == datetime(2026, 7, 2, 1, 45, tzinfo=UTC)


def test_next_refresh_after_daily_fire_rolls_to_next_day() -> None:
    now = datetime(2026, 7, 2, 2, 0, tzinfo=UTC)
    assert compute_next_refresh_at(now=now) == datetime(2026, 7, 3, 1, 45, tzinfo=UTC)


def test_next_refresh_missing_entry_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A renamed / removed beat entry degrades the field to None, no raise."""
    from tasks.celery_app import celery_app

    pruned = {
        k: v
        for k, v in celery_app.conf.beat_schedule.items()
        if k != "kev-catalog-refresh-daily"
    }
    monkeypatch.setattr(celery_app.conf, "beat_schedule", pruned)
    assert compute_next_refresh_at(now=datetime(2026, 7, 2, tzinfo=UTC)) is None


def test_next_refresh_malformed_entry_degrades_to_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A schedule object without crontab semantics (operator swapped the
    entry to something exotic) degrades to None with a WARNING, no raise."""
    from tasks.celery_app import KEV_BEAT_ENTRY_NAME, celery_app

    mangled = dict(celery_app.conf.beat_schedule)
    mangled[KEV_BEAT_ENTRY_NAME] = {"task": "x", "schedule": object()}
    monkeypatch.setattr(celery_app.conf, "beat_schedule", mangled)
    assert compute_next_refresh_at(now=datetime(2026, 7, 2, tzinfo=UTC)) is None


def test_next_refresh_matches_beat_entry_not_a_hardcoded_copy() -> None:
    """Contract guard (hardening rule 2 spirit): the service derives the time
    from the SAME schedule object the beat runs — verified by asking the
    crontab directly and comparing."""
    from tasks.celery_app import KEV_BEAT_ENTRY_NAME, celery_app

    schedule = celery_app.conf.beat_schedule[KEV_BEAT_ENTRY_NAME]["schedule"]
    now = datetime(2026, 7, 2, 12, 34, tzinfo=UTC)
    start, delta, _ = schedule.remaining_delta(now)
    assert compute_next_refresh_at(now=now) == start + delta


# ---------------------------------------------------------------------------
# Config propagation
# ---------------------------------------------------------------------------


async def test_disabled_toggle_reflected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KEV_REFRESH_ENABLED", "false")
    out = await get_kev_feed_health(_FakeSession(row=None, flagged=0))
    assert out.enabled is False
    # The beat still ticks (recording ``disabled`` skips), so the next
    # attempt time remains truthful even when disabled.
    assert out.next_refresh_at is not None


async def test_feed_host_is_host_only_for_mirror_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mirror URL's path / port / userinfo never reach the response."""
    monkeypatch.setenv(
        "KEV_FEED_URL",
        "https://svc:token@mirror.internal:8443/feeds/kev/known_exploited.json",
    )
    out = await get_kev_feed_health(_FakeSession(row=None, flagged=0))
    assert out.feed_host == "mirror.internal"


# ---------------------------------------------------------------------------
# Graceful degrade
# ---------------------------------------------------------------------------


async def test_db_failure_degrades_to_config_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("KEV_FEED_URL", raising=False)
    out = await get_kev_feed_health(_BoomSession())

    assert isinstance(out, KevFeedStatusOut)
    assert out.kev_flagged_total is None  # count could not be read
    assert out.last_attempt_at is None
    assert out.enabled is True
    assert out.feed_host == "www.cisa.gov"
    assert out.next_refresh_at is not None


async def test_out_of_vocabulary_last_result_degrades_not_raises() -> None:
    """A corrupted last_result value fails the closed-Literal validation
    inside the guarded block → config-only payload, no 500."""
    garbage = _synced_row()
    garbage.last_result = "exploded"
    out = await get_kev_feed_health(_FakeSession(row=garbage, flagged=1))

    assert isinstance(out, KevFeedStatusOut)
    assert out.last_result is None
    assert out.kev_flagged_total is None


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_schema_round_trips_json() -> None:
    out = KevFeedStatusOut(
        enabled=True,
        last_synced_at=datetime(2026, 7, 1, 1, 45, 3, tzinfo=UTC),
        last_attempt_at=datetime(2026, 7, 2, 1, 45, 1, tzinfo=UTC),
        last_result="skipped",
        skipped_reason="feed_below_sanity_floor",
        feed_count=1602,
        listed=4,
        delisted=1,
        duration_ms=2314,
        kev_flagged_total=137,
        next_refresh_at=datetime(2026, 7, 3, 1, 45, tzinfo=UTC),
        feed_host="www.cisa.gov",
    )
    payload = json.loads(out.model_dump_json())
    assert set(payload.keys()) == {
        "enabled",
        "last_synced_at",
        "last_attempt_at",
        "last_result",
        "skipped_reason",
        "feed_count",
        "listed",
        "delisted",
        "duration_ms",
        "kev_flagged_total",
        "next_refresh_at",
        "feed_host",
    }
    assert payload["last_result"] == "skipped"


def test_schema_rejects_unknown_last_result() -> None:
    with pytest.raises(ValidationError):
        KevFeedStatusOut(enabled=True, last_result="partial")


def test_schema_rejects_negative_counters() -> None:
    with pytest.raises(ValidationError):
        KevFeedStatusOut(enabled=True, feed_count=-1)


def test_schema_empty_state_serialisable() -> None:
    """Never-ran shape: only ``enabled`` is required."""
    out = KevFeedStatusOut(enabled=False)
    payload = json.loads(out.model_dump_json())
    assert payload["last_attempt_at"] is None
    assert payload["kev_flagged_total"] is None
