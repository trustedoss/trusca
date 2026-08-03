# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Worker-boot hook — make the shared workspace volume writable by the backend.

The backend container runs as a NON-root user (uid 1000, see
``apps/backend/Dockerfile.prod``) while the worker runs as ROOT (see
``apps/backend/Dockerfile.worker``). They share the ``workspace_root()`` named
volume: the worker writes per-scan trees there as root, and the backend's
SBOM-ingest endpoint writes the uploaded document under
``{workspace_root()}/sbom-ingest/<project_id>/`` (see
``services.sbom_ingest_service.sbom_ingest_path``).

On a FRESH named volume the mount root is created owned by ``root:root`` with
mode ``0755``, so the non-root backend cannot create the ``sbom-ingest``
subtree and the ingest endpoint fails with
``PermissionError: '/workspace/sbom-ingest'`` (HTTP 500). Source scans were
unaffected only because the ROOT worker does that writing. This hook closes the
gap: the root worker makes the workspace root shared-writable once at boot
(mode ``1777`` — the ``/tmp`` model: any uid may create entries, the sticky bit
prevents one uid from deleting another's).

Why a ``worker_ready`` hook (not the Dockerfile, an init container, or the
worker ``command``):
  - The worker is the one process that runs as root AND mounts the volume, so it
    is the natural owner of shared-volume permission normalisation.
  - It is command-override-proof: ``docker-compose.demo.yml`` overrides the
    worker ``command`` (concurrency), which would silently drop a fix wired into
    that command; the signal path still runs. This mirrors the existing
    ``tasks.trivy_db_bootstrap`` worker-boot hook.
  - No ``depends_on: service_completed_successfully`` ordering (older Compose
    compatibility) and no extra image are needed.

Idempotent and best-effort: a chmod failure is logged, never raised — a
single-role dev box where backend uid == worker uid never needed the fix, and
boot must not crash on it.
"""

from __future__ import annotations

import os
import stat
from typing import Any

import structlog
from celery.signals import worker_ready

from core.config import workspace_root

log = structlog.get_logger(__name__)

# 1777 = rwx for everyone + the sticky bit (the ``/tmp`` model). Lets the
# non-root backend create its ``sbom-ingest`` subtree on the root-owned shared
# volume, while the sticky bit stops one uid deleting another uid's entries.
_SHARED_WRITABLE_MODE = 0o1777


def ensure_workspace_writable() -> bool:
    """Make the shared workspace root writable by the non-root backend.

    Returns ``True`` on success, ``False`` if the directory could not be
    prepared (logged, never raised — boot and callers must not crash on this).
    """
    root = workspace_root()
    try:
        os.makedirs(root, exist_ok=True)
        os.chmod(root, _SHARED_WRITABLE_MODE)
    except OSError as exc:
        log.warning(
            "workspace_prep.chmod_failed",
            path=root,
            error=str(exc),
            detail=(
                "could not make the shared workspace writable; the backend's "
                "SBOM-ingest may fail with PermissionError until the volume "
                "permissions are fixed by hand (chmod 1777 the workspace root)."
            ),
        )
        return False
    mode = stat.S_IMODE(os.stat(root).st_mode)
    log.info("workspace_prep.ready", path=root, mode=oct(mode))
    return True


@worker_ready.connect  # type: ignore[misc]
def _on_worker_ready(sender: Any | None = None, **_: Any) -> None:
    """Celery signal handler — fires once the worker is consuming the queue.

    Delegates to :func:`ensure_workspace_writable` so the same code path is
    reachable from tests without driving Celery's signal machinery.
    """
    ensure_workspace_writable()


__all__ = ["ensure_workspace_writable", "_on_worker_ready"]
