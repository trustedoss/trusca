# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Unit tests for ``tasks.queue_backlog_alert`` - S6
(concurrency-scaling-plan-2026-08-22.md §3.2/§4).

Coverage:
  - ``_evaluate``: the pure sustained-breach decision function, driven with
    plain values (no Redis, no Celery) - below threshold, first breach,
    breach not yet sustained, sustained → alert, cooldown suppresses a
    repeat, cooldown elapsed → re-alert while still breached, boundary at
    exactly the sustain window.
  - ``_threshold_for_queue``: routes the scan queue and the default queue to
    their own config accessor.
  - ``_run_check`` (the I/O shell): disabled → skip; M2 off → skip; enabled +
    breached + sustained → one notification enqueued with the right kind and
    channels; below threshold → no notification and state cleared; a broker
    error surfaces as zero backlog (never crashes the tick, per
    ``services.metrics_service``'s own N10 rule) rather than raising.
  - The Celery task wrapper never raises on an unexpected error.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from tasks import queue_backlog_alert as qba_module
from tasks.queue_backlog_alert import (
    QueueBacklogState,
    _evaluate,
    _run_check,
    _threshold_for_queue,
    queue_backlog_alert_check,
)

# ---------------------------------------------------------------------------
# _evaluate - pure decision function
# ---------------------------------------------------------------------------


def test_at_or_under_threshold_clears_state_and_never_alerts() -> None:
    decision = _evaluate(
        backlog=5,
        threshold=10,
        now=1_000.0,
        sustain_seconds=600,
        cooldown_seconds=3600,
        state=QueueBacklogState(breach_since=900.0, alerted_at=800.0),
    )
    assert decision.should_alert is False
    assert decision.next_state == QueueBacklogState(breach_since=None, alerted_at=None)


def test_at_exactly_threshold_counts_as_not_breached() -> None:
    """Boundary: ``<=`` is "normal", strictly over is what starts a breach."""
    decision = _evaluate(
        backlog=10,
        threshold=10,
        now=1_000.0,
        sustain_seconds=600,
        cooldown_seconds=3600,
        state=QueueBacklogState(),
    )
    assert decision.should_alert is False
    assert decision.next_state == QueueBacklogState()


def test_first_observation_over_threshold_starts_the_clock_without_alerting() -> None:
    decision = _evaluate(
        backlog=25,
        threshold=10,
        now=1_000.0,
        sustain_seconds=600,
        cooldown_seconds=3600,
        state=QueueBacklogState(),
    )
    assert decision.should_alert is False
    assert decision.next_state == QueueBacklogState(breach_since=1_000.0, alerted_at=None)
    assert decision.sustained_seconds == 0.0


def test_breach_not_yet_sustained_keeps_waiting() -> None:
    decision = _evaluate(
        backlog=25,
        threshold=10,
        now=1_300.0,  # 300s since breach_since
        sustain_seconds=600,
        cooldown_seconds=3600,
        state=QueueBacklogState(breach_since=1_000.0),
    )
    assert decision.should_alert is False
    assert decision.next_state == QueueBacklogState(breach_since=1_000.0, alerted_at=None)
    assert decision.sustained_seconds == 300.0


def test_sustained_exactly_the_window_alerts() -> None:
    """Boundary: ``>=`` sustain_seconds belongs to this tick, same convention
    as ``tasks.vuln_sla_sweep``'s window comparison."""
    decision = _evaluate(
        backlog=25,
        threshold=10,
        now=1_600.0,  # exactly 600s since breach_since
        sustain_seconds=600,
        cooldown_seconds=3600,
        state=QueueBacklogState(breach_since=1_000.0),
    )
    assert decision.should_alert is True
    assert decision.next_state == QueueBacklogState(breach_since=1_000.0, alerted_at=1_600.0)


def test_sustained_breach_with_no_prior_alert_fires() -> None:
    decision = _evaluate(
        backlog=25,
        threshold=10,
        now=2_000.0,
        sustain_seconds=600,
        cooldown_seconds=3600,
        state=QueueBacklogState(breach_since=1_000.0, alerted_at=None),
    )
    assert decision.should_alert is True
    assert decision.next_state == QueueBacklogState(breach_since=1_000.0, alerted_at=2_000.0)
    assert decision.sustained_seconds == 1_000.0


def test_within_cooldown_of_the_last_alert_does_not_repeat() -> None:
    decision = _evaluate(
        backlog=25,
        threshold=10,
        now=2_000.0,
        sustain_seconds=600,
        cooldown_seconds=3600,
        state=QueueBacklogState(breach_since=1_000.0, alerted_at=1_900.0),  # 100s ago
    )
    assert decision.should_alert is False
    assert decision.next_state == QueueBacklogState(breach_since=1_000.0, alerted_at=1_900.0)


def test_past_cooldown_and_still_breached_re_alerts() -> None:
    decision = _evaluate(
        backlog=25,
        threshold=10,
        now=10_000.0,
        sustain_seconds=600,
        cooldown_seconds=3600,
        state=QueueBacklogState(breach_since=1_000.0, alerted_at=5_000.0),  # 5000s ago
    )
    assert decision.should_alert is True
    assert decision.next_state == QueueBacklogState(breach_since=1_000.0, alerted_at=10_000.0)


def test_resolving_and_re_breaching_starts_a_fresh_clock() -> None:
    """Clearing state on resolve (backlog <= threshold) means a NEW breach
    after a dip does not inherit the old breach_since / alerted_at."""
    resolved = _evaluate(
        backlog=2,
        threshold=10,
        now=5_000.0,
        sustain_seconds=600,
        cooldown_seconds=3600,
        state=QueueBacklogState(breach_since=1_000.0, alerted_at=1_600.0),
    )
    assert resolved.next_state == QueueBacklogState()

    re_breached = _evaluate(
        backlog=25,
        threshold=10,
        now=5_100.0,
        sustain_seconds=600,
        cooldown_seconds=3600,
        state=resolved.next_state,
    )
    assert re_breached.should_alert is False
    assert re_breached.next_state == QueueBacklogState(breach_since=5_100.0, alerted_at=None)


# ---------------------------------------------------------------------------
# _threshold_for_queue
# ---------------------------------------------------------------------------


def test_threshold_routes_scan_queue_to_its_own_accessor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(qba_module, "queue_backlog_alert_scan_queue_threshold", lambda: 7)
    monkeypatch.setattr(
        qba_module, "queue_backlog_alert_default_queue_threshold", lambda: 999
    )
    assert _threshold_for_queue("trustedoss.scan") == 7


def test_threshold_routes_default_queue_to_its_own_accessor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(qba_module, "queue_backlog_alert_scan_queue_threshold", lambda: 7)
    monkeypatch.setattr(
        qba_module, "queue_backlog_alert_default_queue_threshold", lambda: 999
    )
    assert _threshold_for_queue("trustedoss.default") == 999


# ---------------------------------------------------------------------------
# _run_check - the I/O shell
# ---------------------------------------------------------------------------


class _FakeRedisClient:
    """In-process stand-in for the sync Redis client, string values only."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.closed = False

    def get(self, key: str) -> str | None:
        return self.store.get(key)

    def set(self, key: str, value: Any, ex: int | None = None) -> None:
        self.store[key] = str(value)

    def delete(self, key: str) -> None:
        self.store.pop(key, None)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def patch_run_check(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch the config toggles, the broker read, Redis, and notification
    dispatch. Returns a mutable dict the test configures before calling
    ``_run_check()``."""
    captured: dict[str, Any] = {
        "enabled": True,
        "metrics_enabled": True,
        "backlogs": {"trustedoss.scan": 0, "trustedoss.default": 0},
        "scan_threshold": 10,
        "default_threshold": 100,
        "sustain_seconds": 600,
        "cooldown_seconds": 3600,
        "notify_calls": [],
    }

    monkeypatch.setattr(qba_module, "queue_backlog_alert_enabled", lambda: captured["enabled"])
    monkeypatch.setattr(
        qba_module, "queue_backlog_metrics_enabled", lambda: captured["metrics_enabled"]
    )
    monkeypatch.setattr(
        qba_module,
        "queue_backlog_alert_scan_queue_threshold",
        lambda: captured["scan_threshold"],
    )
    monkeypatch.setattr(
        qba_module,
        "queue_backlog_alert_default_queue_threshold",
        lambda: captured["default_threshold"],
    )
    monkeypatch.setattr(
        qba_module,
        "queue_backlog_alert_sustain_seconds",
        lambda: captured["sustain_seconds"],
    )
    monkeypatch.setattr(
        qba_module,
        "queue_backlog_alert_cooldown_seconds",
        lambda: captured["cooldown_seconds"],
    )

    import services.metrics_service as metrics_module

    monkeypatch.setattr(metrics_module, "_broker_queue_backlogs", lambda: captured["backlogs"])

    fake_client = _FakeRedisClient()
    captured["redis_client"] = fake_client
    monkeypatch.setattr("redis.Redis.from_url", lambda *a, **k: fake_client)

    fake_send_notification = MagicMock()

    def _fake_delay(*args: Any, **kwargs: Any) -> Any:
        captured["notify_calls"].append((args, kwargs))
        return MagicMock(id="task-id")

    fake_send_notification.delay = _fake_delay

    import tasks.notify as notify_module

    monkeypatch.setattr(notify_module, "send_notification_task", fake_send_notification)

    return captured


def test_disabled_toggle_skips_without_touching_redis_or_the_broker(
    patch_run_check: dict[str, Any],
) -> None:
    patch_run_check["enabled"] = False
    patch_run_check["backlogs"] = {"trustedoss.scan": 999, "trustedoss.default": 999}

    summary = _run_check()

    assert summary == {
        "skipped": True,
        "skipped_reason": "disabled",
        "queues_checked": 0,
        "alerts_sent": 0,
    }
    assert not patch_run_check["notify_calls"]


def test_m2_off_skips_even_when_this_alert_is_on(patch_run_check: dict[str, Any]) -> None:
    patch_run_check["metrics_enabled"] = False
    patch_run_check["backlogs"] = {"trustedoss.scan": 999, "trustedoss.default": 999}

    summary = _run_check()

    assert summary["skipped"] is True
    assert summary["skipped_reason"] == "metrics_disabled"
    assert not patch_run_check["notify_calls"]


def test_below_threshold_checks_both_queues_and_alerts_neither(
    patch_run_check: dict[str, Any],
) -> None:
    patch_run_check["backlogs"] = {"trustedoss.scan": 1, "trustedoss.default": 5}

    summary = _run_check()

    assert summary == {
        "skipped": False,
        "skipped_reason": None,
        "queues_checked": 2,
        "alerts_sent": 0,
    }
    assert not patch_run_check["notify_calls"]


def test_a_sustained_scan_queue_breach_dispatches_one_notification(
    monkeypatch: pytest.MonkeyPatch, patch_run_check: dict[str, Any]
) -> None:
    patch_run_check["backlogs"] = {"trustedoss.scan": 50, "trustedoss.default": 0}
    patch_run_check["sustain_seconds"] = 0  # first tick already "sustained"

    summary = _run_check()

    assert summary["queues_checked"] == 2
    assert summary["alerts_sent"] == 1
    assert len(patch_run_check["notify_calls"]) == 1
    (args, kwargs) = patch_run_check["notify_calls"][0]
    kind, context, channels, recipients = args
    assert kind == "queue_backlog_alert"
    assert context["queue"] == "trustedoss.scan"
    assert context["backlog"] == "50"
    assert context["threshold"] == "10"
    assert channels == ["slack", "teams"]
    assert recipients == []
    assert "user_id" not in kwargs, "system-wide alert - no target user"


def test_a_fresh_breach_does_not_alert_on_its_first_tick(
    patch_run_check: dict[str, Any],
) -> None:
    """Default sustain_seconds (600) is not 0 - the first tick that observes
    a breach only starts the clock. A second tick moments later (real
    elapsed time, well under 600s) still has not sustained long enough."""
    patch_run_check["backlogs"] = {"trustedoss.scan": 50, "trustedoss.default": 0}

    first = _run_check()
    assert first["alerts_sent"] == 0

    second = _run_check()
    assert second["alerts_sent"] == 0


def test_state_persists_across_ticks_via_redis(patch_run_check: dict[str, Any]) -> None:
    patch_run_check["backlogs"] = {"trustedoss.scan": 50, "trustedoss.default": 0}
    patch_run_check["sustain_seconds"] = 100_000  # never sustained in this test's lifetime

    _run_check()
    client = patch_run_check["redis_client"]
    assert client.get("trustedoss:queue_backlog_alert:trustedoss.scan:breach_since") is not None
    assert client.get("trustedoss:queue_backlog_alert:trustedoss.default:breach_since") is None


def test_resolving_a_breach_clears_its_redis_state(patch_run_check: dict[str, Any]) -> None:
    key = "trustedoss:queue_backlog_alert:trustedoss.scan:breach_since"
    patch_run_check["redis_client"].store[key] = "1000.0"
    patch_run_check["backlogs"] = {"trustedoss.scan": 0, "trustedoss.default": 0}

    _run_check()

    assert patch_run_check["redis_client"].get(key) is None


def test_a_broken_notification_enqueue_does_not_crash_the_tick(
    monkeypatch: pytest.MonkeyPatch, patch_run_check: dict[str, Any]
) -> None:
    patch_run_check["backlogs"] = {"trustedoss.scan": 50, "trustedoss.default": 0}
    patch_run_check["sustain_seconds"] = 0

    def _raise(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("broker unreachable")

    import tasks.notify as notify_module

    fake_send_notification = MagicMock()
    fake_send_notification.delay = _raise
    monkeypatch.setattr(notify_module, "send_notification_task", fake_send_notification)

    summary = _run_check()

    assert summary["alerts_sent"] == 0
    assert summary["queues_checked"] == 2


def test_redis_client_is_always_closed(patch_run_check: dict[str, Any]) -> None:
    _run_check()
    assert patch_run_check["redis_client"].closed


# ---------------------------------------------------------------------------
# Celery task wrapper
# ---------------------------------------------------------------------------


def test_the_beat_task_never_raises_on_an_unexpected_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom() -> None:
        raise RuntimeError("something unexpected")

    monkeypatch.setattr(qba_module, "_run_check", _boom)

    result = queue_backlog_alert_check()

    assert result["skipped"] is True
    assert result["skipped_reason"].startswith("unexpected:")
    assert result["alerts_sent"] == 0


# ---------------------------------------------------------------------------
# core.config accessors - the real os.getenv bodies, not the monkeypatched
# stand-ins the fixtures above use for _run_check/_threshold_for_queue.
# ---------------------------------------------------------------------------


def test_queue_backlog_alert_enabled_defaults_to_off(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.config import queue_backlog_alert_enabled

    monkeypatch.delenv("QUEUE_BACKLOG_ALERT_ENABLED", raising=False)
    assert queue_backlog_alert_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_queue_backlog_alert_enabled_reads_truthy_values(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    from core.config import queue_backlog_alert_enabled

    monkeypatch.setenv("QUEUE_BACKLOG_ALERT_ENABLED", value)
    assert queue_backlog_alert_enabled() is True


def test_queue_backlog_alert_scan_queue_threshold_default_and_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.config import queue_backlog_alert_scan_queue_threshold

    monkeypatch.delenv("QUEUE_BACKLOG_ALERT_SCAN_QUEUE_THRESHOLD", raising=False)
    assert queue_backlog_alert_scan_queue_threshold() == 10

    monkeypatch.setenv("QUEUE_BACKLOG_ALERT_SCAN_QUEUE_THRESHOLD", "25")
    assert queue_backlog_alert_scan_queue_threshold() == 25


def test_queue_backlog_alert_default_queue_threshold_default_and_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.config import queue_backlog_alert_default_queue_threshold

    monkeypatch.delenv("QUEUE_BACKLOG_ALERT_DEFAULT_QUEUE_THRESHOLD", raising=False)
    assert queue_backlog_alert_default_queue_threshold() == 100

    monkeypatch.setenv("QUEUE_BACKLOG_ALERT_DEFAULT_QUEUE_THRESHOLD", "250")
    assert queue_backlog_alert_default_queue_threshold() == 250


def test_queue_backlog_alert_sustain_seconds_default_and_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.config import queue_backlog_alert_sustain_seconds

    monkeypatch.delenv("QUEUE_BACKLOG_ALERT_SUSTAIN_SECONDS", raising=False)
    assert queue_backlog_alert_sustain_seconds() == 600

    monkeypatch.setenv("QUEUE_BACKLOG_ALERT_SUSTAIN_SECONDS", "120")
    assert queue_backlog_alert_sustain_seconds() == 120


def test_queue_backlog_alert_cooldown_seconds_default_and_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.config import queue_backlog_alert_cooldown_seconds

    monkeypatch.delenv("QUEUE_BACKLOG_ALERT_COOLDOWN_SECONDS", raising=False)
    assert queue_backlog_alert_cooldown_seconds() == 3600

    monkeypatch.setenv("QUEUE_BACKLOG_ALERT_COOLDOWN_SECONDS", "60")
    assert queue_backlog_alert_cooldown_seconds() == 60
