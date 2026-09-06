# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Unit tests for the pure halves of ``tasks.anonymisation_expiry_sweep`` (#383):
recipient selection/dedup for one expired row, and the enqueue fan-out.

The DB-backed halves (the actual expiry query/commit, super-admin lookup,
end-to-end notification persistence) live in
``tests/integration/test_anonymisation_expiry_sweep.py``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest

from models.user_anonymisation_request import ANONYMISATION_PENDING
from tasks.anonymisation_expiry_sweep import (
    _enqueue_notifications,
    _notification_descriptors,
)


@dataclass
class _Row:
    """A stand-in with the five attributes ``_notification_descriptors`` reads.

    A plain object rather than ``UserAnonymisationRequest`` - constructing the
    ORM model needs a session-bound default for ``id``/``created_at`` this
    test does not want to depend on, and nothing here exercises the ORM.
    """

    id: uuid.UUID
    subject_user_id: uuid.UUID
    requested_by_user_id: uuid.UUID
    approved_by_user_id: uuid.UUID | None
    expires_at: datetime
    state: str = ANONYMISATION_PENDING


def _row(
    *,
    requested_by: uuid.UUID | None = None,
    approved_by: uuid.UUID | None = None,
    subject: uuid.UUID | None = None,
    request_id: uuid.UUID | None = None,
    expires_at: datetime | None = None,
) -> _Row:
    return _Row(
        id=request_id or uuid.uuid4(),
        subject_user_id=subject or uuid.uuid4(),
        requested_by_user_id=requested_by or uuid.uuid4(),
        approved_by_user_id=approved_by,
        expires_at=expires_at or datetime(2026, 9, 1, tzinfo=UTC),
    )


# ---------------------------------------------------------------------------
# _notification_descriptors
# ---------------------------------------------------------------------------


def test_descriptor_includes_requester_and_super_admins() -> None:
    requester = uuid.uuid4()
    admin_a, admin_b = uuid.uuid4(), uuid.uuid4()
    row = _row(requested_by=requester)

    descriptors = _notification_descriptors(row, super_admin_ids=[admin_a, admin_b])

    recipients = {d["user_id"] for d in descriptors}
    assert recipients == {str(requester), str(admin_a), str(admin_b)}


def test_descriptor_dedupes_requester_who_is_also_a_super_admin() -> None:
    requester_and_admin = uuid.uuid4()
    row = _row(requested_by=requester_and_admin)

    descriptors = _notification_descriptors(
        row, super_admin_ids=[requester_and_admin]
    )

    assert len(descriptors) == 1
    assert descriptors[0]["user_id"] == str(requester_and_admin)


def test_descriptor_includes_approver_when_the_row_carries_one() -> None:
    """Defensive path: a ``pending`` row never carries an approver in the
    normal lifecycle (approval moves it to ``approved``, which this sweep
    never selects), but the field exists on the model and a future
    state-machine change should not silently stop notifying whoever is in
    it."""
    requester, approver = uuid.uuid4(), uuid.uuid4()
    row = _row(requested_by=requester, approved_by=approver)

    descriptors = _notification_descriptors(row, super_admin_ids=[])

    assert {d["user_id"] for d in descriptors} == {str(requester), str(approver)}


def test_descriptor_content_names_the_subject_and_deadline() -> None:
    subject = uuid.uuid4()
    expires_at = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
    row = _row(subject=subject, expires_at=expires_at)

    (descriptor,) = _notification_descriptors(row, super_admin_ids=[])

    assert descriptor["kind"] == "approval_pending"
    assert str(subject) in descriptor["body"]
    assert "2026-08-15" in descriptor["body"]
    assert descriptor["link"] == "/admin/users"
    assert descriptor["context"]["subject_user_id"] == str(subject)
    assert descriptor["request_id"] == str(row.id)


def test_descriptor_no_super_admins_still_notifies_the_requester() -> None:
    requester = uuid.uuid4()
    row = _row(requested_by=requester)

    descriptors = _notification_descriptors(row, super_admin_ids=[])

    assert len(descriptors) == 1
    assert descriptors[0]["user_id"] == str(requester)


# ---------------------------------------------------------------------------
# _enqueue_notifications
# ---------------------------------------------------------------------------


def _descriptor(*, request_id: str | None = None) -> dict[str, Any]:
    return {
        "kind": "approval_pending",
        "context": {"request_id": "x", "subject_user_id": "y"},
        "user_id": str(uuid.uuid4()),
        "title": "Anonymisation request expired",
        "body": "expired",
        "link": "/admin/users",
        "request_id": request_id or str(uuid.uuid4()),
    }


def test_enqueue_records_in_app_only_call_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tasks.notify as notify_module

    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def _record(*args: Any, **kwargs: Any) -> None:
        calls.append((args, kwargs))

    monkeypatch.setattr(notify_module.send_notification_task, "delay", _record)

    d = _descriptor()
    assert _enqueue_notifications([d]) == 1
    (args, kwargs) = calls[0]
    assert args == (d["kind"], d["context"], [], [])  # channels=[] → in-app only
    assert kwargs["user_id"] == d["user_id"]
    assert kwargs["in_app_title"] == d["title"]
    assert kwargs["in_app_body"] == d["body"]
    assert kwargs["in_app_link"] == d["link"]
    assert kwargs["in_app_target_table"] == "user_anonymisation_requests"
    assert kwargs["in_app_target_id"] == d["request_id"]


def test_enqueue_swallows_per_descriptor_broker_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tasks.notify as notify_module

    attempts: list[str] = []

    def _flaky(*args: Any, **kwargs: Any) -> None:
        attempts.append(str(kwargs.get("user_id")))
        if len(attempts) == 1:
            raise RuntimeError("broker down")

    monkeypatch.setattr(notify_module.send_notification_task, "delay", _flaky)

    enqueued = _enqueue_notifications([_descriptor(), _descriptor()])
    assert enqueued == 1  # first failed, second went through - never raises
    assert len(attempts) == 2


def test_enqueue_empty_list_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    import tasks.notify as notify_module

    def _explode(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("must not be called for an empty descriptor list")

    monkeypatch.setattr(notify_module.send_notification_task, "delay", _explode)

    assert _enqueue_notifications([]) == 0


# ---------------------------------------------------------------------------
# The Celery-decorated entry point: delegates, and never raises.
# ---------------------------------------------------------------------------


def test_task_entry_point_delegates_to_run_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tasks.anonymisation_expiry_sweep as sweep_module

    sentinel = {"expired": 3, "notifications_enqueued": 5}
    monkeypatch.setattr(sweep_module, "_run_sweep", lambda: sentinel)

    assert sweep_module.anonymisation_expiry_sweep() == sentinel


def test_task_entry_point_never_raises_on_unexpected_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tasks.anonymisation_expiry_sweep as sweep_module

    def _explode() -> dict[str, Any]:
        raise RuntimeError("db is on fire")

    monkeypatch.setattr(sweep_module, "_run_sweep", _explode)

    result = sweep_module.anonymisation_expiry_sweep()

    assert result == {"expired": 0, "notifications_enqueued": 0}
