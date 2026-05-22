"""
Unit tests for ``services/source_tree_service.py`` — G3.2.

These run with NO database and NO Postgres. A tiny fake ``AsyncSession``
answers the three queries the service issues (project lookup, explicit-scan
validation, license-badge join) by inspecting the statement, so the security
behaviour (team scoping, existence-hide, path-traversal rejection) is asserted
hermetically. A real gzip tarball is built in-test under a tmp workspace so the
tar-member read path is exercised against actual ``tarfile`` semantics.

Security focus (adversarial parametrize per MEMORY: untrusted-input parsing):
  - hostile ``?path=``: ``../`` traversal, leading ``/``, backslash variants,
    absolute paths, NUL byte, encoded-ish traversal.
  - cross-team project (404 existence-hide, not 403).
  - explicit scan_id from another project (404, no leak).
  - non-regular tar member (symlink) refused on read.
  - per-file content cap → truncation; binary detection.
  - per-line match projection from the folded scancode JSON for ONE path only.
"""

from __future__ import annotations

import io
import json
import tarfile
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from core.security import CurrentUser
from services.source_preservation_service import (
    SCANCODE_MEMBER_NAME,
    scan_source_tarball_path,
)
from services.source_tree_service import (
    SourceFileTooLarge,
    SourcePathRejected,
    SourceUnavailable,
    _sanitize_member_path,
    list_dir,
    read_file,
    read_file_raw,
)

# ---------------------------------------------------------------------------
# Fakes — keep these tests DB-free + HTTP-free
# ---------------------------------------------------------------------------


class _ScalarResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class _RowsResult:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[Any, ...]]:
        return self._rows


class _FakeSession:
    """Dispatches ``execute`` by inspecting the compiled statement text.

    Three shapes the service issues:
      - ``SELECT projects...``        → project row (scalar_one_or_none)
      - ``SELECT scans.id...``        → scan id or None (scalar_one_or_none)
      - ``SELECT ... license_findings → (source_path, spdx_id) rows (.all)
    """

    def __init__(
        self,
        *,
        project: Any,
        scan_belongs: bool = True,
        badge_rows: list[tuple[str, str]] | None = None,
    ) -> None:
        self._project = project
        self._scan_belongs = scan_belongs
        self._badge_rows = badge_rows or []

    async def execute(self, stmt: Any) -> Any:
        text = str(stmt).lower()
        if "license_findings" in text:
            return _RowsResult(list(self._badge_rows))
        if "from scans" in text:
            return _ScalarResult(uuid.uuid4() if self._scan_belongs else None)
        # Default: the project lookup.
        return _ScalarResult(self._project)


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


# ---------------------------------------------------------------------------
# Tarball fixture helpers
# ---------------------------------------------------------------------------


def _write_tarball(
    *,
    project_id: uuid.UUID,
    scan_id: uuid.UUID,
    files: dict[str, bytes],
    dirs: list[str] | None = None,
    symlinks: dict[str, str] | None = None,
    scancode: dict[str, Any] | None = None,
) -> Path:
    """Build a real gzip tarball at the resolved preserved-source path."""
    dest = scan_source_tarball_path(project_id, scan_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(dest, mode="w:gz") as tar:
        for d in dirs or []:
            info = tarfile.TarInfo(name=d)
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            tar.addfile(info)
        for name, body in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(body)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(body))
        for link_name, target in (symlinks or {}).items():
            info = tarfile.TarInfo(name=link_name)
            info.type = tarfile.SYMTYPE
            info.linkname = target
            tar.addfile(info)
        if scancode is not None:
            body = json.dumps(scancode).encode("utf-8")
            info = tarfile.TarInfo(name=SCANCODE_MEMBER_NAME)
            info.size = len(body)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(body))
    return dest


@pytest.fixture(autouse=True)
def _workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point workspace_root() at a clean tmp dir for every test (rule #11)."""
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    return tmp_path


def _project(team_id: uuid.UUID, latest_scan_id: uuid.UUID | None) -> Any:
    return SimpleNamespace(
        id=uuid.uuid4(), team_id=team_id, latest_scan_id=latest_scan_id
    )


# ===========================================================================
# list_dir — happy path, ordering, paging
# ===========================================================================


async def test_list_dir_root_lists_immediate_children_folder_first() -> None:
    team_id = uuid.uuid4()
    scan_id = uuid.uuid4()
    project = _project(team_id, scan_id)
    _write_tarball(
        project_id=project.id,
        scan_id=scan_id,
        files={
            "README.md": b"# hi\n",
            "src/main.py": b"print('x')\n",
            "src/util/helper.py": b"pass\n",
        },
    )
    session = _FakeSession(project=project)
    actor = _principal(team_ids=[team_id])

    page = await list_dir(
        session,  # type: ignore[arg-type]
        project_id=project.id,
        raw_path="",
        scan_id=None,
        actor=actor,
        page=1,
        size=50,
    )

    assert page.scan_id == scan_id
    assert page.path == ""
    names = [(e.name, e.is_dir) for e in page.entries]
    # Directory ``src`` (inferred from src/main.py) sorts before file README.md.
    assert names == [("src", True), ("README.md", False)]
    assert page.total == 2


async def test_list_dir_subdir_lists_only_immediate_children() -> None:
    team_id = uuid.uuid4()
    scan_id = uuid.uuid4()
    project = _project(team_id, scan_id)
    _write_tarball(
        project_id=project.id,
        scan_id=scan_id,
        files={
            "src/main.py": b"a\n",
            "src/util/helper.py": b"b\n",
        },
    )
    session = _FakeSession(project=project)
    actor = _principal(team_ids=[team_id])

    page = await list_dir(
        session,  # type: ignore[arg-type]
        project_id=project.id,
        raw_path="src",
        scan_id=None,
        actor=actor,
        page=1,
        size=50,
    )
    names = [(e.name, e.is_dir, e.path) for e in page.entries]
    assert names == [
        ("util", True, "src/util"),
        ("main.py", False, "src/main.py"),
    ]


async def test_list_dir_paging_windows_the_sorted_children() -> None:
    team_id = uuid.uuid4()
    scan_id = uuid.uuid4()
    project = _project(team_id, scan_id)
    _write_tarball(
        project_id=project.id,
        scan_id=scan_id,
        files={f"f{i:02d}.txt": b"x" for i in range(10)},
    )
    session = _FakeSession(project=project)
    actor = _principal(team_ids=[team_id])

    page2 = await list_dir(
        session,  # type: ignore[arg-type]
        project_id=project.id,
        raw_path="",
        scan_id=None,
        actor=actor,
        page=2,
        size=3,
    )
    assert page2.total == 10
    assert page2.page == 2
    assert [e.name for e in page2.entries] == ["f03.txt", "f04.txt", "f05.txt"]


async def test_list_dir_attaches_license_badges_for_files() -> None:
    team_id = uuid.uuid4()
    scan_id = uuid.uuid4()
    project = _project(team_id, scan_id)
    _write_tarball(
        project_id=project.id,
        scan_id=scan_id,
        files={"LICENSE": b"MIT...\n"},
    )
    session = _FakeSession(
        project=project,
        badge_rows=[("LICENSE", "MIT"), ("LICENSE", "Apache-2.0")],
    )
    actor = _principal(team_ids=[team_id])

    page = await list_dir(
        session,  # type: ignore[arg-type]
        project_id=project.id,
        raw_path="",
        scan_id=None,
        actor=actor,
        page=1,
        size=50,
    )
    (entry,) = page.entries
    assert entry.license_spdx_ids == ["Apache-2.0", "MIT"]  # sorted


async def test_list_dir_excludes_reserved_scancode_member() -> None:
    team_id = uuid.uuid4()
    scan_id = uuid.uuid4()
    project = _project(team_id, scan_id)
    _write_tarball(
        project_id=project.id,
        scan_id=scan_id,
        files={"a.py": b"x\n"},
        scancode={"files": []},
    )
    session = _FakeSession(project=project)
    actor = _principal(team_ids=[team_id])

    page = await list_dir(
        session,  # type: ignore[arg-type]
        project_id=project.id,
        raw_path="",
        scan_id=None,
        actor=actor,
        page=1,
        size=50,
    )
    names = [e.name for e in page.entries]
    assert names == ["a.py"]
    assert ".trustedoss" not in names


# ===========================================================================
# RBAC / existence-hide
# ===========================================================================


async def test_list_dir_other_team_is_404_existence_hide() -> None:
    """A project in another team must 404 (not 403) — no cross-team enumeration."""
    project = _project(team_id=uuid.uuid4(), latest_scan_id=uuid.uuid4())
    session = _FakeSession(project=project)
    actor = _principal(team_ids=[uuid.uuid4()])  # different team

    with pytest.raises(SourceUnavailable):
        await list_dir(
            session,  # type: ignore[arg-type]
            project_id=project.id,
            raw_path="",
            scan_id=None,
            actor=actor,
            page=1,
            size=50,
        )


async def test_list_dir_super_admin_bypasses_team_check() -> None:
    team_id = uuid.uuid4()
    scan_id = uuid.uuid4()
    project = _project(team_id, scan_id)
    _write_tarball(project_id=project.id, scan_id=scan_id, files={"a.py": b"x\n"})
    session = _FakeSession(project=project)
    actor = _principal(team_ids=[], super_admin=True)  # member of no team

    page = await list_dir(
        session,  # type: ignore[arg-type]
        project_id=project.id,
        raw_path="",
        scan_id=None,
        actor=actor,
        page=1,
        size=50,
    )
    assert [e.name for e in page.entries] == ["a.py"]


async def test_list_dir_unknown_project_is_404() -> None:
    session = _FakeSession(project=None)
    actor = _principal(team_ids=[uuid.uuid4()])
    with pytest.raises(SourceUnavailable):
        await list_dir(
            session,  # type: ignore[arg-type]
            project_id=uuid.uuid4(),
            raw_path="",
            scan_id=None,
            actor=actor,
            page=1,
            size=50,
        )


async def test_list_dir_no_latest_scan_is_404() -> None:
    team_id = uuid.uuid4()
    project = _project(team_id, latest_scan_id=None)
    session = _FakeSession(project=project)
    actor = _principal(team_ids=[team_id])
    with pytest.raises(SourceUnavailable):
        await list_dir(
            session,  # type: ignore[arg-type]
            project_id=project.id,
            raw_path="",
            scan_id=None,
            actor=actor,
            page=1,
            size=50,
        )


async def test_list_dir_explicit_scan_from_other_project_is_404() -> None:
    team_id = uuid.uuid4()
    project = _project(team_id, latest_scan_id=uuid.uuid4())
    # scan_belongs=False → the scans.id validation query returns None.
    session = _FakeSession(project=project, scan_belongs=False)
    actor = _principal(team_ids=[team_id])
    with pytest.raises(SourceUnavailable):
        await list_dir(
            session,  # type: ignore[arg-type]
            project_id=project.id,
            raw_path="",
            scan_id=uuid.uuid4(),  # belongs to another project
            actor=actor,
            page=1,
            size=50,
        )


async def test_list_dir_swept_tarball_is_404() -> None:
    team_id = uuid.uuid4()
    scan_id = uuid.uuid4()
    project = _project(team_id, scan_id)
    # No tarball written → swept / never written.
    session = _FakeSession(project=project)
    actor = _principal(team_ids=[team_id])
    with pytest.raises(SourceUnavailable):
        await list_dir(
            session,  # type: ignore[arg-type]
            project_id=project.id,
            raw_path="",
            scan_id=None,
            actor=actor,
            page=1,
            size=50,
        )


# ===========================================================================
# Path-traversal rejection (the headline security surface)
# ===========================================================================


@pytest.mark.parametrize(
    "hostile",
    [
        "../etc/passwd",
        "a/../../etc/passwd",
        "/etc/passwd",
        "\\windows\\system32",
        "..\\..\\secret",
        "src/../../..",
        "foo/\x00bar",
        "/",
    ],
)
async def test_list_dir_rejects_hostile_path(hostile: str) -> None:
    team_id = uuid.uuid4()
    scan_id = uuid.uuid4()
    project = _project(team_id, scan_id)
    _write_tarball(project_id=project.id, scan_id=scan_id, files={"a.py": b"x\n"})
    session = _FakeSession(project=project)
    actor = _principal(team_ids=[team_id])
    with pytest.raises(SourcePathRejected):
        await list_dir(
            session,  # type: ignore[arg-type]
            project_id=project.id,
            raw_path=hostile,
            scan_id=None,
            actor=actor,
            page=1,
            size=50,
        )


@pytest.mark.parametrize(
    "hostile",
    [
        "../etc/passwd",
        "/etc/passwd",
        "..\\..\\secret",
        "x\x00.py",
    ],
)
async def test_read_file_rejects_hostile_path(hostile: str) -> None:
    team_id = uuid.uuid4()
    scan_id = uuid.uuid4()
    project = _project(team_id, scan_id)
    _write_tarball(project_id=project.id, scan_id=scan_id, files={"a.py": b"x\n"})
    session = _FakeSession(project=project)
    actor = _principal(team_ids=[team_id])
    with pytest.raises(SourcePathRejected):
        await read_file(
            session,  # type: ignore[arg-type]
            project_id=project.id,
            raw_path=hostile,
            scan_id=None,
            actor=actor,
        )


# ===========================================================================
# read_file — content cap, binary detection, line matches
# ===========================================================================


async def test_read_file_returns_text_content() -> None:
    team_id = uuid.uuid4()
    scan_id = uuid.uuid4()
    project = _project(team_id, scan_id)
    body = b"line1\nline2\n"
    _write_tarball(
        project_id=project.id, scan_id=scan_id, files={"src/main.py": body}
    )
    session = _FakeSession(project=project)
    actor = _principal(team_ids=[team_id])

    result = await read_file(
        session,  # type: ignore[arg-type]
        project_id=project.id,
        raw_path="src/main.py",
        scan_id=None,
        actor=actor,
    )
    assert result.encoding == "utf-8"
    assert result.content == "line1\nline2\n"
    assert result.byte_size == len(body)
    assert result.truncated is False


async def test_read_file_caps_and_marks_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCAN_SOURCE_VIEWER_MAX_FILE_BYTES", "8")
    team_id = uuid.uuid4()
    scan_id = uuid.uuid4()
    project = _project(team_id, scan_id)
    body = b"0123456789ABCDEF"  # 16 bytes, cap is 8
    _write_tarball(project_id=project.id, scan_id=scan_id, files={"big.txt": body})
    session = _FakeSession(project=project)
    actor = _principal(team_ids=[team_id])

    result = await read_file(
        session,  # type: ignore[arg-type]
        project_id=project.id,
        raw_path="big.txt",
        scan_id=None,
        actor=actor,
    )
    assert result.truncated is True
    assert result.content == "01234567"
    assert result.byte_size == 16


async def test_read_file_binary_has_no_content() -> None:
    team_id = uuid.uuid4()
    scan_id = uuid.uuid4()
    project = _project(team_id, scan_id)
    body = b"\x89PNG\x00\x01\x02binary"
    _write_tarball(project_id=project.id, scan_id=scan_id, files={"img.png": body})
    session = _FakeSession(project=project)
    actor = _principal(team_ids=[team_id])

    result = await read_file(
        session,  # type: ignore[arg-type]
        project_id=project.id,
        raw_path="img.png",
        scan_id=None,
        actor=actor,
    )
    assert result.encoding == "binary"
    assert result.content is None


async def test_read_file_invalid_utf8_is_binary() -> None:
    team_id = uuid.uuid4()
    scan_id = uuid.uuid4()
    project = _project(team_id, scan_id)
    body = b"\xff\xfe\xfd"  # invalid utf-8, no NUL
    _write_tarball(project_id=project.id, scan_id=scan_id, files={"x.bin": body})
    session = _FakeSession(project=project)
    actor = _principal(team_ids=[team_id])

    result = await read_file(
        session,  # type: ignore[arg-type]
        project_id=project.id,
        raw_path="x.bin",
        scan_id=None,
        actor=actor,
    )
    assert result.encoding == "binary"
    assert result.content is None


async def test_read_file_directory_is_413() -> None:
    team_id = uuid.uuid4()
    scan_id = uuid.uuid4()
    project = _project(team_id, scan_id)
    _write_tarball(
        project_id=project.id,
        scan_id=scan_id,
        files={"src/main.py": b"x\n"},
    )
    session = _FakeSession(project=project)
    actor = _principal(team_ids=[team_id])
    with pytest.raises(SourceFileTooLarge):
        await read_file(
            session,  # type: ignore[arg-type]
            project_id=project.id,
            raw_path="src",  # a directory
            scan_id=None,
            actor=actor,
        )


async def test_read_file_root_is_413() -> None:
    team_id = uuid.uuid4()
    scan_id = uuid.uuid4()
    project = _project(team_id, scan_id)
    _write_tarball(project_id=project.id, scan_id=scan_id, files={"a.py": b"x\n"})
    session = _FakeSession(project=project)
    actor = _principal(team_ids=[team_id])
    with pytest.raises(SourceFileTooLarge):
        await read_file(
            session,  # type: ignore[arg-type]
            project_id=project.id,
            raw_path="",
            scan_id=None,
            actor=actor,
        )


async def test_read_file_missing_member_is_404() -> None:
    team_id = uuid.uuid4()
    scan_id = uuid.uuid4()
    project = _project(team_id, scan_id)
    _write_tarball(project_id=project.id, scan_id=scan_id, files={"a.py": b"x\n"})
    session = _FakeSession(project=project)
    actor = _principal(team_ids=[team_id])
    with pytest.raises(SourceUnavailable):
        await read_file(
            session,  # type: ignore[arg-type]
            project_id=project.id,
            raw_path="does/not/exist.py",
            scan_id=None,
            actor=actor,
        )


async def test_read_file_symlink_member_refused() -> None:
    """A symlink member in the tar must never be readable (defence in depth)."""
    team_id = uuid.uuid4()
    scan_id = uuid.uuid4()
    project = _project(team_id, scan_id)
    _write_tarball(
        project_id=project.id,
        scan_id=scan_id,
        files={"real.py": b"x\n"},
        symlinks={"evil": "/etc/passwd"},
    )
    session = _FakeSession(project=project)
    actor = _principal(team_ids=[team_id])
    with pytest.raises(SourceFileTooLarge):
        await read_file(
            session,  # type: ignore[arg-type]
            project_id=project.id,
            raw_path="evil",
            scan_id=None,
            actor=actor,
        )


# ===========================================================================
# Per-line license-match projection from the folded scancode JSON
# ===========================================================================


async def test_read_file_projects_line_matches_for_this_path_only() -> None:
    team_id = uuid.uuid4()
    scan_id = uuid.uuid4()
    project = _project(team_id, scan_id)
    scancode = {
        "files": [
            {
                "path": "LICENSE",
                "license_detections": [
                    {
                        "matches": [
                            {
                                "license_expression_spdx": "MIT",
                                "start_line": 1,
                                "end_line": 21,
                                "score": 99.5,
                            }
                        ]
                    }
                ],
            },
            {
                "path": "OTHER",  # must NOT bleed into LICENSE's matches
                "license_detections": [
                    {
                        "matches": [
                            {
                                "license_expression_spdx": "Apache-2.0",
                                "start_line": 5,
                                "end_line": 9,
                                "score": 80.0,
                            }
                        ]
                    }
                ],
            },
        ]
    }
    _write_tarball(
        project_id=project.id,
        scan_id=scan_id,
        files={"LICENSE": b"MIT License\n", "OTHER": b"x\n"},
        scancode=scancode,
    )
    session = _FakeSession(project=project)
    actor = _principal(team_ids=[team_id])

    result = await read_file(
        session,  # type: ignore[arg-type]
        project_id=project.id,
        raw_path="LICENSE",
        scan_id=None,
        actor=actor,
    )
    assert len(result.license_matches) == 1
    m = result.license_matches[0]
    assert (m.spdx_id, m.start_line, m.end_line, m.score) == ("MIT", 1, 21, 99.5)


async def test_read_file_line_matches_handle_leading_dotslash_path() -> None:
    team_id = uuid.uuid4()
    scan_id = uuid.uuid4()
    project = _project(team_id, scan_id)
    scancode = {
        "files": [
            {
                "path": "./src/a.py",  # scancode often prefixes with ./<root>
                "license_detections": [
                    {
                        "matches": [
                            {
                                "license_expression_spdx": "MIT",
                                "start_line": 2,
                                "end_line": 2,
                            }
                        ]
                    }
                ],
            }
        ]
    }
    _write_tarball(
        project_id=project.id,
        scan_id=scan_id,
        files={"src/a.py": b"x\n"},
        scancode=scancode,
    )
    session = _FakeSession(project=project)
    actor = _principal(team_ids=[team_id])

    result = await read_file(
        session,  # type: ignore[arg-type]
        project_id=project.id,
        raw_path="src/a.py",
        scan_id=None,
        actor=actor,
    )
    assert [(m.spdx_id, m.start_line, m.score) for m in result.license_matches] == [
        ("MIT", 2, None)
    ]


async def test_read_file_no_scancode_member_yields_empty_matches() -> None:
    team_id = uuid.uuid4()
    scan_id = uuid.uuid4()
    project = _project(team_id, scan_id)
    _write_tarball(project_id=project.id, scan_id=scan_id, files={"a.py": b"x\n"})
    session = _FakeSession(project=project)
    actor = _principal(team_ids=[team_id])

    result = await read_file(
        session,  # type: ignore[arg-type]
        project_id=project.id,
        raw_path="a.py",
        scan_id=None,
        actor=actor,
    )
    assert result.license_matches == []


@pytest.mark.parametrize(
    "match",
    [
        {"start_line": 1, "end_line": 2},  # no spdx
        {"license_expression_spdx": "MIT"},  # no lines
        {"license_expression_spdx": "MIT", "start_line": 0, "end_line": 2},  # start<1
        {"license_expression_spdx": "MIT", "start_line": 5, "end_line": 2},  # end<start
        {"license_expression_spdx": "  ", "start_line": 1, "end_line": 2},  # blank spdx
    ],
)
async def test_read_file_drops_malformed_matches(match: dict[str, Any]) -> None:
    team_id = uuid.uuid4()
    scan_id = uuid.uuid4()
    project = _project(team_id, scan_id)
    scancode = {"files": [{"path": "a.py", "license_detections": [{"matches": [match]}]}]}
    _write_tarball(
        project_id=project.id,
        scan_id=scan_id,
        files={"a.py": b"x\n"},
        scancode=scancode,
    )
    session = _FakeSession(project=project)
    actor = _principal(team_ids=[team_id])

    result = await read_file(
        session,  # type: ignore[arg-type]
        project_id=project.id,
        raw_path="a.py",
        scan_id=None,
        actor=actor,
    )
    assert result.license_matches == []


@pytest.mark.parametrize(
    "scancode",
    [
        {"files": "not-a-list"},  # files not a list
        {"no_files_key": True},  # missing files
        {"files": [{"path": "a.py", "license_detections": "nope"}]},  # bad detections
        {"files": [{"path": "a.py", "license_detections": [{"matches": "nope"}]}]},
        {"files": ["not-a-dict"]},  # entry not a dict
        {"files": [{"path": 123}]},  # path not a str
        "not-a-dict",  # whole doc not a dict
    ],
)
async def test_read_file_malformed_scancode_yields_empty_matches(
    scancode: Any,
) -> None:
    team_id = uuid.uuid4()
    scan_id = uuid.uuid4()
    project = _project(team_id, scan_id)
    _write_tarball(
        project_id=project.id,
        scan_id=scan_id,
        files={"a.py": b"x\n"},
        scancode=scancode,
    )
    session = _FakeSession(project=project)
    actor = _principal(team_ids=[team_id])

    result = await read_file(
        session,  # type: ignore[arg-type]
        project_id=project.id,
        raw_path="a.py",
        scan_id=None,
        actor=actor,
    )
    assert result.license_matches == []


async def test_list_dir_corrupt_tarball_is_404() -> None:
    team_id = uuid.uuid4()
    scan_id = uuid.uuid4()
    project = _project(team_id, scan_id)
    # Write garbage bytes at the resolved tarball path — not a valid gzip tar.
    dest = scan_source_tarball_path(project.id, scan_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"this is not a gzip tarball")
    session = _FakeSession(project=project)
    actor = _principal(team_ids=[team_id])

    with pytest.raises(SourceUnavailable):
        await list_dir(
            session,  # type: ignore[arg-type]
            project_id=project.id,
            raw_path="",
            scan_id=None,
            actor=actor,
            page=1,
            size=50,
        )


async def test_read_file_over_cap_scancode_json_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SCANCODE_MAX_RESULT_BYTES", "10")  # tiny cap
    team_id = uuid.uuid4()
    scan_id = uuid.uuid4()
    project = _project(team_id, scan_id)
    scancode = {
        "files": [
            {
                "path": "a.py",
                "license_detections": [
                    {
                        "matches": [
                            {
                                "license_expression_spdx": "MIT",
                                "start_line": 1,
                                "end_line": 2,
                            }
                        ]
                    }
                ],
            }
        ]
    }
    _write_tarball(
        project_id=project.id,
        scan_id=scan_id,
        files={"a.py": b"x\n"},
        scancode=scancode,
    )
    session = _FakeSession(project=project)
    actor = _principal(team_ids=[team_id])

    result = await read_file(
        session,  # type: ignore[arg-type]
        project_id=project.id,
        raw_path="a.py",
        scan_id=None,
        actor=actor,
    )
    # Over-cap scancode JSON → matches skipped, file body still returned.
    assert result.license_matches == []
    assert result.content == "x\n"


# ===========================================================================
# G3.2 Low (a) — rejected ?path= is NOT echoed into the 4xx detail
# ===========================================================================


@pytest.mark.parametrize(
    "hostile",
    ["/secret/path", "..\\..\\evil", "a/../../etc/passwd", "x\x00y"],
)
def test_sanitize_member_path_uses_static_message_not_reflected_input(
    hostile: str,
) -> None:
    """The rejected raw value must NOT appear in the exception detail."""
    with pytest.raises(SourcePathRejected) as excinfo:
        _sanitize_member_path(hostile)
    detail = str(excinfo.value)
    assert detail == "path selector rejected"
    # The hostile token (minus NUL, which str() drops) never leaks into detail.
    assert hostile.replace("\x00", "") not in detail or hostile == ""


def test_sanitize_member_path_logs_raw_value_to_warning() -> None:
    """The raw selector goes to a structlog WARNING field, not the response."""
    import structlog.testing

    with structlog.testing.capture_logs() as caplog:
        with pytest.raises(SourcePathRejected):
            _sanitize_member_path("/etc/passwd")

    rejected = [e for e in caplog if e.get("event") == "source_tree_path_rejected"]
    assert rejected, "a path rejection must emit a warning event"
    assert rejected[0]["raw_path"] == "/etc/passwd"
    assert rejected[0]["reason"] == "absolute"
    assert rejected[0]["log_level"] == "warning"


# ===========================================================================
# G3.3 — raw full-file download (no per-file viewer cap)
# ===========================================================================


async def test_read_file_raw_returns_full_bytes_ignoring_viewer_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The raw path returns the WHOLE member even when the viewer cap is tiny."""
    monkeypatch.setenv("SCAN_SOURCE_VIEWER_MAX_FILE_BYTES", "4")  # viewer truncates
    team_id = uuid.uuid4()
    scan_id = uuid.uuid4()
    project = _project(team_id, scan_id)
    body = b"0123456789ABCDEF"  # 16 bytes — far over the 4-byte viewer cap
    _write_tarball(project_id=project.id, scan_id=scan_id, files={"big.bin": body})
    session = _FakeSession(project=project)
    actor = _principal(team_ids=[team_id])

    raw = await read_file_raw(
        session,  # type: ignore[arg-type]
        project_id=project.id,
        raw_path="big.bin",
        scan_id=None,
        actor=actor,
    )
    # Full member, NOT the capped viewer bytes.
    assert raw.data == body
    assert raw.byte_size == 16
    assert raw.filename == "big.bin"


async def test_read_file_raw_rejects_over_raw_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SCAN_SOURCE_RAW_DOWNLOAD_MAX_BYTES", "8")  # tiny raw cap
    team_id = uuid.uuid4()
    scan_id = uuid.uuid4()
    project = _project(team_id, scan_id)
    body = b"0123456789"  # 10 bytes, raw cap is 8
    _write_tarball(project_id=project.id, scan_id=scan_id, files={"big.bin": body})
    session = _FakeSession(project=project)
    actor = _principal(team_ids=[team_id])

    with pytest.raises(SourceFileTooLarge):
        await read_file_raw(
            session,  # type: ignore[arg-type]
            project_id=project.id,
            raw_path="big.bin",
            scan_id=None,
            actor=actor,
        )


@pytest.mark.parametrize("hostile", ["../etc/passwd", "/etc/passwd", "x\x00.py"])
async def test_read_file_raw_rejects_hostile_path(hostile: str) -> None:
    team_id = uuid.uuid4()
    scan_id = uuid.uuid4()
    project = _project(team_id, scan_id)
    _write_tarball(project_id=project.id, scan_id=scan_id, files={"a.py": b"x\n"})
    session = _FakeSession(project=project)
    actor = _principal(team_ids=[team_id])
    with pytest.raises(SourcePathRejected):
        await read_file_raw(
            session,  # type: ignore[arg-type]
            project_id=project.id,
            raw_path=hostile,
            scan_id=None,
            actor=actor,
        )


async def test_read_file_raw_refuses_symlink_member() -> None:
    """A symlink member must never be served by the raw download path either."""
    team_id = uuid.uuid4()
    scan_id = uuid.uuid4()
    project = _project(team_id, scan_id)
    _write_tarball(
        project_id=project.id,
        scan_id=scan_id,
        files={"real.py": b"x\n"},
        symlinks={"evil": "/etc/passwd"},
    )
    session = _FakeSession(project=project)
    actor = _principal(team_ids=[team_id])
    with pytest.raises(SourceFileTooLarge):
        await read_file_raw(
            session,  # type: ignore[arg-type]
            project_id=project.id,
            raw_path="evil",
            scan_id=None,
            actor=actor,
        )


async def test_read_file_raw_other_team_is_404() -> None:
    project = _project(team_id=uuid.uuid4(), latest_scan_id=uuid.uuid4())
    session = _FakeSession(project=project)
    actor = _principal(team_ids=[uuid.uuid4()])  # different team
    with pytest.raises(SourceUnavailable):
        await read_file_raw(
            session,  # type: ignore[arg-type]
            project_id=project.id,
            raw_path="a.py",
            scan_id=None,
            actor=actor,
        )


async def test_read_file_raw_root_is_413() -> None:
    team_id = uuid.uuid4()
    scan_id = uuid.uuid4()
    project = _project(team_id, scan_id)
    _write_tarball(project_id=project.id, scan_id=scan_id, files={"a.py": b"x\n"})
    session = _FakeSession(project=project)
    actor = _principal(team_ids=[team_id])
    with pytest.raises(SourceFileTooLarge):
        await read_file_raw(
            session,  # type: ignore[arg-type]
            project_id=project.id,
            raw_path="",
            scan_id=None,
            actor=actor,
        )
