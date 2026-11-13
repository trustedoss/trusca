# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Seeded notifications keep a fixed order in the inbox.

The e2e seed inserts its notifications in one transaction, so they all got the
same server-side ``created_at``. The inbox orders by ``created_at`` and then
by the random UUID id, which made the order of the three rows differ from one
run to the next and turned the notifications visual baseline into a coin
flip. Each row now gets its own timestamp.

Two things can break without any failure: the helper stops spacing the
timestamps, or ``_seed`` stops passing it to the model, in which case the
database default comes back and the order is random again. The e2e run only
sees that as an occasional pixel diff, so both are asserted here.
"""

from __future__ import annotations

import ast
from datetime import UTC, timedelta
from pathlib import Path

SEED_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "seed_e2e_user.py"


def test_each_seeded_notification_is_a_second_newer_than_the_last() -> None:
    from scripts import seed_e2e_user

    stamps = [seed_e2e_user._notification_created_at(i) for i in range(5)]

    assert [b - a for a, b in zip(stamps, stamps[1:], strict=False)] == [
        timedelta(seconds=1)
    ] * 4
    # A naive datetime would be stored as local time and sort against the
    # server's UTC defaults for the same user's other rows.
    assert all(stamp.tzinfo is not None and stamp.utcoffset() == timedelta(0) for stamp in stamps)
    assert stamps[0].tzinfo is UTC


def test_the_seed_passes_that_timestamp_to_every_notification() -> None:
    """Read ``_seed``'s source: its body needs a database to run."""
    tree = ast.parse(SEED_SCRIPT.read_text(encoding="utf-8"))
    seed_fn = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_seed"
    )
    calls = [
        node
        for node in ast.walk(seed_fn)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Notification"
    ]
    # The model must still be built here; a search that finds nothing would
    # pass the assertion below for the wrong reason.
    assert len(calls) == 1

    created_at = [kw.value for kw in calls[0].keywords if kw.arg == "created_at"]
    assert len(created_at) == 1
    value = created_at[0]
    assert isinstance(value, ast.Call)
    assert isinstance(value.func, ast.Name)
    assert value.func.id == "_notification_created_at"
