# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Every container that logs has a bounded log ring.

Docker's json-file driver keeps every line forever unless told otherwise, and
the failure that produces is not a log problem: the disk fills, and a scan
appliance with no room left stops scanning. The bound is therefore part of the
deployment rather than something each operator discovers.

A service added later inherits nothing. It gets the unbounded default, and
nothing about that is visible until the disk is gone, which is why this is a
test rather than a note in a review checklist.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]

_COMPOSE_FILES = ("docker-compose.yml", "docker-compose.dev.yml")


def _services(filename: str) -> dict[str, Any]:
    document = yaml.safe_load((REPO_ROOT / filename).read_text(encoding="utf-8"))
    return document.get("services") or {}


@pytest.mark.parametrize("filename", _COMPOSE_FILES)
def test_every_service_bounds_its_log_ring(filename: str) -> None:
    """No service is left on the unbounded default.

    Asserted over every service the file declares rather than a fixed list, so
    a service added tomorrow fails here instead of quietly writing without a
    limit.
    """
    unbounded = sorted(
        name
        for name, service in _services(filename).items()
        if not (service or {}).get("logging")
    )

    assert unbounded == [], (
        f"{filename}: {unbounded} have no logging block, so Docker keeps their "
        f"output forever. Add `logging: *logging`."
    )


@pytest.mark.parametrize("filename", _COMPOSE_FILES)
def test_the_bound_is_a_size_and_a_count(filename: str) -> None:
    """Both options are needed; either alone leaves the ring unbounded.

    ``max-size`` without ``max-file`` rotates and keeps every rotated file, and
    ``max-file`` without ``max-size`` never rotates in the first place, so the
    count never applies. The pair is the bound.
    """
    for name, service in _services(filename).items():
        options = (service or {}).get("logging", {}).get("options", {})
        assert "max-size" in options, f"{filename}: {name} sets no max-size"
        assert "max-file" in options, f"{filename}: {name} sets no max-file"


@pytest.mark.parametrize("filename", _COMPOSE_FILES)
def test_the_bound_stays_tunable_without_editing_the_file(filename: str) -> None:
    """Both values come from the environment.

    An operator raising the ring for a diagnosis should not have to edit a
    shipped file, because the next upgrade would revert it and the change
    would be lost exactly when the diagnosis needed it.
    """
    for name, service in _services(filename).items():
        options = service["logging"]["options"]
        for key in ("max-size", "max-file"):
            assert "${LOG_MAX_" in str(options[key]), (
                f"{filename}: {name} hard-codes {key}; use the LOG_MAX_* "
                f"variables so it can be changed without editing this file"
            )


def test_the_defaults_are_declared_for_operators() -> None:
    """The two variables appear where an operator would look for them."""
    template = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    reference = (
        REPO_ROOT / "docs-site/docs/reference/env-variables.md"
    ).read_text(encoding="utf-8")

    for key in ("LOG_MAX_SIZE", "LOG_MAX_FILE"):
        assert key in template, f".env.example does not mention {key}"
        assert key in reference, f"the reference page does not mention {key}"
