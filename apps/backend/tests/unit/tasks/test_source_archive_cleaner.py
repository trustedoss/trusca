"""
Unit tests for ``tasks.source_archive_cleaner`` — feat/zip-upload (security H-fix).

The task's value is its *filesystem* decision logic: which uploaded ``*.zip``
files are reclaimable. We isolate that from DB plumbing by patching the two
DB-reading helpers (``_project_exists`` / ``_active_archive_ids``) — the same
isolation style ``test_dt_orphan_cleanup`` uses for the DT client.

Adversarial / edge cases (per MEMORY: untrusted-input parametrize):
  - a project dir whose name is NOT a UUID is ignored (no traversal, no crash)
  - a stale + unreferenced archive is reclaimed
  - a fresh archive is kept (under TTL)
  - an archive referenced by a queued/running scan is kept regardless of age
  - an archive whose project no longer exists is always reclaimed
  - a non-.zip file is never touched
  - an emptied project dir is removed
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

from services.source_archive_service import archive_path, archives_dir_for_project


@pytest.fixture(autouse=True)
def _workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    return tmp_path


def _write_zip(project_id: uuid.UUID, archive_id: uuid.UUID, *, age_seconds: float = 0.0) -> Path:
    path = archive_path(project_id, str(archive_id))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"PK\x03\x04" + b"\x00" * 64)
    if age_seconds:
        old = time.time() - age_seconds
        os.utime(path, (old, old))
    return path


def _run(
    monkeypatch: pytest.MonkeyPatch, *, exists: bool, active: set[str]
) -> dict[str, Any]:
    """Invoke the task with the two DB helpers stubbed."""
    from tasks import source_archive_cleaner as mod

    monkeypatch.setattr(mod, "_project_exists", lambda _s, _p: exists)
    monkeypatch.setattr(mod, "_active_archive_ids", lambda _s, _p: set(active))

    # Stub the session scope so no Postgres is needed; the stubbed helpers
    # ignore the session object entirely.
    from contextlib import contextmanager

    @contextmanager
    def _fake_scope():  # type: ignore[no-untyped-def]
        yield object()

    monkeypatch.setattr(mod, "sync_session_scope", _fake_scope)
    result: dict[str, Any] = mod.source_archive_cleaner_task()
    return result


def test_stale_unreferenced_archive_is_reclaimed(
    _workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SOURCE_ARCHIVE_RETENTION_HOURS", "1")
    pid = uuid.uuid4()
    aid = uuid.uuid4()
    path = _write_zip(pid, aid, age_seconds=7200)  # 2h old, TTL 1h

    result = _run(monkeypatch, exists=True, active=set())

    assert not path.exists()
    assert result["deleted"] == 1
    assert result["reclaimed_bytes"] > 0


def test_fresh_archive_is_kept(_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOURCE_ARCHIVE_RETENTION_HOURS", "24")
    pid = uuid.uuid4()
    aid = uuid.uuid4()
    path = _write_zip(pid, aid, age_seconds=60)  # 1 min old

    result = _run(monkeypatch, exists=True, active=set())

    assert path.exists()
    assert result["deleted"] == 0


def test_active_scan_archive_is_kept_even_when_stale(
    _workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A queued/running scan referencing the archive_id protects it from sweep."""
    monkeypatch.setenv("SOURCE_ARCHIVE_RETENTION_HOURS", "1")
    pid = uuid.uuid4()
    aid = uuid.uuid4()
    path = _write_zip(pid, aid, age_seconds=999999)  # ancient

    result = _run(monkeypatch, exists=True, active={str(aid)})

    assert path.exists()
    assert result["deleted"] == 0


def test_orphaned_project_archive_always_reclaimed(
    _workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the project no longer exists, age is irrelevant — reclaim it."""
    monkeypatch.setenv("SOURCE_ARCHIVE_RETENTION_HOURS", "99999")
    pid = uuid.uuid4()
    aid = uuid.uuid4()
    path = _write_zip(pid, aid, age_seconds=10)  # brand new

    result = _run(monkeypatch, exists=False, active=set())

    assert not path.exists()
    assert result["deleted"] == 1
    # The emptied project dir is removed too.
    assert not archives_dir_for_project(pid).exists()


def test_non_zip_file_is_never_touched(
    _workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid = uuid.uuid4()
    archives_dir_for_project(pid).mkdir(parents=True, exist_ok=True)
    note = archives_dir_for_project(pid) / "README.txt"
    note.write_bytes(b"keep me")

    result = _run(monkeypatch, exists=False, active=set())

    assert note.exists()  # not a *.zip — out of scope
    assert result["scanned"] == 0


@pytest.mark.parametrize("bad_name", ["not-a-uuid", "..", "etc", "0xdeadbeef", "  "])
def test_non_uuid_project_dir_is_ignored(
    _workspace: Path, monkeypatch: pytest.MonkeyPatch, bad_name: str
) -> None:
    """A directory whose name is not a UUID is left strictly alone."""
    from tasks import source_archive_cleaner as mod

    archives_root = Path(_workspace) / "archives"
    rogue = archives_root / bad_name
    rogue.mkdir(parents=True, exist_ok=True)
    (rogue / "x.zip").write_bytes(b"PK\x03\x04")

    # Helpers must never be called for a non-UUID dir.
    def _boom(*_a, **_k):  # type: ignore[no-untyped-def]
        raise AssertionError("DB helper called for a non-UUID project dir")

    monkeypatch.setattr(mod, "_project_exists", _boom)
    monkeypatch.setattr(mod, "_active_archive_ids", _boom)
    from contextlib import contextmanager

    @contextmanager
    def _fake_scope():  # type: ignore[no-untyped-def]
        yield object()

    monkeypatch.setattr(mod, "sync_session_scope", _fake_scope)

    result = mod.source_archive_cleaner_task()
    assert (rogue / "x.zip").exists()
    assert result["scanned"] == 0


def test_no_archives_root_is_a_noop(
    _workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _run(monkeypatch, exists=True, active=set())
    assert result == {"scanned": 0, "deleted": 0, "reclaimed_bytes": 0}


def test_retention_hours_read_at_call_time(monkeypatch: pytest.MonkeyPatch) -> None:
    from tasks.source_archive_cleaner import _retention_seconds

    monkeypatch.setenv("SOURCE_ARCHIVE_RETENTION_HOURS", "2")
    assert _retention_seconds() == 7200
    monkeypatch.setenv("SOURCE_ARCHIVE_RETENTION_HOURS", "0.5")
    assert _retention_seconds() == 1800
    monkeypatch.delenv("SOURCE_ARCHIVE_RETENTION_HOURS", raising=False)
    assert _retention_seconds() == 24 * 3600
