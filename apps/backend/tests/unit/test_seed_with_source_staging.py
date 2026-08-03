"""
G3.3 — ``scripts/seed_e2e_user.py --with-source`` preserved-source staging.

The source-tree e2e (``apps/frontend/tests/e2e/source_tree.spec.ts`` S3/S4)
needs the seed to stage a real preserved-source tarball so the
``/source-tree`` + ``/source-file`` endpoints return a populated tree instead
of the 404 empty-state. This module pins the two staging helpers that build
that fixture WITHOUT touching Postgres:

  - ``_build_synthetic_scancode_json`` — emits a scancode 32.x document whose
    per-file ``license_detections[].matches[]`` shape is exactly what
    ``source_tree_service._matches_from_scancode`` reads back. A drift here
    silently drops the per-line license chip in S3, so we assert the precise
    nesting + line-range fields, not just "it's JSON".

  - ``_stage_preserved_source`` — tars a tiny tree (nested dir + utf-8 text
    file + binary + oversized) via ``preserve_scan_source`` and returns the
    tarball path. We assert the tarball is a readable gzip tar that folds in
    ``.trustedoss/scancode.json`` and carries every fixture member, AND that
    the oversized member is genuinely over the viewer's per-file cap (so
    ``read_file`` returns ``truncated=true`` in S4). No DB is needed — the
    helper only reads ``core.config`` + the filesystem.
"""

from __future__ import annotations

import json
import tarfile
import uuid

import pytest


def test_synthetic_scancode_json_matches_source_tree_reader_shape() -> None:
    """The synthesized scancode JSON must round-trip through the reader.

    We feed the document straight into ``source_tree_service`` so the contract
    is verified against the REAL projection code, not a hand-rolled copy of its
    expectations.
    """
    from scripts.seed_e2e_user import _build_synthetic_scancode_json
    from services.source_tree_service import _matches_from_scancode

    raw = _build_synthetic_scancode_json(
        files_with_matches={"src/app/main.py": "MIT"}
    )
    document = json.loads(raw)

    # The reader returns the per-line matches for exactly that path.
    matches = _matches_from_scancode(document, member_path="src/app/main.py")
    assert len(matches) == 1
    match = matches[0]
    assert match.spdx_id == "MIT"
    assert match.start_line == 1
    assert match.end_line == 3
    assert match.score == 100.0

    # A path with no synthesized entry yields no matches.
    assert _matches_from_scancode(document, member_path="src/app/other.py") == []


def test_stage_preserved_source_writes_readable_tarball(
    monkeypatch: pytest.MonkeyPatch, tmp_path  # type: ignore[no-untyped-def]
) -> None:
    """``_stage_preserved_source`` produces the tarball the viewer reads.

    Point ``WORKSPACE_HOST_PATH`` at a tmp dir so the staged tarball lands
    under a path we can inspect. Then assert:
      - the returned path exists and is a gzip tar,
      - it folds in ``.trustedoss/scancode.json`` (per-line view source),
      - it carries the nested-dir text / binary / oversized members,
      - the oversized member is strictly larger than the viewer per-file cap
        (so the file read reports ``truncated=true`` in S4).
    """
    from scripts.seed_e2e_user import (
        _SOURCE_BINARY_FILE_REL,
        _SOURCE_HUGE_FILE_REL,
        _SOURCE_README_REL,
        _SOURCE_TEXT_FILE_REL,
        _stage_preserved_source,
    )

    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(tmp_path))

    from core.config import (
        scan_source_viewer_max_file_bytes,
        workspace_root,
    )
    from services.source_preservation_service import SCANCODE_MEMBER_NAME

    assert workspace_root() == str(tmp_path)
    viewer_cap = scan_source_viewer_max_file_bytes()

    project_id = uuid.uuid4()
    scan_id = uuid.uuid4()

    tar_path_str = _stage_preserved_source(
        project_id=project_id, scan_id=scan_id
    )
    assert tar_path_str is not None, "staging must produce a tarball path"

    from pathlib import Path

    tar_path = Path(tar_path_str)
    assert tar_path.is_file()
    # The retained tarball lives under the UUID-only path the read service
    # rebuilds (scan-sources/<project>/<scan>.tar.gz).
    assert tar_path.name == f"{scan_id}.tar.gz"
    assert tar_path.parent.name == str(project_id)

    with tarfile.open(tar_path, "r:gz") as tar:
        members = {m.name: m for m in tar.getmembers()}

    # Scancode JSON folded in for the per-line view.
    assert SCANCODE_MEMBER_NAME in members
    # Every fixture member present, including the nested directory's files.
    assert _SOURCE_README_REL in members
    assert _SOURCE_TEXT_FILE_REL in members
    assert _SOURCE_BINARY_FILE_REL in members
    assert _SOURCE_HUGE_FILE_REL in members
    # The nested directory itself is archived so the tree shows folders.
    assert "src" in members and members["src"].isdir()
    assert "src/app" in members and members["src/app"].isdir()

    # The oversized member must exceed the viewer cap → truncated=true in S4.
    assert members[_SOURCE_HUGE_FILE_REL].size > viewer_cap

    # The binary member carries a NUL byte so the reader classifies it binary.
    with tarfile.open(tar_path, "r:gz") as tar:
        extracted = tar.extractfile(members[_SOURCE_BINARY_FILE_REL])
        assert extracted is not None
        with extracted:
            binary_bytes = extracted.read()
    assert b"\x00" in binary_bytes
