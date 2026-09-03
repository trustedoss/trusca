# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""The backup subprocess cannot outlast its timeout or block on a full pipe.

``BACKUP_SUBPROCESS_TIMEOUT`` is documented and settable, and the code reads it
and passes it to ``proc.wait``. It still bounded nothing, because the two calls
before that wait -- copying stdout and reading stderr to EOF -- do not return
until the child has closed its pipes, which is to say until the child has
finished. A pg_dump that stalls on network storage or a lock never reaches the
wait, and the ``kill()`` in its timeout handler is unreachable. Nightly backups
then hold a worker slot forever, and because the task never raises, nothing is
recorded as failed.

The same two calls also deadlock: with stdout being copied and stderr unread,
a child that fills the stderr pipe buffer stops writing, and the copy it is
blocking on never ends. The restore path opens stdout as a pipe and reads it
nowhere at all, which is the same trap with `--quiet` standing in front of it.

Driven with real child processes. ``pg_dump`` and ``psql`` are invoked by bare
name, so a stand-in earlier on PATH is enough, and a fake process object cannot
reproduce any of this: what fills is an operating-system pipe buffer.
"""

from __future__ import annotations

import os
import stat
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

#: Comfortably past the 64 KiB a pipe buffer typically holds, so the child
#: blocks on the write rather than fitting inside it.
_FLOOD_BYTES = 1_000_000


def _install_stand_in(directory: Path, name: str, body: str) -> None:
    """Put an executable Python script on PATH under a command's name."""
    path = directory / name
    path.write_text(f"#!{sys.executable}\n{body}")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def stand_in_bin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "bin"
    directory.mkdir()
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ.get('PATH', '')}")
    return directory


@pytest.fixture(autouse=True)
def _short_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Three seconds, so a failure to enforce shows up as a hang, not a wait."""
    monkeypatch.setenv("BACKUP_SUBPROCESS_TIMEOUT", "3")


def test_a_stalled_dump_is_killed_within_the_timeout(
    stand_in_bin: Path, tmp_path: Path
) -> None:
    """The timeout has to bound the child, not just be passed to a call.

    The stand-in writes nothing and never exits, which is what a dump blocked
    on storage or on a lock looks like from here. Before this change the first
    read blocked ahead of the wait, so the timeout never ran and the task hung
    for as long as the process lived.
    """
    from tasks.backup import BackupTaskError, _run_pg_dump

    _install_stand_in(
        stand_in_bin,
        "pg_dump",
        "import time\nwhile True:\n    time.sleep(60)\n",
    )

    started = time.monotonic()
    with pytest.raises(BackupTaskError) as failure:
        _run_pg_dump(tmp_path / "out.sql.gz")
    elapsed = time.monotonic() - started

    assert "timed out" in str(failure.value).lower(), failure.value
    assert elapsed < 30, (
        f"the dump ran for {elapsed:.1f}s against a 3s timeout, so the timeout "
        "is not bounding anything"
    )


def test_a_dump_that_floods_stderr_still_completes(
    stand_in_bin: Path, tmp_path: Path
) -> None:
    """stderr has to be drained while stdout is being copied.

    A dump emitting a megabyte of warnings fills the stderr pipe, stops
    writing, and never finishes the stdout the copy is waiting on. Both sides
    wait for the other. Reproduced with a stand-in that writes more than a pipe
    holds before it finishes its output.
    """
    from tasks.backup import _run_pg_dump

    _install_stand_in(
        stand_in_bin,
        "pg_dump",
        "import sys\n"
        f"sys.stderr.write('warning: noise\\n' * {_FLOOD_BYTES // 16})\n"
        "sys.stderr.flush()\n"
        "sys.stdout.write('-- dump body\\n')\n"
        "sys.stdout.flush()\n",
    )

    target = tmp_path / "flooded.sql.gz"
    started = time.monotonic()
    _run_pg_dump(target)
    elapsed = time.monotonic() - started

    assert target.is_file() and target.stat().st_size > 0
    assert elapsed < 30, f"took {elapsed:.1f}s: the stderr pipe filled and both sides waited"


def test_a_restore_whose_child_writes_to_stdout_still_completes(
    stand_in_bin: Path, tmp_path: Path
) -> None:
    """The restore opens stdout as a pipe and reads it nowhere.

    psql is run with ``--quiet``, which is why this has not bitten yet. Relying
    on a flag to keep a pipe under 64 KiB is relying on nobody changing the
    flag, and on no version ever becoming chattier.
    """
    import gzip

    from tasks.backup import _run_psql_restore

    _install_stand_in(
        stand_in_bin,
        "psql",
        "import sys\n"
        f"sys.stdout.write('NOTICE: relation exists\\n' * {_FLOOD_BYTES // 24})\n"
        "sys.stdout.flush()\n"
        "sys.stdin.read()\n",
    )

    source = tmp_path / "restore.sql.gz"
    with gzip.open(source, "wb") as handle:
        handle.write(b"SELECT 1;\n" * 1000)

    started = time.monotonic()
    _run_psql_restore(source)
    elapsed = time.monotonic() - started

    assert elapsed < 30, f"took {elapsed:.1f}s: psql filled its stdout pipe and stopped"
