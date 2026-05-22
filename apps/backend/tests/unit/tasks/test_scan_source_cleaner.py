"""
Unit tests for ``tasks.scan_source_cleaner`` — G3.1.

The task's value is its *filesystem* retention decision: which preserved
``<scan_id>.tar.gz`` files survive (latest-succeeded-per-project) and which are
swept. We isolate that from DB plumbing by patching the three DB-reading helpers
(``_project_exists`` / ``_latest_scan_id`` / ``_active_scan_ids``) — the same
isolation style ``test_source_archive_cleaner`` uses.

Cases:
  - the project's ``latest_scan_id`` tarball is KEPT; older ones are swept;
  - a tarball referenced by a non-terminal (queued/running) scan is protected
    even if it is not the latest;
  - when ``latest_scan_id`` has no tarball on disk (latest scan failed/running),
    the newest-mtime tarball is kept as a fallback (no data loss);
  - an orphaned project (no longer exists) has every tarball reclaimed;
  - a non-UUID project dir / non-.tar.gz file is left strictly alone;
  - an emptied project dir is removed.
"""

from __future__ import annotations

import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from services.source_preservation_service import (
    scan_source_tarball_path,
    scan_sources_dir_for_project,
)


@pytest.fixture(autouse=True)
def _workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    return tmp_path


def _write_tarball(
    project_id: uuid.UUID, scan_id: uuid.UUID, *, age_seconds: float = 0.0
) -> Path:
    path = scan_source_tarball_path(project_id, scan_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x1f\x8b" + b"\x00" * 64)  # gzip magic + filler
    if age_seconds:
        old = time.time() - age_seconds
        os.utime(path, (old, old))
    return path


def _run(
    monkeypatch: pytest.MonkeyPatch,
    *,
    exists: bool,
    latest: str | None,
    active: set[str],
) -> dict[str, Any]:
    """Invoke the task with the three DB helpers stubbed."""
    from tasks import scan_source_cleaner as mod

    monkeypatch.setattr(mod, "_project_exists", lambda _s, _p: exists)
    monkeypatch.setattr(mod, "_latest_scan_id", lambda _s, _p: latest)
    monkeypatch.setattr(mod, "_active_scan_ids", lambda _s, _p: set(active))

    @contextmanager
    def _fake_scope():  # type: ignore[no-untyped-def]
        yield object()

    monkeypatch.setattr(mod, "sync_session_scope", _fake_scope)
    result: dict[str, Any] = mod.scan_source_cleaner_task()
    return result


def test_latest_kept_others_swept(
    _workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid = uuid.uuid4()
    latest = uuid.uuid4()
    old = uuid.uuid4()
    latest_path = _write_tarball(pid, latest, age_seconds=10)
    old_path = _write_tarball(pid, old, age_seconds=99999)

    result = _run(monkeypatch, exists=True, latest=str(latest), active=set())

    assert latest_path.exists()
    assert not old_path.exists()
    assert result["deleted"] == 1
    assert result["reclaimed_bytes"] > 0


def test_active_scan_tarball_protected_even_if_not_latest(
    _workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid = uuid.uuid4()
    latest = uuid.uuid4()
    running = uuid.uuid4()  # a queued/running scan's tarball, not the latest
    latest_path = _write_tarball(pid, latest, age_seconds=10)
    running_path = _write_tarball(pid, running, age_seconds=99999)

    result = _run(
        monkeypatch, exists=True, latest=str(latest), active={str(running)}
    )

    assert latest_path.exists()
    assert running_path.exists()  # protected by the active-scan reference
    assert result["deleted"] == 0


def test_latest_without_tarball_keeps_newest_fallback(
    _workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """latest_scan_id (a failed/running scan) has no file → keep newest tarball."""
    pid = uuid.uuid4()
    failed_latest = uuid.uuid4()  # no tarball was ever written for this one
    newer_good = uuid.uuid4()
    older_good = uuid.uuid4()
    newer_path = _write_tarball(pid, newer_good, age_seconds=100)
    older_path = _write_tarball(pid, older_good, age_seconds=99999)

    result = _run(
        monkeypatch, exists=True, latest=str(failed_latest), active=set()
    )

    assert newer_path.exists()  # newest preserved tarball is the fallback keep
    assert not older_path.exists()
    assert result["deleted"] == 1


def test_orphaned_project_all_reclaimed(
    _workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid = uuid.uuid4()
    a = _write_tarball(pid, uuid.uuid4(), age_seconds=10)
    b = _write_tarball(pid, uuid.uuid4(), age_seconds=20)

    result = _run(monkeypatch, exists=False, latest=None, active=set())

    assert not a.exists()
    assert not b.exists()
    assert result["deleted"] == 2
    # The emptied project dir is removed too.
    assert not scan_sources_dir_for_project(pid).exists()


def test_non_tarball_file_is_never_touched(
    _workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid = uuid.uuid4()
    sources_dir = scan_sources_dir_for_project(pid)
    sources_dir.mkdir(parents=True, exist_ok=True)
    note = sources_dir / "README.txt"
    note.write_bytes(b"keep me")

    result = _run(monkeypatch, exists=True, latest=None, active=set())

    assert note.exists()  # not a *.tar.gz — out of scope
    assert result["scanned"] == 0


def test_non_uuid_tarball_stem_is_kept(
    _workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A *.tar.gz whose stem is not a UUID is not ours — never deleted."""
    pid = uuid.uuid4()
    sources_dir = scan_sources_dir_for_project(pid)
    sources_dir.mkdir(parents=True, exist_ok=True)
    rogue = sources_dir / "not-a-uuid.tar.gz"
    rogue.write_bytes(b"\x1f\x8b\x00")

    result = _run(monkeypatch, exists=True, latest=None, active=set())

    assert rogue.exists()
    assert result["deleted"] == 0


@pytest.mark.parametrize("bad_name", ["not-a-uuid", "..", "etc", "0xdeadbeef", "  "])
def test_non_uuid_project_dir_is_ignored(
    _workspace: Path, monkeypatch: pytest.MonkeyPatch, bad_name: str
) -> None:
    from tasks import scan_source_cleaner as mod

    sources_root = Path(_workspace) / "scan-sources"
    rogue = sources_root / bad_name
    rogue.mkdir(parents=True, exist_ok=True)
    (rogue / "x.tar.gz").write_bytes(b"\x1f\x8b\x00")

    def _boom(*_a, **_k):  # type: ignore[no-untyped-def]
        raise AssertionError("DB helper called for a non-UUID project dir")

    monkeypatch.setattr(mod, "_project_exists", _boom)
    monkeypatch.setattr(mod, "_latest_scan_id", _boom)
    monkeypatch.setattr(mod, "_active_scan_ids", _boom)

    @contextmanager
    def _fake_scope():  # type: ignore[no-untyped-def]
        yield object()

    monkeypatch.setattr(mod, "sync_session_scope", _fake_scope)

    result = mod.scan_source_cleaner_task()
    assert (rogue / "x.tar.gz").exists()
    assert result["scanned"] == 0


def test_no_sources_root_is_a_noop(
    _workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _run(monkeypatch, exists=True, latest=None, active=set())
    assert result == {"scanned": 0, "deleted": 0, "reclaimed_bytes": 0}
