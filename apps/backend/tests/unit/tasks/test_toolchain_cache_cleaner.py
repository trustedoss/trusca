# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Unit tests for the toolchain cache cleaner.

The task exists because nothing in the product ever deleted a dependency
cache: they grow with the number of distinct dependencies a corpus has pulled,
which took 4.3 GB in 48 hours on a 26 GB partition shared with Postgres. So
the tests that matter are the two that bound it (over the cap it reclaims,
under the cap it does not) and the one that keeps it safe (a cache being
written to right now is never removed, whatever its size).
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from tasks.toolchain_cache_cleaner import _measure, toolchain_cache_cleaner_task


def _fill(path: Path, *, kib: int, age_seconds: float = 0.0) -> Path:
    """Create ``path`` holding a file of roughly ``kib`` KiB, aged if asked."""
    path.mkdir(parents=True, exist_ok=True)
    blob = path / "artifact.jar"
    blob.write_bytes(b"x" * (kib * 1024))
    if age_seconds:
        old = time.time() - age_seconds
        os.utime(blob, (old, old))
    return path


def _configure(
    monkeypatch: pytest.MonkeyPatch,
    *,
    roots: list[Path],
    cap_bytes: int,
    idle_seconds: int = 900,
) -> None:
    monkeypatch.setenv("TOOLCHAIN_CACHE_ROOTS", ",".join(str(p) for p in roots))
    monkeypatch.setenv("TOOLCHAIN_CACHE_MAX_BYTES", str(cap_bytes))
    monkeypatch.setenv("TOOLCHAIN_CACHE_IDLE_SECONDS", str(idle_seconds))


# ---------------------------------------------------------------------------
# _measure
# ---------------------------------------------------------------------------


def test_measure_reports_size_and_idle_time(tmp_path: Path) -> None:
    root = _fill(tmp_path / "m2", kib=16, age_seconds=3600)

    measured = _measure(root, now=time.time())

    assert measured is not None
    assert measured.size_bytes >= 16 * 1024
    assert measured.idle_seconds == pytest.approx(3600, abs=60)


def test_measure_returns_none_for_a_cache_that_was_never_created(
    tmp_path: Path,
) -> None:
    """A deployment that never scanned a Go project has no Go module cache."""
    assert _measure(tmp_path / "absent", now=time.time()) is None


def test_measure_treats_an_empty_tree_as_never_in_use(tmp_path: Path) -> None:
    """An empty cache has no newest file, so it cannot belong to a live scan."""
    empty = tmp_path / "empty"
    empty.mkdir()

    measured = _measure(empty, now=time.time())

    assert measured is not None
    assert measured.size_bytes == 0
    assert measured.idle_seconds == float("inf")


# ---------------------------------------------------------------------------
# The cap
# ---------------------------------------------------------------------------


def test_under_the_cap_nothing_is_touched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cache earning its keep is the normal state, not a condition to fix."""
    root = _fill(tmp_path / "m2", kib=16, age_seconds=7200)
    _configure(monkeypatch, roots=[root], cap_bytes=10 * 1024 * 1024)

    out = toolchain_cache_cleaner_task()

    assert out["reclaimed"] == []
    assert root.exists()


def test_over_the_cap_an_idle_cache_is_reclaimed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _fill(tmp_path / "m2", kib=64, age_seconds=7200)
    _configure(monkeypatch, roots=[root], cap_bytes=1024)

    out = toolchain_cache_cleaner_task()

    assert out["reclaimed"] == [str(root)]
    assert out["reclaimed_bytes"] >= 64 * 1024
    assert not root.exists()


def test_the_largest_cache_goes_first_and_only_as_far_as_the_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fewest deletions that get back under the cap = fewest slow scans after."""
    big = _fill(tmp_path / "m2", kib=256, age_seconds=7200)
    small = _fill(tmp_path / "npm", kib=16, age_seconds=7200)
    # Removing `big` alone leaves `small`, which is already under the cap.
    _configure(monkeypatch, roots=[big, small], cap_bytes=64 * 1024)

    out = toolchain_cache_cleaner_task()

    assert out["reclaimed"] == [str(big)]
    assert not big.exists()
    assert small.exists(), "a second deletion would have bought nothing"


# ---------------------------------------------------------------------------
# Never pull a cache out from under a running scan
# ---------------------------------------------------------------------------


def test_a_cache_being_written_to_is_spared_however_large(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dependency resolution writes continuously, so a fresh mtime means busy.

    Reclaiming here would fail the scan mid-resolve rather than slow it down,
    which is a worse outcome than being over the cap for another six hours.
    """
    busy = _fill(tmp_path / "m2", kib=256)  # written just now
    _configure(monkeypatch, roots=[busy], cap_bytes=1024, idle_seconds=900)

    out = toolchain_cache_cleaner_task()

    assert out["reclaimed"] == []
    assert out["skipped_busy"] == [str(busy)]
    assert busy.exists()
    assert out["total_bytes"] > out["cap_bytes"], "the pass admits it is over"


def test_a_busy_cache_does_not_stop_an_idle_one_being_reclaimed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    busy = _fill(tmp_path / "m2", kib=256)
    idle = _fill(tmp_path / "npm", kib=128, age_seconds=7200)
    _configure(monkeypatch, roots=[busy, idle], cap_bytes=1024, idle_seconds=900)

    out = toolchain_cache_cleaner_task()

    assert out["reclaimed"] == [str(idle)]
    assert busy.exists()
    assert not idle.exists()


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_zero_cap_disables_the_cleaner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The opt-out for a deployment whose caches have a disk of their own."""
    root = _fill(tmp_path / "m2", kib=256, age_seconds=7200)
    _configure(monkeypatch, roots=[root], cap_bytes=0)

    out = toolchain_cache_cleaner_task()

    assert out["reclaimed"] == []
    assert root.exists()


def test_every_queue_gets_its_own_cleaner_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each worker can only clean the filesystem it is running on.

    The task walks and deletes local paths, and each worker mounts its own
    cache volume. A single beat entry would fall through to the default
    queue, leaving worker-scan -- which holds the larger cache, being the one
    that resolves dependency graphs -- growing unbounded with nothing
    scheduled to touch it.
    """
    from tasks.celery_app import _DEFAULT_QUEUE, _SCAN_QUEUE, celery_app

    entries = [
        entry
        for entry in celery_app.conf.beat_schedule.values()
        if entry["task"] == "trustedoss.toolchain_cache_cleaner"
    ]
    queues = {entry.get("options", {}).get("queue") for entry in entries}

    assert queues == {_DEFAULT_QUEUE, _SCAN_QUEUE}


def test_a_configured_cache_that_does_not_exist_is_not_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Most deployments never touch most ecosystems."""
    present = _fill(tmp_path / "m2", kib=16, age_seconds=7200)
    _configure(
        monkeypatch,
        roots=[present, tmp_path / "never-scanned-go"],
        cap_bytes=10 * 1024 * 1024,
    )

    out = toolchain_cache_cleaner_task()

    assert out["reclaimed"] == []
    assert present.exists()
