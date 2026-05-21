"""
Source archive upload + safe extraction — feat/zip-upload.

Lets a developer scan local source by uploading a ``.zip`` instead of giving a
``git_url``. Two responsibilities live here:

  1. :func:`save_uploaded_archive` — accept a ``multipart/form-data`` upload,
     screen it (size / extension / magic bytes), and stream it to disk under
     ``{workspace_root()}/archives/{project_id}/{archive_id}.zip`` without ever
     buffering the whole body in memory. RBAC (team access) is enforced before
     a single byte touches disk.

  2. :func:`safe_extract_archive` — extract a previously-saved archive into a
     scan workspace inside the Celery worker. This is the dangerous half: an
     attacker-controlled zip can carry path-traversal members (zip slip),
     decompression bombs (zip bomb), and symlink / device members. Every one
     of those is rejected here.

Security decisions (recorded for the security reviewer):

  - **Zip slip.** For every member we resolve ``(target_dir / name).resolve()``
    and require it to live under ``target_dir.resolve()`` (using
    ``Path.is_relative_to``, plus an explicit ``os.sep`` boundary check so a
    sibling directory whose name shares a prefix — ``/work`` vs ``/workevil`` —
    cannot slip through). Absolute member names and ``..`` components are
    rejected up front before any resolution.

  - **Zip bomb.** We never trust the zip central directory's ``file_size``
    blindly: it is advisory and an attacker can lie. We enforce three caps —
    a per-archive *total uncompressed* ceiling, a member-count ceiling, and a
    per-member compression-ratio ceiling — and count the *actual* bytes we
    write while streaming each member through a bounded copy. The running
    total (not the declared header) is the authoritative bomb guard; the
    instant it exceeds the cap we abort and delete the partial output. A
    header that *understates* a member's size is bounded by CPython's
    ``ZipExtFile`` read limit and trips a CRC mismatch (surfaced as
    ``ArchiveInvalid``) rather than landing a silently-truncated file.

  - **Symlink / device members.** Zip stores the unix mode in the high 16 bits
    of ``external_attr``. Any member that is not a regular file or directory
    (symlink, fifo, block / char device, socket) is rejected. We never call
    ``ZipFile.extractall`` — each member is materialised explicitly so a
    symlink can never be created on disk.

CLAUDE.md compliance:
  - Core rule #11: every limit is read via ``os.getenv`` at call time, no
    module-level env caching.
  - §4: failures raise typed domain exceptions carrying an HTTP status; the
    router maps them to RFC 7807 ``application/problem+json``.
  - §5: structlog JSON, one event per line; no raw archive bytes logged.
  - Core rule #2: no schema change — the ``archive_id`` rides inside the scan's
    ``scan_metadata`` JSONB, so no Alembic migration is required.
"""

from __future__ import annotations

import os
import shutil
import uuid
import zipfile
from pathlib import Path

import structlog
from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import workspace_root
from core.security import CurrentUser
from models import Project

log = structlog.get_logger("source_archive.service")

# The first four bytes of every standard (non-empty) local zip file. Empty zips
# start with the end-of-central-directory marker ``PK\x05\x06``; an archive with
# no entries has nothing to scan, so we reject those as a degenerate input.
_ZIP_MAGIC = b"PK\x03\x04"
_ZIP_EMPTY_MAGIC = b"PK\x05\x06"

# Streaming chunk size for both the inbound save and the per-member extract copy.
_CHUNK_SIZE = 1024 * 1024  # 1 MiB

# Content types a browser / CLI realistically sets for a .zip. We treat the
# header as advisory (magic bytes are authoritative) but reject obviously wrong
# declarations so a misrouted upload fails fast with a clear message.
_ALLOWED_CONTENT_TYPES = frozenset(
    {
        "application/zip",
        "application/x-zip-compressed",
        "application/octet-stream",
        "multipart/x-zip",
        "",  # some CLIs omit the part content-type
    }
)


# ---------------------------------------------------------------------------
# Limits — read at call time (core rule #11)
# ---------------------------------------------------------------------------


def _max_upload_bytes() -> int:
    """Hard ceiling on the compressed upload size (default 100 MiB)."""
    return int(os.getenv("SOURCE_ARCHIVE_MAX_BYTES", str(100 * 1024 * 1024)))


def _max_extracted_bytes() -> int:
    """Hard ceiling on total uncompressed bytes across all members (default 1 GiB)."""
    return int(os.getenv("SOURCE_ARCHIVE_MAX_EXTRACTED_BYTES", str(1024**3)))


def _max_members() -> int:
    """Hard ceiling on the number of entries in the archive (default 50,000)."""
    return int(os.getenv("SOURCE_ARCHIVE_MAX_MEMBERS", "50000"))


def _max_compression_ratio() -> float:
    """Per-member uncompressed/compressed ratio ceiling (default 200x).

    A legitimate text-heavy source tree rarely exceeds ~50x; 200x leaves a
    comfortable margin while still catching the classic ``42.zip`` style bomb
    where a few KiB inflates to gigabytes.
    """
    return float(os.getenv("SOURCE_ARCHIVE_MAX_COMPRESSION_RATIO", "200"))


def _project_quota_bytes() -> int:
    """Per-project ceiling on total *stored* archive bytes (default 500 MiB).

    H-fix (security review): a single upload is bounded by
    ``SOURCE_ARCHIVE_MAX_BYTES`` (default 100 MiB), but nothing stops a
    low-privilege developer from looping uploads until the workspace volume
    fills — a disk-exhaustion DoS that takes down every team's scans and the
    nightly backup. We sum the existing ``*.zip`` bytes already saved for the
    project and refuse a new upload whose addition would breach this cap.
    """
    return int(os.getenv("SOURCE_ARCHIVE_PROJECT_QUOTA_BYTES", str(500 * 1024 * 1024)))


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class SourceArchiveError(Exception):
    """Base class for source-archive errors. Each carries an HTTP status."""

    status_code: int = 400
    title: str = "Source Archive Error"
    type_uri: str = "https://docs.trustedoss.io/errors/source-archive"


class ArchiveProjectNotFound(SourceArchiveError):
    """The project does not exist, or the actor's team cannot see it.

    Existence-hide: a project in another team returns 404, never 403, so a
    caller cannot enumerate project ids across team boundaries.
    """

    status_code = 404
    title = "Project Not Found"
    type_uri = "https://docs.trustedoss.io/errors/source-archive-project-not-found"


class ArchiveTooLarge(SourceArchiveError):
    status_code = 413
    title = "Source Archive Too Large"
    type_uri = "https://docs.trustedoss.io/errors/source-archive-too-large"


class ArchiveQuotaExceeded(SourceArchiveError):
    """Saving this archive would breach the per-project storage quota.

    Mapped to 507 (Insufficient Storage): the request is well-formed and the
    individual file is within the single-upload size cap, but the project has
    no remaining archive-storage budget. Distinct from ``ArchiveTooLarge``
    (413, this *one* file is too big) so the UI / CLI can tell the developer
    to delete old archives rather than shrink the current one.
    """

    status_code = 507
    title = "Source Archive Quota Exceeded"
    type_uri = "https://docs.trustedoss.io/errors/source-archive-quota-exceeded"


class ArchiveUnsupportedType(SourceArchiveError):
    status_code = 415
    title = "Unsupported Archive Type"
    type_uri = "https://docs.trustedoss.io/errors/source-archive-unsupported-type"


class ArchiveInvalid(SourceArchiveError):
    status_code = 400
    title = "Invalid Source Archive"
    type_uri = "https://docs.trustedoss.io/errors/source-archive-invalid"


class ArchiveNotFound(SourceArchiveError):
    """A scan referenced an ``archive_id`` whose file is missing on disk."""

    status_code = 404
    title = "Source Archive Not Found"
    type_uri = "https://docs.trustedoss.io/errors/source-archive-missing"


class ArchiveExtractionRejected(SourceArchiveError):
    """The archive failed a worker-side safety check (slip / bomb / symlink).

    Mapped to 422 — the request was well-formed but the *content* is unsafe to
    process. Surfaces on the scan row as ``error_message`` rather than to an
    HTTP caller (extraction happens in the worker), but the typed exception is
    reused so callers that trigger extraction synchronously get a stable shape.
    """

    status_code = 422
    title = "Source Archive Rejected"
    type_uri = "https://docs.trustedoss.io/errors/source-archive-rejected"


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def archives_dir_for_project(project_id: uuid.UUID) -> Path:
    """Return the directory holding a project's uploaded archives."""
    return Path(workspace_root()) / "archives" / str(project_id)


def archive_path(project_id: uuid.UUID, archive_id: str) -> Path:
    """Resolve the on-disk path for one archive.

    ``archive_id`` is validated as a UUID first so a caller cannot smuggle a
    traversal sequence (``../../etc``) through the JSONB scan_metadata into a
    filesystem lookup.
    """
    try:
        parsed = uuid.UUID(str(archive_id))
    except (ValueError, TypeError) as exc:
        raise ArchiveNotFound(f"archive id {archive_id!r} is not a valid identifier") from exc
    return archives_dir_for_project(project_id) / f"{parsed}.zip"


def resolve_existing_archive(project_id: uuid.UUID, archive_id: str) -> Path:
    """Return the archive path, raising :class:`ArchiveNotFound` if absent."""
    path = archive_path(project_id, archive_id)
    if not path.is_file():
        raise ArchiveNotFound(
            f"no uploaded archive {archive_id!r} found for project {project_id}"
        )
    return path


def _project_archive_bytes(project_id: uuid.UUID) -> int:
    """Sum the on-disk size of every saved ``*.zip`` for the project.

    Best-effort: a file that vanishes mid-walk (concurrent delete) contributes
    0 rather than raising. Used by the per-project quota guard so a stale /
    leftover archive still counts against the budget.
    """
    archives_dir = archives_dir_for_project(project_id)
    if not archives_dir.is_dir():
        return 0
    total = 0
    for child in archives_dir.glob("*.zip"):
        try:
            total += child.stat().st_size
        except OSError:  # pragma: no cover — concurrent delete race
            continue
    return total


def delete_archive(project_id: uuid.UUID, archive_id: str) -> bool:
    """Delete one saved archive after a scan has consumed it (H-fix part a).

    Returns ``True`` if a file was removed, ``False`` if it was already gone.
    ``archive_id`` is validated as a UUID by :func:`archive_path` first so a
    crafted metadata value cannot drive an arbitrary unlink. Best-effort: an
    ``OSError`` on unlink is swallowed (the retention beat will sweep it later)
    so cleanup never turns a succeeded scan into a failure.
    """
    path = archive_path(project_id, archive_id)
    existed = path.is_file()
    _unlink_quietly(path)
    return existed


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------


def _can_access_team(actor: CurrentUser, team_id: uuid.UUID) -> bool:
    if actor.is_superuser or actor.role == "super_admin":
        return True
    return team_id in actor.team_ids


async def _load_accessible_project(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    actor: CurrentUser,
) -> Project:
    """Load the project, hiding existence from non-members (404 not 403)."""
    result = await session.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if project is None or not _can_access_team(actor, project.team_id):
        # Existence-hide: identical 404 whether the project is missing or in
        # another team. Cross-team enumeration is a P0 leak.
        raise ArchiveProjectNotFound(f"project {project_id} not found")
    return project


# ---------------------------------------------------------------------------
# Upload — stream to disk with bounded size
# ---------------------------------------------------------------------------


async def save_uploaded_archive(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    upload: UploadFile,
    actor: CurrentUser,
) -> str:
    """Validate + stream a zip upload to disk and return its ``archive_id``.

    Validation order (cheapest / least-trusting first):
      1. RBAC — actor must be able to access the project's team (else 404).
      2. Extension — filename must end in ``.zip`` (case-insensitive).
      3. Content-Type — must be in the advisory allow-list (else 415).
      4. Magic bytes — first 4 bytes must be the local-file zip signature.
         An empty-zip signature (``PK\\x05\\x06``) is rejected as degenerate.
      5. Streamed save — write in 1 MiB chunks, aborting + deleting the partial
         file the instant the running total exceeds ``SOURCE_ARCHIVE_MAX_BYTES``.

    The magic-byte read consumes the first chunk, so we write that chunk back
    out before continuing the stream — the saved file is byte-identical to the
    upload.
    """
    project = await _load_accessible_project(session, project_id=project_id, actor=actor)

    filename = (upload.filename or "").strip()
    if not filename.lower().endswith(".zip"):
        log.warning(
            "source_archive.reject_extension",
            project_id=str(project_id),
            filename=filename or "<none>",
        )
        raise ArchiveUnsupportedType("only .zip source archives are accepted")

    content_type = (upload.content_type or "").lower().split(";", 1)[0].strip()
    if content_type not in _ALLOWED_CONTENT_TYPES:
        log.warning(
            "source_archive.reject_content_type",
            project_id=str(project_id),
            content_type=content_type or "<none>",
        )
        raise ArchiveUnsupportedType(
            f"content-type {content_type!r} is not an accepted zip media type"
        )

    max_bytes = _max_upload_bytes()
    quota_bytes = _project_quota_bytes()

    # H-fix (part b): refuse before reading a single body byte when the project
    # is already at / over its archive-storage quota. The running total below
    # re-checks against (existing + written) so a streamed body that crosses the
    # cap mid-flight is aborted and its partial file deleted, same as the
    # per-upload size guard.
    existing_bytes = _project_archive_bytes(project_id)
    if existing_bytes >= quota_bytes:
        log.warning(
            "source_archive.reject_quota_full",
            project_id=str(project_id),
            existing_bytes=existing_bytes,
            quota_bytes=quota_bytes,
        )
        raise ArchiveQuotaExceeded(
            f"project archive storage is full "
            f"({existing_bytes} of {quota_bytes} bytes used); "
            f"delete old archives before uploading"
        )

    # Read the first chunk to inspect the magic bytes. We need at least 4 bytes;
    # a body shorter than the signature cannot be a valid zip.
    first_chunk = await upload.read(_CHUNK_SIZE)
    if len(first_chunk) < len(_ZIP_MAGIC):
        raise ArchiveInvalid("uploaded file is too short to be a zip archive")
    if first_chunk[:4] == _ZIP_EMPTY_MAGIC:
        raise ArchiveInvalid("uploaded zip archive is empty")
    if first_chunk[:4] != _ZIP_MAGIC:
        log.warning(
            "source_archive.reject_magic",
            project_id=str(project_id),
            # Hex of the first 4 bytes only — never the archive contents.
            magic=first_chunk[:4].hex(),
        )
        raise ArchiveUnsupportedType(
            "uploaded file is not a zip archive (bad magic bytes)"
        )

    archive_id = uuid.uuid4()
    dest_dir = archives_dir_for_project(project_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{archive_id}.zip"

    bytes_written = 0
    try:
        with dest.open("wb") as fh:
            chunk = first_chunk
            while chunk:
                bytes_written += len(chunk)
                if bytes_written > max_bytes:
                    raise ArchiveTooLarge(
                        f"archive exceeds the {max_bytes}-byte upload limit"
                    )
                # H-fix (part b): a body within the single-upload cap can still
                # tip the project past its cumulative quota. Count the running
                # total against (existing + written) and abort the instant it
                # crosses; the partial file is deleted in the handler below.
                if existing_bytes + bytes_written > quota_bytes:
                    raise ArchiveQuotaExceeded(
                        f"upload would exceed the project archive quota "
                        f"({quota_bytes} bytes); delete old archives first"
                    )
                fh.write(chunk)
                chunk = await upload.read(_CHUNK_SIZE)
    except ArchiveTooLarge:
        _unlink_quietly(dest)
        log.warning(
            "source_archive.reject_too_large",
            project_id=str(project_id),
            limit_bytes=max_bytes,
        )
        raise
    except ArchiveQuotaExceeded:
        _unlink_quietly(dest)
        log.warning(
            "source_archive.reject_quota_stream",
            project_id=str(project_id),
            existing_bytes=existing_bytes,
            quota_bytes=quota_bytes,
        )
        raise
    except OSError as exc:
        _unlink_quietly(dest)
        log.error(
            "source_archive.save_failed",
            project_id=str(project_id),
            error=type(exc).__name__,
        )
        raise ArchiveInvalid("failed to persist the uploaded archive") from exc

    log.info(
        "source_archive.saved",
        project_id=str(project_id),
        archive_id=str(archive_id),
        team_id=str(project.team_id),
        bytes=bytes_written,
    )
    return str(archive_id)


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:  # pragma: no cover — best-effort cleanup
        pass


# ---------------------------------------------------------------------------
# Extraction — zip slip / bomb / symlink defence
# ---------------------------------------------------------------------------

# Unix file-type bits (S_IFMT mask is 0o170000). Regular files and directories
# are the only member types we materialise; everything else (symlink 0o120000,
# fifo 0o010000, char/block device, socket) is rejected.
_S_IFMT = 0o170000
_S_IFREG = 0o100000
_S_IFDIR = 0o040000


def _member_unix_mode(info: zipfile.ZipInfo) -> int:
    """Return the unix mode bits stored in the high 16 bits of external_attr.

    Zip files created on unix store ``st_mode`` there. A mode of 0 means the
    archive was created on a system that does not record unix modes (eg. the
    Windows zip tooling) — in that case there is no symlink to worry about and
    we treat the member by its name (trailing slash = directory).
    """
    return (info.external_attr >> 16) & 0xFFFF


def _is_within(base: Path, target: Path) -> bool:
    """True iff ``target`` is ``base`` itself or strictly inside it.

    ``Path.is_relative_to`` already handles the prefix case correctly (it
    splits on path components, so ``/work`` is not relative to ``/workevil``),
    but we keep an explicit ``commonpath`` cross-check as defence in depth in
    case a future refactor swaps the implementation.
    """
    try:
        if target == base:
            return True
        if not target.is_relative_to(base):
            return False
        # Belt-and-suspenders: commonpath must equal base exactly.
        return os.path.commonpath([str(base), str(target)]) == str(base)
    except (ValueError, OSError):
        return False


def safe_extract_archive(*, archive_path: Path, target_dir: Path) -> None:
    """Extract ``archive_path`` into ``target_dir``, rejecting hostile members.

    Raises:
        ArchiveExtractionRejected: on any zip slip, zip bomb, or symlink /
            device member. The caller (Celery worker) maps this to a terminal
            scan failure with a credential-free ``error_message``.
        ArchiveInvalid: if the file is not a readable zip (corrupt central
            directory, truncated stream).
    """
    base = target_dir.resolve()
    base.mkdir(parents=True, exist_ok=True)

    max_extracted = _max_extracted_bytes()
    max_members = _max_members()
    max_ratio = _max_compression_ratio()

    try:
        zf = zipfile.ZipFile(archive_path)
    except (zipfile.BadZipFile, OSError) as exc:
        # M3-fix: a corrupt zip that fails to open still leaves the empty (or
        # partially-prepared) target dir behind. Sweep it before surfacing.
        shutil.rmtree(base, ignore_errors=True)
        raise ArchiveInvalid(f"archive is not a readable zip: {exc}") from exc

    try:
        with zf:
            infos = zf.infolist()
            if len(infos) > max_members:
                raise ArchiveExtractionRejected(
                    f"archive declares {len(infos)} members; the maximum is {max_members}"
                )

            total_written = 0
            for info in infos:
                total_written = _extract_one_member(
                    zf,
                    info,
                    base=base,
                    total_written=total_written,
                    max_extracted=max_extracted,
                    max_ratio=max_ratio,
                )
    except (ArchiveExtractionRejected, ArchiveInvalid):
        # M3-fix: a rejected archive must leave ZERO bytes on disk. Members
        # extracted before the rejecting one (and the rejecting member's own
        # partial file) are swept here so a hostile archive cannot seed a
        # half-populated workspace that a downstream scanner would treat as
        # legitimate source. ``base`` is the resolved target dir we created.
        shutil.rmtree(base, ignore_errors=True)
        raise

    log.info(
        "source_archive.extracted",
        target_dir=str(base),
        members=len(infos),
        bytes=total_written,
    )


def _extract_one_member(
    zf: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    *,
    base: Path,
    total_written: int,
    max_extracted: int,
    max_ratio: float,
) -> int:
    """Validate + materialise a single zip member. Returns the new running total.

    Every rejection path raises :class:`ArchiveExtractionRejected`.
    """
    name = info.filename

    # 1. Absolute paths and traversal components are rejected before any
    #    filesystem resolution — these are the loud, unambiguous attacks.
    if name.startswith("/") or name.startswith("\\"):
        raise ArchiveExtractionRejected(f"absolute member path rejected: {name!r}")
    # Normalise backslashes (Windows-authored zips) so a ``..\\`` segment is
    # caught by the same component check.
    parts = name.replace("\\", "/").split("/")
    if ".." in parts:
        raise ArchiveExtractionRejected(f"path traversal member rejected: {name!r}")

    # 2. Symlink / device / fifo members are rejected.
    #
    #    Many zip producers (Python's ``writestr``, Windows tooling) store only
    #    permission bits with the S_IFMT *type* field left at 0 — a mode of
    #    ``0o600`` does NOT mean "non-regular". We therefore reject a member
    #    only when the type bits are EXPLICITLY set to a non-regular,
    #    non-directory type (symlink 0o120000, fifo, char/block device, socket).
    #    When the type field is 0 we classify by name (trailing slash = dir).
    mode = _member_unix_mode(info)
    file_type = mode & _S_IFMT
    is_dir_member = info.is_dir() or file_type == _S_IFDIR
    if file_type not in (0, _S_IFREG, _S_IFDIR):
        raise ArchiveExtractionRejected(
            f"non-regular member rejected (mode {oct(mode)}): {name!r}"
        )

    # 3. Zip slip — resolve the destination and require it under base.
    dest = (base / Path(*[p for p in parts if p not in ("", ".")])).resolve()
    if not _is_within(base, dest):
        raise ArchiveExtractionRejected(f"path escapes target directory: {name!r}")

    if is_dir_member:
        dest.mkdir(parents=True, exist_ok=True)
        return total_written

    # 4. Zip bomb — per-member compression ratio. ``compress_size`` of 0 with a
    #    positive ``file_size`` is itself a lie we refuse to trust: it claims
    #    "infinite" compression and would otherwise skip the ratio guard
    #    entirely (L5-fix — previously the ``> 0`` branch silently passed). When
    #    compress_size is positive we apply the normal ratio ceiling; the
    #    running total-bytes cap remains the authoritative bomb guard regardless.
    if info.compress_size == 0:
        if info.file_size > 0:
            raise ArchiveExtractionRejected(
                f"member declares 0 compressed bytes for {info.file_size} "
                f"uncompressed (impossible ratio): {name!r}"
            )
    else:
        ratio = info.file_size / info.compress_size
        if ratio > max_ratio:
            raise ArchiveExtractionRejected(
                f"member compression ratio {ratio:.0f}x exceeds {max_ratio:.0f}x: {name!r}"
            )

    dest.parent.mkdir(parents=True, exist_ok=True)

    # 5. Zip bomb — stream the member through a bounded copy, counting the
    #    *actual* decompressed bytes against the per-archive total cap. We never
    #    trust ``info.file_size`` (the central-directory header) for the cap: it
    #    is attacker-controlled and advisory. We count what we actually write.
    #
    #    Note: CPython's ``zipfile.ZipExtFile`` does enforce ``file_size`` as a
    #    read limit, so a header that *understates* the size truncates the read
    #    (and trips a CRC error we surface as ArchiveInvalid). A header that
    #    *overstates* is bounded by the real compressed stream. Either way the
    #    running ``total_written`` below is the authoritative bomb guard.
    try:
        with zf.open(info) as src, dest.open("wb") as out:
            while True:
                chunk = src.read(_CHUNK_SIZE)
                if not chunk:
                    break
                total_written += len(chunk)
                if total_written > max_extracted:
                    raise ArchiveExtractionRejected(
                        f"total extracted size exceeds {max_extracted} bytes "
                        f"(zip bomb): {name!r}"
                    )
                out.write(chunk)
    except ArchiveExtractionRejected:
        _unlink_quietly(dest)
        raise
    except (zipfile.BadZipFile, OSError) as exc:
        _unlink_quietly(dest)
        raise ArchiveInvalid(f"failed to extract member {name!r}: {exc}") from exc

    return total_written


__all__ = [
    "ArchiveExtractionRejected",
    "ArchiveInvalid",
    "ArchiveNotFound",
    "ArchiveProjectNotFound",
    "ArchiveQuotaExceeded",
    "ArchiveTooLarge",
    "ArchiveUnsupportedType",
    "SourceArchiveError",
    "archive_path",
    "archives_dir_for_project",
    "delete_archive",
    "resolve_existing_archive",
    "safe_extract_archive",
    "save_uploaded_archive",
]
