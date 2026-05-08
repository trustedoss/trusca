"""
Unit tests for :mod:`tasks.notify` — the Celery wrapper around the
notifications dispatcher.

We invoke the underlying function directly (the Celery decorator stores it
on ``.run``) so we don't need a worker process. Retry semantics are tested
by inspecting how the task handles a dispatcher report with
``retryable_failures=True`` — the wrapper must raise
:class:`NotificationDeliveryError` so Celery's autoretry envelope kicks in.
"""

from __future__ import annotations

import pytest

from notifications import NotificationDeliveryError
from notifications import dispatcher as disp
from tasks import notify as notify_mod


class _FakeRequest:
    def __init__(self, retries: int = 0) -> None:
        self.id = "task-id-test"
        self.retries = retries


class _FakeTask:
    def __init__(self, retries: int = 0) -> None:
        self.request = _FakeRequest(retries=retries)


def test_send_notification_task_returns_report_when_no_retryable_failures(
    monkeypatch,
) -> None:
    """When all channels are ok / skipped / permanently-failed, the task
    returns the report and Celery does NOT retry."""
    captured: dict[str, object] = {}

    async def _fake_dispatch(*, kind, context, channels, recipients):
        captured.update(
            kind=kind, context=context, channels=channels, recipients=recipients
        )
        return {
            "kind": kind,
            "channels": [{"channel": "email", "status": "ok"}],
            "delivered_count": 1,
            "skipped_count": 0,
            "failed_count": 0,
            "retryable_failures": False,
        }

    monkeypatch.setattr(notify_mod, "dispatch", _fake_dispatch)

    # Use the plain underlying function so we can inject our fake `self`
    # without Celery's bind=True swallowing it.
    out = notify_mod._run_notification(
        _FakeTask(),
        "password_reset",
        {"reset_url": "https://x"},
        ["email"],
        ["alice@example.com"],
    )

    assert out["delivered_count"] == 1
    assert out["retryable_failures"] is False
    assert captured["kind"] == "password_reset"
    assert captured["channels"] == ["email"]
    assert captured["recipients"] == ["alice@example.com"]


def test_send_notification_task_raises_for_retryable_failures(monkeypatch) -> None:
    """When the dispatcher flags retryable_failures the task surfaces a
    :class:`NotificationDeliveryError` so the Celery autoretry envelope
    schedules the next attempt."""

    async def _fake_dispatch(**_kwargs):
        return {
            "kind": "scan_completed",
            "channels": [
                {"channel": "slack", "status": "failed", "retryable": True},
            ],
            "delivered_count": 0,
            "skipped_count": 0,
            "failed_count": 1,
            "retryable_failures": True,
        }

    monkeypatch.setattr(notify_mod, "dispatch", _fake_dispatch)

    with pytest.raises(NotificationDeliveryError):
        notify_mod._run_notification(
            _FakeTask(),
            "scan_completed",
            {"project_name": "p", "scan_id": "s"},
            ["slack"],
            None,
        )


def test_send_notification_task_is_registered_with_autoretry_kwargs() -> None:
    """Smoke-check the Celery task decorator carries the retry envelope.

    We assert on the task name + the autoretry whitelist + max_retries so a
    refactor that drops the retry policy is caught here.
    """
    task = notify_mod.send_notification_task
    assert task.name == "trustedoss.send_notification"
    # autoretry_for is stored on the bound task class
    assert NotificationDeliveryError in tuple(task.autoretry_for)
    assert task.max_retries == 5
    assert task.retry_backoff is True
    assert task.retry_backoff_max == 600
    assert task.retry_jitter is True


def test_dispatcher_module_exposes_kind_enum_values() -> None:
    """Belt-and-braces: the Celery worker passes ``kind`` as a string. The
    dispatcher's _BUILDERS dict therefore must key on the enum's ``.value``
    not the enum object."""
    expected = {
        "new_critical_cve",
        "scan_completed",
        "approval_state_changed",
        "user_deactivated",
        "password_reset",
    }
    assert expected.issubset(set(disp._BUILDERS.keys()))
