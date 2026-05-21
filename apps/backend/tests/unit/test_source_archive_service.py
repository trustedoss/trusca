"""
Unit tests for `services/source_archive_service.py` — feat/zip-upload.

These run with NO database and NO Postgres. The project-lookup path in
`save_uploaded_archive` is exercised through a tiny fake AsyncSession + fake
result so the security behaviour (size / extension / magic / RBAC) can be
asserted hermetically. Extraction tests build real on-disk zips in a tmp_path
and assert the slip / bomb / symlink defences.

Security focus (adversarial parametrize per MEMORY: untrusted-input parsing):
  - magic / MIME / extension forgery
  - oversized upload (mid-stream abort)
  - empty zip + truncated body
  - zip slip (../ traversal, absolute path, backslash traversal, prefix-sibling)
  - zip bomb (total size, member count, per-member compression ratio, lying
    header that streams past its declared size)
  - symlink / device / fifo members
  - nested zip (a zip member that is itself a zip — must extract as a plain file,
    never be recursively expanded)
"""

from __future__ import annotations

import io
import struct
import uuid
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from core.security import CurrentUser
from services.source_archive_service import (
    ArchiveExtractionRejected,
    ArchiveInvalid,
    ArchiveNotFound,
    ArchiveProjectNotFound,
    ArchiveQuotaExceeded,
    ArchiveTooLarge,
    ArchiveUnsupportedType,
    archive_path,
    archives_dir_for_project,
    delete_archive,
    resolve_existing_archive,
    safe_extract_archive,
    save_uploaded_archive,
)

# ---------------------------------------------------------------------------
# Fakes — keep these tests DB-free + HTTP-free
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class _FakeSession:
    """Minimal stand-in for AsyncSession.execute(...).scalar_one_or_none()."""

    def __init__(self, project: Any) -> None:
        self._project = project

    async def execute(self, _stmt: Any) -> _FakeResult:
        return _FakeResult(self._project)


class _FakeUpload:
    """Minimal stand-in for starlette UploadFile (read/filename/content_type)."""

    def __init__(self, body: bytes, *, filename: str, content_type: str) -> None:
        self._buf = io.BytesIO(body)
        self.filename = filename
        self.content_type = content_type

    async def read(self, size: int = -1) -> bytes:
        return self._buf.read(size)


def _principal(*, team_ids: list[uuid.UUID], super_admin: bool = False) -> CurrentUser:
    return CurrentUser(
        id=uuid.uuid4(),
        email="dev@example.com",
        role="super_admin" if super_admin else "developer",
        team_ids=team_ids,
        team_roles={tid: "developer" for tid in team_ids},
        is_active=True,
        is_superuser=super_admin,
    )


def _make_zip(members: dict[str, bytes], *, compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=compression) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point workspace_root() at a clean tmp dir for every test (rule #11)."""
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    return tmp_path


# ===========================================================================
# save_uploaded_archive — happy path + RBAC
# ===========================================================================


async def test_save_uploaded_archive_happy_path_writes_file(_workspace: Path) -> None:
    team_id = uuid.uuid4()
    project_id = uuid.uuid4()
    project = SimpleNamespace(id=project_id, team_id=team_id)
    session = _FakeSession(project)
    actor = _principal(team_ids=[team_id])

    body = _make_zip({"src/main.py": b"print('hi')\n"})
    upload = _FakeUpload(body, filename="source.zip", content_type="application/zip")

    archive_id = await save_uploaded_archive(
        session, project_id=project_id, upload=upload, actor=actor  # type: ignore[arg-type]
    )

    # archive_id is a UUID; the file exists at the resolved path.
    uuid.UUID(archive_id)  # raises if not a UUID
    path = archive_path(project_id, archive_id)
    assert path.is_file()
    assert path.read_bytes() == body


async def test_save_uploaded_archive_super_admin_bypasses_team_check(_workspace: Path) -> None:
    project = SimpleNamespace(id=uuid.uuid4(), team_id=uuid.uuid4())
    session = _FakeSession(project)
    actor = _principal(team_ids=[], super_admin=True)  # member of no team

    upload = _FakeUpload(
        _make_zip({"a.txt": b"x"}), filename="s.zip", content_type="application/zip"
    )
    archive_id = await save_uploaded_archive(
        session, project_id=project.id, upload=upload, actor=actor  # type: ignore[arg-type]
    )
    assert archive_path(project.id, archive_id).is_file()


async def test_save_uploaded_archive_other_team_is_404_existence_hide(_workspace: Path) -> None:
    """A project in another team must 404 (not 403) — no cross-team enumeration."""
    project = SimpleNamespace(id=uuid.uuid4(), team_id=uuid.uuid4())
    session = _FakeSession(project)
    actor = _principal(team_ids=[uuid.uuid4()])  # different team

    upload = _FakeUpload(
        _make_zip({"a.txt": b"x"}), filename="s.zip", content_type="application/zip"
    )
    with pytest.raises(ArchiveProjectNotFound) as ei:
        await save_uploaded_archive(
            session, project_id=project.id, upload=upload, actor=actor  # type: ignore[arg-type]
        )
    assert ei.value.status_code == 404


async def test_save_uploaded_archive_missing_project_is_404(_workspace: Path) -> None:
    session = _FakeSession(None)  # project does not exist
    actor = _principal(team_ids=[uuid.uuid4()])
    upload = _FakeUpload(
        _make_zip({"a.txt": b"x"}), filename="s.zip", content_type="application/zip"
    )
    with pytest.raises(ArchiveProjectNotFound):
        await save_uploaded_archive(
            session, project_id=uuid.uuid4(), upload=upload, actor=actor  # type: ignore[arg-type]
        )


# ===========================================================================
# save_uploaded_archive — extension / content-type / magic forgery
# ===========================================================================


@pytest.mark.parametrize(
    "filename",
    ["source.tar.gz", "source.tar", "source", "source.zip.exe", "evil.ZIPx", ""],
)
async def test_save_rejects_bad_extension(_workspace: Path, filename: str) -> None:
    project = SimpleNamespace(id=uuid.uuid4(), team_id=uuid.uuid4())
    actor = _principal(team_ids=[project.team_id])
    upload = _FakeUpload(
        _make_zip({"a.txt": b"x"}), filename=filename, content_type="application/zip"
    )
    with pytest.raises(ArchiveUnsupportedType) as ei:
        await save_uploaded_archive(
            _FakeSession(project), project_id=project.id, upload=upload, actor=actor  # type: ignore[arg-type]
        )
    assert ei.value.status_code == 415


@pytest.mark.parametrize(
    "content_type",
    ["text/html", "image/png", "application/x-msdownload", "application/json"],
)
async def test_save_rejects_bad_content_type(_workspace: Path, content_type: str) -> None:
    project = SimpleNamespace(id=uuid.uuid4(), team_id=uuid.uuid4())
    actor = _principal(team_ids=[project.team_id])
    upload = _FakeUpload(
        _make_zip({"a.txt": b"x"}), filename="s.zip", content_type=content_type
    )
    with pytest.raises(ArchiveUnsupportedType):
        await save_uploaded_archive(
            _FakeSession(project), project_id=project.id, upload=upload, actor=actor  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "forged",
    [
        b"GIF89a" + b"\x00" * 64,  # real GIF magic, .zip name + zip MIME
        b"\x7fELF" + b"\x00" * 64,  # ELF binary
        b"%PDF-1.7\n" + b"\x00" * 32,  # PDF
        b"PK\x03\x05bogus",  # near-miss: third byte wrong
        b"not-a-zip-at-all-just-text-content-here",
    ],
)
async def test_save_rejects_magic_forgery(_workspace: Path, forged: bytes) -> None:
    """A .zip name + zip MIME but non-zip magic bytes must be rejected (415)."""
    project = SimpleNamespace(id=uuid.uuid4(), team_id=uuid.uuid4())
    actor = _principal(team_ids=[project.team_id])
    upload = _FakeUpload(forged, filename="s.zip", content_type="application/zip")
    with pytest.raises(ArchiveUnsupportedType):
        await save_uploaded_archive(
            _FakeSession(project), project_id=project.id, upload=upload, actor=actor  # type: ignore[arg-type]
        )


async def test_save_rejects_empty_zip(_workspace: Path) -> None:
    """An archive with no entries starts with the EOCD marker — degenerate."""
    project = SimpleNamespace(id=uuid.uuid4(), team_id=uuid.uuid4())
    actor = _principal(team_ids=[project.team_id])
    empty = _make_zip({})  # starts with PK\x05\x06
    assert empty[:4] == b"PK\x05\x06"
    upload = _FakeUpload(empty, filename="s.zip", content_type="application/zip")
    with pytest.raises(ArchiveInvalid):
        await save_uploaded_archive(
            _FakeSession(project), project_id=project.id, upload=upload, actor=actor  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("body", [b"", b"P", b"PK", b"PK\x03"])
async def test_save_rejects_truncated_body(_workspace: Path, body: bytes) -> None:
    project = SimpleNamespace(id=uuid.uuid4(), team_id=uuid.uuid4())
    actor = _principal(team_ids=[project.team_id])
    upload = _FakeUpload(body, filename="s.zip", content_type="application/zip")
    with pytest.raises(ArchiveInvalid):
        await save_uploaded_archive(
            _FakeSession(project), project_id=project.id, upload=upload, actor=actor  # type: ignore[arg-type]
        )


# ===========================================================================
# save_uploaded_archive — size cap (mid-stream abort)
# ===========================================================================


async def test_save_rejects_oversized_upload_and_deletes_partial(
    _workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SOURCE_ARCHIVE_MAX_BYTES", "512")  # tiny cap
    project = SimpleNamespace(id=uuid.uuid4(), team_id=uuid.uuid4())
    actor = _principal(team_ids=[project.team_id])

    # A valid zip header so we pass the magic check, then > 512 bytes total.
    big = _make_zip({"big.bin": b"A" * 4096}, compression=zipfile.ZIP_STORED)
    assert len(big) > 512
    upload = _FakeUpload(big, filename="s.zip", content_type="application/zip")

    with pytest.raises(ArchiveTooLarge) as ei:
        await save_uploaded_archive(
            _FakeSession(project), project_id=project.id, upload=upload, actor=actor  # type: ignore[arg-type]
        )
    assert ei.value.status_code == 413
    # No partial file left behind.
    from services.source_archive_service import archives_dir_for_project

    leftovers = list(archives_dir_for_project(project.id).glob("*.zip"))
    assert leftovers == []


# ===========================================================================
# resolve_existing_archive
# ===========================================================================


def test_resolve_existing_archive_missing_raises(_workspace: Path) -> None:
    with pytest.raises(ArchiveNotFound):
        resolve_existing_archive(uuid.uuid4(), str(uuid.uuid4()))


@pytest.mark.parametrize(
    "bad_id",
    ["../../etc/passwd", "not-a-uuid", "..", "", "a/b", "${IFS}"],
)
def test_resolve_existing_archive_rejects_non_uuid_id(_workspace: Path, bad_id: str) -> None:
    """archive_id from JSONB metadata must be a UUID — no path traversal."""
    with pytest.raises(ArchiveNotFound):
        resolve_existing_archive(uuid.uuid4(), bad_id)


def test_resolve_existing_archive_found(_workspace: Path) -> None:
    project_id = uuid.uuid4()
    archive_id = uuid.uuid4()
    path = archive_path(project_id, str(archive_id))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_make_zip({"a.txt": b"x"}))
    assert resolve_existing_archive(project_id, str(archive_id)) == path


# ===========================================================================
# safe_extract_archive — happy path
# ===========================================================================


def test_safe_extract_happy_path(_workspace: Path, tmp_path: Path) -> None:
    zip_bytes = _make_zip(
        {
            "README.md": b"# hi\n",
            "src/app.py": b"x = 1\n",
            "src/sub/util.py": b"y = 2\n",
        }
    )
    archive = tmp_path / "src.zip"
    archive.write_bytes(zip_bytes)
    target = tmp_path / "out"

    safe_extract_archive(archive_path=archive, target_dir=target)

    assert (target / "README.md").read_bytes() == b"# hi\n"
    assert (target / "src" / "app.py").read_bytes() == b"x = 1\n"
    assert (target / "src" / "sub" / "util.py").read_bytes() == b"y = 2\n"


def test_safe_extract_creates_directory_members(_workspace: Path, tmp_path: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("emptydir/", b"")
        zf.writestr("emptydir/file.txt", b"hi")
    archive = tmp_path / "d.zip"
    archive.write_bytes(buf.getvalue())
    target = tmp_path / "out"
    safe_extract_archive(archive_path=archive, target_dir=target)
    assert (target / "emptydir").is_dir()
    assert (target / "emptydir" / "file.txt").read_bytes() == b"hi"


def test_safe_extract_nested_zip_is_a_plain_file(_workspace: Path, tmp_path: Path) -> None:
    """A zip member that is itself a zip must be written as a file, not expanded."""
    inner = _make_zip({"secret": b"do-not-expand"})
    outer = _make_zip({"vendor/inner.zip": inner})
    archive = tmp_path / "nested.zip"
    archive.write_bytes(outer)
    target = tmp_path / "out"
    safe_extract_archive(archive_path=archive, target_dir=target)
    written = target / "vendor" / "inner.zip"
    assert written.read_bytes() == inner
    # The inner zip's member must NOT have been materialised at top level.
    assert not (target / "secret").exists()


def test_safe_extract_corrupt_zip_raises_invalid(_workspace: Path, tmp_path: Path) -> None:
    archive = tmp_path / "corrupt.zip"
    archive.write_bytes(b"PK\x03\x04" + b"\xff" * 200)  # bad central directory
    target = tmp_path / "out"
    with pytest.raises(ArchiveInvalid):
        safe_extract_archive(archive_path=archive, target_dir=target)


# ===========================================================================
# safe_extract_archive — ZIP SLIP
# ===========================================================================


@pytest.mark.parametrize(
    "evil_name",
    [
        "../escape.txt",
        "../../escape.txt",
        "src/../../escape.txt",
        "a/b/c/../../../../escape.txt",
        "..\\escape.txt",  # backslash traversal (Windows-authored)
        "foo/..\\..\\escape.txt",
    ],
)
def test_safe_extract_rejects_zip_slip_relative(
    _workspace: Path, tmp_path: Path, evil_name: str
) -> None:
    # writestr would normalise some names; build the central directory by hand
    # via ZipInfo so the stored name is exactly the hostile string.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        info = zipfile.ZipInfo(filename=evil_name)
        zf.writestr(info, b"pwned")
    archive = tmp_path / "slip.zip"
    archive.write_bytes(buf.getvalue())
    target = tmp_path / "out"

    with pytest.raises(ArchiveExtractionRejected):
        safe_extract_archive(archive_path=archive, target_dir=target)

    # Nothing escaped the target dir.
    assert not (tmp_path / "escape.txt").exists()


@pytest.mark.parametrize(
    "abs_name",
    ["/etc/passwd", "/tmp/pwned", "\\windows\\system32\\evil"],
)
def test_safe_extract_rejects_absolute_member(
    _workspace: Path, tmp_path: Path, abs_name: str
) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        info = zipfile.ZipInfo(filename=abs_name)
        zf.writestr(info, b"pwned")
    archive = tmp_path / "abs.zip"
    archive.write_bytes(buf.getvalue())
    target = tmp_path / "out"
    with pytest.raises(ArchiveExtractionRejected):
        safe_extract_archive(archive_path=archive, target_dir=target)


def test_safe_extract_prefix_sibling_does_not_slip(
    _workspace: Path, tmp_path: Path
) -> None:
    """`/work` must not be considered inside `/workevil` (prefix-collision)."""
    target = tmp_path / "work"
    target.mkdir()
    # A member that would resolve to a sibling sharing the name prefix only if
    # the boundary check used a naive str.startswith. The component check makes
    # this a normal nested file, so this asserts the happy boundary stays safe.
    zip_bytes = _make_zip({"workevil_neighbor.txt": b"safe"})
    archive = tmp_path / "p.zip"
    archive.write_bytes(zip_bytes)
    safe_extract_archive(archive_path=archive, target_dir=target)
    assert (target / "workevil_neighbor.txt").is_file()
    assert not (tmp_path / "workevil_neighbor.txt").exists()


# ===========================================================================
# safe_extract_archive — ZIP BOMB
# ===========================================================================


def test_safe_extract_rejects_total_size_bomb(
    _workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SOURCE_ARCHIVE_MAX_EXTRACTED_BYTES", "1024")
    # Raise the ratio cap out of the way so the TOTAL-size cap is what trips
    # (STORED content has a 1x ratio anyway). Two members each under the
    # per-member declared size but together blowing past the 1024-byte total.
    monkeypatch.setenv("SOURCE_ARCHIVE_MAX_COMPRESSION_RATIO", "100000")
    zip_bytes = _make_zip(
        {"a.bin": b"A" * 4096, "b.bin": b"B" * 4096},
        compression=zipfile.ZIP_STORED,
    )
    archive = tmp_path / "bomb.zip"
    archive.write_bytes(zip_bytes)
    target = tmp_path / "out"
    with pytest.raises(ArchiveExtractionRejected) as ei:
        safe_extract_archive(archive_path=archive, target_dir=target)
    assert "zip bomb" in str(ei.value).lower() or "extracted size" in str(ei.value).lower()


def test_safe_extract_rejects_member_count_bomb(
    _workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SOURCE_ARCHIVE_MAX_MEMBERS", "5")
    zip_bytes = _make_zip({f"f{i}.txt": b"x" for i in range(20)})
    archive = tmp_path / "many.zip"
    archive.write_bytes(zip_bytes)
    target = tmp_path / "out"
    with pytest.raises(ArchiveExtractionRejected) as ei:
        safe_extract_archive(archive_path=archive, target_dir=target)
    assert "members" in str(ei.value).lower()


def test_safe_extract_rejects_compression_ratio_bomb(
    _workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SOURCE_ARCHIVE_MAX_COMPRESSION_RATIO", "10")
    # 256 KiB of zeros under deflate compresses ~1000x — well past a 10x ratio.
    zip_bytes = _make_zip({"ratio.bin": b"\x00" * (256 * 1024)})
    archive = tmp_path / "ratio.zip"
    archive.write_bytes(zip_bytes)
    target = tmp_path / "out"
    with pytest.raises(ArchiveExtractionRejected) as ei:
        safe_extract_archive(archive_path=archive, target_dir=target)
    assert "ratio" in str(ei.value).lower()


def test_safe_extract_understated_header_does_not_silently_truncate(
    _workspace: Path, tmp_path: Path
) -> None:
    """A member whose central-directory size LIES (understated) must not land a
    silently-truncated file in the workspace.

    CPython's ``zipfile.ZipExtFile`` enforces ``file_size`` as a read limit, so
    an understated header truncates the decompressed stream and trips a CRC
    mismatch. We assert that this surfaces as a hard rejection (ArchiveInvalid)
    and that no partial file is left behind — never a half-written member that
    a downstream scanner would treat as legitimate source.
    """
    real = b"B" * (4 * 1024 * 1024)  # 4 MiB
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("liar.bin", real)
    raw = bytearray(buf.getvalue())

    # Central directory file header signature is PK\x01\x02; the uncompressed
    # size is a 4-byte LE field at offset 24 within that record.
    cd_sig = b"PK\x01\x02"
    idx = raw.index(cd_sig)
    size_off = idx + 24
    assert raw[size_off:size_off + 4] == struct.pack("<I", len(real))
    raw[size_off:size_off + 4] = struct.pack("<I", 64)  # the lie

    archive = tmp_path / "liar.zip"
    archive.write_bytes(bytes(raw))
    target = tmp_path / "out"

    with pytest.raises((ArchiveInvalid, ArchiveExtractionRejected)):
        safe_extract_archive(archive_path=archive, target_dir=target)
    # No partial member left in the workspace.
    assert not (target / "liar.bin").exists()


# ===========================================================================
# safe_extract_archive — SYMLINK / DEVICE members
# ===========================================================================


def _zip_with_unix_mode(name: str, data: bytes, mode: int) -> bytes:
    """Build a zip whose member carries an explicit unix st_mode."""
    buf = io.BytesIO()
    info = zipfile.ZipInfo(filename=name)
    # external_attr high 16 bits = unix mode.
    info.external_attr = (mode & 0xFFFF) << 16
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(info, data)
    return buf.getvalue()


@pytest.mark.parametrize(
    ("mode", "label"),
    [
        (0o120777, "symlink"),
        (0o010644, "fifo"),
        (0o060644, "block_device"),
        (0o020644, "char_device"),
        (0o140644, "socket"),
    ],
)
def test_safe_extract_rejects_non_regular_members(
    _workspace: Path, tmp_path: Path, mode: int, label: str
) -> None:
    # A symlink member's "data" is the link target; the type bits are what
    # matter here.
    zip_bytes = _zip_with_unix_mode("evil_link", b"/etc/passwd", mode)
    archive = tmp_path / f"{label}.zip"
    archive.write_bytes(zip_bytes)
    target = tmp_path / "out"
    with pytest.raises(ArchiveExtractionRejected) as ei:
        safe_extract_archive(archive_path=archive, target_dir=target)
    assert "non-regular" in str(ei.value).lower()
    # No symlink was created.
    link = target / "evil_link"
    assert not link.is_symlink()


def test_safe_extract_accepts_regular_file_with_explicit_mode(
    _workspace: Path, tmp_path: Path
) -> None:
    zip_bytes = _zip_with_unix_mode("ok.sh", b"#!/bin/sh\n", 0o100755)
    archive = tmp_path / "ok.zip"
    archive.write_bytes(zip_bytes)
    target = tmp_path / "out"
    safe_extract_archive(archive_path=archive, target_dir=target)
    assert (target / "ok.sh").read_bytes() == b"#!/bin/sh\n"


# ===========================================================================
# _is_within — boundary unit (prefix-collision + base equality)
# ===========================================================================


def test_is_within_prefix_collision_and_equality(tmp_path: Path) -> None:
    from services.source_archive_service import _is_within

    base = (tmp_path / "work").resolve()
    base.mkdir()
    sibling = (tmp_path / "workevil").resolve()
    sibling.mkdir()

    # base itself counts as within.
    assert _is_within(base, base) is True
    # a real child is within.
    assert _is_within(base, base / "src" / "x.py") is True
    # a prefix-sibling (/work vs /workevil) must NOT be within.
    assert _is_within(base, sibling / "x.py") is False
    # a parent escape must NOT be within.
    assert _is_within(base, tmp_path) is False


# ===========================================================================
# limit accessors honour env at call time (rule #11)
# ===========================================================================


def test_limit_accessors_read_env_at_call_time(monkeypatch: pytest.MonkeyPatch) -> None:
    from services import source_archive_service as svc

    # defaults
    monkeypatch.delenv("SOURCE_ARCHIVE_MAX_BYTES", raising=False)
    assert svc._max_upload_bytes() == 100 * 1024 * 1024
    monkeypatch.setenv("SOURCE_ARCHIVE_MAX_BYTES", "123")
    assert svc._max_upload_bytes() == 123

    monkeypatch.delenv("SOURCE_ARCHIVE_MAX_EXTRACTED_BYTES", raising=False)
    assert svc._max_extracted_bytes() == 1024**3
    monkeypatch.setenv("SOURCE_ARCHIVE_MAX_EXTRACTED_BYTES", "456")
    assert svc._max_extracted_bytes() == 456

    monkeypatch.setenv("SOURCE_ARCHIVE_MAX_MEMBERS", "7")
    assert svc._max_members() == 7
    monkeypatch.setenv("SOURCE_ARCHIVE_MAX_COMPRESSION_RATIO", "9.5")
    assert svc._max_compression_ratio() == 9.5


def test_no_module_level_env_caching(monkeypatch: pytest.MonkeyPatch) -> None:
    """The module must resolve WORKSPACE_HOST_PATH at call time, not import time."""
    from services.source_archive_service import archives_dir_for_project

    pid = uuid.uuid4()
    monkeypatch.setenv("WORKSPACE_HOST_PATH", "/tmp/trustedoss-a")  # noqa: S108
    first = archives_dir_for_project(pid)
    monkeypatch.setenv("WORKSPACE_HOST_PATH", "/tmp/trustedoss-b")  # noqa: S108
    second = archives_dir_for_project(pid)
    assert first != second


# ===========================================================================
# Per-project storage quota (H-fix part b)
# ===========================================================================


def _seed_existing_archive(project_id: uuid.UUID, size: int) -> Path:
    """Drop a dummy ``*.zip`` of ``size`` bytes into the project archives dir."""
    path = archive_path(project_id, str(uuid.uuid4()))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * size)
    return path


async def test_save_rejects_when_project_already_at_quota(
    _workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An upload is refused (507) before any body byte when the project is full."""
    monkeypatch.setenv("SOURCE_ARCHIVE_PROJECT_QUOTA_BYTES", "1024")
    project = SimpleNamespace(id=uuid.uuid4(), team_id=uuid.uuid4())
    actor = _principal(team_ids=[project.team_id])
    # Existing usage already meets the quota.
    _seed_existing_archive(project.id, 1024)

    upload = _FakeUpload(
        _make_zip({"a.txt": b"x"}), filename="s.zip", content_type="application/zip"
    )
    with pytest.raises(ArchiveQuotaExceeded) as ei:
        await save_uploaded_archive(
            _FakeSession(project), project_id=project.id, upload=upload, actor=actor  # type: ignore[arg-type]
        )
    assert ei.value.status_code == 507
    # The pre-existing archive is untouched; no new file written.
    assert len(list(archives_dir_for_project(project.id).glob("*.zip"))) == 1


async def test_save_rejects_when_stream_crosses_quota_and_deletes_partial(
    _workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A body within the single-upload cap but tipping the project past quota
    is aborted mid-stream and its partial file deleted."""
    # Single-upload cap generous; project quota tight.
    monkeypatch.setenv("SOURCE_ARCHIVE_MAX_BYTES", str(10 * 1024 * 1024))
    monkeypatch.setenv("SOURCE_ARCHIVE_PROJECT_QUOTA_BYTES", "4096")
    project = SimpleNamespace(id=uuid.uuid4(), team_id=uuid.uuid4())
    actor = _principal(team_ids=[project.team_id])
    existing = _seed_existing_archive(project.id, 3072)  # 3 KiB already used

    # New upload is ~4 KiB stored — 3072 + 4096 > 4096 quota.
    body = _make_zip({"big.bin": b"A" * 8192}, compression=zipfile.ZIP_STORED)
    upload = _FakeUpload(body, filename="s.zip", content_type="application/zip")

    with pytest.raises(ArchiveQuotaExceeded):
        await save_uploaded_archive(
            _FakeSession(project), project_id=project.id, upload=upload, actor=actor  # type: ignore[arg-type]
        )
    # Only the originally-seeded archive remains; the partial was deleted.
    remaining = list(archives_dir_for_project(project.id).glob("*.zip"))
    assert remaining == [existing]


async def test_save_succeeds_when_under_quota(
    _workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SOURCE_ARCHIVE_PROJECT_QUOTA_BYTES", str(10 * 1024 * 1024))
    project = SimpleNamespace(id=uuid.uuid4(), team_id=uuid.uuid4())
    actor = _principal(team_ids=[project.team_id])
    _seed_existing_archive(project.id, 1024)

    upload = _FakeUpload(
        _make_zip({"a.txt": b"x"}), filename="s.zip", content_type="application/zip"
    )
    archive_id = await save_uploaded_archive(
        _FakeSession(project), project_id=project.id, upload=upload, actor=actor  # type: ignore[arg-type]
    )
    assert archive_path(project.id, archive_id).is_file()


def test_project_archive_bytes_sums_only_zip(_workspace: Path) -> None:
    from services.source_archive_service import _project_archive_bytes

    pid = uuid.uuid4()
    assert _project_archive_bytes(pid) == 0  # no dir yet
    _seed_existing_archive(pid, 100)
    _seed_existing_archive(pid, 250)
    # A non-zip file must NOT count toward the quota.
    (archives_dir_for_project(pid) / "notes.txt").write_bytes(b"x" * 999)
    assert _project_archive_bytes(pid) == 350


# ===========================================================================
# delete_archive (H-fix part a)
# ===========================================================================


def test_delete_archive_removes_existing_returns_true(_workspace: Path) -> None:
    pid = uuid.uuid4()
    aid = uuid.uuid4()
    path = archive_path(pid, str(aid))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_make_zip({"a.txt": b"x"}))
    assert delete_archive(pid, str(aid)) is True
    assert not path.exists()


def test_delete_archive_missing_returns_false(_workspace: Path) -> None:
    assert delete_archive(uuid.uuid4(), str(uuid.uuid4())) is False


@pytest.mark.parametrize("bad_id", ["../../etc/passwd", "not-a-uuid", "..", "", "a/b"])
def test_delete_archive_rejects_non_uuid_id(_workspace: Path, bad_id: str) -> None:
    """A crafted archive_id cannot drive an arbitrary unlink."""
    with pytest.raises(ArchiveNotFound):
        delete_archive(uuid.uuid4(), bad_id)


# ===========================================================================
# compress_size == 0 with positive file_size (L5-fix)
# ===========================================================================


def _zip_with_zero_compress_size(name: str, declared_file_size: int) -> bytes:
    """Forge a central-directory entry claiming 0 compressed / >0 uncompressed."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr(name, b"")  # a genuinely empty member (0/0)
    raw = bytearray(buf.getvalue())
    # Central directory header signature PK\x01\x02; uncompressed size is the
    # 4-byte LE field at offset 24 within that record. Lie that it is >0 while
    # the compressed-size field (offset 20) stays 0.
    cd_sig = b"PK\x01\x02"
    idx = raw.index(cd_sig)
    raw[idx + 24 : idx + 28] = struct.pack("<I", declared_file_size)
    return bytes(raw)


def test_safe_extract_rejects_zero_compress_positive_file_size(
    _workspace: Path, tmp_path: Path
) -> None:
    """compress_size==0 with file_size>0 is an impossible ratio → rejected."""
    archive = tmp_path / "zero.zip"
    archive.write_bytes(_zip_with_zero_compress_size("liar.bin", 1_000_000))
    target = tmp_path / "out"
    with pytest.raises((ArchiveExtractionRejected, ArchiveInvalid)):
        safe_extract_archive(archive_path=archive, target_dir=target)
    # Nothing left behind (M3-fix cleanup).
    assert not target.exists() or not any(target.iterdir())


def test_safe_extract_accepts_genuinely_empty_member(
    _workspace: Path, tmp_path: Path
) -> None:
    """A real 0-byte file (compress_size==0, file_size==0) is fine."""
    zip_bytes = _make_zip({"empty.txt": b""}, compression=zipfile.ZIP_STORED)
    archive = tmp_path / "empty.zip"
    archive.write_bytes(zip_bytes)
    target = tmp_path / "out"
    safe_extract_archive(archive_path=archive, target_dir=target)
    assert (target / "empty.txt").read_bytes() == b""


# ===========================================================================
# Partial-extract cleanup on rejection (M3-fix)
# ===========================================================================


def test_safe_extract_rejection_leaves_no_partial_tree(
    _workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When a later member trips the bomb guard, members already written must
    be swept — a rejected archive seeds ZERO bytes."""
    monkeypatch.setenv("SOURCE_ARCHIVE_MAX_EXTRACTED_BYTES", "2048")
    monkeypatch.setenv("SOURCE_ARCHIVE_MAX_COMPRESSION_RATIO", "100000")
    # First member fits, second tips the total cap.
    zip_bytes = _make_zip(
        {"keep/ok.txt": b"A" * 1024, "keep/bomb.bin": b"B" * 4096},
        compression=zipfile.ZIP_STORED,
    )
    archive = tmp_path / "bomb.zip"
    archive.write_bytes(zip_bytes)
    target = tmp_path / "out"
    with pytest.raises(ArchiveExtractionRejected):
        safe_extract_archive(archive_path=archive, target_dir=target)
    # The whole target tree is gone — not even the first (valid) member remains.
    assert not target.exists() or not any(target.rglob("*.txt"))


def test_safe_extract_corrupt_zip_sweeps_target(
    _workspace: Path, tmp_path: Path
) -> None:
    """A corrupt zip that fails to open leaves no target dir behind."""
    archive = tmp_path / "corrupt.zip"
    archive.write_bytes(b"PK\x03\x04" + b"\xff" * 200)
    target = tmp_path / "out"
    with pytest.raises(ArchiveInvalid):
        safe_extract_archive(archive_path=archive, target_dir=target)
    assert not target.exists()
