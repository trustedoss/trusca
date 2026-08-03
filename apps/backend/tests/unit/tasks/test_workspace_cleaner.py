"""
Unit tests for the workspace orphan cleaner (PR-A1) — no DB required.

The cleaner reclaims per-scan workspace directories left behind by scans whose
own ``finally: rmtree`` did not run (cancel SIGTERM, hard-limit SIGKILL, worker
crash). We pin its safety policy without Postgres by stubbing the single DB
round-trip (``_active_scan_ids``) and exercising the directory walk against a
real tmp filesystem.

Safety invariants under test:
  - Only UUID-named directories are considered (non-UUID dirs are never deleted).
  - Directories younger than the grace period are skipped.
  - Aged directories whose scan is still queued / running are NOT deleted.
  - Aged directories whose scan is terminal OR whose scan row is gone ARE
    reclaimed.
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Any

import pytest


def _age_dir(path: Path, seconds: int) -> None:
    """Backdate a directory's mtime so it looks ``seconds`` old."""
    old = time.time() - seconds
    os.utime(path, (old, old))


def test_cleaner_skips_non_uuid_dirs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import tasks.workspace_cleaner as mod

    monkeypatch.setattr(mod, "workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr(mod, "workspace_orphan_max_age_seconds", lambda: 0)

    keep = tmp_path / "restore-staging"  # not a scan workspace
    keep.mkdir()
    _age_dir(keep, 10_000)

    # No aged UUID dirs → DB query should never even be consulted.
    monkeypatch.setattr(
        mod, "_scan_id_states", lambda *a, **k: pytest.fail("should not query")
    )

    result = mod.workspace_cleaner_task.run()
    assert result["reclaimed"] == []
    assert keep.exists()


def test_cleaner_skips_young_dirs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import tasks.workspace_cleaner as mod

    monkeypatch.setattr(mod, "workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr(mod, "workspace_orphan_max_age_seconds", lambda: 900)

    fresh = tmp_path / str(uuid.uuid4())
    fresh.mkdir()  # just created → inside the grace window

    monkeypatch.setattr(
        mod, "_scan_id_states", lambda *a, **k: pytest.fail("should not query")
    )

    result = mod.workspace_cleaner_task.run()
    assert result["reclaimed"] == []
    assert fresh.exists()


def test_cleaner_keeps_active_scan_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An aged dir whose scan is still running must NOT be reclaimed."""
    import tasks.workspace_cleaner as mod

    monkeypatch.setattr(mod, "workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr(mod, "workspace_orphan_max_age_seconds", lambda: 900)

    running_uuid = uuid.uuid4()
    running_dir = tmp_path / str(running_uuid)
    running_dir.mkdir()
    _age_dir(running_dir, 5_000)

    from contextlib import contextmanager

    @contextmanager
    def fake_scope() -> Any:
        yield object()

    monkeypatch.setattr(mod, "sync_session_scope", fake_scope)
    # Report the scan as still active (queued/running): it has a row and is
    # non-terminal, so it is both "active" and "present".
    monkeypatch.setattr(
        mod, "_scan_id_states", lambda session, ids: ({running_uuid}, {running_uuid})
    )

    result = mod.workspace_cleaner_task.run()
    assert result["reclaimed"] == []
    assert running_dir.exists()
    assert result["skipped"] >= 1


def test_cleaner_reclaims_terminal_and_missing_scan_workspaces(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Aged dirs for terminal scans AND deleted scans are both reclaimed."""
    import tasks.workspace_cleaner as mod

    monkeypatch.setattr(mod, "workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr(mod, "workspace_orphan_max_age_seconds", lambda: 900)

    terminal_uuid = uuid.uuid4()
    missing_uuid = uuid.uuid4()
    for u in (terminal_uuid, missing_uuid):
        d = tmp_path / str(u)
        d.mkdir()
        (d / "leftover").write_text("x")
        _age_dir(d, 5_000)

    from contextlib import contextmanager

    @contextmanager
    def fake_scope() -> Any:
        yield object()

    monkeypatch.setattr(mod, "sync_session_scope", fake_scope)
    # Neither id is "active". The terminal scan has a row (present); the
    # missing scan has no row (not present). Both dirs are aged 5000s, which is
    # past 2*max_age (1800s), so the longer no-row grace (Low #4) still permits
    # reclaiming the missing one here.
    monkeypatch.setattr(
        mod, "_scan_id_states", lambda session, ids: (set(), {terminal_uuid})
    )

    result = mod.workspace_cleaner_task.run()
    reclaimed = set(result["reclaimed"])
    assert reclaimed == {str(terminal_uuid), str(missing_uuid)}
    assert not (tmp_path / str(terminal_uuid)).exists()
    assert not (tmp_path / str(missing_uuid)).exists()


def test_cleaner_missing_root_is_noop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import tasks.workspace_cleaner as mod

    monkeypatch.setattr(mod, "workspace_root", lambda: str(tmp_path / "does-not-exist"))
    monkeypatch.setattr(mod, "workspace_orphan_max_age_seconds", lambda: 0)

    result = mod.workspace_cleaner_task.run()
    assert result == {"scanned": 0, "reclaimed": [], "skipped": 0}


def test_scan_id_states_classifies_active_present_and_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The DB helper splits ids into active (non-terminal) and present (has row)."""
    import tasks.workspace_cleaner as mod

    running = uuid.uuid4()
    succeeded = uuid.uuid4()
    cancelled = uuid.uuid4()
    absent = uuid.uuid4()  # no row returned for this one

    class _Result:
        def all(self) -> list[tuple[uuid.UUID, str]]:
            return [
                (running, "running"),
                (succeeded, "succeeded"),
                (cancelled, "cancelled"),
            ]

    class _Session:
        def execute(self, *a: Any, **k: Any) -> _Result:
            return _Result()

    active, present = mod._scan_id_states(
        _Session(),  # type: ignore[arg-type]  # duck-typed fake Session
        [running, succeeded, cancelled, absent],
    )
    # Only the running scan is non-terminal.
    assert active == {running}
    # Three ids have a row; ``absent`` does not.
    assert present == {running, succeeded, cancelled}
    assert absent not in present


def test_scan_id_states_empty_input_short_circuits() -> None:
    import tasks.workspace_cleaner as mod

    class _Session:
        def execute(self, *a: Any, **k: Any) -> Any:
            raise AssertionError("should not query for an empty id list")

    assert mod._scan_id_states(_Session(), []) == (set(), set())  # type: ignore[arg-type]


def test_cleaner_skips_young_no_row_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Low #4: an aged-but-not-old-enough dir with NO owning row is skipped.

    The dir is older than ``max_age`` (so it clears the terminal-row bar) but
    younger than ``2*max_age`` (the no-row grace). With no owning scan row it
    must NOT be reclaimed — it could be a scan whose INSERT is still in flight.
    """
    import tasks.workspace_cleaner as mod

    monkeypatch.setattr(mod, "workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr(mod, "workspace_orphan_max_age_seconds", lambda: 900)

    no_row_uuid = uuid.uuid4()
    no_row_dir = tmp_path / str(no_row_uuid)
    no_row_dir.mkdir()
    (no_row_dir / "leftover").write_text("x")
    # Aged past max_age (900) but inside the 2*max_age (1800) no-row grace.
    _age_dir(no_row_dir, 1_200)

    from contextlib import contextmanager

    @contextmanager
    def fake_scope() -> Any:
        yield object()

    # No row at all for this id: not active, not present.
    monkeypatch.setattr(mod, "sync_session_scope", fake_scope)
    monkeypatch.setattr(mod, "_scan_id_states", lambda session, ids: (set(), set()))

    result = mod.workspace_cleaner_task.run()
    assert result["reclaimed"] == []
    assert no_row_dir.exists()
    assert result["skipped"] >= 1


def test_cleaner_reclaims_old_no_row_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Low #4: a no-row dir aged past the longer 2*max_age grace IS reclaimed."""
    import tasks.workspace_cleaner as mod

    monkeypatch.setattr(mod, "workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr(mod, "workspace_orphan_max_age_seconds", lambda: 900)

    no_row_uuid = uuid.uuid4()
    no_row_dir = tmp_path / str(no_row_uuid)
    no_row_dir.mkdir()
    (no_row_dir / "leftover").write_text("x")
    _age_dir(no_row_dir, 5_000)  # well past 2*max_age (1800)

    from contextlib import contextmanager

    @contextmanager
    def fake_scope() -> Any:
        yield object()

    monkeypatch.setattr(mod, "sync_session_scope", fake_scope)
    monkeypatch.setattr(mod, "_scan_id_states", lambda session, ids: (set(), set()))

    result = mod.workspace_cleaner_task.run()
    assert result["reclaimed"] == [str(no_row_uuid)]
    assert not no_row_dir.exists()
