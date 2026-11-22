# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Remove BomLens engine containers their scan no longer needs.

The runner removes every container it starts, in a ``finally``. That does
not run when the task never gets there: Celery's hard time limit and a
``terminate=True`` revoke kill the worker process, and a worker that is
SIGKILLed takes its ``finally`` with it. An engine container left that way
keeps running, holding memory and possibly network access, with nothing
waiting for it (security review on #522, M1).

This task removes an engine container (the ``bomlens-runner`` package's
``app.kubernetes.io/part-of=bomlens-engine`` label) when any of these holds:

* its scan is in this database and not ``running`` (cancelled, failed,
  finished);
* it is past its deadline label (its own time limit plus margin), which also
  covers a scan this database does not know;
* it has no readable scan id or deadline label.

Only containers carrying this installation's ``trusca.instance`` label
(``TRUSCA_INSTANCE_ID``) are listed at all, and the package refuses to remove
one that is not in that scope.

It runs on the engine queue, on the only worker that can reach the Docker
API: every five minutes from beat when the BomLens engine is in use, and right
away when a scan is cancelled. It never touches a container without the
engine label.

Before the package, the runner started containers labelled
``trusca.role=bomlens-engine``. Those of this installation are removed too
(:func:`remove_legacy_containers`), so an upgrade does not leave one behind.
That path can go once no installation upgrades from such a release.
"""

from __future__ import annotations

import subprocess  # noqa: S404 - the docker CLI, fixed arguments
import time
import uuid
from typing import Any

import structlog
from sqlalchemy import select

from core.config import scan_engine_backend, trusca_instance_id
from core.db import sync_session_scope
from integrations.bomlens import engine
from models import Scan
from tasks.celery_app import celery_app

log = structlog.get_logger("tasks.bomlens_engine_reaper")


def _scan_statuses(scan_hexes: set[str]) -> dict[str, str]:
    """``{scan hex: status}`` for the scans this database knows."""
    ids: list[uuid.UUID] = []
    for value in scan_hexes:
        try:
            ids.append(uuid.UUID(hex=value))
        except ValueError:
            continue
    if not ids:
        return {}
    with sync_session_scope() as session:
        rows = session.execute(select(Scan.id, Scan.status).where(Scan.id.in_(ids))).all()
        return {scan_id.hex: str(status) for scan_id, status in rows}


def _reap_reason(
    container: engine.EngineContainer, statuses: dict[str, str], now: float
) -> str | None:
    """Why ``container`` should go, or ``None`` to keep it.

    A scan this database does not know keeps its container until the deadline:
    the row may be gone, but the container may also belong to a database this
    worker does not see (a restore, a second installation with the same
    instance id), and the deadline bounds it either way.
    """
    if not container.scan_id or container.deadline is None:
        return "unlabelled"
    if container.deadline < now:
        return "past_deadline"
    status = statuses.get(container.scan_id)
    if status is None:
        return None
    return None if status == "running" else "scan_not_running"


#: The label the runner before the package put on every engine container.
_LEGACY_LABEL = "trusca.role=bomlens-engine"


def remove_legacy_containers() -> int:
    """Remove this installation's containers from the runner before the package.

    They can only be left over from a worker that was stopped by the upgrade,
    whose scans are gone with it, so none is kept. Best-effort: a daemon that
    cannot be asked is logged and the count is 0.
    """
    instance = trusca_instance_id()
    listed = ["docker", "ps", "--all", "--quiet", "--no-trunc"]
    for label in (_LEGACY_LABEL, f"trusca.instance={instance}"):
        listed += ["--filter", f"label={label}"]
    try:
        done = subprocess.run(  # noqa: S603 - fixed arguments, a label from settings
            listed, capture_output=True, text=True, timeout=60, check=False
        )
        if done.returncode != 0:
            return 0
        ids = [line for line in done.stdout.split() if line]
        for cid in ids:
            subprocess.run(  # noqa: S603 - an id the daemon just listed
                ["docker", "rm", "--force", cid],  # noqa: S607
                capture_output=True,
                timeout=60,
                check=False,
            )
    except (OSError, subprocess.SubprocessError):
        log.warning("bomlens_reaper_legacy_failed", exc_info=True)
        return 0
    if ids:
        log.warning("bomlens_reaper_legacy_removed", count=len(ids))
    return len(ids)


def reap_engine_containers(
    backend: Any | None = None, *, now: float | None = None
) -> dict[str, int]:
    """Remove the engine containers no running scan needs; return the counts."""
    moment = time.time() if now is None else now
    try:
        client = backend or engine.engine_backend()
        containers = client.list_engine_containers({"trusca.instance": trusca_instance_id()})
    except Exception:  # noqa: BLE001 - a daemon that cannot be asked is not a crash
        log.warning("bomlens_reaper_list_failed", exc_info=True)
        return {"seen": 0, "removed": 0}
    statuses = _scan_statuses({c.scan_id for c in containers if c.scan_id})
    removed = 0
    for container in containers:
        reason = _reap_reason(container, statuses, moment)
        if reason is None:
            continue
        # The backend refuses a name outside this installation's scope.
        if client.remove_engine_container(container.name):
            removed += 1
            log.warning(
                "bomlens_reaper_removed",
                container=container.name,
                scan_id=container.scan_id,
                reason=reason,
            )
    log.info("bomlens_reaper_done", seen=len(containers), removed=removed)
    return {"seen": len(containers), "removed": removed}


@celery_app.task(  # type: ignore[misc]
    name="trustedoss.bomlens_engine_reaper",
)
def bomlens_engine_reaper_task() -> dict[str, Any]:
    """Celery entry point (engine queue, see ``tasks.celery_app``)."""
    counts = reap_engine_containers()
    # The containers of the runner before the package were Docker containers.
    legacy = remove_legacy_containers() if scan_engine_backend() == "docker" else 0
    return {**(counts or {}), "legacy_removed": legacy}


def enqueue_reap(app: Any | None = None) -> None:
    """Ask the engine worker to reap now (a cancelled scan). Best-effort."""
    try:
        (app or celery_app).send_task("trustedoss.bomlens_engine_reaper")
    except Exception:  # noqa: BLE001 - beat reaps within five minutes anyway
        log.warning("bomlens_reaper_enqueue_failed", exc_info=True)


__all__ = [
    "bomlens_engine_reaper_task",
    "enqueue_reap",
    "reap_engine_containers",
    "remove_legacy_containers",
]
