# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Remove BomLens engine containers their scan no longer needs.

The runner removes every container it starts, in a ``finally``. That does
not run when the task never gets there: Celery's hard time limit and a
``terminate=True`` revoke kill the worker process, and a worker that is
SIGKILLed takes its ``finally`` with it. An engine container left that way
keeps running, holding memory and possibly network access, with nothing
waiting for it (security review on #522, M1).

This task removes an engine container (label ``trusca.role=bomlens-engine``)
when any of these holds:

* its scan is not ``running`` (cancelled, failed, finished, or gone);
* it is past its ``trusca.deadline`` label (its own time limit plus margin);
* it has no readable scan id or deadline label.

It runs on the engine queue, on the only worker that can reach the Docker
API: every five minutes from beat when ``SCAN_ENGINE=bomlens``, and right
away when a scan is cancelled. It never touches a container without the
engine label.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import structlog
from sqlalchemy import select

from core.db import sync_session_scope
from integrations.bomlens.runner import (
    BomLensEngineError,
    Docker,
    DockerCli,
)
from models import Scan
from tasks.celery_app import celery_app

log = structlog.get_logger("tasks.bomlens_engine_reaper")


def _running_scans(scan_hexes: set[str]) -> set[str]:
    ids: list[uuid.UUID] = []
    for value in scan_hexes:
        try:
            ids.append(uuid.UUID(hex=value))
        except ValueError:
            continue
    if not ids:
        return set()
    with sync_session_scope() as session:
        rows = session.execute(
            select(Scan.id).where(Scan.id.in_(ids), Scan.status == "running")
        ).scalars()
        return {row.hex for row in rows}


def reap_engine_containers(
    docker: Docker | None = None, *, now: float | None = None
) -> dict[str, int]:
    """Remove the engine containers no running scan needs; return the counts."""
    client: Docker = docker or DockerCli()
    moment = time.time() if now is None else now
    try:
        containers = client.engine_containers()
    except BomLensEngineError:
        log.warning("bomlens_reaper_list_failed", exc_info=True)
        return {"seen": 0, "removed": 0}
    running = _running_scans({c.scan_hex for c in containers if c.scan_hex})
    removed = 0
    for container in containers:
        if container.scan_hex and container.deadline is not None:
            if container.scan_hex in running and container.deadline >= moment:
                continue
            reason = "past_deadline" if container.deadline < moment else "scan_not_running"
        else:
            reason = "unlabelled"
        client.remove(container.id)
        removed += 1
        log.warning(
            "bomlens_reaper_removed",
            container=container.id[:12],
            scan_id=container.scan_hex or None,
            reason=reason,
        )
    log.info("bomlens_reaper_done", seen=len(containers), removed=removed)
    return {"seen": len(containers), "removed": removed}


@celery_app.task(  # type: ignore[misc]
    name="trustedoss.bomlens_engine_reaper",
)
def bomlens_engine_reaper_task() -> dict[str, Any]:
    """Celery entry point (engine queue, see ``tasks.celery_app``)."""
    return reap_engine_containers()


def enqueue_reap(app: Any | None = None) -> None:
    """Ask the engine worker to reap now (a cancelled scan). Best-effort."""
    try:
        (app or celery_app).send_task("trustedoss.bomlens_engine_reaper")
    except Exception:  # noqa: BLE001 - beat reaps within five minutes anyway
        log.warning("bomlens_reaper_enqueue_failed", exc_info=True)


__all__ = ["bomlens_engine_reaper_task", "enqueue_reap", "reap_engine_containers"]
