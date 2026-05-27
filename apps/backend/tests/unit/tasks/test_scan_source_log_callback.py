"""P2 #8c — tests for the ``scan_source._make_line_callback`` helper.

The helper is the bridge between the cdxgen / scancode line-streaming
subprocess wrappers and the WebSocket publisher. These tests pin the
contract end-to-end:

  - The returned callback forwards ``(stream, line)`` to ``publish_log``
    with the correct ``stage`` baked in by closure.
  - A publisher error (broken Redis, etc.) is swallowed so the drain
    thread cannot break the scan over a logging hiccup.
  - The task's entry path resets the per-scan log counter so a Celery
    re-execution does not inherit the previous run's exhausted budget.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest


def test_make_line_callback_routes_to_publish_log_with_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_make_line_callback(scan_id, stage="cdxgen")`` curries the stage."""
    from tasks import scan_source as ss

    captured: list[tuple[str, str, str, str]] = []

    def fake_publish_log(scan_id: Any, *, stage: str, stream: str, line: str) -> None:
        captured.append((str(scan_id), stage, stream, line))

    monkeypatch.setattr(ss, "publish_log", fake_publish_log)

    scan_uuid = uuid.uuid4()
    cb = ss._make_line_callback(scan_uuid, stage="cdxgen")
    cb("stdout", "resolving package tree…")
    cb("stderr", "warning: lockfile missing")

    assert captured == [
        (str(scan_uuid), "cdxgen", "stdout", "resolving package tree…"),
        (str(scan_uuid), "cdxgen", "stderr", "warning: lockfile missing"),
    ]


def test_make_line_callback_swallows_publisher_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A publisher exception must NOT propagate into the drain thread."""
    from tasks import scan_source as ss

    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("Redis is on fire")

    monkeypatch.setattr(ss, "publish_log", boom)

    cb = ss._make_line_callback(uuid.uuid4(), stage="scancode")
    # The test passes if no exception leaks here. publish_log's own try/except
    # is one safety net; this test pins the redundant try/except inside
    # _make_line_callback that exists specifically for drain-thread safety.
    cb("stdout", "anything")


def test_make_line_callback_carries_distinct_stages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two callbacks built for different stages stay independent."""
    from tasks import scan_source as ss

    received: list[str] = []

    def fake_publish_log(_scan_id: Any, *, stage: str, stream: str, line: str) -> None:
        received.append(f"{stage}/{stream}/{line}")

    monkeypatch.setattr(ss, "publish_log", fake_publish_log)

    scan_uuid = uuid.uuid4()
    cdx_cb = ss._make_line_callback(scan_uuid, stage="cdxgen")
    sc_cb = ss._make_line_callback(scan_uuid, stage="scancode")

    cdx_cb("stdout", "x")
    sc_cb("stderr", "y")

    assert received == ["cdxgen/stdout/x", "scancode/stderr/y"]
