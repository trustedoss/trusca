# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Hand registry credentials to Trivy without putting them in its environment.

Why a file and not environment variables
----------------------------------------
``integrations._subprocess_env.scrubbed_env_for_trivy`` deliberately strips
``TRIVY_USERNAME`` / ``TRIVY_PASSWORD``, and a test pins that closed. The
reasoning is in that module: Trivy parses attacker-influenced container images
and SBOM JSON, so a parser bug, a crash report or a DNS lookup on an error path
must have no credential in the process environment to carry out.

So the credential goes in a file that Trivy reads and the environment carries
only a path. ``DOCKER_CONFIG`` names a DIRECTORY; Trivy (like Docker) reads
``$DOCKER_CONFIG/config.json`` from it.

Where the file must not be
--------------------------
Not under the scan workspace. ``scripts/backup.sh`` tars the whole workspace
into ``workspace.tar.gz``, so a credential file inside it would be written in
plaintext into every backup, and backups get copied and kept.

The workspace root is operator-configurable (``WORKSPACE_HOST_PATH``), so
"somewhere else" cannot be settled by picking a path once: an operator who
points the workspace at ``/tmp`` would pull our directory inside it. The
location is therefore checked against the live workspace root at call time and
refuses to proceed if it falls inside. Failing the scan is better than silently
shipping credentials to the backup archive.

Cleanup
-------
Removed in a ``finally``, so an exception on the Trivy call does not leave it
behind. A SIGKILL cannot be caught and will, so :func:`sweep_stale_auth_dirs`
clears anything older than the hard scan time limit at the start of each run.
That is an admission rather than a claim: saying a file is always deleted when
it sometimes is not would be worse than sweeping.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
import time
import uuid
from base64 import b64encode
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import structlog

from core.config import scan_hard_time_limit_seconds, workspace_root
from services.registry_allowlist import registry_auth_key

log = structlog.get_logger("tasks.registry_auth")

#: Marks the directories this module owns, so the sweep never removes anything
#: else that happens to live in the temp root.
_DIR_PREFIX = "trusca-registry-auth-"


class RegistryAuthLocationError(RuntimeError):
    """The chosen credential directory is not a safe place to write one."""


def _temp_root() -> Path:
    """Base directory for credential dirs, outside the scan workspace.

    ``TMPDIR`` is honoured (``tempfile.gettempdir()``) so a deployment that
    already points temp storage somewhere private keeps that, but wherever it
    lands is verified below rather than trusted.
    """
    return Path(tempfile.gettempdir())


def _assert_outside_workspace(path: Path) -> None:
    """Refuse a path inside the scan workspace, which backup.sh archives."""
    try:
        workspace = Path(workspace_root()).resolve()
    except OSError:  # pragma: no cover - unresolvable root, treat as unknown
        workspace = Path(workspace_root())
    resolved = path.resolve()
    if resolved == workspace or workspace in resolved.parents:
        raise RegistryAuthLocationError(
            f"refusing to write registry credentials to {resolved}: it is inside "
            f"the scan workspace ({workspace}), which scripts/backup.sh archives "
            "in full. Point TMPDIR outside WORKSPACE_HOST_PATH."
        )


def build_docker_config(credentials: dict[str, tuple[str, str]]) -> dict[str, object]:
    """Render a Docker ``config.json`` for ``{host: (username, password)}``.

    The ``auths`` map is keyed by the registry's credential-store key, which is
    what binds a credential to the registry it belongs to: Trivy sends it only
    when pulling from that registry, so a credential for one is never offered
    to another.

    That key is the host for every registry except Docker Hub, which is why it
    goes through :func:`registry_auth_key` rather than being used directly. A
    wrong key here is silent: the pull falls back to anonymous and fails with a
    permission error that looks like a rejected password.
    """
    auths: dict[str, dict[str, str]] = {}
    for host, (username, password) in credentials.items():
        token = b64encode(f"{username}:{password}".encode()).decode("ascii")
        auths[registry_auth_key(host)] = {"auth": token}
    return {"auths": auths}


@contextmanager
def registry_auth_dir(
    credentials: dict[str, tuple[str, str]],
    *,
    scan_id: uuid.UUID | str,
) -> Iterator[Path | None]:
    """Yield a directory holding ``config.json``, or None when there is nothing.

    The directory is per scan, so two scans running at once never share one and
    one scan's credentials are never visible in another's ``DOCKER_CONFIG``.
    Mode 0700 on the directory and 0600 on the file keep it off OTHER USERS on
    the same host.

    What that does not buy, stated plainly because the mode bits invite the
    stronger reading: worker processes all run as the same user, so a scan's
    Trivy can read another concurrent scan's file. The separation above is by
    ``DOCKER_CONFIG`` and not by the filesystem. Reaching it takes arbitrary
    file read or code execution inside Trivy, which is the same class of bug
    this module keeps credentials out of the environment for, so it is a
    residual risk rather than an open door. Closing it needs either a separate
    uid per scan or a container-scan concurrency of 1; both cost throughput and
    neither is decided here.

    Yields None for an empty credential set so the caller passes no
    ``DOCKER_CONFIG`` at all rather than an empty one.
    """
    if not credentials:
        yield None
        return

    root = _temp_root()
    _assert_outside_workspace(root)

    # mkdtemp creates with 0700 already; set it explicitly so the guarantee does
    # not depend on the umask of whoever started the worker.
    path = Path(tempfile.mkdtemp(prefix=f"{_DIR_PREFIX}{scan_id}-", dir=root))
    try:
        path.chmod(stat.S_IRWXU)
        _assert_outside_workspace(path)

        config_path = path / "config.json"
        # Open with 0600 from the start rather than writing then chmod'ing:
        # between those two calls the file is readable by anyone.
        fd = os.open(
            config_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            stat.S_IRUSR | stat.S_IWUSR,
        )
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(build_docker_config(credentials), handle)

        log.info(
            "registry_auth_dir_created",
            scan_id=str(scan_id),
            # The HOSTS are operational information an operator needs when a
            # pull fails. The credentials themselves are never logged.
            registries=sorted(credentials),
        )
        yield path
    finally:
        # `finally`, not a success path: an exception from the Trivy call must
        # not leave a credential file behind.
        shutil.rmtree(path, ignore_errors=True)


def sweep_stale_auth_dirs(*, now: float | None = None) -> int:
    """Delete credential dirs a previous run could not clean up. Returns count.

    A SIGKILL (the hard time limit, an OOM kill, a container stop) skips the
    ``finally`` above, so a directory can outlive its scan. Anything older than
    the hard scan time limit cannot belong to a live scan, which is the same
    reasoning ``tasks.stale_scan_reaper`` uses for a running row.

    Never raises: a sweep failure must not stop the scan that called it. The
    worst case is a file that survives one more cycle.
    """
    # The margin is not cosmetic. The hard limit is read at call time (rule
    # #11), so an operator who lowers it puts already-running scans below the
    # cutoff at once and the next scan's sweep deletes a live scan's
    # credentials out from under it. An hour past the limit cannot belong to a
    # task the worker still has, and sweeping late only costs one more cycle.
    _SWEEP_MARGIN_SECONDS = 3600
    cutoff = (
        (now if now is not None else time.time())
        - scan_hard_time_limit_seconds()
        - _SWEEP_MARGIN_SECONDS
    )
    removed = 0
    try:
        root = _temp_root()
        if not root.is_dir():
            return 0
        for entry in root.iterdir():
            if not entry.name.startswith(_DIR_PREFIX) or not entry.is_dir():
                continue
            try:
                if entry.stat().st_mtime >= cutoff:
                    continue
            except OSError:
                continue
            shutil.rmtree(entry, ignore_errors=True)
            removed += 1
    except Exception as exc:  # noqa: BLE001 - a sweep must never fail a scan
        log.warning("registry_auth_sweep_failed", error=str(exc)[:200])
        return removed
    if removed:
        log.info("registry_auth_swept", removed=removed)
    return removed


__all__ = [
    "RegistryAuthLocationError",
    "build_docker_config",
    "registry_auth_dir",
    "sweep_stale_auth_dirs",
]
