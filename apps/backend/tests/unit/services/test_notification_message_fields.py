# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Pure tests for the ``message_key`` / ``params`` validation (migration 0094).

The SPA looks the key up in its catalog and interpolates the params as text,
so the service holds both to a narrow shape. These run without a database.
"""

from __future__ import annotations

import pytest

from schemas.notification import NotificationOut
from services.notification_service import _clean_params, _message_fields


def test_valid_key_and_scalar_params_pass_through() -> None:
    out = _message_fields(
        "vuln_sla_breach",
        {"project_name": "web", "count": 3, "critical": 1, "ratio": 0.5, "flag": True},
    )
    assert out == {
        "message_key": "vuln_sla_breach",
        "params": {"project_name": "web", "count": 3, "critical": 1, "ratio": 0.5, "flag": True},
    }


def test_no_key_means_no_params() -> None:
    assert _message_fields(None, {"project_name": "web"}) == {
        "message_key": None,
        "params": None,
    }


@pytest.mark.parametrize(
    "key",
    [
        # A dot would walk into another part of the i18next catalog.
        "kind.scan_completed",
        "Scan_Completed",
        "1scan",
        "",
        "scan-completed",
        "a" * 65,
        "__proto__",
    ],
)
def test_malformed_key_is_dropped_with_its_params(key: str) -> None:
    assert _message_fields(key, {"project_name": "web"}) == {
        "message_key": None,
        "params": None,
    }


def test_key_at_the_length_cap_is_kept() -> None:
    key = "a" * 64
    assert _message_fields(key, None)["message_key"] == key


def test_nested_and_badly_named_params_are_dropped() -> None:
    cleaned = _clean_params(
        {
            "project_name": "web",
            "nested": {"a": 1},
            "listed": [1, 2],
            "Bad-Name": "x",
            "has.dot": "x",
        }
    )
    assert cleaned == {"project_name": "web"}


def test_long_string_param_is_truncated() -> None:
    cleaned = _clean_params({"error": "x" * 2000})
    assert cleaned is not None
    assert len(cleaned["error"]) == 512
    assert cleaned["error"].endswith("…")


def test_param_count_is_capped() -> None:
    cleaned = _clean_params({f"p{i}": i for i in range(40)})
    assert cleaned is not None
    assert len(cleaned) == 16
    # The first sixteen, in the order the producer gave them.
    assert list(cleaned) == [f"p{i}" for i in range(16)]


def test_params_with_nothing_valid_collapse_to_none() -> None:
    assert _clean_params({"nested": {"a": 1}}) is None
    assert _clean_params({}) is None
    assert _clean_params(None) is None


def test_notification_out_carries_message_fields() -> None:
    """The wire shape exposes both fields and defaults them to null."""
    import uuid
    from datetime import UTC, datetime
    from types import SimpleNamespace

    base = {
        "id": uuid.uuid4(),
        "kind": "account_security",
        "title": "Two-step sign-in is now on",
        "body": "...",
        "link": "/profile",
        "target_table": "users",
        "target_id": uuid.uuid4(),
        "read_at": None,
        "created_at": datetime.now(UTC),
    }
    old_row = NotificationOut.model_validate(
        SimpleNamespace(**base, message_key=None, params=None)
    )
    assert old_row.message_key is None
    assert old_row.params is None

    new_row = NotificationOut.model_validate(
        SimpleNamespace(**base, message_key="mfa_enabled", params={"a": "b"})
    )
    dumped = new_row.model_dump(mode="json")
    assert dumped["message_key"] == "mfa_enabled"
    assert dumped["params"] == {"a": "b"}


@pytest.mark.parametrize("name", ["lng", "ns", "context", "replace", "interpolation"])
def test_reserved_i18next_option_names_are_dropped(name: str) -> None:
    """These names would steer the client's lookup instead of filling a slot."""
    cleaned = _clean_params({"project_name": "web", name: "x"})
    assert cleaned == {"project_name": "web"}
