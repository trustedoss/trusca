# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
M2 (concurrency plan 2026-08-22 §3.1): the two helpers behind the queue
backlog toggle, in isolation from the six DB-only series in
``services/metrics_service.py``.

``render_metrics`` itself is exercised end to end by
``tests/integration/test_metrics_endpoint.py`` against a live Postgres and,
for the broker series, a live Redis. This file pins the two new helpers'
own logic (the fallback on a broker error, the zero baseline with nothing
queued) with fakes, which is what makes those states reachable and
deterministic instead of depending on what a shared integration database
happens to hold at the moment the suite runs.
"""

from __future__ import annotations

from typing import Any

import pytest

from services.metrics_service import _broker_queue_backlog, _oldest_queued_scan_wait_seconds

# ---------------------------------------------------------------------------
# _oldest_queued_scan_wait_seconds
# ---------------------------------------------------------------------------


class _FakeScalarResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one(self) -> Any:
        return self._value


class _FakeSession:
    """Stands in for AsyncSession.execute(...).scalar_one(). The query itself
    is not re-verified here, since it is a single aggregate expression; the
    integration test proves the SQL is correct against a real Postgres. This
    pins what the helper does with the value that comes back."""

    def __init__(self, value: Any) -> None:
        self._value = value

    async def execute(self, _stmt: Any) -> _FakeScalarResult:
        return _FakeScalarResult(self._value)


async def test_no_queued_scan_reports_zero() -> None:
    """COALESCE's NULL branch, when nothing is queued: a bare MAX() over zero
    rows is None, not 0, unless the query coalesces it (it does). This pins
    the Python-side half of that same guarantee: a None the query could still
    hand back is treated as zero, not raised or emitted as the literal text
    `None` into the exposition."""
    session = _FakeSession(None)

    result = await _oldest_queued_scan_wait_seconds(session)  # type: ignore[arg-type]

    assert result == 0.0


async def test_the_aggregate_value_passes_through_rounded() -> None:
    session = _FakeSession(742.198765)

    result = await _oldest_queued_scan_wait_seconds(session)  # type: ignore[arg-type]

    assert result == 742.199


async def test_never_negative_even_from_a_surprising_value() -> None:
    """0.0 is falsy, so ``value or 0.0`` would also swallow a real 0.0, not
    only a None. The query cannot itself hand back a negative wait, but the
    expression here is a Python truthiness check rather than a None check,
    so the boundary is worth pinning on its own."""
    session = _FakeSession(0.0)

    result = await _oldest_queued_scan_wait_seconds(session)  # type: ignore[arg-type]

    assert result == 0.0


# ---------------------------------------------------------------------------
# _broker_queue_backlog
# ---------------------------------------------------------------------------


class _FakeCeleryApp:
    class _Conf:
        task_default_queue = "trustedoss.default"

    conf = _Conf()


class _FakeRedisClient:
    def __init__(self, length: int) -> None:
        self._length = length
        self.closed = False

    def llen(self, _queue: str) -> int:
        return self._length

    def close(self) -> None:
        self.closed = True


class _RaisingRedisClient:
    def llen(self, _queue: str) -> int:
        raise ConnectionError("simulated broker outage")

    def close(self) -> None:
        pass


def test_the_backlog_is_the_queues_llen(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys
    import types

    fake_tasks_module = types.ModuleType("tasks.celery_app")
    fake_tasks_module.celery_app = _FakeCeleryApp()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "tasks.celery_app", fake_tasks_module)

    client = _FakeRedisClient(length=17)
    monkeypatch.setattr(
        "services.metrics_service._redis.Redis.from_url",
        lambda *a, **k: client,
    )

    queue, backlog = _broker_queue_backlog()

    assert queue == "trustedoss.default"
    assert backlog == 17
    assert client.closed, "the client must be closed rather than left open per scrape"


def test_a_broker_error_reports_zero_rather_than_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    """The module docstring's rule (N10): one series failing does not take
    the rest of the scrape down with it."""
    import sys
    import types

    fake_tasks_module = types.ModuleType("tasks.celery_app")
    fake_tasks_module.celery_app = _FakeCeleryApp()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "tasks.celery_app", fake_tasks_module)

    monkeypatch.setattr(
        "services.metrics_service._redis.Redis.from_url",
        lambda *a, **k: _RaisingRedisClient(),
    )

    queue, backlog = _broker_queue_backlog()

    assert queue == "trustedoss.default"
    assert backlog == 0
