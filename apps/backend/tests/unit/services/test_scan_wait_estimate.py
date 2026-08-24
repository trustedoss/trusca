# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Unit tests for ``services.scan_service._estimate_scan_queue_wait_seconds`` -
S7 (concurrency-scaling-plan-2026-08-22.md §1.1/§3.2/§4).

No DB, no Redis, no Celery: everything the function reads is monkeypatched at
its source module (the function does lazy imports, matching the convention
``services.metrics_service`` / ``tasks.queue_backlog_alert`` already use, so
patching has to target ``core.config`` / ``services.metrics_service``
directly rather than a name imported into ``scan_service``'s own namespace).
"""

from __future__ import annotations

import pytest

import core.config as config_module
import services.metrics_service as metrics_module
from services.scan_service import _estimate_scan_queue_wait_seconds


def _patch_common(
    monkeypatch: pytest.MonkeyPatch,
    *,
    metrics_enabled: bool,
    backlog: int = 0,
    slots: int = 2,
    average_seconds: int = 1200,
) -> None:
    monkeypatch.setattr(config_module, "queue_backlog_metrics_enabled", lambda: metrics_enabled)
    monkeypatch.setattr(config_module, "scan_queue_slot_count", lambda: slots)
    monkeypatch.setattr(config_module, "scan_average_duration_seconds", lambda: average_seconds)
    monkeypatch.setattr(
        metrics_module, "_broker_queue_backlogs", lambda: {"trustedoss.scan": backlog}
    )


async def test_metrics_disabled_returns_none_not_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """Off is 'unknown', not 'no wait' - the 429 body must omit the field
    rather than assert a wait of 0 it never measured."""
    _patch_common(monkeypatch, metrics_enabled=False, backlog=40, slots=2)
    assert await _estimate_scan_queue_wait_seconds() is None


async def test_empty_backlog_estimates_zero_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_common(monkeypatch, metrics_enabled=True, backlog=0, slots=2, average_seconds=1200)
    assert await _estimate_scan_queue_wait_seconds() == 0


async def test_applies_the_plan_s_floor_backlog_over_slots_times_average_formula(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§1.1: floor(backlog / S) x M. 5 queued, 2 slots, 1200s average -> 2 x 1200."""
    _patch_common(monkeypatch, metrics_enabled=True, backlog=5, slots=2, average_seconds=1200)
    assert await _estimate_scan_queue_wait_seconds() == 2 * 1200


async def test_backlog_smaller_than_slot_count_still_estimates_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """1 queued against 4 slots: the caller's own trigger is expected to clear
    within the first round, so the floor division yields 0."""
    _patch_common(monkeypatch, metrics_enabled=True, backlog=1, slots=4, average_seconds=1200)
    assert await _estimate_scan_queue_wait_seconds() == 0


async def test_reads_only_the_scan_queue_backlog_not_the_default_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A large default-queue backlog (notifications, backups, ...) must not
    inflate a scan-capacity wait estimate - the two queues are unrelated
    capacity pools since S3's split."""
    monkeypatch.setattr(config_module, "queue_backlog_metrics_enabled", lambda: True)
    monkeypatch.setattr(config_module, "scan_queue_slot_count", lambda: 2)
    monkeypatch.setattr(config_module, "scan_average_duration_seconds", lambda: 1200)
    monkeypatch.setattr(
        metrics_module,
        "_broker_queue_backlogs",
        lambda: {"trustedoss.scan": 0, "trustedoss.default": 500},
    )
    assert await _estimate_scan_queue_wait_seconds() == 0


async def test_missing_scan_queue_key_defaults_to_zero_backlog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defensive: a broker reader that returns an unexpected shape must not
    raise out of a 429 handler."""
    monkeypatch.setattr(config_module, "queue_backlog_metrics_enabled", lambda: True)
    monkeypatch.setattr(config_module, "scan_queue_slot_count", lambda: 2)
    monkeypatch.setattr(config_module, "scan_average_duration_seconds", lambda: 1200)
    monkeypatch.setattr(metrics_module, "_broker_queue_backlogs", lambda: {})
    assert await _estimate_scan_queue_wait_seconds() == 0


# ---------------------------------------------------------------------------
# core.config accessors - the real os.getenv bodies, not monkeypatched away.
# ---------------------------------------------------------------------------


def test_scan_queue_slot_count_default_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SCAN_QUEUE_SLOT_COUNT", raising=False)
    assert config_module.scan_queue_slot_count() == 2
    monkeypatch.setenv("SCAN_QUEUE_SLOT_COUNT", "8")
    assert config_module.scan_queue_slot_count() == 8


def test_scan_queue_slot_count_clamps_below_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zero slots can never produce a finite estimate - clamp up to 1."""
    monkeypatch.setenv("SCAN_QUEUE_SLOT_COUNT", "0")
    assert config_module.scan_queue_slot_count() == 1
    monkeypatch.setenv("SCAN_QUEUE_SLOT_COUNT", "-5")
    assert config_module.scan_queue_slot_count() == 1


def test_scan_average_duration_seconds_default_and_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SCAN_AVERAGE_DURATION_SECONDS", raising=False)
    assert config_module.scan_average_duration_seconds() == 1200
    monkeypatch.setenv("SCAN_AVERAGE_DURATION_SECONDS", "600")
    assert config_module.scan_average_duration_seconds() == 600


def test_scan_average_duration_seconds_clamps_below_sixty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SCAN_AVERAGE_DURATION_SECONDS", "5")
    assert config_module.scan_average_duration_seconds() == 60
