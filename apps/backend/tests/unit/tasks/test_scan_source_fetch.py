"""
Unit tests for `tasks.scan_source._fetch_source` — Phase 2 PR #9 (I-1 closure).

The fetch step is the worker-side defence-in-depth layer for the SSRF guard.
We pin:

  - A missing git_url falls through to the legacy placeholder (PR #7/#8
    backward compat — projects without a git_url still scan).
  - A git_url that fails worker-side validation raises ``_FetchAborted``.
  - A safe git_url + mock_only=True writes the placeholder file AND runs
    validation (so DNS rebinding between insert-time and scan-time is
    detected even before we activate the real ``git clone`` branch).
"""

from __future__ import annotations

import socket
import uuid
from pathlib import Path

import pytest


def test_fetch_source_with_no_git_url_uses_placeholder(
    tmp_path: Path,
) -> None:
    """Legacy projects (git_url=None) still get a placeholder workspace."""
    from tasks.scan_source import _fetch_source

    workspace = tmp_path / "ws"
    workspace.mkdir()

    source_dir = _fetch_source(
        scan_uuid=uuid.uuid4(),
        workspace=workspace,
        git_url=None,
    )
    assert source_dir.exists()
    assert (source_dir / ".trustedoss-placeholder").exists()


def test_fetch_source_with_empty_git_url_uses_placeholder(
    tmp_path: Path,
) -> None:
    """Empty string is treated identically to None — no validation, placeholder."""
    from tasks.scan_source import _fetch_source

    workspace = tmp_path / "ws"
    workspace.mkdir()

    source_dir = _fetch_source(
        scan_uuid=uuid.uuid4(),
        workspace=workspace,
        git_url="",
    )
    assert (source_dir / ".trustedoss-placeholder").exists()


def test_fetch_source_with_safe_url_validates_and_writes_placeholder(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When git_url is set, the validator runs even in mock_only mode."""
    from tasks.scan_source import _fetch_source

    monkeypatch.setattr(
        "core.url_guard.socket.getaddrinfo",
        lambda host, port: [(socket.AF_INET, 0, 0, "", ("140.82.121.4", 0))],
    )

    workspace = tmp_path / "ws"
    workspace.mkdir()

    source_dir = _fetch_source(
        scan_uuid=uuid.uuid4(),
        workspace=workspace,
        git_url="https://github.com/foo/bar.git",
    )
    assert (source_dir / ".trustedoss-placeholder").exists()


def test_fetch_source_aborts_on_private_ip_url(
    tmp_path: Path,
) -> None:
    """A worker-time check on an RFC 1918 URL raises _FetchAborted."""
    from tasks.scan_source import _fetch_source, _FetchAborted

    workspace = tmp_path / "ws"
    workspace.mkdir()

    with pytest.raises(_FetchAborted):
        _fetch_source(
            scan_uuid=uuid.uuid4(),
            workspace=workspace,
            git_url="http://10.0.0.5/repo",
        )


def test_fetch_source_aborts_on_metadata_hostname(
    tmp_path: Path,
) -> None:
    """Cloud metadata hostnames are rejected at the worker boundary."""
    from tasks.scan_source import _fetch_source, _FetchAborted

    workspace = tmp_path / "ws"
    workspace.mkdir()

    with pytest.raises(_FetchAborted):
        _fetch_source(
            scan_uuid=uuid.uuid4(),
            workspace=workspace,
            git_url="https://metadata.google.internal/repo",
        )


def test_fetch_source_aborts_when_dns_rotates_to_private(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The TOCTOU scenario the PR closes: DNS now resolves to a private IP.

    The schema layer accepted the URL minutes ago when DNS was public; the
    attacker has since updated their DNS A record to point at 192.168.x.y
    (rebinding). The worker re-validates here and refuses to proceed.
    """
    from tasks.scan_source import _fetch_source, _FetchAborted

    monkeypatch.setattr(
        "core.url_guard.socket.getaddrinfo",
        lambda host, port: [(socket.AF_INET, 0, 0, "", ("192.168.99.99", 0))],
    )

    workspace = tmp_path / "ws"
    workspace.mkdir()

    with pytest.raises(_FetchAborted):
        _fetch_source(
            scan_uuid=uuid.uuid4(),
            workspace=workspace,
            git_url="https://attacker.example.com/repo.git",
        )


# ---------------------------------------------------------------------------
# Upload source type (feat/zip-upload)
# ---------------------------------------------------------------------------


def _write_archive(project_id: uuid.UUID, archive_id: uuid.UUID, body: bytes) -> None:
    from services.source_archive_service import archive_path

    path = archive_path(project_id, str(archive_id))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)


def _zip(members: dict[str, bytes]) -> bytes:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_fetch_source_upload_extracts_archive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """source_type='upload' extracts the saved zip into source/."""
    from tasks.scan_source import _fetch_source

    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    workspace = tmp_path / "ws"
    workspace.mkdir()

    project_id = uuid.uuid4()
    archive_id = uuid.uuid4()
    _write_archive(project_id, archive_id, _zip({"src/main.py": b"x = 1\n"}))

    source_dir = _fetch_source(
        scan_uuid=uuid.uuid4(),
        workspace=workspace,
        git_url=None,  # ignored on the upload path
        project_id=project_id,
        scan_metadata={"source_type": "upload", "archive_id": str(archive_id)},
    )
    assert (source_dir / "src" / "main.py").read_bytes() == b"x = 1\n"


def test_fetch_source_upload_missing_archive_aborts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from tasks.scan_source import _fetch_source, _FetchAborted

    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    workspace = tmp_path / "ws"
    workspace.mkdir()

    with pytest.raises(_FetchAborted):
        _fetch_source(
            scan_uuid=uuid.uuid4(),
            workspace=workspace,
            git_url=None,
            project_id=uuid.uuid4(),
            scan_metadata={"source_type": "upload", "archive_id": str(uuid.uuid4())},
        )


def test_fetch_source_upload_zip_slip_aborts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A hostile archive (zip slip) terminates the fetch via _FetchAborted."""
    import io
    import zipfile

    from tasks.scan_source import _fetch_source, _FetchAborted

    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    workspace = tmp_path / "ws"
    workspace.mkdir()

    project_id = uuid.uuid4()
    archive_id = uuid.uuid4()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        info = zipfile.ZipInfo("../escape.txt")
        zf.writestr(info, b"pwned")
    _write_archive(project_id, archive_id, buf.getvalue())

    with pytest.raises(_FetchAborted):
        _fetch_source(
            scan_uuid=uuid.uuid4(),
            workspace=workspace,
            git_url=None,
            project_id=project_id,
            scan_metadata={"source_type": "upload", "archive_id": str(archive_id)},
        )
    assert not (tmp_path / "escape.txt").exists()


def test_fetch_source_upload_without_project_id_aborts(
    tmp_path: Path,
) -> None:
    from tasks.scan_source import _fetch_source, _FetchAborted

    workspace = tmp_path / "ws"
    workspace.mkdir()
    with pytest.raises(_FetchAborted):
        _fetch_source(
            scan_uuid=uuid.uuid4(),
            workspace=workspace,
            git_url=None,
            project_id=None,
            scan_metadata={"source_type": "upload", "archive_id": str(uuid.uuid4())},
        )


def test_fetch_source_upload_deletes_archive_after_extract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """H-fix (part a): the saved zip is removed once it has been extracted —
    no permanent accumulation on the workspace volume."""
    from services.source_archive_service import archive_path
    from tasks.scan_source import _fetch_source

    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    workspace = tmp_path / "ws"
    workspace.mkdir()

    project_id = uuid.uuid4()
    archive_id = uuid.uuid4()
    _write_archive(project_id, archive_id, _zip({"src/main.py": b"x = 1\n"}))
    zip_on_disk = archive_path(project_id, str(archive_id))
    assert zip_on_disk.is_file()

    source_dir = _fetch_source(
        scan_uuid=uuid.uuid4(),
        workspace=workspace,
        git_url=None,
        project_id=project_id,
        scan_metadata={"source_type": "upload", "archive_id": str(archive_id)},
    )
    assert (source_dir / "src" / "main.py").read_bytes() == b"x = 1\n"
    # The archive zip is gone after a successful extract.
    assert not zip_on_disk.exists()


def test_fetch_source_upload_rejected_archive_is_deleted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A rejected (zip-slip) archive is also deleted — it can never succeed,
    so it must not sit on the volume forever."""
    import io
    import zipfile

    from services.source_archive_service import archive_path
    from tasks.scan_source import _fetch_source, _FetchAborted

    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    workspace = tmp_path / "ws"
    workspace.mkdir()

    project_id = uuid.uuid4()
    archive_id = uuid.uuid4()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(zipfile.ZipInfo("../escape.txt"), b"pwned")
    _write_archive(project_id, archive_id, buf.getvalue())
    zip_on_disk = archive_path(project_id, str(archive_id))

    with pytest.raises(_FetchAborted):
        _fetch_source(
            scan_uuid=uuid.uuid4(),
            workspace=workspace,
            git_url=None,
            project_id=project_id,
            scan_metadata={"source_type": "upload", "archive_id": str(archive_id)},
        )
    assert not zip_on_disk.exists()
