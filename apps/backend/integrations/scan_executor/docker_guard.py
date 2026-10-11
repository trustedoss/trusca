# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Keep a scanned project's code and the Docker API on different workers.

The in-process pipeline runs code from the scanned repository: build
preparation (``bundle lock``, Gradle, ``dotnet restore``, ``npm install``
scripts), cdxgen's own resolvers, and govulncheck's module loading. If that
code can reach a Docker API, it can create a privileged container and become
root on the host. The Docker API is meant for the BomLens engine worker only
(compose service ``worker-engine``), which never runs that code. Both
directions are enforced (security review on #522, H1 and N-L1):

* :func:`refuse_build_with_docker_access` - build preparation, cdxgen and
  govulncheck refuse to start where the API is reachable.
* :func:`refuse_task_with_docker_access` - on a worker that can reach the API,
  every Celery task but the engine's own is refused (``tasks.celery_app``
  installs it as the task base class).

A worker counts as reaching the Docker API when ``DOCKER_HOST`` is set (any
scheme, ``unix://`` included), when a Docker socket exists at a standard path
(a raw socket mount, as ``SCAN_EXECUTOR=local_docker`` used to need), or when
the proxy's service name (``docker-socket-proxy``) resolves.
"""

from __future__ import annotations

import os
import socket
import stat
from collections.abc import Iterable
from typing import Final

#: The compose service name of the BomLens engine's Docker API proxy.
PROXY_SERVICE_NAME: Final = "docker-socket-proxy"

#: Where a Docker socket is mounted into a container. A module attribute so
#: the test suite can point it away from a CI runner's own socket.
DOCKER_SOCKET_PATHS: tuple[str, ...] = ("/var/run/docker.sock", "/run/docker.sock")


class DockerApiReachable(RuntimeError):
    """The worker can reach the Docker API, so it must not run a scanned build."""


def _socket_at(paths: Iterable[str]) -> str | None:
    for path in paths:
        try:
            mode = os.stat(path).st_mode
        except OSError:
            continue
        if stat.S_ISSOCK(mode):
            return path
    return None


def docker_api_reachable() -> str | None:
    """Why this worker can reach the Docker API, or ``None`` when it cannot."""
    if os.getenv("DOCKER_HOST", "").strip():
        return "DOCKER_HOST is set"
    found = _socket_at(DOCKER_SOCKET_PATHS)
    if found is not None:
        return f"a Docker socket is mounted at {found}"
    try:
        socket.getaddrinfo(PROXY_SERVICE_NAME, 2375)
    except OSError:
        return None
    return f"the Docker API proxy ({PROXY_SERVICE_NAME}) is reachable by name"


def refuse_build_with_docker_access(what: str) -> None:
    """Raise :class:`DockerApiReachable` when the Docker API is reachable."""
    reason = docker_api_reachable()
    if reason is not None:
        raise DockerApiReachable(
            f"refusing to run {what} on a worker that can reach the Docker API ({reason}); "
            "in-process scans belong on worker-scan, which has no Docker access"
        )


def refuse_task_with_docker_access(task_name: str, allowed: Iterable[str]) -> None:
    """Raise unless ``task_name`` is allowed, when the Docker API is reachable."""
    if task_name in set(allowed):
        return
    reason = docker_api_reachable()
    if reason is not None:
        raise DockerApiReachable(
            f"refusing task {task_name} on a worker that can reach the Docker API ({reason}); "
            "this worker runs the BomLens engine's tasks only"
        )


__all__ = [
    "DOCKER_SOCKET_PATHS",
    "PROXY_SERVICE_NAME",
    "DockerApiReachable",
    "docker_api_reachable",
    "refuse_build_with_docker_access",
    "refuse_task_with_docker_access",
]
