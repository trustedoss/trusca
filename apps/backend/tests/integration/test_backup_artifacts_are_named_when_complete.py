# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""A half-written artifact must not carry the name a restore looks for.

The manifest is written last, so a killed run leaves a directory the listing
marks incomplete, and the restore refuses it. That covers the case where the
run dies. It does not cover what the file itself is: `postgres.sql.gz` sitting
in a backup directory is a name that means "this is the dump", and a process
that was killed while writing it leaves no Python running to say otherwise.

So the artifacts are written under a temporary name and moved into place once
they are complete. The move is a rename inside one directory, which is atomic:
the name either does not exist or names a finished file, with nothing in
between.

Driven against real files rather than mocks, because what is being asserted is
what is on disk after each step.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._db_required import migrate_to_head

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


@pytest.fixture
def backups_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "backups"
    root.mkdir()
    monkeypatch.setenv("BACKUPS_ROOT", str(root))
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path / "workspace"))
    return root


def test_a_dump_killed_partway_leaves_no_file_under_the_real_name(
    backups_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The name is the claim, and a killed write must not make it.

    `_run_pg_dump` is made to fail after writing, which is what a process
    killed mid-stream looks like from the caller's side. The bytes are on disk
    either way; what matters is which name they are under.
    """
    from tasks import backup as task

    written: dict[str, Path] = {}

    def _writes_then_dies(target: Path) -> None:
        target.write_bytes(b"half a dump")
        written["target"] = target
        raise task.BackupTaskError("killed partway")

    monkeypatch.setattr(task, "_run_pg_dump", _writes_then_dies)

    with pytest.raises(task.BackupTaskError):
        task._run_backup(None, kind="manual", actor_user_id=None)

    assert written.get("target"), "the stand-in never ran"
    assert written["target"].name.endswith(".partial"), (
        f"the dump was written straight to {written['target'].name}, so a "
        "process killed mid-write leaves a truncated file under the name a "
        "restore reads"
    )


def test_a_finished_dump_is_moved_into_place(backups_dir: Path, monkeypatch) -> None:
    """And the ordinary path still produces the names everything else expects."""
    from tasks import backup as task

    def _writes_a_dump(target: Path) -> None:
        target.write_bytes(b"a whole dump")

    monkeypatch.setattr(task, "_run_pg_dump", _writes_a_dump)
    monkeypatch.setattr(task, "_alembic_head", lambda: "abc1234")

    result = task._run_backup(None, kind="manual", actor_user_id=None)

    directory = backups_dir / result["name"]
    assert (directory / "postgres.sql.gz").is_file()
    assert (directory / "manifest.json").is_file()
    assert not list(directory.glob("*.partial")), (
        f"a temporary name was left behind: {[p.name for p in directory.iterdir()]}"
    )
