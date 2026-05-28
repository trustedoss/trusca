"""
Unit tests for ``services.backup_service`` — Phase 6 chore PR #19.

Coverage targets:
  - Adversarial ``name`` inputs are rejected at the regex layer (path
    traversal, command injection, null bytes, control chars).
  - 7-day retention math is correct, **and manual backups are NEVER pruned**.
  - ``list_backups`` skips non-conforming directory names without raising.
  - ``get_backup_path`` containment guard catches realpath escapes.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from services.backup_service import (
    BackupNotFoundError,
    _validate_name,
    backups_root,
    compute_retention_cutoff,
    delete_backup,
    get_backup_path,
    list_backups,
    prune_auto_backups,
)


@pytest.fixture
def temp_backups_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point ``BACKUPS_ROOT`` at a fresh tmp dir for each test."""
    monkeypatch.setenv("BACKUPS_ROOT", str(tmp_path))
    return tmp_path


def _write_backup(
    root: Path,
    *,
    name: str,
    alembic_head: str | None = "abc123",
    mtime: datetime | None = None,
    extra_size: int = 0,
) -> Path:
    """Create a fake backup directory with a manifest + dummy artifacts."""
    target = root / name
    target.mkdir(parents=True, exist_ok=True)
    (target / "postgres.sql.gz").write_bytes(b"\x1f\x8b\x08fake" + b"x" * extra_size)
    (target / "workspace.tar.gz").write_bytes(b"\x1f\x8b\x08fake")
    manifest: dict[str, object] = {"timestamp": "2026-05-09-000000"}
    if alembic_head is not None:
        manifest["alembic_head"] = alembic_head
    (target / "manifest.json").write_text(json.dumps(manifest))
    if mtime is not None:
        ts = mtime.timestamp()
        import os

        os.utime(target, (ts, ts))
    return target


# ---------------------------------------------------------------------------
# Name validation — adversarial parametrize
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_name",
    [
        "",
        "..",
        "../etc/passwd",
        "auto-../etc/passwd",
        "/etc/passwd",
        "auto-20260509T000000Z;rm -rf /",
        "auto-20260509T000000Z\x00",
        "auto-20260509T000000Z\n",
        "manual-20260509T000000Z\r",
        "manual",
        "auto-",
        "AUTO-20260509T000000Z",
        "auto-2026-05-09T000000Z",
        "auto-20260509T000000",  # missing Z
        "auto-20260509T000000Zextra",
        " auto-20260509T000000Z",  # leading space
        "rogue-20260509T000000Z",
        "javascript:alert(1)",
    ],
)
def test_validate_name_rejects_adversarial_inputs(bad_name: str) -> None:
    with pytest.raises(BackupNotFoundError):
        _validate_name(bad_name)


@pytest.mark.parametrize(
    "good_name,expected_kind",
    [
        ("auto-20260509T000000Z", "auto"),
        ("manual-20260509T235959Z", "manual"),
        ("auto-99991231T235959Z", "auto"),
    ],
)
def test_validate_name_accepts_well_formed_inputs(good_name: str, expected_kind: str) -> None:
    assert _validate_name(good_name) == expected_kind


def test_validate_name_rejects_non_string() -> None:
    with pytest.raises(BackupNotFoundError):
        _validate_name(None)  # type: ignore[arg-type]
    with pytest.raises(BackupNotFoundError):
        _validate_name(123)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# get_backup_path
# ---------------------------------------------------------------------------


def test_get_backup_path_returns_directory_when_present(temp_backups_root: Path) -> None:
    _write_backup(temp_backups_root, name="manual-20260509T120000Z")
    resolved = get_backup_path("manual-20260509T120000Z")
    assert resolved.is_dir()
    assert resolved.parent == backups_root()


def test_get_backup_path_raises_when_directory_missing(temp_backups_root: Path) -> None:
    # Name passes the regex but no directory on disk.
    with pytest.raises(BackupNotFoundError):
        get_backup_path("manual-20260509T120000Z")


def test_get_backup_path_raises_on_invalid_name(temp_backups_root: Path) -> None:
    # Even with a directory existing under a hand-crafted name, the regex
    # gate must fire first.
    (temp_backups_root / "..").mkdir(exist_ok=True)
    with pytest.raises(BackupNotFoundError):
        get_backup_path("../etc/passwd")


# ---------------------------------------------------------------------------
# delete_backup
# ---------------------------------------------------------------------------


def test_delete_backup_removes_directory(temp_backups_root: Path) -> None:
    target = _write_backup(temp_backups_root, name="manual-20260509T120000Z")
    assert target.is_dir()
    delete_backup("manual-20260509T120000Z")
    assert not target.exists()


def test_delete_backup_raises_on_missing(temp_backups_root: Path) -> None:
    with pytest.raises(BackupNotFoundError):
        delete_backup("manual-20260509T120000Z")


def test_delete_backup_rejects_path_traversal(temp_backups_root: Path) -> None:
    # A real directory at the traversal target — confirms the regex gate
    # fires before any rmtree happens.
    sibling = temp_backups_root.parent / "victim"
    sibling.mkdir(exist_ok=True)
    try:
        with pytest.raises(BackupNotFoundError):
            delete_backup("../victim")
        assert sibling.is_dir(), "victim directory must survive the rejected delete"
    finally:
        sibling.rmdir()


# ---------------------------------------------------------------------------
# list_backups
# ---------------------------------------------------------------------------


def test_list_backups_returns_empty_when_root_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("BACKUPS_ROOT", str(tmp_path / "does-not-exist"))
    assert list_backups() == []


def test_list_backups_skips_non_conforming_directories(temp_backups_root: Path) -> None:
    _write_backup(temp_backups_root, name="manual-20260509T120000Z")
    # Directories that look like operator scratch.
    (temp_backups_root / "scratch").mkdir()
    (temp_backups_root / "uploaded-20260509T120000Z").mkdir()
    items = list_backups()
    names = [i.name for i in items]
    assert names == ["manual-20260509T120000Z"]


def test_list_backups_orders_newest_first(temp_backups_root: Path) -> None:
    older = datetime(2026, 5, 1, tzinfo=UTC)
    newer = datetime(2026, 5, 9, tzinfo=UTC)
    _write_backup(temp_backups_root, name="manual-20260501T000000Z", mtime=older)
    _write_backup(temp_backups_root, name="auto-20260509T000000Z", mtime=newer)

    items = list_backups()
    assert [i.name for i in items] == [
        "auto-20260509T000000Z",
        "manual-20260501T000000Z",
    ]


def test_list_backups_handles_unreadable_manifest(temp_backups_root: Path) -> None:
    target = _write_backup(temp_backups_root, name="manual-20260509T120000Z")
    # Corrupt the manifest — listing must still surface the row.
    (target / "manifest.json").write_text("not json {{{")
    items = list_backups()
    assert len(items) == 1
    assert items[0].db_revision is None


def test_list_backups_treats_unknown_revision_as_none(temp_backups_root: Path) -> None:
    _write_backup(
        temp_backups_root,
        name="manual-20260509T120000Z",
        alembic_head="unknown",
    )
    items = list_backups()
    assert items[0].db_revision is None


def test_list_backups_computes_size_bytes(temp_backups_root: Path) -> None:
    _write_backup(temp_backups_root, name="manual-20260509T120000Z", extra_size=1024)
    items = list_backups()
    assert items[0].size_bytes >= 1024


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------


def test_compute_retention_cutoff_default_is_seven_days() -> None:
    now = datetime(2026, 5, 9, 12, 0, tzinfo=UTC)
    cutoff = compute_retention_cutoff(now=now)
    assert cutoff == now - timedelta(days=7)


def test_prune_auto_backups_removes_only_old_auto_backups(
    temp_backups_root: Path,
) -> None:
    now = datetime(2026, 5, 9, 12, 0, tzinfo=UTC)
    # 8 days old auto — must be pruned.
    _write_backup(
        temp_backups_root,
        name="auto-20260501T000000Z",
        mtime=now - timedelta(days=8),
    )
    # 1 day old auto — must survive.
    _write_backup(
        temp_backups_root,
        name="auto-20260508T000000Z",
        mtime=now - timedelta(days=1),
    )
    # 90 days old MANUAL — must survive (manual never pruned).
    _write_backup(
        temp_backups_root,
        name="manual-20260201T000000Z",
        mtime=now - timedelta(days=90),
    )

    pruned = prune_auto_backups(now=now)
    assert pruned == ["auto-20260501T000000Z"]

    surviving = sorted(i.name for i in list_backups())
    assert surviving == ["auto-20260508T000000Z", "manual-20260201T000000Z"]


def test_prune_auto_backups_idempotent(temp_backups_root: Path) -> None:
    now = datetime(2026, 5, 9, 12, 0, tzinfo=UTC)
    _write_backup(
        temp_backups_root,
        name="auto-20260501T000000Z",
        mtime=now - timedelta(days=8),
    )
    first = prune_auto_backups(now=now)
    assert first == ["auto-20260501T000000Z"]
    # Re-running prunes nothing — directory is gone.
    second = prune_auto_backups(now=now)
    assert second == []


def test_prune_auto_backups_with_custom_window(temp_backups_root: Path) -> None:
    now = datetime(2026, 5, 9, 12, 0, tzinfo=UTC)
    _write_backup(
        temp_backups_root,
        name="auto-20260507T000000Z",
        mtime=now - timedelta(days=2),
    )
    # 1-day window catches the 2-day-old entry.
    pruned = prune_auto_backups(now=now, days=1)
    assert pruned == ["auto-20260507T000000Z"]
