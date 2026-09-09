# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Backups outlive the container that wrote them.

``services.backup_service.backups_root`` resolves the relative default
``"backups"`` against the working directory. In the published images that is
``/app``, so a deployment that does not set ``BACKUPS_ROOT`` writes every
nightly artifact into the container's writable layer. That store is deleted
when the container is recreated, which is exactly what deploying a new image
tag does: the upgrade that most warrants a backup is the one that destroys the
backups. The artifact is a pg_dump plus the scan workspace, so it is sized like
the workspace, not like the database, and it fills the host disk on the way.

docker-compose.dev.yml has mounted a volume here since the non-root switch.
The production file did not, and nothing failed loudly enough to say so, which
is why this is a test and not a comment.

Asserted against the compose files rather than against a running stack: the
defect is a missing line in a YAML file, and that is what has to be caught
before the release, not after an operator loses a backup history.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]

_COMPOSE_FILES = ("docker-compose.yml", "docker-compose.dev.yml")

_INTERPOLATION = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*:-(?P<default>[^}]*)\}$")


def _document(filename: str) -> dict[str, Any]:
    document: dict[str, Any] = yaml.safe_load(
        (REPO_ROOT / filename).read_text(encoding="utf-8")
    )
    return document


def _resolved(value: str) -> str:
    """Return what compose would substitute for ``value`` with nothing in the
    environment.

    ``yaml.safe_load`` hands back the raw ``${NAME:-default}`` text, and the
    dev stack writes the path that way while its mounts spell it out. Comparing
    the two forms literally would report a mismatch that does not exist, so the
    default is taken here. A bare ``${NAME}`` has no default to take and is
    returned unchanged, which fails the comparison it feeds, correctly: a path
    the deployment must supply is not one this file can promise a mount for.
    """
    match = _INTERPOLATION.match(value)
    return match.group("default") if match else value


def _services_touching_backups(filename: str) -> dict[str, dict[str, Any]]:
    """Return the services that read or write the backup store.

    Derived from the file rather than from a fixed list of names, because the
    two stacks do not agree on them: production splits the workers into
    worker-scan / worker-default, dev runs a single celery-worker. A service
    is in scope when it sets BACKUPS_ROOT, which is how each file declares
    that the store is part of that container's job. That also makes the first
    test below meaningful: a service that touches backups without setting the
    variable would not be in this mapping, so it is checked separately against
    the names each file actually declares.
    """
    return {
        name: service
        for name, service in (_document(filename).get("services") or {}).items()
        if ((service or {}).get("environment") or {}).get("BACKUPS_ROOT")
    }


def _mount_targets(service: dict[str, Any]) -> list[str]:
    """Return the container-side path of each of a service's volume entries.

    Handles both the short ``source:target[:mode]`` string form and the long
    mapping form, since a compose file may use either.
    """
    targets: list[str] = []
    for entry in service.get("volumes") or []:
        if isinstance(entry, str):
            parts = entry.split(":")
            if len(parts) >= 2:
                targets.append(parts[1])
        elif isinstance(entry, dict) and entry.get("target"):
            targets.append(str(entry["target"]))
    return targets


def test_production_names_the_services_that_touch_backups() -> None:
    """The production stack sets BACKUPS_ROOT on the writer and the reader.

    Named explicitly, and only for this file, because these are the service
    names the shipped stack uses and the omission this guards against was
    exactly a missing entry: the variable was set nowhere, so nothing in the
    derived checks below had anything to be derived from.
    """
    services = _document("docker-compose.yml").get("services") or {}

    unset = sorted(
        name
        for name in ("backend", "worker-scan", "worker-default")
        if not ((services.get(name) or {}).get("environment") or {}).get("BACKUPS_ROOT")
    )

    assert unset == [], (
        f"docker-compose.yml: {unset} do not set BACKUPS_ROOT, so "
        f'backups_root() falls back to the relative "backups" and resolves '
        f"inside the image at /app/backups."
    )


@pytest.mark.parametrize("filename", _COMPOSE_FILES)
def test_backups_root_is_a_mount_and_not_the_writable_layer(filename: str) -> None:
    """The path BACKUPS_ROOT names is backed by a volume on every service that
    uses it.

    Setting the variable without mounting anything there moves the leak rather
    than fixing it, so the two are checked together and against the same value:
    a mount at some other path would satisfy a weaker assertion while leaving
    the configured directory in the container layer.
    """
    unmounted = []
    for name, service in _services_touching_backups(filename).items():
        configured = _resolved(service["environment"]["BACKUPS_ROOT"])
        if configured not in _mount_targets(service):
            unmounted.append(f"{name} (BACKUPS_ROOT={configured})")

    assert unmounted == [], (
        f"{filename}: {sorted(unmounted)} set BACKUPS_ROOT but mount nothing "
        f"there, so the artifacts still land in the container's writable layer "
        f"and are deleted when the container is recreated."
    )


@pytest.mark.parametrize("filename", _COMPOSE_FILES)
def test_the_writer_and_the_reader_share_one_store(filename: str) -> None:
    """The workers and the backend point at the same volume.

    A per-service volume would let each container write a backup nobody else
    can list, so the admin API would show an empty history while the disk
    filled. The caches are deliberately per-worker; this one is deliberately
    not.
    """
    sources = set()
    for service in _services_touching_backups(filename).values():
        configured = _resolved(service["environment"]["BACKUPS_ROOT"])
        for entry in service.get("volumes") or []:
            if isinstance(entry, str) and entry.split(":")[1:2] == [configured]:
                sources.add(entry.split(":")[0])
            elif isinstance(entry, dict) and str(entry.get("target")) == configured:
                sources.add(str(entry.get("source")))

    assert len(sources) == 1, (
        f"{filename}: the backup mount resolves to {sorted(sources)}. The "
        f"writer and the admin API that lists what it wrote must share one "
        f"store."
    )
