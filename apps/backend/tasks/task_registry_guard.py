# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
A worker that registered none of our tasks refuses to run (ER61).

NOT a Celery task. This module registers a ``worker_ready`` handler, so it has
to be on ``celery_app``'s include list or it never runs.

Why refusing rather than warning
--------------------------------
A worker with no tasks of ours is not degraded, it is inert, and staying up is
worse than stopping. Measured on a worker with an empty include list: a message
for a task it does not have is answered with ``Received unregistered task``,
rejected, and NOT requeued. The queue went from one message to none. So the
running misconfigured worker does not leave the work waiting for somebody to
fix the deployment; it consumes the work and discards it, and a healthy worker
started afterwards finds nothing to do. Stopping leaves the message in the
queue.

Nothing about the failure is visible before that. The worker logs ``ready.``
exactly like a healthy one. The error arrives only once somebody triggers a
scan, it arrives in the worker's log while the person is watching the portal,
and in a deployment with several workers a healthy one may take the message so
the line never appears at all.

Refusing shows up in every deployment shape an operator already reads: a
restart loop under Compose, CrashLoopBackOff under Kubernetes.

Why ``os._exit`` and not an exception
-------------------------------------
Measured, because the obvious spellings do not do what they read like:

    raise RuntimeError    the worker keeps running. Celery catches exceptions
                          from signal handlers, so the code says refuse and the
                          behaviour is warn.
    WorkerShutdown        the worker stops, and the process exits 0.
    sys.exit(1)           the same, exit code 0.
    os._exit(1)           the process ends with 1.

Exit code zero is not a small difference here. Both Compose and Kubernetes
restart on it, so the restart loop appears either way, but ``Exited (0)`` tells
an operator and any monitoring that reads exit codes that the worker finished
normally, which is false about a deployment that cannot run a single task.

``os._exit`` skips interpreter cleanup, which is acceptable for a process that
registered nothing and consumed nothing, but it also skips flushing. The
diagnosis is the entire value of this guard, so the log is flushed explicitly
before the call: ``logging.shutdown()`` first, because a handler writing to a
file or a collector holds its own buffer that flushing the streams does not
touch, then the streams.

Why ``worker_ready`` and not something earlier
----------------------------------------------
Not at import: ``tasks.celery_app`` on its own registers zero of our tasks,
because Celery imports the include list when a worker starts. A count taken
there refuses every boot, which is a worse outage than the one being prevented.
``tests/unit/test_celery_app.py`` records the same fact for its own reasons.

``celeryd_init`` and ``worker_init`` were measured with the modules already
loaded, so an earlier check would appear to work. It was not chosen because
that is one observation of one pool on one Celery version, and being wrong in
that direction takes every worker down. ``worker_ready`` is certainly after the
import and is where this repository's other boot hooks already live.

The cost is a window: the consumer is up when this fires. Measured five times
with a message waiting in the queue, it was still there when the guard ran, so
nothing was lost. The window is not provably zero, and the guard's claim is
that it turns unbounded loss into at most whatever one worker can take in the
moment between the consumer starting and this handler running.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

import structlog
from celery.signals import worker_ready

log = structlog.get_logger("tasks.task_registry_guard")

#: Every task this deployment defines is named ``trustedoss.<something>``.
#: Celery registers nine builtins of its own on every app (``celery.chain``,
#: ``celery.group`` and so on), so "no tasks at all" is a condition that never
#: occurs and a guard written that way would never fire.
TASK_PREFIX = "trustedoss."


def registered_task_count(app: Any) -> int:
    """How many of OUR tasks this app has, ignoring Celery's own."""
    return sum(1 for name in app.tasks if name.startswith(TASK_PREFIX))


def refuse_if_no_tasks(app: Any) -> int:
    """Stop the process when the worker has none of our tasks.

    Returns the count when there is at least one, so a caller and a test can
    see what it decided. Does not return otherwise.

    A plain function rather than only a signal handler: the decision is worth
    calling directly, and the signal path is worth checking separately by what
    it does to the process rather than by whether it was called.
    """
    count = registered_task_count(app)
    if count > 0:
        log.info("task_registry.ready", tasks=count, prefix=TASK_PREFIX)
        return count

    log.error(
        "task_registry.empty",
        tasks=0,
        prefix=TASK_PREFIX,
        include_count=len(app.conf.include or []),
        action=(
            "This worker registered none of the portal's tasks and cannot run "
            "any work. Left running it would take messages it cannot execute "
            "and discard them, so it is stopping instead, which leaves them in "
            "the queue. Check that tasks.celery_app's include list is intact "
            "and that every module on it imports."
        ),
    )
    # The structured line above may not survive, and the diagnosis is the whole
    # value of this guard, so it is also written to the process's real stderr.
    # A running worker has replaced ``sys.stdout`` and ``sys.stderr`` with its
    # own logging proxies; ``sys.__stderr__`` is the descriptor the container
    # runtime is actually reading. Measured: without this the guard stopped the
    # worker and its explanation never appeared, leaving a container that dies
    # on boot saying nothing.
    if sys.__stderr__ is not None:
        sys.__stderr__.write(
            "FATAL task_registry.empty: this worker registered none of the "
            "portal's tasks and cannot run any work. It is stopping rather "
            "than consuming messages it would have to discard. Check that "
            "tasks.celery_app's include list is intact and that every module "
            "on it imports.\n"
        )
        sys.__stderr__.flush()

    # Then the ordinary machinery, in the order that gets the most out before
    # the process ends: handlers first, because one writing to a file or a
    # collector holds a buffer that flushing the streams does not touch.
    logging.shutdown()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:  # noqa: BLE001, S110 - see below
            # Deliberately silent: the message this is flushing has already
            # been written to the real stderr above, and a stream that cannot
            # be flushed must not turn a clean refusal into a traceback that
            # buries it.
            pass
    os._exit(1)


@worker_ready.connect  # type: ignore[misc]
def _on_worker_ready(sender: Any | None = None, **_: Any) -> None:
    """Celery signal handler. Delegates so the decision is reachable directly."""
    from tasks.celery_app import celery_app

    refuse_if_no_tasks(celery_app)


__all__ = [
    "TASK_PREFIX",
    "_on_worker_ready",
    "refuse_if_no_tasks",
    "registered_task_count",
]
