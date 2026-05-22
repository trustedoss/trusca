r"""
Source-tree read service — G3.2 (Protex-style source-tree view).

G3.1 preserves, per succeeded source scan, a gzip tarball of the scanned tree
PLUS the scancode result JSON folded in as ``.trustedoss/scancode.json`` under
``{workspace_root()}/scan-sources/{project_id}/{scan_id}.tar.gz``. This module
reads that tarball back for the viewer UI:

  - :func:`list_dir` — the immediate children of one directory (lazy, per-dir),
    paged, with a cheap per-file license-badge set from ``license_findings``.
  - :func:`read_file` — one file's bytes (capped at the viewer's per-file limit),
    with binary detection, PLUS the per-LINE license matches projected from the
    folded scancode JSON for THAT path only.

Both entry points resolve the scan (defaulting to ``Project.latest_scan_id``),
enforce team access with 404 existence-hide, and then read DIRECTLY from the tar
by exact member name — never by joining a user string onto a real directory.

==========================================================================
SECURITY — path traversal is THE risk (recorded for the security reviewer)
==========================================================================

The on-disk container path is derived from UUIDs ONLY. ``project_id`` and
``scan_id`` are validated as ``uuid.UUID`` by the API layer and re-formatted from
real UUID objects here (``scan_source_tarball_path``); no user-controlled string
ever lands in the filesystem path, so there is no real-directory to escape.

The ``?path=`` member selector is the attacker surface. It is NOT used to build a
filesystem path. We:

  1. normalise backslashes to ``/`` (Windows-authored arcnames),
  2. reject a leading ``/`` or ``\`` (absolute member),
  3. split on ``/`` and reject ANY ``..`` component (and any NUL byte),
  4. drop empty / ``.`` components and re-join to a canonical POSIX key,

then look the member up by EXACT name in the tar (``TarFile.getmember`` /
membership in our pre-built index). Because the lookup is an exact-string match
against arcnames that the G3.1 writer itself sanitised (it skips non-regular
members and rejects escaping arcnames), a crafted ``?path=`` can only ever miss
(404) — it cannot read outside the tar. We additionally reject non-regular tar
members (symlink / hardlink / device / fifo) on the read path as defence in depth
against a tarball produced outside our writer.

Caps:
  - the per-file content read is bounded by ``scan_source_viewer_max_file_bytes()``
    (default 2 MiB) — we never read an unbounded member into memory; we read at
    most cap+1 bytes to detect truncation.
  - the folded scancode-JSON parse is bounded by ``scancode_max_result_bytes()``
    (default 256 MiB) — we ``stat``-then-skip an over-cap member rather than
    materialising a multi-GiB document.

CLAUDE.md compliance:
  - Core rule #11: every limit is read via the ``core.config`` accessors at call
    time, no module-level env caching.
  - §4: failures raise typed domain exceptions carrying an HTTP status; the
    router maps them to RFC 7807 ``application/problem+json``.
  - §5: structlog JSON, one event per line; no file contents logged.
  - Core rule #2: no schema change — the tree + matches are derived from tar
    members and ``license_findings``; no Alembic migration is required.
"""

from __future__ import annotations

import json
import tarfile
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import structlog
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.authz import assert_team_access
from core.config import (
    scan_source_raw_download_max_bytes,
    scan_source_viewer_max_file_bytes,
    scancode_max_result_bytes,
)
from core.security import CurrentUser
from models import License, LicenseFinding, Project, Scan
from services.source_preservation_service import (
    SCANCODE_MEMBER_NAME,
    scan_source_tarball_path,
)

log = structlog.get_logger("source_tree.service")


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class SourceTreeError(Exception):
    """Base class for source-tree errors. Each carries an HTTP status."""

    status_code: int = 400
    title: str = "Source Tree Error"
    type_uri: str = "https://docs.trustedoss.io/errors/source-tree"


class SourceUnavailable(SourceTreeError):
    """The preserved tarball (or the project / scan) is missing or swept.

    Existence-hide: a project / scan in another team, an unknown id, and a
    swept-or-never-written tarball all return the SAME 404 so a caller cannot
    enumerate ids or distinguish "no access" from "no tarball" across teams.
    """

    status_code = 404
    title = "Source Not Available"
    type_uri = "https://docs.trustedoss.io/errors/source-unavailable"


class SourcePathRejected(SourceTreeError):
    """The ``?path=`` selector was malformed (absolute, traversal, NUL, …)."""

    status_code = 400
    title = "Source Path Rejected"
    type_uri = "https://docs.trustedoss.io/errors/source-path-rejected"


class SourceFileTooLarge(SourceTreeError):
    """The requested member is a directory or otherwise not a readable file.

    Mapped to 413: the viewer's per-file content cap is enforced by truncation
    (not rejection), so this is reserved for the "you asked to read a directory
    as a file" / non-regular-member case where there is no file body to return.
    """

    status_code = 413
    title = "Source File Not Readable"
    type_uri = "https://docs.trustedoss.io/errors/source-file-not-readable"


# ---------------------------------------------------------------------------
# Result containers (service-internal; the router maps to schemas)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TreeEntry:
    name: str
    path: str
    is_dir: bool
    byte_size: int
    license_spdx_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TreePage:
    scan_id: uuid.UUID
    path: str
    entries: list[TreeEntry]
    total: int
    page: int
    size: int


@dataclass(frozen=True)
class LineMatch:
    spdx_id: str
    start_line: int
    end_line: int
    score: float | None


@dataclass(frozen=True)
class FileContent:
    scan_id: uuid.UUID
    path: str
    byte_size: int
    truncated: bool
    encoding: str
    content: str | None
    license_matches: list[LineMatch]


@dataclass(frozen=True)
class RawFileContent:
    """A streaming handle to one preserved member for the raw download (G3.3).

    Unlike :class:`FileContent` this carries no per-line matches and no viewer
    cap — it is the whole member, bounded only by the (generous) raw-download
    ceiling, streamed back as ``application/octet-stream`` for the download
    button on a truncated / binary file.

    The body is NOT buffered: ``chunks`` is a generator that yields the member in
    bounded slices (and owns closing the open tarball + member handle when it is
    fully consumed or the client disconnects), so peak memory is one chunk — not
    the whole (up to 512 MiB) member. ``byte_size`` is the member's declared size
    from the tar header (the Content-Length is intentionally omitted: a member
    that grows past the cap mid-stream is aborted, so we never promise a length
    we might not deliver).
    """

    scan_id: uuid.UUID
    path: str
    filename: str
    byte_size: int
    chunks: Iterator[bytes]


# ---------------------------------------------------------------------------
# Path sanitisation (the attacker surface)
# ---------------------------------------------------------------------------


def _sanitize_member_path(raw: str) -> str:
    """Normalise a ``?path=`` selector to a canonical POSIX member key.

    Returns the canonical key (``""`` for the root). Raises
    :class:`SourcePathRejected` for any hostile shape. This NEVER touches the
    filesystem — the returned key is only ever used for an exact-string lookup
    against tar arcnames.

    Rejections (mirroring ``source_archive_service._extract_one_member``):
      - a NUL byte anywhere (truncation / C-string smuggling),
      - a leading ``/`` or ``\\`` (absolute member),
      - any ``..`` path component (traversal),
    Empty / ``.`` components are dropped; backslashes are normalised to ``/``.
    """
    if raw is None:  # pragma: no cover — Query default is ""
        return ""
    if "\x00" in raw:
        # G3.2 Low (a): never echo the rejected selector back into the 4xx
        # detail (reflected-input). The raw value goes to a WARNING field only;
        # the client sees a STATIC message.
        log.warning("source_tree_path_rejected", reason="nul_byte", raw_path=raw)
        raise SourcePathRejected("path selector rejected")
    # Absolute paths are rejected before any normalisation — loud, unambiguous.
    if raw.startswith("/") or raw.startswith("\\"):
        log.warning("source_tree_path_rejected", reason="absolute", raw_path=raw)
        raise SourcePathRejected("path selector rejected")
    normalised = raw.replace("\\", "/")
    parts = normalised.split("/")
    if ".." in parts:
        log.warning("source_tree_path_rejected", reason="traversal", raw_path=raw)
        raise SourcePathRejected("path selector rejected")
    clean = [p for p in parts if p not in ("", ".")]
    return "/".join(clean)


def _canonical_arcname(name: str) -> str:
    """Canonicalise a tar arcname / scancode path to the member-key shape.

    Normalises backslashes, then drops empty / ``.`` components so ``./src/a.py``,
    ``src/a.py/`` and ``src//a.py`` all collapse to ``src/a.py``. Used on the
    READ side (the tar's own members + the scancode JSON's ``path`` field) so the
    comparison is symmetric with :func:`_sanitize_member_path`.
    """
    parts = name.replace("\\", "/").split("/")
    return "/".join(p for p in parts if p not in ("", "."))


# ---------------------------------------------------------------------------
# Scan / tarball resolution (UUID-only on-disk path)
# ---------------------------------------------------------------------------


async def _resolve_accessible_scan(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    scan_id: uuid.UUID | None,
    actor: CurrentUser,
) -> tuple[Project, uuid.UUID]:
    """Load the project (team-scoped, existence-hide) and resolve the scan id.

    ``scan_id`` defaults to ``Project.latest_scan_id``. When an explicit
    ``scan_id`` is given it MUST belong to ``project_id`` — a cross-project scan
    id is treated as "not available" (404), never a leak.
    """
    result = await session.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if project is None:
        raise SourceUnavailable(f"project {project_id} not found")

    # Existence-hide: a project in another team returns the same 404 as an
    # unknown project id. assert_team_access logs the cross-team attempt.
    assert_team_access(
        actor,
        project.team_id,
        log=log,
        resource="source_tree",
        resource_id=str(project_id),
        deny=lambda: SourceUnavailable(f"project {project_id} not found"),
    )

    if scan_id is not None:
        # Validate the explicit scan belongs to THIS project before it can drive
        # a tarball read. A scan id from another project must not be readable
        # through this project's surface.
        scan_result = await session.execute(
            select(Scan.id).where(Scan.id == scan_id, Scan.project_id == project_id)
        )
        if scan_result.scalar_one_or_none() is None:
            raise SourceUnavailable(
                f"scan {scan_id} not found for project {project_id}"
            )
        resolved = scan_id
    else:
        if project.latest_scan_id is None:
            raise SourceUnavailable(
                f"project {project_id} has no scan with preserved source"
            )
        resolved = project.latest_scan_id

    return project, resolved


def _open_tarball(project_id: uuid.UUID, scan_id: uuid.UUID) -> tarfile.TarFile:
    """Open the preserved tarball (UUID-only path), or raise SourceUnavailable.

    The path is built from real UUID objects via ``scan_source_tarball_path`` —
    no user-controlled string participates, so there is no traversal surface in
    the container path itself.
    """
    path = scan_source_tarball_path(project_id, scan_id)
    if not path.is_file():
        raise SourceUnavailable(
            f"no preserved source for scan {scan_id} (swept or never written)"
        )
    try:
        # ``r:gz`` matches the writer's ``w:gz``; a corrupt / wrong-type file
        # surfaces as "not available" rather than a 500.
        return tarfile.open(path, mode="r:gz")
    except (tarfile.TarError, OSError) as exc:
        log.warning(
            "source_tree_tarball_unreadable",
            project_id=str(project_id),
            scan_id=str(scan_id),
            error=type(exc).__name__,
        )
        raise SourceUnavailable(
            f"preserved source for scan {scan_id} is not readable"
        ) from exc


# ---------------------------------------------------------------------------
# License badge set (cheap, from license_findings)
# ---------------------------------------------------------------------------


async def _license_badges_for_paths(
    session: AsyncSession,
    *,
    scan_id: uuid.UUID,
    source_paths: set[str],
) -> dict[str, list[str]]:
    """Return ``{source_path: sorted[spdx_id]}`` for the given paths under a scan.

    One query joins ``license_findings`` to ``licenses`` for this scan and the
    requested paths; we group SPDX ids per path in Python. Paths with no finding
    simply do not appear in the result (the caller defaults to ``[]``).
    """
    if not source_paths:
        return {}
    stmt = (
        select(LicenseFinding.source_path, License.spdx_id)
        .join(License, License.id == LicenseFinding.license_id)
        .where(
            LicenseFinding.scan_id == scan_id,
            LicenseFinding.source_path.in_(source_paths),
            License.spdx_id.is_not(None),
        )
    )
    result = await session.execute(stmt)
    badges: dict[str, set[str]] = {}
    for source_path, spdx_id in result.all():
        if source_path is None or spdx_id is None:
            continue
        badges.setdefault(source_path, set()).add(spdx_id)
    return {path: sorted(ids) for path, ids in badges.items()}


# ---------------------------------------------------------------------------
# Tree listing
# ---------------------------------------------------------------------------


def _immediate_children(
    members: list[tarfile.TarInfo], *, parent: str
) -> list[tuple[str, bool, int]]:
    """Compute the immediate children of ``parent`` from a flat member list.

    Returns ``[(child_path, is_dir, byte_size), ...]`` deduplicated. Directories
    are inferred both from explicit dir members AND from intermediate path
    components of file members (a tar may omit empty-dir entries, but it always
    carries the file ``a/b/c`` from which ``a`` and ``a/b`` are derived).

    ``parent`` is the canonical key ("" for root). The reserved scancode member
    (``.trustedoss/scancode.json``) and its synthetic ``.trustedoss`` dir are
    excluded so the writer's bookkeeping never shows up in the tree.
    """
    prefix = f"{parent}/" if parent else ""
    children: dict[str, tuple[bool, int]] = {}
    for info in members:
        # Normalise the arcname the same way we sanitise input so a tar produced
        # outside our writer cannot inject a leading-slash / backslash member.
        name = _canonical_arcname(info.name)
        if not name:
            continue
        if name == SCANCODE_MEMBER_NAME or name.startswith(".trustedoss/"):
            # Reserved bookkeeping member — never part of the user's tree.
            continue
        if not name.startswith(prefix):
            continue
        remainder = name[len(prefix):]
        if not remainder:
            continue
        head, sep, _tail = remainder.partition("/")
        if not head:
            continue
        child_path = f"{prefix}{head}"
        if sep:
            # ``head`` is an intermediate directory component of a deeper file.
            existing = children.get(child_path)
            if existing is None:
                children[child_path] = (True, 0)
        else:
            # ``head`` is the leaf member itself.
            is_dir = bool(info.isdir())
            size = 0 if is_dir else int(info.size)
            prev = children.get(child_path)
            if prev is None or (prev[0] and not is_dir):
                # A later file entry supersedes an inferred-directory placeholder
                # of the same name (shouldn't happen, but pick the file).
                children[child_path] = (is_dir, size)
    return [(path, meta[0], meta[1]) for path, meta in children.items()]


async def list_dir(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    raw_path: str,
    scan_id: uuid.UUID | None,
    actor: CurrentUser,
    page: int,
    size: int,
) -> TreePage:
    """List the immediate children of ``raw_path`` under the resolved scan.

    Directories sort before files, then by name, so the UI gets a stable,
    folder-first ordering. Paging is applied AFTER that sort.
    """
    page = max(page, 1)
    size = max(min(size, 500), 1)

    member_path = _sanitize_member_path(raw_path)
    _project, resolved_scan = await _resolve_accessible_scan(
        session, project_id=project_id, scan_id=scan_id, actor=actor
    )

    def _load_members() -> list[tarfile.TarInfo]:
        tar = _open_tarball(project_id, resolved_scan)
        try:
            return tar.getmembers()
        finally:
            tar.close()

    # tarfile open + getmembers is blocking gzip I/O; offload off the event loop
    # so concurrent source-tree reads don't serialise (test-hardening Tier 3).
    members = await run_in_threadpool(_load_members)

    raw_children = _immediate_children(members, parent=member_path)

    # Directories first, then files, both case-insensitive by leaf name.
    def _sort_key(item: tuple[str, bool, int]) -> tuple[int, str]:
        path, is_dir, _size = item
        leaf = path.rsplit("/", 1)[-1]
        return (0 if is_dir else 1, leaf.lower())

    raw_children.sort(key=_sort_key)
    total = len(raw_children)

    start = (page - 1) * size
    window = raw_children[start : start + size]

    # Cheap license badges only for the FILE entries on this page.
    file_paths = {path for path, is_dir, _ in window if not is_dir}
    badges = await _license_badges_for_paths(
        session, scan_id=resolved_scan, source_paths=file_paths
    )

    entries = [
        TreeEntry(
            name=path.rsplit("/", 1)[-1],
            path=path,
            is_dir=is_dir,
            byte_size=byte_size,
            license_spdx_ids=badges.get(path, []) if not is_dir else [],
        )
        for path, is_dir, byte_size in window
    ]

    log.info(
        "source_tree_listed",
        project_id=str(project_id),
        scan_id=str(resolved_scan),
        path=member_path,
        total=total,
        page=page,
        size=size,
    )
    return TreePage(
        scan_id=resolved_scan,
        path=member_path,
        entries=entries,
        total=total,
        page=page,
        size=size,
    )


# ---------------------------------------------------------------------------
# File read
# ---------------------------------------------------------------------------

# A non-regular tar member type set we refuse to read (symlink / hardlink /
# block / char device / fifo). Defence in depth: our G3.1 writer never adds
# these, but a tarball produced outside our pipeline might.
_NON_REGULAR_TYPES = frozenset(
    {tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.FIFOTYPE}
)


def _lookup_member(
    members: list[tarfile.TarInfo], member_path: str
) -> tarfile.TarInfo:
    """Return the tar member for ``member_path`` (exact match), or raise.

    Matches against the arcname normalised the same way the input was sanitised
    so a writer that stored ``./a`` or ``a/`` style names still resolves.

    When there is no exact match we distinguish two cases:
      - the path is an *inferred directory* (some member lives under
        ``member_path/``) → :class:`SourceFileTooLarge` (413, "it's a dir"); a tar
        often omits explicit directory entries, so a real folder has no member.
      - otherwise → :class:`SourceUnavailable` (404).
    """
    prefix = f"{member_path}/"
    is_dir_prefix = False
    for info in members:
        canonical = _canonical_arcname(info.name)
        if canonical == member_path:
            return info
        if canonical.startswith(prefix):
            is_dir_prefix = True
    if is_dir_prefix:
        raise SourceFileTooLarge(f"{member_path!r} is a directory, not a file")
    raise SourceUnavailable(f"no such file in preserved source: {member_path!r}")


def _detect_encoding(data: bytes) -> tuple[str, str | None]:
    """Classify ``data`` as text or binary.

    A NUL byte is the classic binary marker; we also treat an undecodable
    UTF-8 stream as binary. Returns ``("utf-8", text)`` or ``("binary", None)``.
    """
    if b"\x00" in data:
        return "binary", None
    try:
        return "utf-8", data.decode("utf-8")
    except UnicodeDecodeError:
        return "binary", None


async def read_file(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    raw_path: str,
    scan_id: uuid.UUID | None,
    actor: CurrentUser,
) -> FileContent:
    """Read one file from the preserved source + project per-line license matches.

    ``content`` is capped at ``scan_source_viewer_max_file_bytes()``; we read at
    most cap+1 bytes from the member so a huge file cannot OOM the API, and set
    ``truncated`` when the full file is larger than the cap. Binary files carry
    ``encoding="binary"`` and ``content=None``.
    """
    member_path = _sanitize_member_path(raw_path)
    if not member_path:
        # The root is a directory, not a file.
        raise SourceFileTooLarge("the source root is a directory, not a file")

    _project, resolved_scan = await _resolve_accessible_scan(
        session, project_id=project_id, scan_id=scan_id, actor=actor
    )

    cap = scan_source_viewer_max_file_bytes()

    def _read_member() -> tuple[int, bool, str, str | None, list[LineMatch]]:
        # All of this is blocking gzip tar I/O — runs in the threadpool (below)
        # so concurrent file reads never stall the event loop (Tier 3 hardening).
        tar = _open_tarball(project_id, resolved_scan)
        try:
            # G3.2 Low (b): open + parse the tarball ONCE per request. ``getmembers``
            # walks the whole archive; reuse that single member list for the file
            # lookup AND the per-line scancode-match projection below.
            members = tar.getmembers()
            info = _lookup_member(members, member_path)

            if info.isdir():
                raise SourceFileTooLarge(f"{member_path!r} is a directory, not a file")
            # Reject non-regular members (symlink / hardlink / device / fifo).
            if info.type in _NON_REGULAR_TYPES or not info.isreg():
                raise SourceFileTooLarge(
                    f"{member_path!r} is not a regular file (type {info.type!r})"
                )

            full_size = int(info.size)
            extracted = tar.extractfile(info)
            if extracted is None:  # pragma: no cover — isreg() guards this
                raise SourceUnavailable(
                    f"could not read member from preserved source: {member_path!r}"
                )
            # Bounded read: cap+1 bytes detects truncation without reading an
            # unbounded member into memory.
            with extracted:
                data = extracted.read(cap + 1)

            truncated = len(data) > cap
            if truncated:
                data = data[:cap]

            encoding, content = _detect_encoding(data)

            # Project per-line matches from the SAME open tar (no second open).
            matches = _line_matches_for_path(
                tar,
                members,
                project_id=project_id,
                scan_id=resolved_scan,
                member_path=member_path,
            )
            return full_size, truncated, encoding, content, matches
        finally:
            tar.close()

    full_size, truncated, encoding, content, matches = await run_in_threadpool(_read_member)

    log.info(
        "source_tree_file_read",
        project_id=str(project_id),
        scan_id=str(resolved_scan),
        path=member_path,
        byte_size=full_size,
        truncated=truncated,
        encoding=encoding,
        matches=len(matches),
    )
    return FileContent(
        scan_id=resolved_scan,
        path=member_path,
        byte_size=full_size,
        truncated=truncated,
        encoding=encoding,
        content=content,
        license_matches=matches,
    )


# ---------------------------------------------------------------------------
# Raw full-file download (G3.3) — no per-file viewer cap
# ---------------------------------------------------------------------------

# The streamed-chunk size for the raw download. Peak memory per request is one
# chunk (plus tarfile's own gzip window), NOT the whole member, so even a
# 512 MiB member streams at ~64 KiB of resident body memory.
_RAW_STREAM_CHUNK_BYTES = 64 * 1024


def _stream_member_bytes(
    tar: tarfile.TarFile,
    extracted: Any,
    *,
    cap: int,
    chunk_size: int,
    member_path: str,
    project_id: uuid.UUID,
    scan_id: uuid.UUID,
) -> Iterator[bytes]:
    """Yield ``extracted`` in ``chunk_size`` slices, owning handle cleanup.

    The cap is enforced WHILE streaming: we tally the bytes already yielded and
    abort with :class:`SourceFileTooLarge` if the running total would exceed the
    raw-download cap. The eager size pre-check in :func:`read_file_raw` already
    rejects an over-cap member from a STATIC tarball, but enforcing the cap again
    here keeps the bound honest for a member whose declared header size understates
    its body (defence in depth, mirroring the old ``cap + 1`` re-check) and means
    the stream can never deliver more than ``cap`` bytes.

    The generator owns both the extracted member handle and the open tarball: a
    ``finally`` closes them whether the stream is fully consumed, aborted by the
    cap, or abandoned (``GeneratorExit`` on client disconnect). This is why the
    eager phase must NOT close the tar.
    """
    yielded = 0
    try:
        while True:
            chunk = extracted.read(chunk_size)
            if not chunk:
                break
            yielded += len(chunk)
            if yielded > cap:
                log.warning(
                    "source_tree_file_raw_cap_abort",
                    project_id=str(project_id),
                    scan_id=str(scan_id),
                    path=member_path,
                    cap_bytes=cap,
                )
                raise SourceFileTooLarge(
                    f"{member_path!r} exceeds the {cap}-byte raw-download cap"
                )
            yield chunk
        log.info(
            "source_tree_file_raw_read",
            project_id=str(project_id),
            scan_id=str(scan_id),
            path=member_path,
            byte_size=yielded,
        )
    finally:
        # Close both handles regardless of how the generator terminates
        # (exhausted, cap-abort, or GeneratorExit on client disconnect).
        try:
            extracted.close()
        finally:
            tar.close()


async def read_file_raw(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    raw_path: str,
    scan_id: uuid.UUID | None,
    actor: CurrentUser,
) -> RawFileContent:
    """Open one preserved member for a STREAMING raw download (G3.3).

    The in-app viewer (:func:`read_file`) caps content at
    ``scan_source_viewer_max_file_bytes()`` for the rendered preview. The
    download button on a truncated / binary file needs the WHOLE member, so this
    path streams the full member — bounded only by the generous
    ``scan_source_raw_download_max_bytes()`` ceiling.

    Memory bound: the member is NOT read into a single ``bytes`` (which would peak
    at up to 512 MiB per request, then be buffered AGAIN by the response). The
    returned :class:`RawFileContent` carries a ``chunks`` generator that yields
    the member in ``_RAW_STREAM_CHUNK_BYTES`` (64 KiB) slices, so peak resident
    body memory is one chunk. The router hands ``chunks`` to a ``StreamingResponse``.

    It reuses EXACTLY the same defences as :func:`read_file`:
      - the same ``?path=`` sanitisation (NUL / absolute / ``..`` rejection),
      - the same exact-name member lookup (never a filesystem join),
      - the same non-regular-member (symlink / hardlink / device / fifo) refusal,
      - the same team scoping + 404 existence-hide via ``_resolve_accessible_scan``,
      - the same UUID-only on-disk tarball path.

    All of the above run EAGERLY (before any byte is streamed) so a rejection
    surfaces as an RFC 7807 problem response, never a partial 200 body. The
    raw-download cap is enforced both eagerly (declared member size) AND while
    streaming (running byte tally) — see :func:`_stream_member_bytes`.

    Raises :class:`SourceFileTooLarge` (413) when the member exceeds the raw cap.
    """
    member_path = _sanitize_member_path(raw_path)
    if not member_path:
        # The root is a directory, not a file.
        raise SourceFileTooLarge("the source root is a directory, not a file")

    _project, resolved_scan = await _resolve_accessible_scan(
        session, project_id=project_id, scan_id=scan_id, actor=actor
    )

    cap = scan_source_raw_download_max_bytes()

    def _eager_open() -> tuple[tarfile.TarFile, Any, int]:
        # Eager validation + open is blocking gzip I/O → offload off the event
        # loop (Tier 3 hardening). The streaming generator (run by Starlette in
        # its own threadpool) then owns the open tar. On ANY eager failure we
        # close the tar here — the generator never runs in those cases.
        tar = _open_tarball(project_id, resolved_scan)
        try:
            info = _lookup_member(tar.getmembers(), member_path)

            if info.isdir():
                raise SourceFileTooLarge(f"{member_path!r} is a directory, not a file")
            # Reject non-regular members (symlink / hardlink / device / fifo).
            if info.type in _NON_REGULAR_TYPES or not info.isreg():
                raise SourceFileTooLarge(
                    f"{member_path!r} is not a regular file (type {info.type!r})"
                )

            full_size = int(info.size)
            # Refuse a member over the raw cap rather than streaming an unbounded
            # body. The explicit guard keeps the bound honest BEFORE the body.
            if full_size > cap:
                raise SourceFileTooLarge(
                    f"{member_path!r} is {full_size} bytes, over the "
                    f"{cap}-byte raw-download cap"
                )

            extracted = tar.extractfile(info)
            if extracted is None:  # pragma: no cover — isreg() guards this
                raise SourceUnavailable(
                    f"could not read member from preserved source: {member_path!r}"
                )
            return tar, extracted, full_size
        except BaseException:
            tar.close()
            raise

    tar, extracted, full_size = await run_in_threadpool(_eager_open)

    filename = member_path.rsplit("/", 1)[-1] or "download"

    # The generator now OWNS ``tar`` + ``extracted`` and closes them when the
    # stream ends / aborts / is abandoned. No byte is read here — peak body
    # memory is one chunk inside ``StreamingResponse``.
    chunks = _stream_member_bytes(
        tar,
        extracted,
        cap=cap,
        chunk_size=_RAW_STREAM_CHUNK_BYTES,
        member_path=member_path,
        project_id=project_id,
        scan_id=resolved_scan,
    )

    return RawFileContent(
        scan_id=resolved_scan,
        path=member_path,
        filename=filename,
        byte_size=full_size,
        chunks=chunks,
    )


# ---------------------------------------------------------------------------
# Per-line license-match projection from the folded scancode JSON
# ---------------------------------------------------------------------------


def _line_matches_for_path(
    tar: tarfile.TarFile,
    members: list[tarfile.TarInfo],
    *,
    project_id: uuid.UUID,
    scan_id: uuid.UUID,
    member_path: str,
) -> list[LineMatch]:
    """Project per-line license matches for ONE path from the folded scancode JSON.

    The scancode result lives inside the tarball as ``.trustedoss/scancode.json``
    (G3.1). scancode 32.x emits ``{"files": [{"path": ..., "license_detections":
    [{"matches": [{"license_expression_spdx": ..., "start_line": ..., "end_line":
    ..., "score": ...}]}]}]}``. We read ONLY the entry whose ``path`` equals
    ``member_path`` and flatten its matches.

    G3.2 Low (b): this reads the scancode member from the ALREADY-OPEN ``tar``
    handed in by :func:`read_file` (with its pre-parsed ``members`` list), so the
    tarball is opened and walked exactly ONCE per request — no second
    ``tarfile.open`` / ``getmembers`` pass.

    Best-effort: a missing / over-cap / unparseable JSON yields ``[]`` — the
    per-line view is auxiliary (the file content + badge set still render).
    The parse is bounded by ``scancode_max_result_bytes()``.
    """
    member = next(
        (m for m in members if _canonical_arcname(m.name) == SCANCODE_MEMBER_NAME),
        None,
    )
    if member is None or not member.isreg():
        return []
    limit = scancode_max_result_bytes()
    if int(member.size) > limit:
        log.warning(
            "source_tree_scancode_too_large",
            project_id=str(project_id),
            scan_id=str(scan_id),
            size_bytes=int(member.size),
            limit_bytes=limit,
        )
        return []
    try:
        extracted = tar.extractfile(member)
        if extracted is None:  # pragma: no cover
            return []
        with extracted:
            raw = extracted.read(limit + 1)
        if len(raw) > limit:  # pragma: no cover — size checked above
            return []
        data = json.loads(raw.decode("utf-8"))
    except (tarfile.TarError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        log.warning(
            "source_tree_scancode_unreadable",
            project_id=str(project_id),
            scan_id=str(scan_id),
            error=type(exc).__name__,
        )
        return []

    return _matches_from_scancode(data, member_path=member_path)


def _matches_from_scancode(data: Any, *, member_path: str) -> list[LineMatch]:
    """Pull the per-line matches for ``member_path`` out of a scancode document.

    Defensive against shape drift: every level is type-checked and a malformed
    entry is skipped rather than raising. The scancode ``path`` may carry a
    leading ``./`` or a top-level directory prefix; we compare on the normalised
    tail so ``src/a.py`` matches ``./src/a.py``.
    """
    if not isinstance(data, dict):
        return []
    files = data.get("files")
    if not isinstance(files, list):
        return []

    out: list[LineMatch] = []
    for entry in files:
        if not isinstance(entry, dict):
            continue
        path = entry.get("path")
        if not isinstance(path, str):
            continue
        if _canonical_arcname(path) != member_path:
            continue
        detections = entry.get("license_detections")
        if not isinstance(detections, list):
            continue
        for detection in detections:
            if not isinstance(detection, dict):
                continue
            matches = detection.get("matches")
            if not isinstance(matches, list):
                continue
            for match in matches:
                line_match = _parse_match(match)
                if line_match is not None:
                    out.append(line_match)
    return out


def _parse_match(match: Any) -> LineMatch | None:
    """Build a :class:`LineMatch` from one scancode ``matches[]`` entry, or None.

    Requires a non-empty SPDX id and positive, ordered line numbers. ``score``
    is optional (null when absent / non-numeric).
    """
    if not isinstance(match, dict):
        return None
    spdx = match.get("license_expression_spdx") or match.get("spdx_license_expression")
    if not isinstance(spdx, str) or not spdx.strip():
        return None
    start = match.get("start_line")
    end = match.get("end_line")
    if not isinstance(start, int) or not isinstance(end, int):
        return None
    if start < 1 or end < start:
        return None
    score = match.get("score")
    parsed_score: float | None
    if isinstance(score, int | float) and not isinstance(score, bool):
        parsed_score = float(score)
    else:
        parsed_score = None
    return LineMatch(
        spdx_id=spdx.strip(),
        start_line=start,
        end_line=end,
        score=parsed_score,
    )


__all__ = [
    "FileContent",
    "LineMatch",
    "RawFileContent",
    "SourceFileTooLarge",
    "SourcePathRejected",
    "SourceTreeError",
    "SourceUnavailable",
    "TreeEntry",
    "TreePage",
    "list_dir",
    "read_file",
    "read_file_raw",
]
