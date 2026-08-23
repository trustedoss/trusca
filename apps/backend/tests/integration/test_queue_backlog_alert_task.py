# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Integration test for ``tasks.queue_backlog_alert`` against a real Redis
broker - S6 (concurrency-scaling-plan-2026-08-22.md §3.2/§4).

The unit tests (``tests/unit/tasks/test_queue_backlog_alert.py``) cover the
decision logic and the I/O shell against fakes. This file's job is the one
thing a fake cannot prove: a real message pushed onto the real
``trustedoss.scan`` list is what the sweep counts as backlog, and a real
Redis SET/GET round trip is what carries the sustained-breach clock between
two separate ``_run_check()`` calls - the same "not a stub" standard
``test_the_broker_backlog_value_reflects_a_real_redis_list`` already holds M2
to. Notification delivery itself (Slack/Teams webhooks) is an external paid
integration and stays mocked, per CLAUDE.md.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import MagicMock

import pytest
import redis as redis_lib

from core.config import redis_url
from tasks.celery_app import _SCAN_QUEUE
from tasks.queue_backlog_alert import _run_check


@pytest.fixture
def redis_conn() -> Any:
    conn = redis_lib.Redis.from_url(os.getenv("REDIS_URL") or redis_url(), decode_responses=True)
    yield conn
    # Best-effort cleanup so a failed assertion does not leak state that
    # confuses the NEXT test run against a shared dev Redis instance.
    conn.delete(f"trustedoss:queue_backlog_alert:{_SCAN_QUEUE}:breach_since")
    conn.delete(f"trustedoss:queue_backlog_alert:{_SCAN_QUEUE}:alerted_at")
    conn.close()


@pytest.fixture
def patch_toggles_and_notify(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    import tasks.notify as notify_module
    import tasks.queue_backlog_alert as qba_module

    captured: dict[str, Any] = {"notify_calls": []}

    monkeypatch.setattr(qba_module, "queue_backlog_alert_enabled", lambda: True)
    monkeypatch.setattr(qba_module, "queue_backlog_metrics_enabled", lambda: True)
    monkeypatch.setattr(qba_module, "queue_backlog_alert_scan_queue_threshold", lambda: 2)
    monkeypatch.setattr(
        qba_module, "queue_backlog_alert_default_queue_threshold", lambda: 1_000_000
    )
    monkeypatch.setattr(qba_module, "queue_backlog_alert_sustain_seconds", lambda: 0)
    monkeypatch.setattr(qba_module, "queue_backlog_alert_cooldown_seconds", lambda: 3600)

    fake_send_notification = MagicMock()

    def _fake_delay(*args: Any, **kwargs: Any) -> Any:
        captured["notify_calls"].append((args, kwargs))
        return MagicMock(id="task-id")

    fake_send_notification.delay = _fake_delay
    monkeypatch.setattr(notify_module, "send_notification_task", fake_send_notification)

    return captured


def test_a_real_backlog_over_threshold_is_read_from_redis_and_alerts(
    redis_conn: Any, patch_toggles_and_notify: dict[str, Any]
) -> None:
    """Push 3 real messages onto the real scan queue (threshold 2, sustain 0
    in the fixture) and confirm the sweep both reads them AND alerts."""
    probes = [f'{{"probe": "s6-integration-{i}"}}' for i in range(3)]
    for probe in probes:
        redis_conn.lpush(_SCAN_QUEUE, probe)
    try:
        summary = _run_check()
    finally:
        for probe in probes:
            redis_conn.lrem(_SCAN_QUEUE, 1, probe)

    assert summary["queues_checked"] == 2
    assert summary["alerts_sent"] == 1
    assert len(patch_toggles_and_notify["notify_calls"]) == 1
    (args, _kwargs) = patch_toggles_and_notify["notify_calls"][0]
    _kind, context, _channels, _recipients = args
    assert context["queue"] == _SCAN_QUEUE
    assert int(context["backlog"]) >= 3


def test_the_breach_clock_survives_between_two_separate_ticks(
    redis_conn: Any, patch_toggles_and_notify: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real Redis round trip, not an in-process fake: raise the sustain
    window so tick 1 only starts the clock, confirm the SET landed in Redis
    directly (bypassing the module under test), then let tick 2 read that
    same key back and alert."""
    import tasks.queue_backlog_alert as qba_module

    monkeypatch.setattr(qba_module, "queue_backlog_alert_sustain_seconds", lambda: 10_000)

    # scan_threshold in the fixture is 2 - push 3 so the queue is actually
    # over threshold, not just non-empty.
    probes = [f'{{"probe": "s6-integration-clock-{i}"}}' for i in range(3)]
    for probe in probes:
        redis_conn.lpush(_SCAN_QUEUE, probe)
    try:
        first = _run_check()
        assert first["alerts_sent"] == 0

        key = f"trustedoss:queue_backlog_alert:{_SCAN_QUEUE}:breach_since"
        assert redis_conn.get(key) is not None, (
            "the first tick must have written its breach timestamp to the "
            "real broker, not just to this process's memory"
        )

        # Second tick, sustain window now satisfied - same real key.
        monkeypatch.setattr(qba_module, "queue_backlog_alert_sustain_seconds", lambda: 0)
        second = _run_check()
        assert second["alerts_sent"] == 1
    finally:
        for probe in probes:
            redis_conn.lrem(_SCAN_QUEUE, 1, probe)
