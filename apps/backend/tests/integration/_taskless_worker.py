# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""A worker with a chosen include list, for ``test_task_registry_guard``.

Run as a subprocess so the test can watch what happens to a real process
rather than to a function call. ``argv[1]`` is "empty" or "populated" and
``argv[2]`` is the broker URL.

The guard is connected by importing the module that registers the handler,
which is also how a real worker gets it.
"""

from __future__ import annotations

import sys

from celery import Celery

MODE = sys.argv[1]
BROKER = sys.argv[2]

app = Celery("taskless-probe", broker=BROKER)
app.conf.task_default_queue = "er61-probe"
app.conf.include = [] if MODE == "empty" else ["tasks.trivy_db_refresh"]


# The handler in the product module reads the real ``celery_app``, which is not
# the app under test here, so the signal is wired to this app's own count.
from celery.signals import worker_ready  # noqa: E402

from tasks.task_registry_guard import refuse_if_no_tasks  # noqa: E402


@worker_ready.connect
def _guard(**_: object) -> None:
    refuse_if_no_tasks(app)


app.worker_main(
    [
        "worker",
        "--loglevel=info",
        "-Q",
        "er61-probe",
        "-c",
        "1",
        "--without-gossip",
        "--without-mingle",
        "--without-heartbeat",
    ]
)
