# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Worker and beat boot hooks for the outbound trust report (ER25).

NOT a Celery task. This module registers signal handlers, so it has to be in
``celery_app``'s import list or they never register and the processes stay
silent.

Why all three processes report
------------------------------
Docker Compose gives every service its own environment. A certificate
configured for the API and missed on the worker is an ordinary mistake, and it
is the worse direction: the scanners go out from the worker, so that is where a
private certificate authority matters most. One line saying the trust set is
configured, from whichever process happened to log it, would be read as
covering all of them.

``worker_ready`` rather than ``worker_init`` for the same reason
``trivy_db_bootstrap`` uses it: it is the documented point at which the worker
is up, and a report that fires before the process is really running would be
describing a state nobody is in yet. Beat has its own signal.
"""

from __future__ import annotations

from typing import Any

from celery.signals import beat_init, worker_ready

from core.tls_trust import log_trust_store


@worker_ready.connect  # type: ignore[misc]
def _on_worker_ready(sender: Any | None = None, **_: Any) -> None:
    """Report the worker's own trust set once it is consuming the queue."""
    log_trust_store(process="worker")


@beat_init.connect  # type: ignore[misc]
def _on_beat_init(sender: Any | None = None, **_: Any) -> None:
    """Report the scheduler's own trust set. It sends notifications too."""
    log_trust_store(process="beat")


__all__ = ["_on_beat_init", "_on_worker_ready"]
