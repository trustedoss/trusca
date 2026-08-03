"""
Unit tests for ``tasks.trivy_db_refresh`` — W6-#44.

Coverage:
  - Happy path: refresh returns ``downloaded`` → summary populated, no
    notification, before/after vuln_count surfaced.
  - Failure path: refresh returns ``failed`` → notification dispatched
    via the existing notify infrastructure (Slack + Teams, dev email
    suppressed by the empty recipients list).
  - Timeout path: refresh returns ``timeout`` → notification dispatched
    with the timeout-flavoured cve_id.
  - Skipped path: dev / mock backend → no notification, no failure log.
  - Unexpected error inside the task body never propagates — beat tick
    keeps progressing through subsequent ticks.
  - Notification dispatcher itself raising → task still returns clean.

The test never spins up Celery; we drive the task body directly so the
result dict is deterministic. We also never reach Trivy — the
``download_db_only`` adapter is monkeypatched per test.
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from integrations.trivy import (
    TrivyDbDownloadResult,
    TrivyDbStatus,
)
from notifications.dispatcher import NotificationKind
from tasks import trivy_db_refresh as refresh_module
from tasks.trivy_db_refresh import refresh_trivy_db

# ---------------------------------------------------------------------------
# Helpers — fake snapshot factory + patcher
# ---------------------------------------------------------------------------


def _make_status(*, vuln_count: int | None, version: str | None) -> TrivyDbStatus:
    """Minimal TrivyDbStatus fixture for the before/after snapshot assertions."""
    from datetime import UTC, datetime

    return TrivyDbStatus(
        last_update=datetime(2026, 5, 27, 3, 14, tzinfo=UTC),
        next_refresh_at=None,
        vuln_count=vuln_count,
        db_version=version,
        db_size_bytes=None,
        refresh_interval_hours=168,
        cache_dir="/var/lib/trivy",
        repository="ghcr.io/aquasecurity/trivy-db",
        freshness="fresh",
    )


@pytest.fixture
def patch_download(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch ``download_db_only`` + ``get_trivy_db_status`` + send_notification.

    Returns a dict the test can mutate to control the call outcomes.
    """
    captured: dict[str, Any] = {
        "result": TrivyDbDownloadResult(status="downloaded", duration_seconds=42.0),
        "snapshots": [
            _make_status(vuln_count=400_000, version="trivy-db schema v2"),
            _make_status(vuln_count=401_500, version="trivy-db schema v2"),
        ],
        "snapshot_call_count": 0,
        "snapshot_should_raise": [],  # per-call list of bool — True raises on that call
        "notify_calls": [],
        "notify_should_raise": False,
    }

    def _fake_download(*, timeout_seconds: int) -> TrivyDbDownloadResult:
        captured["timeout"] = timeout_seconds
        return cast(TrivyDbDownloadResult, captured["result"])

    def _fake_snapshot(*_a: Any, **_kw: Any) -> TrivyDbStatus:
        idx = captured["snapshot_call_count"]
        captured["snapshot_call_count"] += 1
        should_raise = (
            captured["snapshot_should_raise"][idx]
            if idx < len(captured["snapshot_should_raise"])
            else False
        )
        if should_raise:
            raise OSError("simulated metadata read failure")
        snapshots = cast(list[TrivyDbStatus], captured["snapshots"])
        chosen = snapshots[idx] if idx < len(snapshots) else snapshots[-1]
        return cast(TrivyDbStatus, chosen)

    # Patch the names AS IMPORTED INTO the task module.
    monkeypatch.setattr(refresh_module, "download_db_only", _fake_download)
    monkeypatch.setattr(refresh_module, "get_trivy_db_status", _fake_snapshot)

    fake_send_notification = MagicMock()

    def _fake_delay(*args: Any, **kwargs: Any) -> Any:
        if captured["notify_should_raise"]:
            raise RuntimeError("broker unreachable")
        captured["notify_calls"].append((args, kwargs))
        return MagicMock(id="task-id")

    fake_send_notification.delay = _fake_delay

    # Patch the late-imported notification task.
    import tasks.notify as notify_module

    monkeypatch.setattr(
        notify_module, "send_notification_task", fake_send_notification
    )

    return captured


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_refresh_happy_path_returns_summary(patch_download: dict[str, Any]) -> None:
    """Successful download → status='downloaded', counts populated, no notification."""
    summary = refresh_trivy_db()
    assert summary["status"] == "downloaded"
    assert summary["duration_seconds"] == 42.0
    assert summary["vuln_count_before"] == 400_000
    assert summary["vuln_count_after"] == 401_500
    assert summary["db_version_before"] == "trivy-db schema v2"
    assert summary["db_version_after"] == "trivy-db schema v2"
    assert summary["notification_enqueued"] == 0
    assert not patch_download["notify_calls"]


def test_refresh_happy_path_timeout_forwarded_from_config(
    monkeypatch: pytest.MonkeyPatch, patch_download: dict[str, Any]
) -> None:
    """``TRIVY_DB_REFRESH_TIMEOUT_SECONDS`` env knob flows into the call."""
    monkeypatch.setenv("TRIVY_DB_REFRESH_TIMEOUT_SECONDS", "300")
    refresh_trivy_db()
    assert patch_download["timeout"] == 300


# ---------------------------------------------------------------------------
# Skipped path — mock backend / trivy absent
# ---------------------------------------------------------------------------


def test_refresh_skipped_emits_no_notification(patch_download: dict[str, Any]) -> None:
    """status='skipped' is the dev / mock path — no operator page."""
    patch_download["result"] = TrivyDbDownloadResult(status="skipped", duration_seconds=0.0)
    summary = refresh_trivy_db()
    assert summary["status"] == "skipped"
    assert summary["notification_enqueued"] == 0
    assert not patch_download["notify_calls"]


# ---------------------------------------------------------------------------
# Failure paths — failed / timeout → notification
# ---------------------------------------------------------------------------


def test_refresh_failed_dispatches_notification(patch_download: dict[str, Any]) -> None:
    """status='failed' → one Slack/Teams notification, descriptor shape stable."""
    patch_download["result"] = TrivyDbDownloadResult(
        status="failed",
        duration_seconds=12.5,
        error="trivy --download-db-only exited 1",
        stderr_tail="FATAL: unauthorized",
    )
    summary = refresh_trivy_db()
    assert summary["status"] == "failed"
    assert summary["notification_enqueued"] == 1
    assert len(patch_download["notify_calls"]) == 1

    args, _ = patch_download["notify_calls"][0]
    kind, context, channels, recipients = args
    assert kind == NotificationKind.NEW_CRITICAL_CVE.value
    assert context["cve_id"] == "TRIVY-DB-REFRESH-FAILED"
    assert context["severity"] == "HIGH"
    assert context["project_name"] == "Trivy DB lifecycle"
    assert "FATAL: unauthorized" in context.get("body", "")
    assert "slack" in channels
    assert "teams" in channels
    # Empty recipients so the email channel no-ops without raising.
    assert recipients == []


def test_refresh_timeout_dispatches_notification(patch_download: dict[str, Any]) -> None:
    """status='timeout' is also notifiable — distinct cve_id suffix."""
    patch_download["result"] = TrivyDbDownloadResult(
        status="timeout",
        duration_seconds=900.0,
        error="trivy --download-db-only exceeded 900s",
        stderr_tail=None,
    )
    summary = refresh_trivy_db()
    assert summary["status"] == "timeout"
    assert summary["notification_enqueued"] == 1

    args, _ = patch_download["notify_calls"][0]
    _, context, _, _ = args
    assert context["cve_id"] == "TRIVY-DB-REFRESH-TIMEOUT"


def test_refresh_notification_broker_failure_is_swallowed(
    patch_download: dict[str, Any],
) -> None:
    """A broker outage during notify dispatch must not crash the beat tick."""
    patch_download["result"] = TrivyDbDownloadResult(
        status="failed", duration_seconds=1.0, error="boom"
    )
    patch_download["notify_should_raise"] = True

    summary = refresh_trivy_db()
    assert summary["status"] == "failed"
    # Dispatcher tried once, failed once, summary records zero enqueued.
    assert summary["notification_enqueued"] == 0


# ---------------------------------------------------------------------------
# Snapshot probe failures degrade gracefully
# ---------------------------------------------------------------------------


def test_refresh_snapshot_before_failure_leaves_field_null(
    patch_download: dict[str, Any],
) -> None:
    """A failed pre-snapshot read leaves vuln_count_before=None but doesn't crash."""
    patch_download["snapshot_should_raise"] = [True, False]
    summary = refresh_trivy_db()
    assert summary["status"] == "downloaded"
    assert summary["vuln_count_before"] is None
    assert summary["vuln_count_after"] == 401_500


def test_refresh_snapshot_after_failure_leaves_field_null(
    patch_download: dict[str, Any],
) -> None:
    """A failed post-snapshot read leaves vuln_count_after=None but doesn't crash."""
    patch_download["snapshot_should_raise"] = [False, True]
    summary = refresh_trivy_db()
    assert summary["status"] == "downloaded"
    assert summary["vuln_count_before"] == 400_000
    assert summary["vuln_count_after"] is None


# ---------------------------------------------------------------------------
# Unexpected failure swallow
# ---------------------------------------------------------------------------


def test_refresh_unexpected_error_inside_task_does_not_propagate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A truly unexpected error (e.g. config raises) returns status=failed."""

    def _boom(*_a: Any, **_kw: Any) -> Any:
        raise RuntimeError("config exploded")

    monkeypatch.setattr(refresh_module, "trivy_db_refresh_timeout_seconds", _boom)

    summary = refresh_trivy_db()
    assert summary["status"] == "failed"
    assert "RuntimeError" in summary.get("error", "")


# ---------------------------------------------------------------------------
# Idempotency assertion — two consecutive calls each pull a fresh snapshot
# ---------------------------------------------------------------------------


def test_refresh_two_consecutive_calls_each_return_clean(
    patch_download: dict[str, Any],
) -> None:
    """Beat ticks repeatedly; each invocation must be independently clean.

    Trivy's own file lock serialises subprocess access to the cache dir —
    we never see a python-level race here. Two consecutive calls should
    each return a fully-populated summary.
    """
    # 4 snapshots → before/after for each of two consecutive refreshes.
    patch_download["snapshots"] = [
        _make_status(vuln_count=400_000, version="v1"),
        _make_status(vuln_count=401_000, version="v1"),
        _make_status(vuln_count=401_000, version="v1"),
        _make_status(vuln_count=401_750, version="v1"),
    ]
    a = refresh_trivy_db()
    b = refresh_trivy_db()
    assert a["status"] == "downloaded"
    assert b["status"] == "downloaded"
    assert a["vuln_count_after"] == 401_000
    assert b["vuln_count_before"] == 401_000
    assert b["vuln_count_after"] == 401_750
