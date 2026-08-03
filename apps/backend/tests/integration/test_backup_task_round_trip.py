"""
Integration test for ``tasks.backup`` round-trip — Marathon bundle 3 (D2).

Drives the real ``pg_dump`` / ``psql`` binaries shipped in the backend +
worker images (postgresql-client-17, installed by D2). The test:

  1. Inserts a sentinel row into a temporary table so we can prove the
     restore actually replayed the dump.
  2. Runs ``_run_backup`` end-to-end → expects manifest.json + non-empty
     postgres.sql.gz + sha256 checksum present.
  3. Drops the sentinel table.
  4. Runs ``_run_restore`` against the just-written backup → expects the
     sentinel row to be back.

Hermeticity:
  - Uses a sentinel table prefix unique to this test so concurrent
    integration runs don't collide.
  - Backups land in ``tmp_path / backups`` (BACKUPS_ROOT override).
  - Audit emission is captured via the same fake-session pattern the
    unit tests use, so the integration test does NOT pollute audit_logs.
  - WORKSPACE_HOST_PATH is pointed at an empty tmp path so the workspace
    archive is skipped (we test that branch in the unit tests).

This is the test that would have caught the docker-compose-inside-the-
worker bug the original PR #19 + chore PRs missed for months — running
``_run_backup`` against a live Postgres uses pg_dump from PATH and
nothing else.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from core.config import database_url

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip backup round-trip integration test")
    return url


def _require_pg_client() -> None:
    try:
        subprocess.run(
            ["pg_dump", "--version"],
            check=True,
            capture_output=True,
            timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        pytest.skip("pg_dump not on PATH — backup round-trip needs postgresql-client-17")


class _FakeSession:
    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass


class _SessionAccumulator:
    def __init__(self) -> None:
        self.sessions: list[_FakeSession] = []

    @property
    def all_added(self) -> list[Any]:
        return [obj for s in self.sessions for obj in s.added]


def _scope_factory(acc: _SessionAccumulator) -> Any:
    @contextmanager
    def _scope() -> Any:
        s = _FakeSession()
        acc.sessions.append(s)
        try:
            yield s
        finally:
            s.close()

    return _scope


def _sentinel_table_name() -> str:
    """Unique-per-run table name so concurrent integration runs don't collide."""
    return f"backup_round_trip_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def temp_backups(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("BACKUPS_ROOT", str(tmp_path / "backups"))
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path / "workspace-empty"))
    (tmp_path / "backups").mkdir()
    return tmp_path / "backups"


async def test_backup_round_trip_preserves_sentinel_row(
    monkeypatch: pytest.MonkeyPatch, temp_backups: Path
) -> None:
    """Insert sentinel → backup → drop → restore → sentinel row is back.

    Uses ``async`` so we can drive psycopg / asyncpg via the live engine.
    The ORM ``sync_session_scope`` is patched to a fake so audit rows
    don't pollute audit_logs (the test cleans up only the sentinel
    table, not the audit log).
    """
    _require_database_url()
    _require_pg_client()

    from sqlalchemy.ext.asyncio import create_async_engine

    from tasks import backup as task_module

    table = _sentinel_table_name()
    sentinel_value = uuid.uuid4().hex

    engine = create_async_engine(database_url(), pool_pre_ping=True)

    # Step 1: insert sentinel row.
    async with engine.begin() as conn:
        await conn.execute(text(f'CREATE TABLE "{table}" (k text PRIMARY KEY, v text)'))
        await conn.execute(
            text(f'INSERT INTO "{table}" (k, v) VALUES (:k, :v)'),
            {"k": "sentinel", "v": sentinel_value},
        )

    try:
        accumulator = _SessionAccumulator()
        monkeypatch.setattr(task_module, "sync_session_scope", _scope_factory(accumulator))

        # Step 2: full backup.
        result = task_module.run_backup_task.run(kind="manual", actor_user_id=None)
        backup_dir = temp_backups / result["name"]
        assert (backup_dir / "postgres.sql.gz").is_file()
        assert (backup_dir / "postgres.sql.gz").stat().st_size > 0
        assert (backup_dir / "manifest.json").is_file()
        # Workspace dir doesn't exist → archive skipped per design.
        assert not (backup_dir / "workspace.tar.gz").exists()
        # Sha256 recorded in manifest.
        import json

        manifest = json.loads((backup_dir / "manifest.json").read_text())
        assert len(manifest["checksums"]["postgres.sql.gz"]) == 64
        assert manifest["has_workspace"] is False

        # Step 3: drop the sentinel table.
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP TABLE "{table}"'))

        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT 1 FROM information_schema.tables "
                        "WHERE table_name = :t"
                    ),
                    {"t": table},
                )
            ).first()
        assert row is None, "sentinel table should be gone before restore"

        # Step 4: restore. The dump uses --clean --if-exists so the restore
        # replays DROP+CREATE for every object — including our sentinel.
        # The actor must be a UUID string (validated by the task layer).
        actor_id = str(uuid.uuid4())
        restore_result = task_module.restore_backup_task.run(
            name=result["name"], actor_user_id=actor_id
        )
        assert restore_result["mismatch"] is False

        # Sentinel row must be back.
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text(f'SELECT v FROM "{table}" WHERE k = :k'),
                    {"k": "sentinel"},
                )
            ).first()
        assert row is not None
        assert row[0] == sentinel_value

        # Audit rows recorded for the round-trip.
        actions = [getattr(o, "action", "") for o in accumulator.all_added]
        assert "backup.completed" in actions
        assert "backup.restored" in actions
    finally:
        # Always drop the sentinel table (idempotent).
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP TABLE IF EXISTS "{table}"'))
        await engine.dispose()
