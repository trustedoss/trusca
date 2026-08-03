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


# ---------------------------------------------------------------------------
# Chore A2 — fan-out hook tests
#
# These exercise ``_run_notification`` with a fake ``_apply_prefs_filter``.
# The fan-out itself is integration-tested elsewhere (it requires a live
# Postgres for ``sync_session_scope``); here we validate the wiring:
#   - filter is invoked with the correct kwargs when ``user_id`` is set
#   - filter is NOT invoked when ``user_id`` is None (legacy path)
#   - an empty channel list short-circuits dispatch entirely
# ---------------------------------------------------------------------------


def test_run_notification_skips_filter_when_no_user_id(monkeypatch) -> None:
    """Legacy callers (no user_id) bypass the prefs gate."""
    captured = {}

    async def _fake_dispatch(*, kind, context, channels, recipients):
        captured["channels"] = list(channels)
        return {
            "kind": kind,
            "channels": [],
            "delivered_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "retryable_failures": False,
        }

    def _filter_should_not_be_called(**_kw):  # pragma: no cover
        raise AssertionError("prefs filter must not run when user_id is None")

    monkeypatch.setattr(notify_mod, "dispatch", _fake_dispatch)
    monkeypatch.setattr(notify_mod, "_apply_prefs_filter", _filter_should_not_be_called)

    notify_mod._run_notification(
        _FakeTask(),
        "password_reset",
        {"reset_url": "x"},
        ["email"],
        ["a@x.com"],
    )
    assert captured["channels"] == ["email"]


def test_run_notification_invokes_filter_when_user_id_supplied(monkeypatch) -> None:
    """user_id triggers the prefs fan-out and uses the filtered channels."""
    import uuid as _uuid

    seen: dict[str, object] = {}

    def _fake_filter(**kwargs):
        seen.update(kwargs)
        # User has email off, slack on.
        return [c for c in kwargs["channels"] if c != "email"]

    captured = {}

    async def _fake_dispatch(*, kind, context, channels, recipients):
        captured["channels"] = list(channels)
        return {
            "kind": kind,
            "channels": [],
            "delivered_count": 1,
            "skipped_count": 0,
            "failed_count": 0,
            "retryable_failures": False,
        }

    monkeypatch.setattr(notify_mod, "_apply_prefs_filter", _fake_filter)
    monkeypatch.setattr(notify_mod, "dispatch", _fake_dispatch)

    user_id = _uuid.uuid4()
    notify_mod._run_notification(
        _FakeTask(),
        "scan_completed",
        {"project_name": "p", "scan_id": "s"},
        ["email", "slack"],
        None,
        user_id=str(user_id),
        in_app_title="Scan complete",
        in_app_body="Project p finished",
        in_app_link="/projects/p",
        in_app_target_table="scans",
        in_app_target_id=str(_uuid.uuid4()),
    )

    assert seen["user_id"] == user_id
    assert seen["kind"] == "scan_completed"
    assert seen["title"] == "Scan complete"
    assert seen["body"] == "Project p finished"
    assert seen["link"] == "/projects/p"
    assert seen["target_table"] == "scans"
    assert seen["channels"] == ["email", "slack"]
    # Filter dropped email; dispatch sees only slack.
    assert captured["channels"] == ["slack"]


def test_run_notification_short_circuits_when_filter_returns_empty(
    monkeypatch,
) -> None:
    """User has every outbound channel off → no dispatch + synthetic empty report."""
    import uuid as _uuid

    monkeypatch.setattr(notify_mod, "_apply_prefs_filter", lambda **_: [])

    def _dispatch_should_not_run(**_kwargs):  # pragma: no cover
        raise AssertionError("dispatch must not be called when channels is empty")

    monkeypatch.setattr(notify_mod, "dispatch", _dispatch_should_not_run)

    out = notify_mod._run_notification(
        _FakeTask(),
        "scan_completed",
        {"project_name": "p", "scan_id": "s"},
        ["email", "slack", "teams"],
        None,
        user_id=str(_uuid.uuid4()),
        in_app_title="t",
        in_app_body="b",
    )
    assert out["delivered_count"] == 0
    assert out["skipped_count"] == 0
    assert out["failed_count"] == 0
    assert out["retryable_failures"] is False
    assert out["channels"] == []


def test_run_notification_coerces_invalid_user_id_to_none(monkeypatch) -> None:
    """A garbage user_id string falls back to the legacy (no-fan-out) path."""

    def _filter_should_not_be_called(**_kw):  # pragma: no cover
        raise AssertionError("prefs filter must not run for invalid user_id")

    captured = {}

    async def _fake_dispatch(*, kind, context, channels, recipients):
        captured["channels"] = list(channels)
        return {
            "kind": kind,
            "channels": [],
            "delivered_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "retryable_failures": False,
        }

    monkeypatch.setattr(notify_mod, "_apply_prefs_filter", _filter_should_not_be_called)
    monkeypatch.setattr(notify_mod, "dispatch", _fake_dispatch)

    notify_mod._run_notification(
        _FakeTask(),
        "password_reset",
        {"reset_url": "x"},
        ["email"],
        ["a@x.com"],
        user_id="not-a-uuid",
    )
    assert captured["channels"] == ["email"]


def test_run_notification_accepts_uuid_object_for_user_id(monkeypatch) -> None:
    """Passing a real UUID instance (not just a string) reaches the filter."""
    import uuid as _uuid

    seen = {}

    def _fake_filter(**kwargs):
        seen.update(kwargs)
        return list(kwargs["channels"])

    async def _fake_dispatch(**_kwargs):
        return {
            "kind": "scan_completed",
            "channels": [],
            "delivered_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "retryable_failures": False,
        }

    monkeypatch.setattr(notify_mod, "_apply_prefs_filter", _fake_filter)
    monkeypatch.setattr(notify_mod, "dispatch", _fake_dispatch)

    user_id = _uuid.uuid4()
    notify_mod._run_notification(
        _FakeTask(),
        "scan_completed",
        {"project_name": "p", "scan_id": "s"},
        ["email"],
        None,
        user_id=user_id,  # NOT a string — pass the UUID directly.
    )
    assert seen["user_id"] == user_id


def test_apply_prefs_filter_writes_in_app_row_and_filters_channels(
    monkeypatch,
) -> None:
    """End-to-end check of the fan-out helper against a stub session.

    Drives the real ``_apply_prefs_filter`` (no monkey-patching) but
    swaps out ``sync_session_scope`` and the service helpers so the test
    can run without a live Postgres. The point is to assert that the
    helper:
      - Calls ``get_prefs_sync`` with the right user_id.
      - Skips email when ``email_enabled=False``.
      - Calls ``create_notification_sync`` only when ``in_app_enabled=True``.
    """
    import uuid as _uuid
    from contextlib import contextmanager
    from types import SimpleNamespace

    fake_session = object()

    @contextmanager
    def _fake_session_scope():
        yield fake_session

    fake_prefs = SimpleNamespace(
        email_enabled=False,
        slack_enabled=True,
        teams_enabled=False,
        in_app_enabled=True,
    )

    seen_get: dict[str, object] = {}
    seen_create: dict[str, object] = {}

    def _fake_get_prefs_sync(session, *, user_id):
        seen_get["session"] = session
        seen_get["user_id"] = user_id
        return fake_prefs

    def _fake_create_notification_sync(session, **kwargs):
        seen_create["session"] = session
        seen_create.update(kwargs)
        return SimpleNamespace(id=_uuid.uuid4(), **kwargs)

    # Patch the late-bound imports inside _apply_prefs_filter.
    import core.db as core_db
    import services.notification_service as svc

    monkeypatch.setattr(core_db, "sync_session_scope", _fake_session_scope)
    monkeypatch.setattr(svc, "get_prefs_sync", _fake_get_prefs_sync)
    monkeypatch.setattr(svc, "create_notification_sync", _fake_create_notification_sync)

    user_id = _uuid.uuid4()
    target_id = _uuid.uuid4()
    out = notify_mod._apply_prefs_filter(
        user_id=user_id,
        kind="scan_completed",
        title="t",
        body="b",
        link="/x",
        target_table="scans",
        target_id=target_id,
        channels=["email", "slack", "teams"],
    )
    # email and teams are disabled; only slack survives.
    assert out == ["slack"]
    # in-app row was written.
    assert seen_create["user_id"] == user_id
    assert seen_create["kind"] == "scan_completed"
    assert seen_create["target_id"] == target_id
    assert seen_get["user_id"] == user_id


def test_apply_prefs_filter_skips_in_app_when_disabled(monkeypatch) -> None:
    """``in_app_enabled=False`` skips the create_notification_sync call."""
    import uuid as _uuid
    from contextlib import contextmanager
    from types import SimpleNamespace

    fake_session = object()

    @contextmanager
    def _fake_session_scope():
        yield fake_session

    fake_prefs = SimpleNamespace(
        email_enabled=True,
        slack_enabled=False,
        teams_enabled=False,
        in_app_enabled=False,
    )

    def _fake_get_prefs_sync(session, *, user_id):  # noqa: ARG001
        return fake_prefs

    def _create_should_not_be_called(*_a, **_kw):  # pragma: no cover
        raise AssertionError(
            "create_notification_sync must NOT run when in_app_enabled=False"
        )

    import core.db as core_db
    import services.notification_service as svc

    monkeypatch.setattr(core_db, "sync_session_scope", _fake_session_scope)
    monkeypatch.setattr(svc, "get_prefs_sync", _fake_get_prefs_sync)
    monkeypatch.setattr(svc, "create_notification_sync", _create_should_not_be_called)

    out = notify_mod._apply_prefs_filter(
        user_id=_uuid.uuid4(),
        kind="scan_completed",
        title="t",
        body="b",
        link=None,
        target_table=None,
        target_id=None,
        channels=["email"],
    )
    # email is enabled in the prefs; channel survives.
    assert out == ["email"]


def test_apply_prefs_filter_passes_through_unknown_channel(monkeypatch) -> None:
    """A channel name that has no matching prefs attr passes through."""
    import uuid as _uuid
    from contextlib import contextmanager
    from types import SimpleNamespace

    fake_session = object()

    @contextmanager
    def _fake_session_scope():
        yield fake_session

    fake_prefs = SimpleNamespace(
        email_enabled=False,
        slack_enabled=False,
        teams_enabled=False,
        in_app_enabled=False,
    )

    import core.db as core_db
    import services.notification_service as svc

    monkeypatch.setattr(core_db, "sync_session_scope", _fake_session_scope)
    monkeypatch.setattr(svc, "get_prefs_sync", lambda s, *, user_id: fake_prefs)
    monkeypatch.setattr(svc, "create_notification_sync", lambda *a, **k: None)

    out = notify_mod._apply_prefs_filter(
        user_id=_uuid.uuid4(),
        kind="scan_completed",
        title="t",
        body="b",
        link=None,
        target_table=None,
        target_id=None,
        channels=["email", "sms"],
    )
    # email is dropped (pref off); "sms" has no mapping → pass through.
    assert out == ["sms"]
