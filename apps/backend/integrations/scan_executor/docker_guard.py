# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Refuse to run a scanned project's build where the Docker API is reachable.

The in-process pipeline runs code from the scanned repository: build
preparation (``bundle lock``, Gradle, ``dotnet restore``, ``npm install``
scripts), cdxgen's own resolvers, and govulncheck's module loading. If that
code can reach a Docker API, it can create a privileged container and become
root on the host. The Docker API proxy of the BomLens engine is meant for the
engine worker only (compose service ``worker-engine``), which never runs that
code; this guard makes the other side hold too (security review on #522, H1).

A worker counts as reaching the Docker API when ``DOCKER_HOST`` is set or the
proxy's service name (``docker-socket-proxy``) resolves. Either way the build
is refused before it starts, with a message that says why. A raw socket mount
(``docker-compose.dev.yml``'s ``SCAN_EXECUTOR=local_docker`` development
setup) is not detected here; that file is not for untrusted source.
"""

from __future__ import annotations

import os
import socket
from typing import Final

#: The compose service name of the BomLens engine's Docker API proxy.
PROXY_SERVICE_NAME: Final = "docker-socket-proxy"


class DockerApiReachable(RuntimeError):
    """The worker can reach the Docker API, so it must not run a scanned build."""


def docker_api_reachable() -> str | None:
    """Why this worker can reach the Docker API, or ``None`` when it cannot."""
    if os.getenv("DOCKER_HOST", "").strip():
        return "DOCKER_HOST is set"
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


__all__ = [
    "PROXY_SERVICE_NAME",
    "DockerApiReachable",
    "docker_api_reachable",
    "refuse_build_with_docker_access",
]
