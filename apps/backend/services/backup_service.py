"""
Admin backup service — Phase 6 chore PR #19.

Pure (FastAPI-free) helpers that the route layer and the Celery task share:

  - :func:`list_backups`   — scan ``backups/`` and return ``BackupInfo`` rows.
  - :func:`get_backup_path` — validate ``name`` and return the absolute path
    to the backup directory.
  - :func:`delete_backup`  — recursively remove a backup directory.
  - :func:`compute_retention_cutoff` / :func:`prune_auto_backups` — 7-day
    retention helpers used by the daily ``run_backup_task`` retention pass.

Security:
  - Every ``name`` parameter is run through ``_NAME_RE`` (anchored regex).
    Anything that does not match the ``{auto,manual}-YYYYMMDDTHHMMSSZ``
    shape is rejected with :class:`BackupNotFoundError`. This blocks path
    traversal (``..``), shell-injection fragments (``;rm``), null bytes,
    and absolute paths from ever touching the filesystem.
  - We resolve the candidate path with ``Path.resolve()`` and verify it
    lies inside ``backups/``. Even if the regex were ever loosened, the
    realpath check would still contain the directory blast radius.

CLAUDE.md core rule #11 — environment variables are read at call time inside
:func:`backups_root`. Do not cache at module import.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import structlog

from schemas.backup import BackupInfo

log = structlog.get_logger("admin.backup.service")

# Anchored to prevent prefix / suffix smuggling. The script writes timestamps
# as ``YYYYMMDDTHHMMSSZ`` (UTC, ``T`` literal, ``Z`` literal); the regex
# matches that exactly. Auto + manual prefixes mirror the two ``kind`` values
# the task accepts. Any other shape -> BackupNotFoundError.
_NAME_RE = re.compile(r"^(auto|manual)-\d{8}T\d{6}Z$")

# Retention window for auto backups (7 days, per CLAUDE.md backup spec).
_AUTO_RETENTION_DAYS = 7


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class BackupNotFoundError(Exception):
    """Raised when a backup name fails validation or does not exist on disk."""


class BackupServiceError(Exception):
    """Generic admin-backup service error (path resolution / IO)."""


# ---------------------------------------------------------------------------
# Configuration accessors
# ---------------------------------------------------------------------------


def backups_root() -> Path:
    """Return the absolute path of the ``backups/`` directory.

    ``BACKUPS_ROOT`` env override exists primarily for tests — production
    leaves it unset so the path resolves relative to the repo root, matching
    ``scripts/backup.sh``.
    """
    raw = os.getenv("BACKUPS_ROOT", "backups")
    return Path(raw).resolve()


# ---------------------------------------------------------------------------
# Validation + resolution
# ---------------------------------------------------------------------------


def _validate_name(name: str) -> Literal["auto", "manual"]:
    """
    Run ``name`` through the anchored regex.

    Returns the parsed ``kind`` (``auto`` / ``manual``) on success.
    Raises :class:`BackupNotFoundError` on any rejection — we deliberately
    use the not-found exception (not ``ValueError``) so adversarial probing
    looks the same to the caller as a missing directory: existence-hide
    extends to the validation layer, not just the disk layer.
    """
    if not isinstance(name, str):
        raise BackupNotFoundError("invalid backup name")
    # Strip nothing — leading whitespace would not match _NAME_RE anyway, and
    # silently stripping would let ``" auto-..."`` pass when the operator
    # almost certainly meant something else.
    if not _NAME_RE.fullmatch(name):
        raise BackupNotFoundError("invalid backup name")
    kind = name.split("-", 1)[0]
    return "auto" if kind == "auto" else "manual"


def get_backup_path(name: str) -> Path:
    """
    Validate ``name`` and return the absolute path to the backup directory.

    Raises :class:`BackupNotFoundError` when:
      - ``name`` does not match the regex,
      - the resolved path escapes ``backups/`` (defence in depth),
      - the directory does not exist.
    """
    _validate_name(name)
    root = backups_root()
    candidate = (root / name).resolve()
    # Defence in depth: even with the regex, a future change could let a
    # crafted name escape. Realpath containment guarantees we never operate
    # on a path outside backups/.
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise BackupNotFoundError("invalid backup name") from exc
    if not candidate.is_dir():
        raise BackupNotFoundError(f"backup not found: {name}")
    return candidate


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def _parse_manifest(path: Path) -> dict[str, object]:
    """Read ``manifest.json`` from a backup dir, return ``{}`` on failure.

    A missing / corrupt manifest must not crash the listing — the operator
    still needs to see the directory so they can delete it.
    """
    manifest_path = path / "manifest.json"
    if not manifest_path.is_file():
        return {}
    try:
        with manifest_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("admin.backup.manifest_unreadable", path=str(path), error=str(exc))
        return {}
    return data if isinstance(data, dict) else {}


def _dir_size_bytes(path: Path) -> int:
    """Sum sizes of all regular files under ``path`` (non-recursive symlinks)."""
    total = 0
    try:
        for entry in path.rglob("*"):
            if entry.is_file() and not entry.is_symlink():
                try:
                    total += entry.stat().st_size
                except OSError:
                    # File may have been deleted mid-walk; skip.
                    continue
    except OSError as exc:
        log.warning("admin.backup.size_walk_failed", path=str(path), error=str(exc))
    return total


def _backup_info_from_dir(path: Path) -> BackupInfo | None:
    """Build a :class:`BackupInfo` row from a backup directory, or None."""
    name = path.name
    try:
        kind = _validate_name(name)
    except BackupNotFoundError:
        # Non-conforming directory (e.g. operator-created scratch). Skip.
        return None

    manifest = _parse_manifest(path)
    db_revision_raw = manifest.get("alembic_head")
    db_revision = (
        str(db_revision_raw)
        if isinstance(db_revision_raw, str) and db_revision_raw and db_revision_raw != "unknown"
        else None
    )

    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    except OSError:
        mtime = datetime.now(tz=UTC)

    return BackupInfo(
        name=name,
        kind=kind,
        created_at=mtime,
        size_bytes=_dir_size_bytes(path),
        db_revision=db_revision,
    )


def list_backups() -> list[BackupInfo]:
    """
    Return all backups under ``backups/``, newest first.

    The directory may not exist yet (fresh deployment with no backups). We
    return an empty list in that case rather than raising — listing is a
    read-only operation and "no backups yet" is a normal state.
    """
    root = backups_root()
    if not root.is_dir():
        return []

    items: list[BackupInfo] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        info = _backup_info_from_dir(child)
        if info is not None:
            items.append(info)
    items.sort(key=lambda i: i.created_at, reverse=True)
    return items


# ---------------------------------------------------------------------------
# Mutation
# ---------------------------------------------------------------------------


def delete_backup(name: str) -> None:
    """
    Recursively remove a backup directory.

    Validates ``name`` through :func:`get_backup_path` (which enforces the
    regex + realpath containment), then ``shutil.rmtree``s the directory.
    Raises :class:`BackupNotFoundError` if the name is invalid or the
    directory does not exist.
    """
    target = get_backup_path(name)
    shutil.rmtree(target)
    log.warning("admin.backup.deleted", name=name, path=str(target))


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------


def compute_retention_cutoff(
    *,
    now: datetime | None = None,
    days: int = _AUTO_RETENTION_DAYS,
) -> datetime:
    """Return the UTC datetime past which auto-backups should be pruned."""
    base = now if now is not None else datetime.now(tz=UTC)
    return base - timedelta(days=days)


def prune_auto_backups(
    *,
    now: datetime | None = None,
    days: int = _AUTO_RETENTION_DAYS,
) -> list[str]:
    """
    Delete ``auto-*`` backups older than ``days``.

    Returns the list of names that were deleted. **Manual backups are NEVER
    pruned** — operators may keep them indefinitely as known-good restore
    points.

    The function is idempotent: re-running it with the same now/days
    produces an empty list once the cutoff has passed.
    """
    cutoff = compute_retention_cutoff(now=now, days=days)
    pruned: list[str] = []
    for info in list_backups():
        if info.kind != "auto":
            continue
        if info.created_at < cutoff:
            try:
                delete_backup(info.name)
                pruned.append(info.name)
            except BackupNotFoundError:
                # Concurrent prune / hand-deletion — skip silently.
                continue
    return pruned


__all__ = [
    "BackupNotFoundError",
    "BackupServiceError",
    "backups_root",
    "compute_retention_cutoff",
    "delete_backup",
    "get_backup_path",
    "list_backups",
    "prune_auto_backups",
]
