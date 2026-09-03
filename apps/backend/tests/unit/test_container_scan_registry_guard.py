# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Both places the registry allow-list is enforced (ER3).

The check exists twice on purpose and the two are not redundant. The schema
check is what gives a caller an error they can act on instead of a 202 followed
by a failed scan. The worker check is what actually protects the pull, because
a scan row can reach that task without passing through the schema again: a
re-run of a scan queued before the list was tightened does exactly that.

A test that only covered the schema would let someone delete the worker check
believing it duplicated this one. The worker half is covered by
``tests/integration/scan/test_container_registry_allowlist.py``, which drives
the real task: a unit test asserting the helpers are importable was tried here
first and stayed green with the enforcement deleted, so it is deliberately not
in this file.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from schemas.scan import ScanCreate


def _container_scan(image_ref: str) -> ScanCreate:
    return ScanCreate(kind="container", metadata={"image_ref": image_ref})


def test_unrestricted_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Upgrading must not start rejecting scans that worked yesterday."""
    monkeypatch.delenv("CONTAINER_SCAN_ALLOWED_REGISTRIES", raising=False)
    assert _container_scan("anything.example.com/app:1").metadata["image_ref"]


def test_an_allowed_registry_still_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTAINER_SCAN_ALLOWED_REGISTRIES", "ghcr.io,docker.io")
    assert _container_scan("ghcr.io/org/app:1").metadata["image_ref"]
    assert _container_scan("alpine:3.19").metadata["image_ref"]


def test_a_disallowed_registry_is_rejected_at_trigger_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONTAINER_SCAN_ALLOWED_REGISTRIES", "ghcr.io")
    with pytest.raises(ValidationError) as excinfo:
        _container_scan("evil.example.com/app:1")
    message = str(excinfo.value)
    # The operator's fix is naming the registry, so the error must name it and
    # the setting to change.
    assert "evil.example.com" in message
    assert "CONTAINER_SCAN_ALLOWED_REGISTRIES" in message


def test_the_path_bypass_is_rejected_at_trigger_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bypass most likely to be attempted, asserted at the API boundary."""
    monkeypatch.setenv("CONTAINER_SCAN_ALLOWED_REGISTRIES", "ghcr.io")
    with pytest.raises(ValidationError):
        _container_scan("evil.example.com/ghcr.io/app:1")
    with pytest.raises(ValidationError):
        _container_scan("ghcr.io.evil.example.com/app:1")
