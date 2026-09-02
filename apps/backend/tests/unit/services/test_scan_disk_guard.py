"""
Unit tests for the disk guard introduced in Phase 6 PR #19 — scans must 503
when the workspace volume is past the hard limit.

Also covers how ``DISK_HARD_LIMIT_PCT`` is parsed (#36) and that the guard
runs off the event loop (#41).
"""

from __future__ import annotations

import os
import threading
from types import SimpleNamespace

import pytest

from services.scan_service import (
    ScanDiskFull,
    _check_disk_guard,
    _disk_hard_limit_pct,
    _guarded_paths,
    check_disk_guard,
)


def _fake_statvfs(*, blocks: int, bavail: int, frsize: int = 4096) -> SimpleNamespace:
    """Build a fake `os.statvfs_result`-shaped object."""
    return SimpleNamespace(f_blocks=blocks, f_bavail=bavail, f_frsize=frsize)


def test_disk_hard_limit_pct_default() -> None:
    """Default hard limit is 95% (matches PR #19 spec)."""
    if "DISK_HARD_LIMIT_PCT" in os.environ:
        del os.environ["DISK_HARD_LIMIT_PCT"]
    assert _disk_hard_limit_pct() == 95.0


def test_disk_hard_limit_pct_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISK_HARD_LIMIT_PCT", "80.0")
    assert _disk_hard_limit_pct() == 80.0


def test_check_disk_guard_passes_when_below_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """50% used → no exception."""
    monkeypatch.setenv("DISK_HARD_LIMIT_PCT", "95.0")
    monkeypatch.setattr(os, "statvfs", lambda _p: _fake_statvfs(blocks=100, bavail=50))
    # No exception expected.
    _check_disk_guard()


def test_check_disk_guard_blocks_at_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """98% used + 95 limit → ScanDiskFull."""
    monkeypatch.setenv("DISK_HARD_LIMIT_PCT", "95.0")
    # 100 blocks, 2 free → 98% used.
    monkeypatch.setattr(os, "statvfs", lambda _p: _fake_statvfs(blocks=100, bavail=2))
    with pytest.raises(ScanDiskFull):
        _check_disk_guard()


def test_check_disk_guard_passes_when_statvfs_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """If we can't read the filesystem, fall through (best-effort)."""

    def _raise(_p: str) -> None:
        raise OSError("no such directory")

    monkeypatch.setattr(os, "statvfs", _raise)
    # No exception expected — disk_guard_unavailable warning logged instead.
    _check_disk_guard()


def test_check_disk_guard_passes_when_total_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """Edge case: an unmounted/empty filesystem reports total=0; do not divide by zero."""
    monkeypatch.setattr(os, "statvfs", lambda _p: _fake_statvfs(blocks=0, bavail=0))
    _check_disk_guard()


def test_scan_disk_full_status_code_is_503() -> None:
    """ScanDiskFull maps to 503 so CI integrations know to retry later."""
    assert ScanDiskFull.status_code == 503


# ---------------------------------------------------------------------------
# #36: a misconfigured DISK_HARD_LIMIT_PCT must not reach the guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["ninety-five", "95%", "", "  ", "nan", "inf", "-inf"])
def test_disk_hard_limit_junk_falls_back_to_default(
    raw: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Junk yields the default instead of raising out of the guard.

    The webhook receiver calls this through ``capacity_guard_reason``, which
    catches only the two typed guard exceptions, a ``ValueError`` from here
    escaped as a 500 and the Git host answered by retrying the delivery.

    ``nan`` and the infinities parse as floats but break the ``>=`` comparison
    they feed, so they count as junk too.
    """
    monkeypatch.setenv("DISK_HARD_LIMIT_PCT", raw)
    assert _disk_hard_limit_pct() == 95.0


def test_disk_hard_limit_zero_is_clamped_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """``0`` used to make every delivery report skipped_disk_full, silently."""
    monkeypatch.setenv("DISK_HARD_LIMIT_PCT", "0")
    assert _disk_hard_limit_pct() == 50.0


def test_disk_hard_limit_above_hundred_is_clamped_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DISK_HARD_LIMIT_PCT", "150")
    assert _disk_hard_limit_pct() == 100.0


@pytest.mark.parametrize("raw", ["50", "100", "98.5"])
def test_disk_hard_limit_in_range_is_untouched(
    raw: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both bounds and the runbook's drain-a-full-volume value pass through."""
    monkeypatch.setenv("DISK_HARD_LIMIT_PCT", raw)
    assert _disk_hard_limit_pct() == float(raw)


def test_guard_still_runs_with_a_junk_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """End to end: junk config leaves a working guard, not a broken one."""
    monkeypatch.setenv("DISK_HARD_LIMIT_PCT", "ninety-five")
    monkeypatch.setattr(os, "statvfs", lambda _p: _fake_statvfs(blocks=100, bavail=50))
    _check_disk_guard()  # 50% used, default limit 95, passes.

    monkeypatch.setattr(os, "statvfs", lambda _p: _fake_statvfs(blocks=100, bavail=1))
    with pytest.raises(ScanDiskFull):
        _check_disk_guard()  # 99% used, still blocks.


# ---------------------------------------------------------------------------
# #41: the statvfs call belongs off the event loop
# ---------------------------------------------------------------------------


async def test_check_disk_guard_runs_statvfs_in_a_worker_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The syscall must not run on the loop thread.

    An unresponsive network mount would otherwise stall every request in the
    process, including the health endpoint that would show why.
    """
    loop_thread = threading.get_ident()
    seen: list[int] = []

    def _statvfs(_p: str) -> SimpleNamespace:
        seen.append(threading.get_ident())
        return _fake_statvfs(blocks=100, bavail=50)

    monkeypatch.setattr(os, "statvfs", _statvfs)
    await check_disk_guard()

    assert seen and loop_thread not in seen


async def test_check_disk_guard_propagates_scan_disk_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Moving to a thread must not swallow the guard's verdict."""
    monkeypatch.setenv("DISK_HARD_LIMIT_PCT", "95.0")
    monkeypatch.setattr(os, "statvfs", lambda _p: _fake_statvfs(blocks=100, bavail=2))
    with pytest.raises(ScanDiskFull):
        await check_disk_guard()


# ---------------------------------------------------------------------------
# The guard watches more than the workspace volume
#
# Reading only ``WORKSPACE_HOST_PATH`` is correct only while the workspace
# shares a filesystem with everything else a scan writes. Put the workspace on
# network storage (the documented way to scan a corpus bigger than one host's
# disk) and the guard reports the network volume while the toolchain caches
# fill the container's own. A 26 GB root partition hit 100% that way with the
# guard reporting 12%, and no scan was ever refused.
# ---------------------------------------------------------------------------


def test_guarded_paths_include_the_workspace_and_the_root_filesystem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKSPACE_HOST_PATH", "/mnt/nas/workspace")
    monkeypatch.delenv("DISK_GUARD_EXTRA_PATHS", raising=False)

    paths = _guarded_paths()

    assert paths[0] == "/mnt/nas/workspace", "workspace stays the headline path"
    assert "/" in paths


def test_guarded_paths_take_extra_mounts_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operators who split caches onto their own mount can guard those too."""
    monkeypatch.setenv("WORKSPACE_HOST_PATH", "/mnt/nas/workspace")
    monkeypatch.setenv("DISK_GUARD_EXTRA_PATHS", " /, /var/cache/toolchains ,")

    assert _guarded_paths() == [
        "/mnt/nas/workspace",
        "/",
        "/var/cache/toolchains",
    ]


def test_guarded_paths_do_not_repeat_the_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single-filesystem deployment naming the same path twice reads it once."""
    monkeypatch.setenv("WORKSPACE_HOST_PATH", "/")
    monkeypatch.setenv("DISK_GUARD_EXTRA_PATHS", "/")

    assert _guarded_paths() == ["/"]


def test_guarded_paths_can_be_narrowed_back_to_the_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty list is how an operator opts out, not a parse failure."""
    monkeypatch.setenv("WORKSPACE_HOST_PATH", "/mnt/nas/workspace")
    monkeypatch.setenv("DISK_GUARD_EXTRA_PATHS", "")

    assert _guarded_paths() == ["/mnt/nas/workspace"]


def test_guard_blocks_on_a_full_root_while_the_workspace_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exact shape of the outage: roomy network workspace, full root."""
    monkeypatch.setenv("DISK_HARD_LIMIT_PCT", "95.0")
    monkeypatch.setenv("WORKSPACE_HOST_PATH", "/mnt/nas/workspace")
    monkeypatch.delenv("DISK_GUARD_EXTRA_PATHS", raising=False)

    def _statvfs(path: str) -> SimpleNamespace:
        if path == "/mnt/nas/workspace":
            return _fake_statvfs(blocks=100, bavail=88)  # 12% used
        return _fake_statvfs(blocks=100, bavail=0)  # 100% used

    monkeypatch.setattr(os, "statvfs", _statvfs)

    with pytest.raises(ScanDiskFull) as excinfo:
        _check_disk_guard()

    # The operator has to know WHICH volume to drain; "workspace disk usage"
    # would have sent them at the one that was fine.
    assert "/" in str(excinfo.value)
    assert "12" not in str(excinfo.value)


def test_guard_skips_an_unreadable_path_and_keeps_checking_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One missing mount must not blind the guard to the others."""
    monkeypatch.setenv("DISK_HARD_LIMIT_PCT", "95.0")
    monkeypatch.setenv("WORKSPACE_HOST_PATH", "/mnt/nas/workspace")
    monkeypatch.delenv("DISK_GUARD_EXTRA_PATHS", raising=False)

    def _statvfs(path: str) -> SimpleNamespace:
        if path == "/mnt/nas/workspace":
            raise OSError("not mounted")
        return _fake_statvfs(blocks=100, bavail=1)  # 99% used

    monkeypatch.setattr(os, "statvfs", _statvfs)

    with pytest.raises(ScanDiskFull):
        _check_disk_guard()


def test_guard_passes_when_every_guarded_volume_has_room(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DISK_HARD_LIMIT_PCT", "95.0")
    monkeypatch.setenv("WORKSPACE_HOST_PATH", "/mnt/nas/workspace")
    monkeypatch.delenv("DISK_GUARD_EXTRA_PATHS", raising=False)
    monkeypatch.setattr(os, "statvfs", lambda _p: _fake_statvfs(blocks=100, bavail=50))

    _check_disk_guard()
