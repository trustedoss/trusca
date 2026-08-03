"""
Unit tests for the disk guard introduced in Phase 6 PR #19 — scans must 503
when the workspace volume is past the hard limit.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from services.scan_service import ScanDiskFull, _check_disk_guard, _disk_hard_limit_pct


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
