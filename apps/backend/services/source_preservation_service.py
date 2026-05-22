"""
Scan-source preservation — G3.1 (Protex-style source-tree view groundwork).

The scan pipeline deletes the scanned source after every scan (the ``finally:
shutil.rmtree(workspace)`` in ``tasks.scan_source``). To later render a file
tree + per-line license matches we must preserve, per scan, TWO things that die
with that workspace:

  1. the source tree itself (so the viewer can show files), and
  2. the **scancode result JSON** — the ONLY place per-line license-match data
     lives. The scancode adapter discards line numbers when it builds
     ``DetectedLicense`` rows, and ``license_findings`` keeps only ``spdx_id`` +
     ``source_path``. Lose the JSON and the per-line view is unrecoverable.

This module owns :func:`preserve_scan_source`, which tars ``source_dir`` with
**stdlib gzip** (NO zstd / native dependency — core constraint) and folds the
scancode JSON in as a reserved member ``.trustedoss/scancode.json``. The tarball
is written under ``{workspace_root()}/scan-sources/{project_id}/{scan_id}.tar.gz``.

Retention is **latest-succeeded-per-project**: a new scan supersedes the prior
tarball. The actual sweep is done by ``tasks.scan_source_cleaner`` (the retention
beat); this module just writes the new tarball atomically.

Security / robustness decisions (mirroring ``source_archive_service.py`` so the
security reviewer sees one shape, recorded here):

  - **Best-effort, never fatal.** Every public entry point returns ``None`` (or
    the path) and NEVER raises into the scan. A preservation failure (quota,
    over-cap tree, I/O error, weird member) is a degraded-output scenario, not a
    terminal scan failure — same philosophy as the scancode stage.

  - **Caps.** Two ceilings bound the disk footprint: a single-tarball byte cap
    (``SCAN_SOURCE_MAX_TARBALL_BYTES``) counted against the *actual* gzip bytes we
    write, and a per-project quota (``SCAN_SOURCE_PROJECT_QUOTA_BYTES``) summed
    over the project's existing ``*.tar.gz`` before we start. On exceed we skip +
    log; the partial temp file is deleted.

  - **Non-regular members skipped.** Symlinks, devices, fifos, sockets are never
    added to the tar (a preserved tree must not carry a symlink that a later
    extractor could follow out of the viewer sandbox, and a device member is
    meaningless). Only regular files and directories are archived.

  - **Path / zip-slip defence on read-side too.** Member arcnames are computed
    from the file's path *relative to* ``source_dir``; we resolve each candidate
    and require it to stay within ``source_dir`` so a symlink-followed walk (we do
    not follow symlinks, but defence in depth) cannot smuggle an absolute / ``..``
    arcname into the archive.

  - **Atomic write.** We write to a ``{scan_id}.tar.gz.{token}.tmp`` sibling and
    ``os.replace`` it over the final name only on success — a crashed / over-cap
    run never leaves a half-written tarball that a reader would treat as valid.

CLAUDE.md compliance:
  - Core rule #3: invoked from the Celery worker, never the request path.
  - Core rule #11: every limit is read via ``os.getenv`` at call time (through
    the ``core.config`` accessors), no module-level env caching.
  - §5: structlog JSON, one event per line; no file contents logged.
  - Core rule #2: no schema change — the tarball path rides on a free-form
    ``ScanArtifact`` row (``kind='source_tarball'``, ``String(32)``), so no
    Alembic migration is required.
"""

from __future__ import annotations

import os
import stat
import tarfile
import uuid
from pathlib import Path

import structlog

from core.config import (
    scan_source_max_tarball_bytes,
    scan_source_project_quota_bytes,
    workspace_root,
)

log = structlog.get_logger("source_preservation.service")

# Reserved arcname for the folded-in scancode result JSON. Namespaced under a
# dotted ``.trustedoss/`` prefix so it can never collide with a real source file
# the tree happens to contain (a repo with a top-level ``scancode.json`` keeps
# its own copy under its real path; ours always lives under ``.trustedoss/``).
SCANCODE_MEMBER_NAME = ".trustedoss/scancode.json"

# Streaming chunk for the size-counting copy of the scancode JSON into the tar.
_CHUNK_SIZE = 1024 * 1024  # 1 MiB

# Unix file-type bits (S_IFMT mask is 0o170000). Only regular files and
# directories are archived; everything else (symlink, fifo, char/block device,
# socket) is skipped — see module docstring.
_S_IFMT = 0o170000


# ---------------------------------------------------------------------------
# Domain exceptions (internal — never surface to an HTTP caller)
# ---------------------------------------------------------------------------


class SourcePreservationError(Exception):
    """Base class for preservation errors.

    These are caught internally by :func:`preserve_scan_source` and turned into
    a ``None`` return + a WARNING log — they never propagate into the scan.
    """


class PreservationQuotaExceeded(SourcePreservationError):
    """Adding this tarball would breach the per-project preserved-source quota."""


class PreservationTooLarge(SourcePreservationError):
    """The tarball exceeded ``SCAN_SOURCE_MAX_TARBALL_BYTES`` while being written."""


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def scan_sources_dir_for_project(project_id: uuid.UUID) -> Path:
    """Return the directory holding a project's preserved source tarballs."""
    return Path(workspace_root()) / "scan-sources" / str(project_id)


def scan_source_tarball_path(project_id: uuid.UUID, scan_id: uuid.UUID) -> Path:
    """Resolve the on-disk path for one scan's preserved source tarball.

    Both ids are formatted from real ``uuid.UUID`` objects by the caller, so
    there is no traversal surface here; the cleaner re-parses the stem as a UUID
    before any unlink as defence in depth.
    """
    return scan_sources_dir_for_project(project_id) / f"{scan_id}.tar.gz"


def _project_tarball_bytes(project_id: uuid.UUID) -> int:
    """Sum the on-disk size of every preserved ``*.tar.gz`` for the project.

    Best-effort: a file that vanishes mid-walk (a concurrent sweep) contributes
    0 rather than raising. Used by the per-project quota guard so a stale tarball
    still counts against the budget.
    """
    sources_dir = scan_sources_dir_for_project(project_id)
    if not sources_dir.is_dir():
        return 0
    total = 0
    for child in sources_dir.glob("*.tar.gz"):
        try:
            total += child.stat().st_size
        except OSError:  # pragma: no cover — concurrent delete race
            continue
    return total


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:  # pragma: no cover — best-effort cleanup
        pass


def _is_within(base: Path, target: Path) -> bool:
    """True iff ``target`` is ``base`` itself or strictly inside it.

    ``Path.is_relative_to`` splits on path components, so ``/work`` is not
    relative to ``/workevil``; we keep an explicit ``commonpath`` cross-check as
    defence in depth (mirrors ``source_archive_service._is_within``).
    """
    try:
        if target == base:
            return True
        if not target.is_relative_to(base):
            return False
        return os.path.commonpath([str(base), str(target)]) == str(base)
    except (ValueError, OSError):
        return False


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def preserve_scan_source(
    *,
    scan_id: uuid.UUID,
    project_id: uuid.UUID,
    source_dir: Path,
    scancode_json_path: Path | None,
) -> Path | None:
    """Tar ``source_dir`` + fold in the scancode JSON; return the tar path or None.

    Best-effort end to end: ANY failure (missing source dir, quota, over-cap
    tree, I/O error) returns ``None`` after a WARNING and leaves no partial file
    behind. The caller (the scan task) must treat ``None`` as "preservation
    skipped" and continue — the scan still succeeds.

    Steps:
      1. Verify ``source_dir`` exists (a fetch that produced nothing → skip).
      2. Per-project quota pre-check (cheap, fail fast before any tar work).
      3. Stream every *regular file* under ``source_dir`` into a gzip tar at a
         temp path, counting actual written bytes against the single-tarball cap.
         Non-regular members (symlink / device / fifo) are skipped.
      4. Fold the scancode JSON in as ``.trustedoss/scancode.json`` when present.
      5. ``os.replace`` the temp file over the final ``{scan_id}.tar.gz`` so the
         retained tarball is always complete (atomic overwrite on re-run).

    Returns:
        The final tarball ``Path`` on success, or ``None`` when preservation was
        skipped for any reason.
    """
    try:
        source_dir = source_dir.resolve()
        if not source_dir.is_dir():
            log.warning(
                "scan_source_preserve_no_source_dir",
                scan_id=str(scan_id),
                project_id=str(project_id),
            )
            return None

        quota = scan_source_project_quota_bytes()
        existing = _project_tarball_bytes(project_id)
        # With retention=latest the project's own prior tarball (the one this run
        # supersedes) still counts here; that is intentional — the quota bounds
        # the transient two-tarball window before the sweep / overwrite reclaims
        # the old one. We only refuse when the project is ALREADY at/over budget
        # before writing a single byte; the running cap below guards the rest.
        if existing >= quota:
            log.warning(
                "scan_source_preserve_quota_full",
                scan_id=str(scan_id),
                project_id=str(project_id),
                existing_bytes=existing,
                quota_bytes=quota,
            )
            return None

        dest = scan_source_tarball_path(project_id, scan_id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(f"{dest.name}.{uuid.uuid4().hex}.tmp")

        max_bytes = scan_source_max_tarball_bytes()
        try:
            files_added, scancode_added = _write_tarball(
                tmp_path=tmp,
                source_dir=source_dir,
                scancode_json_path=scancode_json_path,
                max_bytes=max_bytes,
            )
        except (PreservationTooLarge, PreservationQuotaExceeded) as exc:
            _unlink_quietly(tmp)
            log.warning(
                "scan_source_preserve_skipped",
                scan_id=str(scan_id),
                project_id=str(project_id),
                reason=type(exc).__name__,
                error=str(exc)[:300],
            )
            return None
        except OSError as exc:
            _unlink_quietly(tmp)
            log.warning(
                "scan_source_preserve_io_error",
                scan_id=str(scan_id),
                project_id=str(project_id),
                error=type(exc).__name__,
            )
            return None

        # Atomic publish: a reader only ever sees a fully-written tarball. This
        # also gives free overwrite-on-re-run (idempotency) since os.replace
        # clobbers any prior {scan_id}.tar.gz in one syscall.
        try:
            os.replace(tmp, dest)
        except OSError as exc:  # pragma: no cover — rename across a vanished dir
            _unlink_quietly(tmp)
            log.warning(
                "scan_source_preserve_publish_failed",
                scan_id=str(scan_id),
                project_id=str(project_id),
                error=type(exc).__name__,
            )
            return None

        size = dest.stat().st_size
        log.info(
            "scan_source_preserved",
            scan_id=str(scan_id),
            project_id=str(project_id),
            files=files_added,
            scancode_json=scancode_added,
            bytes=size,
        )
        return dest
    except Exception as exc:  # noqa: BLE001 — preservation must never fail a scan
        # Belt-and-suspenders: even an unexpected bug here (e.g. a tarfile edge
        # case we did not anticipate) degrades to "no tarball" rather than
        # turning a succeeded scan into a failure. The scancode stage uses the
        # same swallow-and-log contract.
        log.warning(
            "scan_source_preserve_unexpected_error",
            scan_id=str(scan_id),
            project_id=str(project_id),
            error=str(exc)[:300],
        )
        return None


# ---------------------------------------------------------------------------
# Tarball writer
# ---------------------------------------------------------------------------


def _write_tarball(
    *,
    tmp_path: Path,
    source_dir: Path,
    scancode_json_path: Path | None,
    max_bytes: int,
) -> tuple[int, bool]:
    """Write the gzip tarball at ``tmp_path``. Returns ``(files_added, scancode_added)``.

    Raises:
        PreservationTooLarge: the written gzip stream crossed ``max_bytes`` — the
            caller deletes the temp file and skips preservation.
        OSError: an I/O failure opening / writing the tar.
    """
    files_added = 0
    scancode_added = False

    with tarfile.open(tmp_path, mode="w:gz") as tar:
        # Deterministic walk for stable archives + a predictable size profile.
        for path in sorted(source_dir.rglob("*")):
            arcname = _safe_arcname(source_dir, path)
            if arcname is None:
                continue

            # Skip the reserved scancode slot if the source tree itself happens
            # to carry a ``.trustedoss/scancode.json`` — ours (the real result)
            # is folded in below and must win that arcname.
            if arcname == SCANCODE_MEMBER_NAME:
                continue

            try:
                lst = path.lstat()
            except OSError:  # pragma: no cover — vanished mid-walk
                continue

            file_type = stat.S_IFMT(lst.st_mode)
            if file_type == stat.S_IFDIR:
                # Directories are added as entries so an empty dir survives the
                # round-trip; ``recursive=False`` keeps tarfile from re-walking.
                _add_member(tar, path, arcname, recursive=False)
                continue
            if file_type != stat.S_IFREG:
                # Symlink / device / fifo / socket — skipped (see docstring).
                log.debug(
                    "scan_source_preserve_skip_non_regular",
                    arcname=arcname,
                    mode=oct(lst.st_mode),
                )
                continue

            _add_member(tar, path, arcname, recursive=False)
            files_added += 1

            _enforce_running_cap(tar, tmp_path=tmp_path, max_bytes=max_bytes)

        # Fold in the scancode result JSON last so its arcname always wins.
        if scancode_json_path is not None and scancode_json_path.is_file():
            _add_member(
                tar,
                scancode_json_path,
                SCANCODE_MEMBER_NAME,
                recursive=False,
            )
            scancode_added = True
            _enforce_running_cap(tar, tmp_path=tmp_path, max_bytes=max_bytes)

    # Final cap check after the gzip trailer is flushed on close.
    final_size = tmp_path.stat().st_size
    if final_size > max_bytes:
        raise PreservationTooLarge(
            f"preserved tarball is {final_size} bytes, over the "
            f"{max_bytes}-byte cap"
        )

    return files_added, scancode_added


def _safe_arcname(source_dir: Path, path: Path) -> str | None:
    """Return ``path``'s arcname relative to ``source_dir``, or None if unsafe.

    Defence in depth: although ``rglob`` does not escape ``source_dir`` and we do
    not follow symlinks, we resolve the candidate without following the final
    component and require it to stay within ``source_dir`` so a hostile tree
    cannot drive an absolute / ``..`` arcname into the archive.
    """
    try:
        rel = path.relative_to(source_dir)
    except ValueError:  # pragma: no cover — rglob always yields children
        return None
    arcname = rel.as_posix()
    if not arcname or arcname == ".":
        return None
    # Reject any arcname that would resolve outside the source root.
    candidate = (source_dir / rel).parent.resolve() / rel.name
    if not _is_within(source_dir, candidate):
        log.warning("scan_source_preserve_skip_escape", arcname=arcname)
        return None
    return arcname


def _add_member(
    tar: tarfile.TarFile,
    path: Path,
    arcname: str,
    *,
    recursive: bool,
) -> None:
    """Add a single member to the tar with a sanitized, deterministic header.

    We build the ``TarInfo`` from the file but strip ownership / mtime jitter so
    two runs over identical content produce byte-stable archives, and force a
    conservative mode (no setuid/setgid/sticky, no exec smuggling). ``recursive``
    is always False at the call sites — we drive the walk ourselves so the
    non-regular filter applies to every member.
    """
    info = tar.gettarinfo(str(path), arcname=arcname)
    if info is None:  # pragma: no cover — gettarinfo returns None only for FIFOs we already skip
        return
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    if info.isdir():
        info.mode = 0o755
        tar.addfile(info)
        return
    info.mode = 0o644
    with path.open("rb") as fh:
        tar.addfile(info, fileobj=fh)


def _enforce_running_cap(tar: tarfile.TarFile, *, tmp_path: Path, max_bytes: int) -> None:
    """Abort the write when the gzip stream so far exceeds ``max_bytes``.

    ``tarfile`` flushes through the gzip layer as it goes, so the temp file's
    on-disk size is a sound (slightly lagging) proxy for the written total. We
    flush the underlying fileobj first so the stat reflects everything addfile
    has handed to the compressor. Raising here unwinds the ``with tarfile.open``
    block; the caller deletes the partial temp file.
    """
    fileobj = getattr(tar, "fileobj", None)
    if fileobj is not None:
        try:
            fileobj.flush()
        except (OSError, ValueError):  # pragma: no cover — closed/raw obj
            pass
    try:
        current = tmp_path.stat().st_size
    except OSError:  # pragma: no cover — vanished mid-write
        return
    if current > max_bytes:
        raise PreservationTooLarge(
            f"preserved tarball exceeded the {max_bytes}-byte cap mid-write "
            f"({current} bytes)"
        )


__all__ = [
    "SCANCODE_MEMBER_NAME",
    "PreservationQuotaExceeded",
    "PreservationTooLarge",
    "SourcePreservationError",
    "preserve_scan_source",
    "scan_source_tarball_path",
    "scan_sources_dir_for_project",
]
