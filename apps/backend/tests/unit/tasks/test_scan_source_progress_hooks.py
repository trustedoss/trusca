"""
Unit tests for the four publish_progress hook points in the scan pipeline.

The integration tests (which require Postgres) cover the full pipeline
end-to-end. These unit tests use lightweight mocks to cover the publish
hooks specifically, so a regression in the order ("commit then publish")
or in the percent value passed to the publisher is caught without a DB.

The hook implementations were extracted to ``tasks._scan_pipeline`` (public
names ``set_stage`` / ``mark_succeeded`` / ``mark_failed`` /
``record_terminal_failure``) so an SBOM-ingest task can reuse them. The
``scan_source`` privates (``_set_stage`` etc.) remain thin aliases / wrappers
over the shared implementations, so the tests still exercise them through the
``scan_source`` seam while monkeypatching the dependencies on the module that
actually owns the implementation (``tasks._scan_pipeline``).

Pinned hook points:
  - ``_set_stage``       — emits step=<stage>, percent=_STAGE_PROGRESS[stage]
  - ``_mark_succeeded``  — emits step="succeeded", percent=100
  - ``_mark_failed``     — emits step="failed", percent=<last known>
  - ``_record_terminal_failure`` — same as _mark_failed (delegates to it)
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from typing import Any

import pytest


class _FakeScan:
    """Minimal Scan stand-in for the in-session writers."""

    def __init__(self, *, progress_percent: int = 0) -> None:
        self.id = uuid.uuid4()
        self.project_id = uuid.uuid4()
        self.status: str = "queued"
        self.error_message: str | None = None
        self.completed_at: Any = None
        self.current_step: str = "bootstrap"
        self.progress_percent: int = progress_percent
        self.started_at: Any = None
        # scan-retention: _mark_succeeded calls supersede_prior_ref_scans, which
        # short-circuits (no session.execute) when ref is None — so a ref-less
        # fake never touches the fake session's missing execute().
        self.ref: str | None = None


class _FakeSession:
    """Records commits and `get` lookups; ignores SQLAlchemy specifics."""

    def __init__(self, scan: _FakeScan) -> None:
        self._scan = scan
        self.commits = 0

    def get(self, _model: Any, _ident: Any) -> _FakeScan:
        return self._scan

    def commit(self) -> None:
        self.commits += 1


@pytest.fixture
def patch_session(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Replace ``sync_session_scope`` with a context manager yielding a fake."""
    scan = _FakeScan()

    @contextmanager
    def _scope() -> Any:
        yield _FakeSession(scan)

    monkeypatch.setattr("tasks._scan_pipeline.sync_session_scope", _scope)
    return scan


@pytest.fixture
def captured_publishes(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture every ``publish_progress`` call made from scan_source."""
    captured: list[dict[str, Any]] = []

    def _capture(scan_id: Any, *, step: str, percent: int) -> None:
        captured.append({"scan_id": scan_id, "step": step, "percent": percent})

    monkeypatch.setattr("tasks._scan_pipeline.publish_progress", _capture)
    return captured


# ---------------------------------------------------------------------------
# _set_stage
# ---------------------------------------------------------------------------


def test_set_stage_publishes_with_known_stage_percent(
    patch_session: _FakeScan, captured_publishes: list[dict[str, Any]]
) -> None:
    """Each known stage publishes its mapped percent."""
    from tasks.scan_source import _STAGE_PROGRESS, _set_stage

    scan_uuid = uuid.uuid4()
    _set_stage(scan_uuid, "cdxgen")
    assert len(captured_publishes) == 1
    assert captured_publishes[0]["step"] == "cdxgen"
    assert captured_publishes[0]["percent"] == _STAGE_PROGRESS["cdxgen"]


def test_set_stage_falls_back_to_existing_percent_for_unknown_stage(
    patch_session: _FakeScan, captured_publishes: list[dict[str, Any]]
) -> None:
    """An unmapped stage keeps the row's prior percent."""
    from tasks.scan_source import _set_stage

    patch_session.progress_percent = 42
    _set_stage(uuid.uuid4(), "wat")
    assert captured_publishes[-1]["step"] == "wat"
    assert captured_publishes[-1]["percent"] == 42


# ---------------------------------------------------------------------------
# _mark_succeeded
# ---------------------------------------------------------------------------


def test_mark_succeeded_publishes_terminal_event(
    patch_session: _FakeScan, captured_publishes: list[dict[str, Any]]
) -> None:
    """Success → step='succeeded' / percent=100."""
    from tasks.scan_source import _mark_succeeded

    _mark_succeeded(uuid.uuid4())
    assert captured_publishes[-1]["step"] == "succeeded"
    assert captured_publishes[-1]["percent"] == 100


# ---------------------------------------------------------------------------
# _mark_failed (in-session caller)
# ---------------------------------------------------------------------------


def test_mark_failed_publishes_with_last_known_percent(
    patch_session: _FakeScan, captured_publishes: list[dict[str, Any]]
) -> None:
    """Failure mid-pipeline carries the snapshot of progress_percent."""
    from tasks.scan_source import _mark_failed

    patch_session.progress_percent = 50  # we'd failed during scancode
    fake_session = _FakeSession(patch_session)
    _mark_failed(fake_session, patch_session, "scancode exited 1")  # type: ignore[arg-type]
    assert captured_publishes[-1]["step"] == "failed"
    assert captured_publishes[-1]["percent"] == 50


def test_mark_failed_with_no_progress_publishes_zero(
    patch_session: _FakeScan, captured_publishes: list[dict[str, Any]]
) -> None:
    """A scan that never started (progress_percent=None) publishes 0, not None."""
    from tasks.scan_source import _mark_failed

    patch_session.progress_percent = 0  # equivalent to "never set" via `or 0`
    fake_session = _FakeSession(patch_session)
    _mark_failed(fake_session, patch_session, "boom")  # type: ignore[arg-type]
    assert captured_publishes[-1]["percent"] == 0


# ---------------------------------------------------------------------------
# _record_terminal_failure (out-of-session caller)
# ---------------------------------------------------------------------------


def test_record_terminal_failure_emits_failed_event(
    patch_session: _FakeScan, captured_publishes: list[dict[str, Any]]
) -> None:
    """The exception-handler path goes through the same publish_progress hook."""
    from tasks.scan_source import _record_terminal_failure

    patch_session.progress_percent = 25
    _record_terminal_failure(uuid.uuid4(), "unexpected: rsa key bad")
    assert captured_publishes[-1]["step"] == "failed"
    assert captured_publishes[-1]["percent"] == 25
