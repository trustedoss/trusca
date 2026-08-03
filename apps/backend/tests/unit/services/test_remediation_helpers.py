"""
Pure-unit tests for remediation-service helpers (v2.2 2.2-b2) that do NOT need a
DB session: the purl→npm-name decoder and the preserved-source tarball reader.

The tarball reader is exercised against a real on-disk gzip tar built in a temp
dir (no DB), matching the layout the source-preservation writer produces.
"""

from __future__ import annotations

import io
import tarfile
import uuid
from pathlib import Path

import pytest

from services.remediation_service import (
    _read_package_json_from_tarball,
    decode_npm_package_name,
)


@pytest.mark.parametrize(
    ("purl", "expected"),
    [
        ("pkg:npm/lodash@4.17.21", "lodash"),
        ("pkg:npm/lodash", "lodash"),
        ("pkg:npm/%40scope%2Fpkg@1.0.0", "@scope/pkg"),
        ("pkg:npm/@scope/pkg@1.0.0", "@scope/pkg"),
        ("pkg:npm/@scope/pkg", "@scope/pkg"),
        ("pkg:npm/left-pad@1.3.0?arch=any#sub", "left-pad"),
        ("pkg:pypi/requests@2.0.0", None),  # non-npm
        ("not-a-purl", None),
        ("pkg:npm/", None),
        ("", None),
        (None, None),
        (123, None),  # non-string
    ],
)
def test_decode_npm_package_name(purl, expected) -> None:
    assert decode_npm_package_name(purl) == expected  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Tarball reader
# ---------------------------------------------------------------------------


def _write_tarball(
    root: Path, project_id: uuid.UUID, scan_id: uuid.UUID, members: dict[str, bytes]
) -> Path:
    """Build the preserved-source tarball at the canonical UUID-only path."""
    from services.source_preservation_service import scan_source_tarball_path

    path = scan_source_tarball_path(project_id, scan_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, mode="w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


def test_reads_root_package_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    project_id, scan_id = uuid.uuid4(), uuid.uuid4()
    content = b'{"dependencies": {"x": "^1.0.0"}}'
    _write_tarball(tmp_path, project_id, scan_id, {"package.json": content})

    result = _read_package_json_from_tarball(project_id, scan_id, max_bytes=1024)
    assert result == content.decode()


def test_prefers_shallowest_package_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    project_id, scan_id = uuid.uuid4(), uuid.uuid4()
    _write_tarball(
        tmp_path,
        project_id,
        scan_id,
        {
            "packages/sub/package.json": b'{"name": "nested"}',
            "package.json": b'{"name": "root"}',
        },
    )
    result = _read_package_json_from_tarball(project_id, scan_id, max_bytes=1024)
    assert result is not None
    assert "root" in result


def test_missing_tarball_returns_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    result = _read_package_json_from_tarball(uuid.uuid4(), uuid.uuid4(), max_bytes=1024)
    assert result is None


def test_no_package_json_member_returns_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    project_id, scan_id = uuid.uuid4(), uuid.uuid4()
    _write_tarball(tmp_path, project_id, scan_id, {"README.md": b"hello"})
    result = _read_package_json_from_tarball(project_id, scan_id, max_bytes=1024)
    assert result is None


def test_decoy_basename_not_matched(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # A member named to LOOK like package.json but with a different basename must
    # NOT be picked up (exact-basename match only).
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    project_id, scan_id = uuid.uuid4(), uuid.uuid4()
    _write_tarball(tmp_path, project_id, scan_id, {"evil/package.json.sh": b"rm -rf /"})
    result = _read_package_json_from_tarball(project_id, scan_id, max_bytes=1024)
    assert result is None


def test_oversized_member_returns_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    project_id, scan_id = uuid.uuid4(), uuid.uuid4()
    big = b'{"dependencies": {}}' + b" " * 4096
    _write_tarball(tmp_path, project_id, scan_id, {"package.json": big})
    result = _read_package_json_from_tarball(project_id, scan_id, max_bytes=64)
    assert result is None


def test_binary_member_returns_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    project_id, scan_id = uuid.uuid4(), uuid.uuid4()
    _write_tarball(tmp_path, project_id, scan_id, {"package.json": b"\xff\xfe\x00binary"})
    result = _read_package_json_from_tarball(project_id, scan_id, max_bytes=1024)
    assert result is None
