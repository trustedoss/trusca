"""
Unit tests for ``services/source_preservation_service.py`` — G3.1.

These run with NO database and NO Postgres: ``preserve_scan_source`` is a pure
filesystem operation (tar a source dir + fold in the scancode JSON). We build
real on-disk source trees in a ``tmp_path`` and assert:

  - happy path: a tarball is written, contains the source files + the folded
    scancode JSON under ``.trustedoss/scancode.json``;
  - the write is atomic (no ``.tmp`` left behind on success; nothing left on a
    cap breach);
  - non-regular members (symlinks) are skipped;
  - the per-project quota and single-tarball caps skip preservation (return
    None) and NEVER raise;
  - re-run overwrites the prior tarball for the same scan id;
  - the scancode JSON is optional (source-only tarball when absent).

Adversarial / edge focus (per MEMORY: untrusted-input parametrize):
  - a symlink in the tree must never be archived as a symlink;
  - a source tree carrying its own ``.trustedoss/scancode.json`` must not shadow
    the real folded-in result.
"""

from __future__ import annotations

import os
import tarfile
import uuid
from pathlib import Path

import pytest

from services.source_preservation_service import (
    SBOM_MEMBER_NAME,
    SCANCODE_MEMBER_NAME,
    PreservationTooLarge,
    PreservedSbomMissing,
    extract_preserved_sbom,
    preserve_scan_source,
    preserved_tarball_has_sbom,
    scan_source_tarball_path,
    scan_sources_dir_for_project,
)


@pytest.fixture(autouse=True)
def _workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))
    return tmp_path


def _make_source_tree(root: Path) -> Path:
    """Create a small representative first-party source tree under ``root``."""
    src = root / "source"
    (src / "pkg").mkdir(parents=True)
    (src / "LICENSE").write_text("MIT License\n")
    (src / "README.md").write_text("# demo\n")
    (src / "pkg" / "app.py").write_text("print('hi')\n")
    (src / "pkg" / "empty").mkdir()  # an empty dir should survive the round-trip
    return src


def _make_scancode_json(root: Path) -> Path:
    path = root / "scancode" / "scancode.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"files": [{"path": "LICENSE", "matched_text": "MIT"}]}')
    return path


def _members(tar_path: Path) -> set[str]:
    with tarfile.open(tar_path, mode="r:gz") as tar:
        return set(tar.getnames())


def test_happy_path_writes_tarball_with_source_and_scancode(
    tmp_path: Path,
) -> None:
    src = _make_source_tree(tmp_path)
    scancode = _make_scancode_json(tmp_path)
    project_id, scan_id = uuid.uuid4(), uuid.uuid4()

    result = preserve_scan_source(
        scan_id=scan_id,
        project_id=project_id,
        source_dir=src,
        scancode_json_path=scancode,
    )

    assert result == scan_source_tarball_path(project_id, scan_id)
    assert result is not None and result.is_file()
    names = _members(result)
    assert "LICENSE" in names
    assert "pkg/app.py" in names
    assert "pkg/empty" in names  # empty dir preserved
    assert SCANCODE_MEMBER_NAME in names

    # The folded-in member is the REAL scancode JSON content.
    with tarfile.open(result, mode="r:gz") as tar:
        member = tar.extractfile(SCANCODE_MEMBER_NAME)
        assert member is not None
        assert b"matched_text" in member.read()


def test_atomic_write_leaves_no_temp_file(tmp_path: Path) -> None:
    src = _make_source_tree(tmp_path)
    project_id, scan_id = uuid.uuid4(), uuid.uuid4()

    preserve_scan_source(
        scan_id=scan_id,
        project_id=project_id,
        source_dir=src,
        scancode_json_path=None,
    )

    sources_dir = scan_sources_dir_for_project(project_id)
    leftovers = list(sources_dir.glob("*.tmp"))
    assert leftovers == []


def test_scancode_json_optional_source_only_tarball(tmp_path: Path) -> None:
    src = _make_source_tree(tmp_path)
    project_id, scan_id = uuid.uuid4(), uuid.uuid4()

    result = preserve_scan_source(
        scan_id=scan_id,
        project_id=project_id,
        source_dir=src,
        scancode_json_path=None,
    )

    assert result is not None
    names = _members(result)
    assert "LICENSE" in names
    assert SCANCODE_MEMBER_NAME not in names


def test_symlink_member_is_skipped(tmp_path: Path) -> None:
    src = _make_source_tree(tmp_path)
    # A symlink pointing outside the tree must never be archived.
    secret = tmp_path / "secret.txt"
    secret.write_text("do not leak")
    (src / "evil_link").symlink_to(secret)

    project_id, scan_id = uuid.uuid4(), uuid.uuid4()
    result = preserve_scan_source(
        scan_id=scan_id,
        project_id=project_id,
        source_dir=src,
        scancode_json_path=None,
    )

    assert result is not None
    names = _members(result)
    assert "evil_link" not in names
    # And no member is a symlink type.
    with tarfile.open(result, mode="r:gz") as tar:
        assert all(not m.issym() and not m.islnk() for m in tar.getmembers())


def test_source_tree_scancode_member_does_not_shadow_real_one(tmp_path: Path) -> None:
    """A repo carrying its own .trustedoss/scancode.json must not win the slot."""
    src = _make_source_tree(tmp_path)
    decoy = src / ".trustedoss" / "scancode.json"
    decoy.parent.mkdir(parents=True)
    decoy.write_text('{"decoy": true}')
    scancode = _make_scancode_json(tmp_path)

    project_id, scan_id = uuid.uuid4(), uuid.uuid4()
    result = preserve_scan_source(
        scan_id=scan_id,
        project_id=project_id,
        source_dir=src,
        scancode_json_path=scancode,
    )

    assert result is not None
    with tarfile.open(result, mode="r:gz") as tar:
        # Exactly one member at the reserved name, and it is the real result.
        names = tar.getnames()
        assert names.count(SCANCODE_MEMBER_NAME) == 1
        member = tar.extractfile(SCANCODE_MEMBER_NAME)
        assert member is not None
        assert b"decoy" not in member.read()


def test_quota_full_skips_and_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An existing tarball already over quota → preservation skipped, no raise."""
    src = _make_source_tree(tmp_path)
    project_id, scan_id = uuid.uuid4(), uuid.uuid4()

    # Pre-seed a fat existing tarball so the project is already over a tiny quota.
    sources_dir = scan_sources_dir_for_project(project_id)
    sources_dir.mkdir(parents=True, exist_ok=True)
    (sources_dir / f"{uuid.uuid4()}.tar.gz").write_bytes(b"x" * 4096)
    monkeypatch.setenv("SCAN_SOURCE_PROJECT_QUOTA_BYTES", "1024")

    result = preserve_scan_source(
        scan_id=scan_id,
        project_id=project_id,
        source_dir=src,
        scancode_json_path=None,
    )

    assert result is None
    # The new scan's tarball was never written.
    assert not scan_source_tarball_path(project_id, scan_id).exists()


def test_max_tarball_bytes_cap_skips_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tree larger than the single-tarball cap → None + no partial file."""
    src = tmp_path / "source"
    src.mkdir()
    # Highly-incompressible content so gzip cannot duck under the cap.
    for i in range(8):
        (src / f"blob{i}.bin").write_bytes(os.urandom(64 * 1024))
    monkeypatch.setenv("SCAN_SOURCE_MAX_TARBALL_BYTES", "1024")

    project_id, scan_id = uuid.uuid4(), uuid.uuid4()
    result = preserve_scan_source(
        scan_id=scan_id,
        project_id=project_id,
        source_dir=src,
        scancode_json_path=None,
    )

    assert result is None
    sources_dir = scan_sources_dir_for_project(project_id)
    if sources_dir.is_dir():
        assert list(sources_dir.glob("*.tmp")) == []
        assert not scan_source_tarball_path(project_id, scan_id).exists()


def test_unexpected_error_is_swallowed_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unexpected internal failure must degrade to None, never raise."""
    import services.source_preservation_service as mod

    src = _make_source_tree(tmp_path)

    def _boom(**_kw: object) -> tuple[int, bool, bool]:
        raise RuntimeError("simulated tarfile edge case")

    monkeypatch.setattr(mod, "_write_tarball", _boom)

    result = preserve_scan_source(
        scan_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        source_dir=src,
        scancode_json_path=None,
    )
    assert result is None


def test_missing_source_dir_returns_none(tmp_path: Path) -> None:
    project_id, scan_id = uuid.uuid4(), uuid.uuid4()
    result = preserve_scan_source(
        scan_id=scan_id,
        project_id=project_id,
        source_dir=tmp_path / "does-not-exist",
        scancode_json_path=None,
    )
    assert result is None


def test_rerun_overwrites_prior_tarball(tmp_path: Path) -> None:
    src = _make_source_tree(tmp_path)
    project_id, scan_id = uuid.uuid4(), uuid.uuid4()

    first = preserve_scan_source(
        scan_id=scan_id,
        project_id=project_id,
        source_dir=src,
        scancode_json_path=None,
    )
    assert first is not None
    # Add a file then re-run with the SAME scan id — the path must be reused.
    (src / "NEW.txt").write_text("added on re-run\n")
    second = preserve_scan_source(
        scan_id=scan_id,
        project_id=project_id,
        source_dir=src,
        scancode_json_path=None,
    )

    assert second == first
    names = _members(second)
    assert "NEW.txt" in names
    # Exactly one tarball for the scan (overwrite, not a second file).
    sources_dir = scan_sources_dir_for_project(project_id)
    assert len(list(sources_dir.glob("*.tar.gz"))) == 1


def test_limits_read_at_call_time(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.config import (
        scan_source_max_tarball_bytes,
        scan_source_project_quota_bytes,
        scan_source_retention,
        scan_source_viewer_max_file_bytes,
    )

    monkeypatch.delenv("SCAN_SOURCE_RETENTION", raising=False)
    assert scan_source_retention() == "latest"
    monkeypatch.setenv("SCAN_SOURCE_RETENTION", "all")
    assert scan_source_retention() == "all"

    monkeypatch.delenv("SCAN_SOURCE_PROJECT_QUOTA_BYTES", raising=False)
    assert scan_source_project_quota_bytes() == 1024**3
    monkeypatch.setenv("SCAN_SOURCE_PROJECT_QUOTA_BYTES", "123")
    assert scan_source_project_quota_bytes() == 123

    monkeypatch.delenv("SCAN_SOURCE_MAX_TARBALL_BYTES", raising=False)
    assert scan_source_max_tarball_bytes() == 512 * 1024 * 1024

    monkeypatch.delenv("SCAN_SOURCE_VIEWER_MAX_FILE_BYTES", raising=False)
    assert scan_source_viewer_max_file_bytes() == 2 * 1024 * 1024


# ---------------------------------------------------------------------------
# W6-#42 — cdxgen SBOM fold + extract
# ---------------------------------------------------------------------------


def _make_cdxgen_sbom(root: Path, *, content: bytes | None = None) -> Path:
    """Create a representative cdxgen CycloneDX JSON SBOM file under ``root``."""
    path = root / "cdxgen" / "cdxgen.cdx.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = content if content is not None else (
        b'{"bomFormat": "CycloneDX", "specVersion": "1.5", "components": []}'
    )
    path.write_bytes(payload)
    return path


def test_sbom_preserved_when_present(tmp_path: Path) -> None:
    """SBOM path → tarball carries ``.trustedoss/cdxgen.cdx.json``."""
    src = _make_source_tree(tmp_path)
    sbom = _make_cdxgen_sbom(tmp_path)
    project_id, scan_id = uuid.uuid4(), uuid.uuid4()

    result = preserve_scan_source(
        scan_id=scan_id,
        project_id=project_id,
        source_dir=src,
        scancode_json_path=None,
        sbom_path=sbom,
    )

    assert result is not None
    names = _members(result)
    assert SBOM_MEMBER_NAME in names
    with tarfile.open(result, mode="r:gz") as tar:
        member = tar.extractfile(SBOM_MEMBER_NAME)
        assert member is not None
        body = member.read()
        assert b"CycloneDX" in body
        assert b"specVersion" in body


def test_sbom_optional_no_member_when_absent(tmp_path: Path) -> None:
    """sbom_path None or omitted → tarball has no SBOM member (backwards compat)."""
    src = _make_source_tree(tmp_path)
    project_id, scan_id = uuid.uuid4(), uuid.uuid4()

    result = preserve_scan_source(
        scan_id=scan_id,
        project_id=project_id,
        source_dir=src,
        scancode_json_path=None,
    )

    assert result is not None
    names = _members(result)
    assert SBOM_MEMBER_NAME not in names


def test_sbom_missing_file_silently_skipped(tmp_path: Path) -> None:
    """A non-existent sbom_path is treated as None (best-effort fold-in)."""
    src = _make_source_tree(tmp_path)
    project_id, scan_id = uuid.uuid4(), uuid.uuid4()

    result = preserve_scan_source(
        scan_id=scan_id,
        project_id=project_id,
        source_dir=src,
        scancode_json_path=None,
        sbom_path=tmp_path / "cdxgen" / "does-not-exist.cdx.json",
    )

    assert result is not None
    names = _members(result)
    assert SBOM_MEMBER_NAME not in names


def test_source_tree_sbom_member_does_not_shadow_real_one(tmp_path: Path) -> None:
    """A repo carrying its own .trustedoss/cdxgen.cdx.json must not win the slot."""
    src = _make_source_tree(tmp_path)
    decoy = src / ".trustedoss" / "cdxgen.cdx.json"
    decoy.parent.mkdir(parents=True)
    decoy.write_bytes(b'{"decoy": true}')
    sbom = _make_cdxgen_sbom(tmp_path)

    project_id, scan_id = uuid.uuid4(), uuid.uuid4()
    result = preserve_scan_source(
        scan_id=scan_id,
        project_id=project_id,
        source_dir=src,
        scancode_json_path=None,
        sbom_path=sbom,
    )

    assert result is not None
    with tarfile.open(result, mode="r:gz") as tar:
        names = tar.getnames()
        assert names.count(SBOM_MEMBER_NAME) == 1
        member = tar.extractfile(SBOM_MEMBER_NAME)
        assert member is not None
        assert b"decoy" not in member.read()


def test_extract_preserved_sbom_happy_path(tmp_path: Path) -> None:
    """Round-trip: preserve with SBOM → extract recovers the bytes."""
    src = _make_source_tree(tmp_path)
    sbom_content = b'{"bomFormat": "CycloneDX", "components": [{"name": "left-pad"}]}'
    sbom = _make_cdxgen_sbom(tmp_path, content=sbom_content)
    project_id, scan_id = uuid.uuid4(), uuid.uuid4()

    preserve_scan_source(
        scan_id=scan_id,
        project_id=project_id,
        source_dir=src,
        scancode_json_path=None,
        sbom_path=sbom,
    )

    dest_dir = tmp_path / "extract"
    extracted = extract_preserved_sbom(
        scan_id=scan_id, project_id=project_id, dest_dir=dest_dir
    )
    assert extracted == dest_dir / "cdxgen.cdx.json"
    assert extracted.read_bytes() == sbom_content


def test_extract_preserved_sbom_missing_tarball_raises(tmp_path: Path) -> None:
    """No tarball on disk → FileNotFoundError so the rematch beat can skip."""
    with pytest.raises(FileNotFoundError):
        extract_preserved_sbom(
            scan_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            dest_dir=tmp_path / "extract",
        )


def test_extract_preserved_sbom_missing_member_raises(tmp_path: Path) -> None:
    """Tarball exists but has no SBOM member → PreservedSbomMissing (scan predates W6-#42)."""
    src = _make_source_tree(tmp_path)
    project_id, scan_id = uuid.uuid4(), uuid.uuid4()
    preserve_scan_source(
        scan_id=scan_id,
        project_id=project_id,
        source_dir=src,
        scancode_json_path=None,
    )

    with pytest.raises(PreservedSbomMissing):
        extract_preserved_sbom(
            scan_id=scan_id, project_id=project_id, dest_dir=tmp_path / "extract"
        )


def test_extract_preserved_sbom_size_cap_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SBOM larger than the extract cap → PreservationTooLarge + dest cleaned up."""
    import services.source_preservation_service as mod

    # Squeeze the cap so a small file trips the guard without writing megabytes.
    monkeypatch.setattr(mod, "_SBOM_EXTRACT_MAX_BYTES", 64)

    src = _make_source_tree(tmp_path)
    # 256 bytes of incompressible content — well over the patched 64-byte cap.
    sbom = _make_cdxgen_sbom(tmp_path, content=os.urandom(256))
    project_id, scan_id = uuid.uuid4(), uuid.uuid4()
    preserve_scan_source(
        scan_id=scan_id,
        project_id=project_id,
        source_dir=src,
        scancode_json_path=None,
        sbom_path=sbom,
    )

    dest_dir = tmp_path / "extract"
    with pytest.raises(PreservationTooLarge):
        extract_preserved_sbom(
            scan_id=scan_id, project_id=project_id, dest_dir=dest_dir
        )
    # Cleanup: the partial output is removed so the caller's tmp dir is empty.
    assert not (dest_dir / "cdxgen.cdx.json").exists()


def test_preserved_tarball_has_sbom_predicate(tmp_path: Path) -> None:
    """Predicate returns True iff the tarball carries the SBOM member."""
    src = _make_source_tree(tmp_path)
    sbom = _make_cdxgen_sbom(tmp_path)
    project_id = uuid.uuid4()

    # No tarball at all → False (the predicate must not raise for the beat).
    assert not preserved_tarball_has_sbom(
        scan_id=uuid.uuid4(), project_id=project_id
    )

    # Tarball without SBOM → False (legacy scan predating W6-#42).
    legacy_id = uuid.uuid4()
    preserve_scan_source(
        scan_id=legacy_id,
        project_id=project_id,
        source_dir=src,
        scancode_json_path=None,
    )
    assert not preserved_tarball_has_sbom(scan_id=legacy_id, project_id=project_id)

    # Tarball with SBOM → True (W6-#42-era scan eligible for rematch).
    modern_id = uuid.uuid4()
    preserve_scan_source(
        scan_id=modern_id,
        project_id=project_id,
        source_dir=src,
        scancode_json_path=None,
        sbom_path=sbom,
    )
    assert preserved_tarball_has_sbom(scan_id=modern_id, project_id=project_id)


def test_extract_preserved_sbom_rejects_symlink_member(tmp_path: Path) -> None:
    """security-reviewer H-1: a SYMTYPE/LNKTYPE member named SBOM_MEMBER_NAME
    must NOT be honoured even if ``TarInfo.isfile()`` returns True for it.

    Constructs a tarball by hand (NOT via ``preserve_scan_source`` — the writer
    only emits regular files) so the extract path's strict isreg() check is
    exercised against a tampered shape.
    """
    project_id, scan_id = uuid.uuid4(), uuid.uuid4()
    from services.source_preservation_service import scan_source_tarball_path

    tar_path = scan_source_tarball_path(project_id, scan_id)
    tar_path.parent.mkdir(parents=True, exist_ok=True)

    # Plant a real innocuous regular member + a SYMTYPE member sharing the
    # reserved SBOM arcname pointing at it.
    with tarfile.open(tar_path, mode="w:gz") as tar:
        payload_path = tmp_path / "innocuous-payload"
        payload_path.write_bytes(b"would-be-exfiltrated-via-symlink")
        tar.add(str(payload_path), arcname="innocuous-payload", recursive=False)
        sym = tarfile.TarInfo(name=SBOM_MEMBER_NAME)
        sym.type = tarfile.SYMTYPE
        sym.linkname = "innocuous-payload"
        tar.addfile(sym)

    with pytest.raises(PreservedSbomMissing):
        extract_preserved_sbom(
            scan_id=scan_id,
            project_id=project_id,
            dest_dir=tmp_path / "extract",
        )
