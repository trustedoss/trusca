# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Start a real Celery worker for a test, as the ``celery`` command would.

A worker that can reach the Docker API runs the BomLens engine's tasks only
(``integrations.scan_executor.docker_guard``). A CI runner has its own Docker
socket at /var/run/docker.sock, and a test's worker subprocess would see it and
refuse the tasks the test sends. The in-process tests hide the socket with a
fixture (``tests/conftest.py``); a subprocess needs the same done inside it,
before the worker starts, so this runs ``celery`` through ``python -c``.
The prefork children inherit the setting.
"""

from __future__ import annotations

import sys

_BOOT = (
    "import sys;"
    "from integrations.scan_executor import docker_guard;"
    "docker_guard.DOCKER_SOCKET_PATHS = ();"
    "from celery.__main__ import main;"
    "sys.argv = ['celery', *sys.argv[1:]];"
    "sys.exit(main())"
)


def celery_command(*args: str) -> list[str]:
    """``celery <args>`` as a subprocess argv, with the host's socket hidden."""
    return [sys.executable, "-c", _BOOT, *args]


__all__ = ["celery_command"]
