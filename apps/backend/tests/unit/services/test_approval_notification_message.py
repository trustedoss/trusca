# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
The requester's approval notification names its message (migration 0094).

``_notify_approval_requester`` runs after the decision commits; this drives it
directly with a stub row and records what it enqueues.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from models.component_approval import ApprovalStatus
from services.component_approval_service import _notify_approval_requester


def _record(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    import tasks.notify as notify_module

    calls: list[dict[str, Any]] = []

    def _delay(*args: Any, **kwargs: Any) -> None:
        calls.append({"args": args, **kwargs})

    monkeypatch.setattr(notify_module.send_notification_task, "delay", _delay)
    return calls


def _row() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        component_id=uuid.uuid4(),
        requested_by_user_id=uuid.uuid4(),
    )


@pytest.mark.parametrize(
    ("action", "expected_key"),
    [
        (ApprovalStatus.approved, "approval_approved"),
        (ApprovalStatus.rejected, "approval_rejected"),
        # The plain string form reaches the same message.
        ("approved", "approval_approved"),
        ("rejected", "approval_rejected"),
    ],
)
def test_each_final_state_has_its_own_message(
    monkeypatch: pytest.MonkeyPatch, action: Any, expected_key: str
) -> None:
    calls = _record(monkeypatch)
    row = _row()

    _notify_approval_requester(row, action)  # type: ignore[arg-type]

    (call,) = calls
    assert call["args"][0] == "approval_state_changed"
    assert call["user_id"] == str(row.requested_by_user_id)
    assert call["in_app_message_key"] == expected_key


def test_a_state_without_a_message_keeps_the_english_text_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _record(monkeypatch)

    _notify_approval_requester(_row(), "under_review")  # type: ignore[arg-type]

    (call,) = calls
    assert call["in_app_message_key"] is None
    assert call["in_app_title"]
