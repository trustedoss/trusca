"""Resolve how a sidecar container reaches the scan workspace.

The worker's workspace (``/tmp/trustedoss`` dev, ``/workspace`` prod) is a *named
Docker volume*, not a host bind mount. The Docker daemon therefore cannot resolve
the worker's in-container path — ``docker run -v /tmp/trustedoss/<scan>:/app`` would
create an empty directory. Two strategies make the same files visible inside the
sidecar at the *same path* so ``source_dir`` needs no translation:

- ``named`` (DEFAULT, secure): ``-v <SCAN_WORKSPACE_VOLUME>:<mount>`` mounts ONLY the
  workspace volume. The sidecar (running an untrusted build) sees the scan tree and
  nothing else. Requires the operator to set ``SCAN_WORKSPACE_VOLUME`` once; if unset
  this raises and the executor falls back to in-process — secure by default.
- ``volumes_from`` (opt-in, DANGEROUS): ``--volumes-from <worker>`` re-mounts EVERY
  volume the worker has — including the cosign signing key, Trivy cache, and backups —
  into the untrusted sidecar. A malicious build could read the cosign private key and
  forge release signatures (security review 2026-06-14, Critical). Gated behind an
  explicit ``SCAN_VOLUMES_FROM_ACK=1`` acknowledgement + a startup warning, and
  ``SCAN_WORKER_CONTAINER`` must be set explicitly (no hostname guess).

All knobs resolve at call time (CLAUDE.md core rule #11).
"""

from __future__ import annotations

import os

import structlog

log = structlog.get_logger("integrations.scan_executor.docker_volume")


class DockerVolumeError(RuntimeError):
    """The sidecar's view of the workspace volume could not be resolved."""


def _strategy() -> str:
    # Default `named` (workspace-only) — `volumes_from` over-shares the worker's
    # secret volumes (cosign key) into the untrusted sidecar (security review).
    return os.getenv("SCAN_DOCKER_VOLUME_STRATEGY", "named").lower()


def worker_container_ref() -> str:
    """Identifier the Docker daemon uses to reference the worker container.

    For ``volumes_from`` this MUST be set explicitly — guessing the hostname can
    resolve to the wrong container and silently over-share its volumes into the
    untrusted sidecar, so an unset ``SCAN_WORKER_CONTAINER`` raises rather than
    falling back to ``gethostname()``.
    """
    ref = os.getenv("SCAN_WORKER_CONTAINER", "").strip()
    if not ref:
        raise DockerVolumeError(
            "SCAN_DOCKER_VOLUME_STRATEGY=volumes_from requires SCAN_WORKER_CONTAINER "
            "to be set explicitly (a wrong hostname guess would over-share volumes "
            "into the untrusted sidecar)",
        )
    return ref


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


def _volumes_from_acked() -> bool:
    return os.getenv("SCAN_VOLUMES_FROM_ACK", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def volume_run_args() -> list[str]:
    """Return the ``docker run`` arguments that expose the workspace to a sidecar.

    ``named`` (default) mounts only the workspace volume. ``volumes_from`` re-mounts
    every worker volume — including the cosign signing key — into the untrusted
    sidecar, so it is refused unless the operator explicitly acknowledges the
    over-share via ``SCAN_VOLUMES_FROM_ACK=1`` (and a startup WARNING names the risk).
    """
    strategy = _strategy()
    if strategy == "named":
        return ["-v", f"{_workspace_volume()}:{_workspace_mount()}"]
    if strategy == "volumes_from":
        if not _volumes_from_acked():
            raise DockerVolumeError(
                "SCAN_DOCKER_VOLUME_STRATEGY=volumes_from re-mounts ALL worker "
                "volumes (incl. the cosign signing key) into the untrusted build "
                "sidecar. Refused. Use the default 'named' strategy, or set "
                "SCAN_VOLUMES_FROM_ACK=1 to accept the over-share explicitly.",
            )
        ref = worker_container_ref()
        log.warning(
            "scan_sidecar_volumes_from_over_share",
            worker=ref,
            detail=(
                "sidecar receives ALL worker volumes (cosign key / trivy cache / "
                "backups); prefer SCAN_DOCKER_VOLUME_STRATEGY=named"
            ),
        )
        return ["--volumes-from", ref]
    raise DockerVolumeError(
        f"unknown SCAN_DOCKER_VOLUME_STRATEGY={strategy!r} "
        "(expected 'named' or 'volumes_from')",
    )


__all__ = ["DockerVolumeError", "volume_run_args", "worker_container_ref"]
