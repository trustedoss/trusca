# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""``_record_scancode_skipped``: a skipped scancode stage reaches the scan record.

The stage is best-effort, so a skip used to leave one worker log line and a scan
that looked complete. The record is what the overview reads to say that licences
in the project's own source were not detected.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

import tasks.scan_source as mod
from integrations import scancode as scancode_adapter
from services.scan_outcome import SCANCODE_SKIP_KEY, SCANCODE_SKIP_REASONS


class _FakeScan:
    def __init__(self) -> None:
        self.scan_metadata: dict[str, Any] | None = {"detected_env": "node"}


class _FakeSession:
    def __init__(self, scan: _FakeScan | None) -> None:
        self._scan = scan
        self.committed = 0

    def get(self, _model: Any, _pk: Any) -> _FakeScan | None:
        return self._scan

    def commit(self) -> None:
        self.committed += 1

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None


@pytest.fixture
def scan_session(monkeypatch: pytest.MonkeyPatch) -> _FakeSession:
    session = _FakeSession(_FakeScan())
    monkeypatch.setattr(mod, "sync_session_scope", lambda: session)
    return session


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (scancode_adapter.ScancodeNotInstalled("x"), "not_installed"),
        (scancode_adapter.ScancodeFailed("x"), "failed"),
        (scancode_adapter.ScancodeTimeout("x"), "timeout"),
        (scancode_adapter.ScancodeTooLarge("x"), "too_large"),
        # Any other adapter error is still "did not run": never dropped.
        (scancode_adapter.ScancodeError("x"), "failed"),
    ],
)
def test_each_skip_kind_is_recorded_and_committed(
    scan_session: _FakeSession, error: Exception, reason: str
) -> None:
    mod._record_scancode_skipped(uuid.uuid4(), error)

    assert scan_session._scan is not None
    assert scan_session._scan.scan_metadata is not None
    assert scan_session._scan.scan_metadata[SCANCODE_SKIP_KEY] == reason
    assert reason in SCANCODE_SKIP_REASONS
    # Merged, not replaced.
    assert scan_session._scan.scan_metadata["detected_env"] == "node"
    assert scan_session.committed == 1


def test_a_stage_turned_off_on_purpose_is_not_recorded(scan_session: _FakeSession) -> None:
    mod._record_scancode_skipped(uuid.uuid4(), scancode_adapter.ScancodeDisabled("off"))

    assert scan_session._scan is not None
    assert scan_session._scan.scan_metadata == {"detected_env": "node"}
    assert scan_session.committed == 0


def test_a_persistence_failure_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(mod, "sync_session_scope", boom)
    mod._record_scancode_skipped(uuid.uuid4(), scancode_adapter.ScancodeFailed("x"))


def test_the_pipeline_stage_calls_the_recorder() -> None:
    """The recorder is only useful if the ``except`` in the stage calls it."""
    import inspect

    source = inspect.getsource(mod)
    handler = source.split("except scancode_adapter.ScancodeError as exc:", 1)[1].split(
        "# Persist the SBOM components", 1
    )[0]
    assert "_record_scancode_skipped(scan_uuid, exc)" in handler
