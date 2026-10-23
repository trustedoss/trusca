# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Retries and redeliveries of ``trustedoss.send_notification`` deliver each
unit once.

These run the registered Celery task through ``apply``, so the real
autoretry envelope decides when to retry. Two pieces of the worker are
reproduced so the run matches production:

  - The worker's stack protection (``celery.app.trace``), which the worker
    installs because our base task class overrides ``__call__``. Without it
    ``self.request.id`` is empty inside the task.
  - The broker. ``Task.retry`` is replaced by one that only raises ``Retry``
    (what it does after publishing), and the test publishes the retry
    itself: the next ``apply`` with the same task id and ``retries + 1``.
    Eager mode's own retry nests the next attempt inside the current one,
    which no worker does.

The real dispatcher runs too; only the outermost edges are replaced: the
per-channel sends, the DB session behind the in-app row, the ticket enqueue,
and Redis (fakeredis).

A redelivery is the same message delivered again, so it is modelled as
another ``apply`` with the same task id and ``retries`` back at 0.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import fakeredis
import pytest

from notifications import NotificationDeliveryError, NotificationError, delivery_ledger
from notifications import dispatcher as disp
from tasks import notify as notify_mod


class _Edges:
    """Counts what reached the outside world."""

    def __init__(self) -> None:
        self.emails: list[str] = []
        self.email_attempts: list[str] = []
        self.slack_attempts = 0
        self.slack_delivered = 0
        self.in_app_rows: list[dict[str, Any]] = []
        self.tickets: list[str] = []
        # Scripts: how many times each edge should fail before succeeding.
        self.slack_failures_left = 0
        self.email_failures_left: dict[str, int] = {}
        self.email_permanent: set[str] = set()
        self.smtp_connections = 0
        self.redis: Any = None
        self.ticket_raises_left = 0


@pytest.fixture
def worker_like(monkeypatch):
    from celery.app import trace
    from celery.app.task import Task as BaseTask
    from celery.exceptions import Retry

    already = getattr(BaseTask, "_stackprotected", False)
    trace._install_stack_protection()

    def _retry(*_args: Any, exc: BaseException | None = None, **_kwargs: Any) -> None:
        raise Retry(exc=exc)

    monkeypatch.setattr(notify_mod.send_notification_task, "retry", _retry)
    yield
    if not already:
        BaseTask.__call__ = trace._patched.pop("BaseTask.__call__")
        del BaseTask._stackprotected


@pytest.fixture
def edges(monkeypatch, worker_like) -> _Edges:  # noqa: ARG001
    state = _Edges()
    server = fakeredis.FakeServer()
    state.redis = fakeredis.FakeRedis(server=server, decode_responses=True)
    monkeypatch.setattr(
        delivery_ledger,
        "redis_client",
        lambda: fakeredis.FakeRedis(server=server, decode_responses=True),
    )

    class _Batch:
        """Stands in for EmailBatch: one connection per batch, one send per
        recipient. Opening is counted so a batch with nothing left to send
        can be seen not to connect."""

        async def __aenter__(self):
            state.smtp_connections += 1
            return self

        async def __aexit__(self, *_exc):
            return None

        async def send_to(self, to):
            state.email_attempts.append(to)
            if to in state.email_permanent:
                raise NotificationError("SMTP rejected recipient: 550")
            if state.email_failures_left.get(to, 0) > 0:
                state.email_failures_left[to] -= 1
                raise NotificationDeliveryError("SMTP delivery failed: timeout")
            state.emails.append(to)

    async def _slack(*, payload):  # noqa: ARG001
        state.slack_attempts += 1
        if state.slack_failures_left > 0:
            state.slack_failures_left -= 1
            raise NotificationDeliveryError("Slack 503")
        state.slack_delivered += 1

    async def _teams(*, payload):  # noqa: ARG001
        return None

    monkeypatch.setattr(disp, "_open_email_batch", lambda payload: _Batch())
    monkeypatch.setattr(disp, "_send_slack_channel", _slack)
    monkeypatch.setattr(disp, "_send_teams_channel", _teams)

    @contextmanager
    def _session_scope():
        yield object()

    prefs = SimpleNamespace(
        email_enabled=True, slack_enabled=True, teams_enabled=True, in_app_enabled=True
    )

    def _create(session, **kwargs):  # noqa: ARG001
        state.in_app_rows.append(kwargs)
        return SimpleNamespace(id=uuid.uuid4(), **kwargs)

    import core.db as core_db
    import services.notification_service as svc
    import tasks.ticket_webhook as ticket_webhook

    monkeypatch.setattr(core_db, "sync_session_scope", _session_scope)
    monkeypatch.setattr(svc, "get_prefs_sync", lambda session, *, user_id: prefs)
    monkeypatch.setattr(svc, "create_notification_sync", _create)

    def _enqueue(*, event, context):  # noqa: ARG001
        if state.ticket_raises_left > 0:
            state.ticket_raises_left -= 1
            raise RuntimeError("broker refused")
        state.tickets.append(event)
        return True

    monkeypatch.setattr(ticket_webhook, "enqueue_ticket_event", _enqueue)
    return state


def _apply(task_id: str, *, recipients: list[str], channels: list[str]) -> Any:
    """Deliver one message, then every retry it asks for, like a worker."""
    task = notify_mod.send_notification_task
    retries = 0
    while True:
        result = task.apply(
            args=["scan_completed", {"project_name": "p", "scan_id": "s"}, channels, recipients],
            kwargs={"user_id": str(uuid.UUID(int=7)), "in_app_title": "CVE"},
            task_id=task_id,
            retries=retries,
        )
        if result.state != "RETRY":
            return result
        retries += 1
        assert retries <= task.max_retries, "retried past max_retries"


def test_the_task_sees_its_own_id(edges: _Edges, monkeypatch) -> None:  # noqa: ARG001
    """The ledger is keyed on it; an empty id would silently disable it."""
    seen: list[str | None] = []
    real = delivery_ledger.ledger_for_event

    def _spy(event_id: str | None) -> delivery_ledger.DeliveryLedger:
        seen.append(event_id)
        return real(event_id)

    monkeypatch.setattr(notify_mod, "ledger_for_event", _spy)
    _apply("evt-id", recipients=[], channels=["slack"])
    assert seen == ["evt-id"]


def test_slack_failing_once_retries_slack_only(edges: _Edges) -> None:
    edges.slack_failures_left = 1

    _apply("evt-slack-once", recipients=["a@example.com"], channels=["email", "slack"])

    assert edges.slack_attempts == 2
    assert edges.slack_delivered == 1
    assert edges.emails == ["a@example.com"]
    assert edges.email_attempts == ["a@example.com"]
    assert len(edges.in_app_rows) == 1
    assert edges.tickets == ["scan_completed"]


def test_redelivery_after_success_sends_nothing(edges: _Edges) -> None:
    _apply("evt-redeliver", recipients=["a@example.com"], channels=["email", "slack"])
    assert edges.emails == ["a@example.com"]

    # acks_late redelivery: same message, same task id, retries back at 0.
    result = _apply(
        "evt-redeliver", recipients=["a@example.com"], channels=["email", "slack"]
    )

    assert edges.email_attempts == ["a@example.com"]
    assert edges.slack_attempts == 1
    assert len(edges.in_app_rows) == 1
    assert edges.tickets == ["scan_completed"]
    report = result.get()
    assert report["retryable_failures"] is False
    assert {c["channel"] for c in report["channels"]} == {"email", "slack"}
    slack = next(c for c in report["channels"] if c["channel"] == "slack")
    assert slack == {"channel": "slack", "status": "ok", "already_delivered": True}
    email = next(c for c in report["channels"] if c["channel"] == "email")
    assert email == {"channel": "email", "status": "ok", "already_delivered": True}
    # Nothing left to send, so the redelivery did not even connect.
    assert edges.smtp_connections == 1


def test_partial_email_failure_resends_only_to_failed_recipient(edges: _Edges) -> None:
    edges.email_failures_left = {"b@example.com": 1}

    _apply(
        "evt-partial",
        recipients=["a@example.com", "b@example.com", "c@example.com"],
        channels=["email"],
    )

    assert sorted(edges.emails) == ["a@example.com", "b@example.com", "c@example.com"]
    # First attempt tried all three; the retry tried only b.
    assert edges.email_attempts == [
        "a@example.com",
        "b@example.com",
        "c@example.com",
        "b@example.com",
    ]
    # One connection per attempt, not one per recipient.
    assert edges.smtp_connections == 2
    assert len(edges.in_app_rows) == 1
    assert edges.tickets == ["scan_completed"]


def test_different_events_are_not_deduplicated(edges: _Edges) -> None:
    _apply("evt-one", recipients=["a@example.com"], channels=["email", "slack"])
    _apply("evt-two", recipients=["a@example.com"], channels=["email", "slack"])

    assert edges.emails == ["a@example.com", "a@example.com"]
    assert edges.slack_delivered == 2
    assert len(edges.in_app_rows) == 2
    assert len(edges.tickets) == 2


def test_same_address_in_different_case_is_one_recipient(edges: _Edges) -> None:
    _apply("evt-case", recipients=["A@Example.com", "a@example.com"], channels=["email"])
    assert edges.emails == ["A@Example.com"]


def test_permanent_email_failure_does_not_retry(edges: _Edges) -> None:
    edges.email_permanent = {"gone@example.com"}

    result = _apply(
        "evt-permanent",
        recipients=["a@example.com", "gone@example.com"],
        channels=["email"],
    )

    report = result.get()
    assert report["retryable_failures"] is False
    assert report["channels"][0]["status"] == "failed"
    assert report["channels"][0]["retryable"] is False
    assert edges.email_attempts == ["a@example.com", "gone@example.com"]


def test_in_app_claim_is_released_when_the_insert_fails(edges: _Edges, monkeypatch) -> None:
    import services.notification_service as svc

    calls: list[int] = []

    def _fail_then_write(session, **kwargs):  # noqa: ARG001
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("db down")
        edges.in_app_rows.append(kwargs)

    monkeypatch.setattr(svc, "create_notification_sync", _fail_then_write)

    first = _apply("evt-db", recipients=[], channels=["slack"])
    assert first.failed()
    second = _apply("evt-db", recipients=[], channels=["slack"])
    assert second.successful()
    assert len(edges.in_app_rows) == 1


def test_no_task_id_means_no_ledger(monkeypatch) -> None:
    """Direct calls outside Celery behave as before: nothing is recorded."""

    def _no_redis():  # pragma: no cover
        raise AssertionError("no ledger without a task id")

    monkeypatch.setattr(delivery_ledger, "redis_client", _no_redis)
    ledger = delivery_ledger.ledger_for_event(None)
    assert isinstance(ledger, delivery_ledger.NullLedger)
    assert ledger.claim("x") is True
    assert ledger.is_done("x") is False


def test_ledger_redis_errors_fall_back_to_delivering() -> None:
    class _Broken:
        def __getattr__(self, name: str) -> Any:
            def _raise(*_a: Any, **_k: Any) -> Any:
                raise ConnectionError("redis down")

            return _raise

    ledger = delivery_ledger.RedisDeliveryLedger(event_id="e", client=_Broken())
    assert ledger.is_done("slack") is False
    assert ledger.claim("in_app") is True
    ledger.mark_done("slack")
    ledger.release("in_app")


def test_email_unit_does_not_hold_the_address() -> None:
    unit = delivery_ledger.email_unit(" Alice@Example.com ")
    assert unit == delivery_ledger.email_unit("alice@example.com")
    assert "alice" not in unit
    assert unit.startswith("email:")


def test_permanently_rejected_recipient_is_not_retried(edges: _Edges) -> None:
    """A 550 for one address must not be resent when another recipient's
    transient failure retries the task."""
    edges.email_permanent = {"gone@example.com"}
    edges.email_failures_left = {"b@example.com": 1}

    result = _apply(
        "evt-mixed",
        recipients=["gone@example.com", "b@example.com"],
        channels=["email"],
    )

    assert edges.email_attempts == ["gone@example.com", "b@example.com", "b@example.com"]
    assert edges.emails == ["b@example.com"]
    assert result.get()["retryable_failures"] is False


def test_in_app_claim_is_short_until_the_row_commits(edges: _Edges, monkeypatch) -> None:
    """A worker killed after the claim and before the commit must not lose
    the row for the whole ledger TTL."""
    import services.notification_service as svc

    ttls: list[int] = []

    def _create(session, **kwargs):  # noqa: ARG001
        ttls.append(edges.redis.ttl("trusca:notify:delivery:evt-ttl:in_app"))
        edges.in_app_rows.append(kwargs)

    monkeypatch.setattr(svc, "create_notification_sync", _create)

    _apply("evt-ttl", recipients=[], channels=["slack"])

    (during,) = ttls
    assert 0 < during <= delivery_ledger.CLAIM_TTL_SECONDS
    after = edges.redis.ttl("trusca:notify:delivery:evt-ttl:in_app")
    assert after > delivery_ledger.CLAIM_TTL_SECONDS


def test_ticket_claim_is_released_when_the_enqueue_raises(edges: _Edges) -> None:
    edges.ticket_raises_left = 1
    edges.slack_failures_left = 1  # forces a second attempt

    _apply("evt-ticket", recipients=[], channels=["slack"])

    assert edges.tickets == ["scan_completed"]
    assert edges.redis.ttl("trusca:notify:delivery:evt-ticket:ticket") > (
        delivery_ledger.CLAIM_TTL_SECONDS
    )
