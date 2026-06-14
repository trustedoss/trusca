"""Resolve how a sidecar container reaches the scan workspace.

The worker's workspace (``/tmp/trustedoss`` dev, ``/workspace`` prod) is a *named
Docker volume*, not a host bind mount. The Docker daemon therefore cannot resolve
the worker's in-container path — ``docker run -v /tmp/trustedoss/<scan>:/app`` would
create an empty directory. Two strategies make the same files visible inside the
sidecar at the *same path* so ``source_dir`` needs no translation:

- ``volumes_from`` (default): ``--volumes-from <worker>`` re-mounts every volume the
  worker has, including the workspace, at the identical mount point. Simplest and
  path-identical. Over-shares the worker's other volumes (cosign keys, caches) —
  increment 6 narrows this to a dedicated workspace mount.
- ``named``: ``-v <SCAN_WORKSPACE_VOLUME>:<mount>`` mounts only the workspace volume.
  Requires the operator to name the volume; the mount point still matches the
  worker so paths line up.

All knobs resolve at call time (CLAUDE.md core rule #11).
"""

from __future__ import annotations

import os
import socket


class DockerVolumeError(RuntimeError):
    """The sidecar's view of the workspace volume could not be resolved."""


def _strategy() -> str:
    return os.getenv("SCAN_DOCKER_VOLUME_STRATEGY", "volumes_from").lower()


def worker_container_ref() -> str:
    """Identifier the Docker daemon uses to reference the worker container.

    Defaults to the worker's hostname, which Docker sets to the short container
    ID unless overridden. ``SCAN_WORKER_CONTAINER`` overrides it for deployments
    that pin ``container_name`` / ``hostname``.
    """
    ref = os.getenv("SCAN_WORKER_CONTAINER")
    if ref:
        return ref
    host = socket.gethostname().strip()
    if not host:
        raise DockerVolumeError(
            "cannot determine worker container id for --volumes-from; "
            "set SCAN_WORKER_CONTAINER",
        )
    return host


def _workspace_volume() -> str:
    vol = os.getenv("SCAN_WORKSPACE_VOLUME", "").strip()
    if not vol:
        raise DockerVolumeError(
            "SCAN_DOCKER_VOLUME_STRATEGY=named requires SCAN_WORKSPACE_VOLUME "
            "(the workspace volume name)",
        )
    return vol


def _workspace_mount() -> str:
    # Where the worker mounts the workspace volume; the sidecar must use the same
    # mount point so source_dir resolves to the identical absolute path. Dev mounts
    # at /tmp/trustedoss; prod sets this to /workspace.
    return os.getenv("SCAN_WORKSPACE_MOUNT", "/tmp/trustedoss")  # noqa: S108 — volume mount point, not a tempfile


def volume_run_args() -> list[str]:
    """Return the ``docker run`` arguments that expose the workspace to a sidecar."""
    strategy = _strategy()
    if strategy == "volumes_from":
        return ["--volumes-from", worker_container_ref()]
    if strategy == "named":
        return ["-v", f"{_workspace_volume()}:{_workspace_mount()}"]
    raise DockerVolumeError(
        f"unknown SCAN_DOCKER_VOLUME_STRATEGY={strategy!r} "
        "(expected 'volumes_from' or 'named')",
    )


__all__ = ["DockerVolumeError", "volume_run_args", "worker_container_ref"]
