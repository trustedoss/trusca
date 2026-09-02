# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Carry the request id from the dispatching request into the worker.

A scan starts as an HTTP request and finishes minutes later inside a worker
process. Until now those two halves could not be joined up in the logs. The
middleware binds ``request_id`` for the request, but ``apply_async`` sends
only the task arguments, so every log line the worker emits belongs to no
request at all. Answering "what happened to the scan that user kicked off"
meant guessing from timestamps.

Three signals close that gap, and none of them requires touching a call site:

``before_task_publish``
    Runs in the dispatching process, where the request context still exists.
    Copies ``request_id`` into the message headers.

``task_prerun``
    Runs in the worker. Reads the header back and binds it, together with the
    task name and the Celery task id, so every line the task logs carries all
    three.

``task_postrun``
    Clears what was bound. Worker processes are reused, so a value left behind
    would attach itself to the next task that runs in that slot and quietly
    misattribute its logs.

Doing this at the signal level rather than per task matters for coverage.
Twenty-four task modules bind some context by hand today and each one binds a
different subset; a task added tomorrow binds whatever its author remembers.
The signals cover every task, including ones dispatched by beat, which have no
request id at all and are simply left without one.

The handlers never raise. A logging concern must not be able to fail a scan,
so each one swallows its own errors: losing a context field is a degradation,
losing the task is an outage.
"""

from __future__ import annotations

from typing import Any

import structlog
from celery.signals import before_task_publish, task_postrun, task_prerun

log = structlog.get_logger("tasks.log_context")

#: Header key on the Celery message. Namespaced to avoid colliding with the
#: protocol's own header names, present and future.
REQUEST_ID_HEADER = "trusca_request_id"

#: Context keys this module owns. ``task_postrun`` clears exactly these, so a
#: task that binds something of its own keeps responsibility for unbinding it.
_BOUND_KEYS = ("request_id", "task_name", "celery_task_id")


def _current_request_id() -> str | None:
    """The request id bound to this context, if any.

    Absent for anything beat dispatches, and for a task dispatched by another
    task. Both are legitimate: the point is to propagate an id that exists,
    not to invent one that does not.
    """
    bound = structlog.contextvars.get_contextvars()
    value = bound.get("request_id")
    return str(value) if value else None


def attach_request_id(
    headers: dict[str, Any] | None = None,
    **_: Any,
) -> None:
    """Copy the dispatching request's id onto the outgoing message.

    Celery protocol 2 puts custom keys in ``headers``, which is what the
    worker sees on the other side as ``task.request``.
    """
    try:
        if headers is None:
            return
        request_id = _current_request_id()
        if request_id:
            headers[REQUEST_ID_HEADER] = request_id
    except Exception as exc:  # noqa: BLE001 - never break a dispatch
        log.warning("log_context.attach_failed", error=str(exc))


def bind_task_context(
    task_id: str | None = None,
    task: Any = None,
    **_: Any,
) -> None:
    """Bind the request id, the task name and the Celery task id."""
    try:
        fields: dict[str, str] = {}
        if task_id:
            fields["celery_task_id"] = str(task_id)
        name = getattr(task, "name", None)
        if name:
            fields["task_name"] = str(name)

        request = getattr(task, "request", None)
        request_id = getattr(request, REQUEST_ID_HEADER, None) if request else None
        if request_id:
            fields["request_id"] = str(request_id)

        if fields:
            structlog.contextvars.bind_contextvars(**fields)
    except Exception as exc:  # noqa: BLE001 - never fail a task
        log.warning("log_context.bind_failed", error=str(exc))


def clear_task_context(**_: Any) -> None:
    """Unbind what this module bound.

    Only these keys. Clearing everything would also drop context a task bound
    for itself, and would clear the request context in an eager-mode test that
    dispatches inside a request.
    """
    try:
        structlog.contextvars.unbind_contextvars(*_BOUND_KEYS)
    except Exception as exc:  # noqa: BLE001 - never fail a task
        log.warning("log_context.clear_failed", error=str(exc))


# Connected explicitly rather than with the ``@signal.connect`` decorator.
# Celery ships no type information for those decorators, so decorating erases
# the annotations from the functions below them and mypy stops checking their
# bodies. Calling ``connect`` keeps the functions typed, and it puts every
# connection in one visible place.
before_task_publish.connect(attach_request_id)
task_prerun.connect(bind_task_context)
task_postrun.connect(clear_task_context)
