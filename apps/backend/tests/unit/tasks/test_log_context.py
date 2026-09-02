# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""The request id survives the hop into a worker, and nothing leaks after it.

Three properties carry the design and each one fails silently if it breaks:
the id reaches the worker, it is cleared when the task ends, and a fault in
any handler cannot take the task down with it. A wrong or missing context
field looks exactly like a correct one until somebody tries to trace a scan
back to the request that started it, which is months later and too late.
"""

from __future__ import annotations

from typing import Any

import pytest
import structlog

from tasks.log_context import (
    REQUEST_ID_HEADER,
    attach_request_id,
    bind_task_context,
    clear_task_context,
    close_task_run,
)


class _Request:
    """Stand-in for ``task.request``, which exposes headers as attributes."""

    def __init__(self, **headers: Any) -> None:
        for key, value in headers.items():
            setattr(self, key, value)


class _Task:
    def __init__(self, name: str, request: _Request | None = None) -> None:
        self.name = name
        self.request = request


@pytest.fixture(autouse=True)
def _clean_context() -> Any:
    """Each test starts and ends with an empty context.

    Without this a leak in one test would look like correct propagation in the
    next, which is the exact failure these tests exist to catch.
    """
    structlog.contextvars.clear_contextvars()
    yield
    structlog.contextvars.clear_contextvars()


def _bound() -> dict[str, Any]:
    return dict(structlog.contextvars.get_contextvars())


# ---------------------------------------------------------------------------
# Dispatch side
# ---------------------------------------------------------------------------


def test_dispatch_puts_the_request_id_on_the_message() -> None:
    structlog.contextvars.bind_contextvars(request_id="req-1")
    headers: dict[str, Any] = {}

    attach_request_id(headers=headers)

    assert headers[REQUEST_ID_HEADER] == "req-1"


def test_dispatch_without_a_request_adds_nothing() -> None:
    """Beat dispatches have no request. The header stays absent rather than
    carrying an invented id, so a later trace cannot join unrelated work."""
    headers: dict[str, Any] = {}

    attach_request_id(headers=headers)

    assert headers == {}


def test_dispatch_tolerates_a_missing_headers_mapping() -> None:
    """Celery calls this signal for several message shapes; not all pass
    headers. Returning quietly beats raising inside a dispatch."""
    attach_request_id(headers=None)


def test_dispatch_never_raises_into_the_caller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken context read must not stop a scan from being queued."""

    def _boom() -> dict[str, Any]:
        raise RuntimeError("contextvars unavailable")

    monkeypatch.setattr(structlog.contextvars, "get_contextvars", _boom)

    attach_request_id(headers={})


# ---------------------------------------------------------------------------
# Worker side
# ---------------------------------------------------------------------------


def test_worker_binds_all_three_fields() -> None:
    task = _Task("trustedoss.scan_source", _Request(**{REQUEST_ID_HEADER: "req-2"}))

    bind_task_context(task_id="celery-1", task=task)

    assert _bound() == {
        "request_id": "req-2",
        "task_name": "trustedoss.scan_source",
        "task_id": "celery-1",
    }


def test_worker_binds_what_it_has_when_there_is_no_request_id() -> None:
    """A beat task still gets its name and id; only the request id is absent."""
    task = _Task("trustedoss.kev_catalog_refresh", _Request())

    bind_task_context(task_id="celery-2", task=task)

    bound = _bound()
    assert bound == {
        "task_name": "trustedoss.kev_catalog_refresh",
        "task_id": "celery-2",
    }
    assert "request_id" not in bound


def test_worker_handles_a_task_without_a_request() -> None:
    bind_task_context(task_id="celery-3", task=_Task("trustedoss.x"))

    assert _bound() == {"task_name": "trustedoss.x", "task_id": "celery-3"}


def test_worker_never_raises_into_the_task() -> None:
    """A logging fault must not fail the work. Losing a context field is a
    degradation; losing the task is an outage."""

    class _Hostile:
        name = "trustedoss.x"

        @property
        def request(self) -> Any:
            raise RuntimeError("no request here")

    bind_task_context(task_id="celery-4", task=_Hostile())


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


def test_finishing_clears_everything_this_module_bound() -> None:
    """Worker slots are reused. A left-behind id would attach itself to the
    next task and misattribute its logs to an unrelated request."""
    task = _Task("trustedoss.scan_source", _Request(**{REQUEST_ID_HEADER: "req-3"}))
    bind_task_context(task_id="celery-5", task=task)

    clear_task_context()

    assert _bound() == {}


def test_finishing_leaves_context_the_task_bound_itself() -> None:
    """``scan_id`` and friends belong to the task, which unbinds them itself.

    Clearing the whole context here would delete them mid-flight and would
    also wipe the surrounding request context under eager execution.
    """
    bind_task_context(
        task_id="celery-6",
        task=_Task("trustedoss.scan_source", _Request(**{REQUEST_ID_HEADER: "r"})),
    )
    structlog.contextvars.bind_contextvars(scan_id="scan-9")

    clear_task_context()

    assert _bound() == {"scan_id": "scan-9"}


def test_clearing_an_empty_context_is_harmless() -> None:
    """``task_postrun`` fires even when prerun bound nothing."""
    clear_task_context()

    assert _bound() == {}


def test_clear_never_raises_into_the_task(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_: Any) -> None:
        raise RuntimeError("unbind failed")

    monkeypatch.setattr(structlog.contextvars, "unbind_contextvars", _boom)

    clear_task_context()


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------


def test_the_id_survives_the_hop_and_leaves_nothing_behind() -> None:
    """The whole point, end to end: request, dispatch, worker, cleanup."""
    structlog.contextvars.bind_contextvars(request_id="req-7")
    headers: dict[str, Any] = {}
    attach_request_id(headers=headers)

    # The worker is a different process: nothing carries over but the message.
    structlog.contextvars.clear_contextvars()

    task = _Task("trustedoss.scan_source", _Request(**headers))
    bind_task_context(task_id="celery-7", task=task)
    assert _bound()["request_id"] == "req-7"

    clear_task_context()
    assert _bound() == {}


def test_two_tasks_in_a_reused_slot_do_not_bleed_into_each_other() -> None:
    """The failure this guards is invisible in production: the second task's
    logs would carry the first task's request id and look perfectly valid."""
    first = _Task("trustedoss.a", _Request(**{REQUEST_ID_HEADER: "req-A"}))
    bind_task_context(task_id="celery-A", task=first)
    clear_task_context()

    second = _Task("trustedoss.b", _Request())
    bind_task_context(task_id="celery-B", task=second)

    bound = _bound()
    assert "request_id" not in bound
    assert bound["task_name"] == "trustedoss.b"


# ---------------------------------------------------------------------------
# Wiring
#
# The handlers above are correct in isolation, which is worth nothing if
# nothing calls them. Celery connects receivers at import time, so the check
# is that importing the app leaves all three attached.
# ---------------------------------------------------------------------------


def test_importing_the_app_connects_all_three_signals() -> None:
    from celery.signals import before_task_publish, task_postrun, task_prerun

    import tasks.celery_app  # noqa: F401  (import is the thing under test)

    for signal, handler in (
        (before_task_publish, attach_request_id),
        (task_prerun, bind_task_context),
        # postrun runs the composite: it closes the history row and then
        # clears the context, in that order. Clearing first would drop the
        # ids the row is matched on.
        (task_postrun, close_task_run),
    ):
        receivers = [r[1]() if callable(r[1]) else r[1] for r in signal.receivers]
        assert handler in receivers, f"{handler.__name__} is not connected"
