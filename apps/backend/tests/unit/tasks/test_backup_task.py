"""
Unit tests for ``tasks.backup`` — Phase 6 chore PR #19, refactored
under marathon bundle 3 (D2).

The D2 refactor swapped the ``scripts/{backup,restore}.sh`` delegation
for direct ``pg_dump`` / ``psql`` invocation against ``DATABASE_URL``.
These tests drive the task body via ``run_backup_task.run`` /
``restore_backup_task.run`` (bypasses Celery's bind-self injection
without needing a broker) and patch the subprocess + workspace + audit
boundaries so the tests are hermetic.
"""

from __future__ import annotations

import gzip
import io
import json
import subprocess
import tarfile
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeSession:
    def __init__(self) -> None:
        self.added: list[Any] = []
        self.committed = False

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass


class _SessionAccumulator:
    """Collects every session opened via the patched scope."""

    def __init__(self) -> None:
        self.sessions: list[_FakeSession] = []

    @property
    def all_added(self) -> list[Any]:
        return [obj for session in self.sessions for obj in session.added]


def _scope_factory(accumulator: _SessionAccumulator) -> Any:
    """Return a ``sync_session_scope``-compatible context manager."""

    @contextmanager
    def _scope() -> Any:
        session = _FakeSession()
        accumulator.sessions.append(session)
        try:
            yield session
        finally:
            session.close()

    return _scope


def _gzipped_sql_bytes() -> bytes:
    """Return a tiny valid gzip stream — enough to satisfy `gzip.open` reads."""
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        gz.write(b"-- fake dump\nSELECT 1;\n")
    return buf.getvalue()


def _seed_manifest(
    root: Path,
    name: str,
    *,
    alembic_head: str = "abc123",
    checksums: dict[str, str] | None = None,
    has_workspace: bool = False,
) -> None:
    target = root / name
    target.mkdir(parents=True, exist_ok=True)
    (target / "postgres.sql.gz").write_bytes(_gzipped_sql_bytes())
    if has_workspace:
        # Tiny tar.gz for path-presence checks; never extracted in these
        # unit tests because we patch the extract helper.
        (target / "workspace.tar.gz").write_bytes(b"\x1f\x8bfake")
    payload: dict[str, Any] = {
        "name": name,
        "alembic_head": alembic_head,
        "timestamp": "20260509T120000Z",
        "checksums": checksums or {},
        "has_workspace": has_workspace,
    }
    (target / "manifest.json").write_text(json.dumps(payload))


# ---------------------------------------------------------------------------
# DATABASE_URL parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        (
            "postgresql+asyncpg://trustedoss:secret@db:5432/trustedoss",
            (["-h", "db", "-p", "5432", "-U", "trustedoss", "-d", "trustedoss"], "secret"),
        ),
        (
            "postgresql://app:p@ss%40word@127.0.0.1:5433/app_db",
            (["-h", "127.0.0.1", "-p", "5433", "-U", "app", "-d", "app_db"], "p@ss@word"),
        ),
        (
            "postgresql://trustedoss@host/db",  # no password, default port
            (["-h", "host", "-p", "5432", "-U", "trustedoss", "-d", "db"], ""),
        ),
        # I5 — IPv6 host (urlparse strips the brackets, libpq accepts ::1).
        (
            "postgresql://u:p@[::1]:5432/d",
            (["-h", "::1", "-p", "5432", "-U", "u", "-d", "d"], "p"),
        ),
        # asyncpg variant of the same.
        (
            "postgresql+psycopg2://app:s%26cr%26t@host/db",
            (["-h", "host", "-p", "5432", "-U", "app", "-d", "db"], "s&cr&t"),
        ),
    ],
)
def test_pg_connection_args_parses_url(
    monkeypatch: pytest.MonkeyPatch, url: str, expected: tuple[list[str], str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", url)
    from tasks import backup as task_module

    args, env = task_module._pg_connection_args()
    assert args == expected[0]
    assert env == {"PGPASSWORD": expected[1]}


@pytest.mark.parametrize(
    "url",
    [
        "mysql://root@host/db",
        "redis://host:6379/0",
        "://no-scheme",
        # M3 — adversarial scheme corner cases that previously slipped past.
        "postgresql:foo://host/db",       # malformed scheme suffix
        "postgresql+://host/db",          # empty driver
        "postgresql+evil!@://host/db",    # non-identifier driver
        "no-scheme-at-all",               # missing ://
    ],
)
def test_pg_connection_args_rejects_non_postgres_url(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    monkeypatch.setenv("DATABASE_URL", url)
    from tasks import backup as task_module

    with pytest.raises(task_module.BackupTaskError):
        task_module._pg_connection_args()


# ---------------------------------------------------------------------------
# run_backup_task
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_backups(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("BACKUPS_ROOT", str(tmp_path))
    return tmp_path


@pytest.fixture
def patched_pg_dump(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Replace ``_run_pg_dump`` with a stub that writes a small gzip file."""
    from tasks import backup as task_module

    written: list[Path] = []

    def _stub(target_gz: Path) -> None:
        written.append(target_gz)
        target_gz.write_bytes(_gzipped_sql_bytes())

    monkeypatch.setattr(task_module, "_run_pg_dump", _stub)
    monkeypatch.setattr(task_module, "_alembic_head", lambda: "rev42")
    return written


@pytest.fixture
def patched_workspace(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace workspace tar helpers so unit tests don't touch the FS."""
    from tasks import backup as task_module

    state = {"created": False, "extracted": False}

    def _create_stub(target: Path) -> bool:
        state["created"] = True
        # Write a tiny tar.gz so size + checksum are non-zero.
        with tarfile.open(target, "w:gz") as tf:
            data = b"workspace-stub"
            info = tarfile.TarInfo(name="workspace/README")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        return True

    def _extract_stub(_source: Path) -> None:
        state["extracted"] = True

    monkeypatch.setattr(task_module, "_create_workspace_archive", _create_stub)
    monkeypatch.setattr(task_module, "_extract_workspace_archive", _extract_stub)
    return state


def test_run_backup_emits_completed_audit_on_success(
    monkeypatch: pytest.MonkeyPatch,
    temp_backups: Path,
    patched_pg_dump: list[Path],
    patched_workspace: dict[str, Any],
) -> None:
    from tasks import backup as task_module

    accumulator = _SessionAccumulator()
    monkeypatch.setattr(task_module, "sync_session_scope", _scope_factory(accumulator))

    result = task_module.run_backup_task.run(kind="manual", actor_user_id=None)

    assert result["name"].startswith("manual-")
    assert result["db_revision"] == "rev42"
    assert result["size_bytes"] > 0
    assert result["pruned"] == []

    actions = sorted(getattr(obj, "action", "") for obj in accumulator.all_added)
    assert "backup.completed" in actions
    assert "backup.failed" not in actions

    completed_row = next(
        obj for obj in accumulator.all_added if getattr(obj, "action", "") == "backup.completed"
    )
    assert completed_row.target_table == "backups"
    assert completed_row.diff["kind"] == "manual"
    assert completed_row.diff["db_revision"] == "rev42"
    assert completed_row.diff["has_workspace"] is True
    # SHA-256 hex digests recorded for the artifacts.
    checksums = completed_row.diff["checksums"]
    assert len(checksums["postgres.sql.gz"]) == 64
    assert len(checksums["workspace.tar.gz"]) == 64

    # Manifest written to disk with matching shape.
    manifest_path = temp_backups / result["name"] / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["alembic_head"] == "rev42"
    assert manifest["has_workspace"] is True
    assert manifest["checksums"]["postgres.sql.gz"] == checksums["postgres.sql.gz"]


def test_run_backup_raises_and_emits_failed_audit_on_pg_dump_error(
    monkeypatch: pytest.MonkeyPatch, temp_backups: Path, patched_workspace: dict[str, Any]
) -> None:
    from tasks import backup as task_module

    accumulator = _SessionAccumulator()
    monkeypatch.setattr(task_module, "sync_session_scope", _scope_factory(accumulator))

    def _fail(_target: Path) -> None:
        raise task_module.BackupTaskError("pg_dump exited 2: relation does not exist")

    monkeypatch.setattr(task_module, "_run_pg_dump", _fail)

    with pytest.raises(task_module.BackupTaskError):
        task_module.run_backup_task.run(kind="auto", actor_user_id=None)

    actions = [getattr(obj, "action", "") for obj in accumulator.all_added]
    assert actions == ["backup.failed"]
    failed = accumulator.all_added[0]
    assert failed.diff["stage"] == "pg_dump"
    assert "pg_dump exited 2" in failed.diff["error"]
    # The partial backup directory was cleaned so the next attempt is fresh.
    backup_dirs = [p for p in temp_backups.iterdir() if p.is_dir()]
    assert backup_dirs == []


def test_run_backup_applies_retention_after_success(
    monkeypatch: pytest.MonkeyPatch,
    temp_backups: Path,
    patched_pg_dump: list[Path],
    patched_workspace: dict[str, Any],
) -> None:
    """An old auto backup is pruned during the post-success retention pass."""
    from tasks import backup as task_module

    accumulator = _SessionAccumulator()
    monkeypatch.setattr(task_module, "sync_session_scope", _scope_factory(accumulator))

    # Pre-seed an old auto backup that the retention pass should remove.
    old_name = "auto-20260101T000000Z"
    old_dir = temp_backups / old_name
    old_dir.mkdir()
    (old_dir / "postgres.sql.gz").write_bytes(b"old")
    (old_dir / "manifest.json").write_text(json.dumps({"alembic_head": "old"}))
    eight_days_ago = (datetime.now(tz=UTC) - timedelta(days=8)).timestamp()
    import os

    os.utime(old_dir, (eight_days_ago, eight_days_ago))

    # Pre-seed a manual that must NOT be pruned even though it is also old.
    manual_old_name = "manual-20260101T000000Z"
    manual_old = temp_backups / manual_old_name
    manual_old.mkdir()
    (manual_old / "postgres.sql.gz").write_bytes(b"old")
    (manual_old / "manifest.json").write_text(json.dumps({"alembic_head": "old"}))
    os.utime(manual_old, (eight_days_ago, eight_days_ago))

    result = task_module.run_backup_task.run(kind="auto", actor_user_id=None)

    assert old_name in result["pruned"]
    assert manual_old_name not in result["pruned"]
    assert not old_dir.exists()
    assert manual_old.is_dir()

    pruned_rows = [
        obj for obj in accumulator.all_added if getattr(obj, "action", "") == "backup.pruned"
    ]
    assert [row.target_id for row in pruned_rows] == [old_name]


def test_run_backup_records_actor_uuid_when_supplied(
    monkeypatch: pytest.MonkeyPatch,
    temp_backups: Path,
    patched_pg_dump: list[Path],
    patched_workspace: dict[str, Any],
) -> None:
    from tasks import backup as task_module

    accumulator = _SessionAccumulator()
    monkeypatch.setattr(task_module, "sync_session_scope", _scope_factory(accumulator))

    actor = uuid.uuid4()
    task_module.run_backup_task.run(kind="manual", actor_user_id=str(actor))

    completed = next(
        obj for obj in accumulator.all_added if getattr(obj, "action", "") == "backup.completed"
    )
    assert completed.actor_user_id == actor


# ---------------------------------------------------------------------------
# restore_backup_task
# ---------------------------------------------------------------------------


def test_restore_backup_emits_restored_audit_on_success(
    monkeypatch: pytest.MonkeyPatch, temp_backups: Path
) -> None:
    from tasks import backup as task_module

    name = "manual-20260509T120000Z"
    _seed_manifest(temp_backups, name, alembic_head="rev99")

    accumulator = _SessionAccumulator()
    monkeypatch.setattr(task_module, "sync_session_scope", _scope_factory(accumulator))
    # Stub the actual psql restore so the test stays hermetic.
    monkeypatch.setattr(task_module, "_run_psql_restore", lambda _src: None)

    actor = uuid.uuid4()
    result = task_module.restore_backup_task.run(name=name, actor_user_id=str(actor))

    assert result["revision_before"] == "rev99"
    assert result["revision_after"] == "rev99"
    assert result["mismatch"] is False

    actions = [getattr(obj, "action", "") for obj in accumulator.all_added]
    assert actions == ["backup.restored"]


def test_restore_backup_raises_when_artifact_missing(
    monkeypatch: pytest.MonkeyPatch, temp_backups: Path
) -> None:
    from tasks import backup as task_module

    name = "manual-20260509T120000Z"
    target = temp_backups / name
    target.mkdir()
    # Only the manifest is present — postgres.sql.gz missing.
    (target / "manifest.json").write_text(json.dumps({"alembic_head": "rev1"}))

    accumulator = _SessionAccumulator()
    monkeypatch.setattr(task_module, "sync_session_scope", _scope_factory(accumulator))

    actor = uuid.uuid4()
    with pytest.raises(task_module.RestoreTaskError):
        task_module.restore_backup_task.run(name=name, actor_user_id=str(actor))

    actions = [getattr(obj, "action", "") for obj in accumulator.all_added]
    assert actions == ["backup.restore_failed"]
    failed = accumulator.all_added[0]
    assert failed.diff["error"] == "incomplete_artifacts"
    assert failed.diff["missing"] == "postgres.sql.gz"


def test_restore_backup_raises_on_invalid_name(
    monkeypatch: pytest.MonkeyPatch, temp_backups: Path
) -> None:
    """Path-traversal name → RestoreTaskError + audit row."""
    from tasks import backup as task_module

    accumulator = _SessionAccumulator()
    monkeypatch.setattr(task_module, "sync_session_scope", _scope_factory(accumulator))

    actor = uuid.uuid4()
    with pytest.raises(task_module.RestoreTaskError):
        task_module.restore_backup_task.run(name="../etc/passwd", actor_user_id=str(actor))

    actions = [getattr(obj, "action", "") for obj in accumulator.all_added]
    assert actions == ["backup.restore_failed"]
    assert accumulator.all_added[0].diff["error"] == "not_found"


def test_restore_backup_logs_warning_on_revision_mismatch(
    monkeypatch: pytest.MonkeyPatch, temp_backups: Path
) -> None:
    """If the manifest revision changes between probes, mismatch=True."""
    from tasks import backup as task_module

    name = "manual-20260509T120000Z"
    _seed_manifest(temp_backups, name, alembic_head="rev_before")

    accumulator = _SessionAccumulator()
    monkeypatch.setattr(task_module, "sync_session_scope", _scope_factory(accumulator))

    def _stub_restore(_src: Path) -> None:
        # Simulate the manifest being rewritten mid-restore.
        backup_path = temp_backups / name
        (backup_path / "manifest.json").write_text(
            json.dumps({"alembic_head": "rev_after"})
        )

    monkeypatch.setattr(task_module, "_run_psql_restore", _stub_restore)

    actor = uuid.uuid4()
    result = task_module.restore_backup_task.run(name=name, actor_user_id=str(actor))

    assert result["mismatch"] is True
    restored = accumulator.all_added[0]
    assert restored.action == "backup.restored"
    assert restored.diff["mismatch"] is True
    assert restored.diff["revision_before"] == "rev_before"
    assert restored.diff["revision_after"] == "rev_after"


def test_restore_backup_psql_error_emits_failed_audit(
    monkeypatch: pytest.MonkeyPatch, temp_backups: Path
) -> None:
    from tasks import backup as task_module

    name = "manual-20260509T120000Z"
    _seed_manifest(temp_backups, name)

    accumulator = _SessionAccumulator()
    monkeypatch.setattr(task_module, "sync_session_scope", _scope_factory(accumulator))

    def _fail(_src: Path) -> None:
        raise task_module.RestoreTaskError("psql exited 3: syntax error")

    monkeypatch.setattr(task_module, "_run_psql_restore", _fail)

    actor = uuid.uuid4()
    with pytest.raises(task_module.RestoreTaskError):
        task_module.restore_backup_task.run(name=name, actor_user_id=str(actor))

    actions = [getattr(obj, "action", "") for obj in accumulator.all_added]
    assert actions == ["backup.restore_failed"]
    assert accumulator.all_added[0].diff["stage"] == "psql_restore"
    assert "psql exited 3" in accumulator.all_added[0].diff["error"]


def test_restore_backup_rejects_checksum_mismatch(
    monkeypatch: pytest.MonkeyPatch, temp_backups: Path
) -> None:
    """A manifest with a wrong sha256 must abort BEFORE psql touches the DB."""
    from tasks import backup as task_module

    name = "manual-20260509T120000Z"
    _seed_manifest(
        temp_backups,
        name,
        checksums={"postgres.sql.gz": "deadbeef" * 8},  # 64 hex but wrong
    )

    accumulator = _SessionAccumulator()
    monkeypatch.setattr(task_module, "sync_session_scope", _scope_factory(accumulator))

    psql_called = {"n": 0}

    def _stub_restore(_src: Path) -> None:
        psql_called["n"] += 1

    monkeypatch.setattr(task_module, "_run_psql_restore", _stub_restore)

    actor = uuid.uuid4()
    with pytest.raises(task_module.RestoreTaskError):
        task_module.restore_backup_task.run(name=name, actor_user_id=str(actor))

    assert psql_called["n"] == 0  # restore aborted before psql was called.
    actions = [getattr(obj, "action", "") for obj in accumulator.all_added]
    assert actions == ["backup.restore_failed"]
    assert accumulator.all_added[0].diff["error"] == "checksum_mismatch"
    assert accumulator.all_added[0].diff["artifact"] == "postgres.sql.gz"


# ---------------------------------------------------------------------------
# Workspace tar — path-traversal + size-cap guards
# ---------------------------------------------------------------------------


def _build_tar(tmp: Path, member_name: str, member_size: int = 16) -> Path:
    """Build a tar.gz with a single member whose archive-name is `member_name`."""
    target = tmp / "evil.tar.gz"
    with tarfile.open(target, "w:gz") as tf:
        info = tarfile.TarInfo(name=member_name)
        info.size = member_size
        tf.addfile(info, io.BytesIO(b"x" * member_size))
    return target


def test_extract_workspace_rejects_path_traversal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from tasks import backup as task_module

    workspace = tmp_path / "workspace"
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(workspace))
    archive = _build_tar(tmp_path, "../escape/evil.txt")

    with pytest.raises(task_module.RestoreTaskError, match="escapes destination"):
        task_module._extract_workspace_archive(archive)


def test_extract_workspace_rejects_oversized_member(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from tasks import backup as task_module

    workspace = tmp_path / "workspace"
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(workspace))

    # Shrink the per-member cap to 4 bytes so we can build a real archive
    # whose ONE member is "oversized" relative to the cap without having
    # to allocate gigabytes on disk. The guard logic is independent of
    # the constant value.
    monkeypatch.setattr(task_module, "_MAX_MEMBER_BYTES", 4)
    archive = _build_tar(tmp_path, "workspace/oversized", member_size=16)

    with pytest.raises(task_module.RestoreTaskError, match="exceeds"):
        task_module._extract_workspace_archive(archive)


def test_create_workspace_archive_skips_when_path_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When WORKSPACE_HOST_PATH is absent, archive helper returns False without
    raising — the backup proceeds without the workspace artifact."""
    from tasks import backup as task_module

    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path / "does-not-exist"))
    target = tmp_path / "workspace.tar.gz"
    assert task_module._create_workspace_archive(target) is False
    assert not target.exists()


def test_run_backup_skips_workspace_when_missing(
    monkeypatch: pytest.MonkeyPatch,
    temp_backups: Path,
    patched_pg_dump: list[Path],
) -> None:
    """workspace tar is best-effort — task succeeds when the dir is absent."""
    from tasks import backup as task_module

    accumulator = _SessionAccumulator()
    monkeypatch.setattr(task_module, "sync_session_scope", _scope_factory(accumulator))
    # Force the workspace archive to report "no workspace present".
    monkeypatch.setattr(task_module, "_create_workspace_archive", lambda _t: False)

    result = task_module.run_backup_task.run(kind="manual", actor_user_id=None)
    completed = next(
        obj for obj in accumulator.all_added if getattr(obj, "action", "") == "backup.completed"
    )
    assert completed.diff["has_workspace"] is False
    assert "workspace.tar.gz" not in completed.diff["checksums"]
    # Manifest reflects the same.
    manifest = json.loads(
        (temp_backups / result["name"] / "manifest.json").read_text()
    )
    assert manifest["has_workspace"] is False


# ---------------------------------------------------------------------------
# pg_dump password is passed via env (PGPASSWORD), never argv
# ---------------------------------------------------------------------------


def test_run_pg_dump_passes_password_via_env_not_argv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Adversarial: confirm pg_dump argv never contains the password.

    A regression that switches to ``-W <password>`` would expose the
    secret in ``ps`` output. The structural guarantee is in
    ``_pg_connection_args`` returning the password under PGPASSWORD;
    we pin the contract by capturing the Popen invocation (M1 streaming
    refactor).
    """
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+asyncpg://trustedoss:s3cret-pw@db:5432/trustedoss"
    )
    from tasks import backup as task_module

    captured: dict[str, Any] = {}

    class _FakePopen:
        def __init__(self, argv: list[str], **kwargs: Any) -> None:
            captured["argv"] = list(argv)
            captured["env"] = dict(kwargs.get("env") or {})
            self.stdout = io.BytesIO(b"-- ok\n")
            self.stderr = io.BytesIO(b"")
            self.returncode = 0

        def wait(self, timeout: float | None = None) -> int:
            return self.returncode

        def kill(self) -> None:
            pass

    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    target = tmp_path / "out.sql.gz"
    task_module._run_pg_dump(target)

    argv_blob = " ".join(captured["argv"])
    assert "s3cret-pw" not in argv_blob
    assert captured["env"].get("PGPASSWORD") == "s3cret-pw"
    # pg_dump invoked, not bash + script.
    assert captured["argv"][0] == "pg_dump"


def test_allocate_backup_slot_retries_on_collision_then_succeeds(
    monkeypatch: pytest.MonkeyPatch, temp_backups: Path
) -> None:
    """Marathon bundle 4 (R / M4): a slot collision triggers up to 3
    retries; if the next ``_utc_stamp()`` produces a fresh dir name the
    backup proceeds normally."""
    from tasks import backup as task_module

    monkeypatch.setattr(task_module, "_NAME_COLLISION_SLEEP_SECONDS", 0)
    stamps = iter(["20260510T120000Z", "20260510T120001Z"])
    monkeypatch.setattr(task_module, "_utc_stamp", lambda **_: next(stamps))
    (temp_backups / "manual-20260510T120000Z").mkdir()

    name, path = task_module._allocate_backup_slot("manual", None)
    assert name == "manual-20260510T120001Z"
    assert path.is_dir()


def test_allocate_backup_slot_raises_after_max_retries(
    monkeypatch: pytest.MonkeyPatch, temp_backups: Path
) -> None:
    """Marathon bundle 4 (R / M4): every slot taken → BackupNameCollisionError
    + audit row with stage=name_collision (no torn backup written)."""
    from tasks import backup as task_module

    accumulator = _SessionAccumulator()
    monkeypatch.setattr(task_module, "sync_session_scope", _scope_factory(accumulator))
    monkeypatch.setattr(task_module, "_NAME_COLLISION_SLEEP_SECONDS", 0)
    monkeypatch.setattr(task_module, "_utc_stamp", lambda **_: "20260510T120000Z")
    (temp_backups / "manual-20260510T120000Z").mkdir()

    with pytest.raises(task_module.BackupNameCollisionError):
        task_module._allocate_backup_slot("manual", None)

    actions = [getattr(o, "action", "") for o in accumulator.all_added]
    assert actions == ["backup.failed"]
    assert accumulator.all_added[0].diff["stage"] == "name_collision"


def test_run_pg_dump_streams_output_through_gzip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """M1 regression: pg_dump output must reach the gzip file even when the
    Popen stdout is read in chunks rather than all at once."""
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+asyncpg://t:p@db:5432/t"
    )
    from tasks import backup as task_module

    payload = b"-- streamed dump\n" * 1024  # ~17 KB raw input

    class _FakePopen:
        def __init__(self, _argv: list[str], **_kwargs: Any) -> None:
            self.stdout = io.BytesIO(payload)
            self.stderr = io.BytesIO(b"")
            self.returncode = 0

        def wait(self, timeout: float | None = None) -> int:
            return self.returncode

        def kill(self) -> None:
            pass

    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    target = tmp_path / "out.sql.gz"
    task_module._run_pg_dump(target)

    # Round-trip the gzip file and confirm the streamed payload survived.
    import gzip as _gz

    with _gz.open(target, "rb") as fh:
        roundtrip = fh.read()
    assert roundtrip == payload
