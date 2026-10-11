"""The npm lockfile reader trusts nothing about the file it is handed.

With the BomLens engine, stage 1 has the source tree writable before TRUSCA
reads ``package-lock.json`` from it for scope enrichment (security review on
#522, L5). A planted link, a FIFO or an oversized file must not be read.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from integrations import npm_lockfile
from integrations.npm_lockfile import read_lockfile

LOCK = {
    "name": "app",
    "lockfileVersion": 3,
    "packages": {
        "": {"name": "app", "dependencies": {"lodash": "4.17.21"}},
        "node_modules/lodash": {"version": "4.17.21"},
    },
}


def test_a_regular_lockfile_is_read(tmp_path: Path) -> None:
    (tmp_path / "package-lock.json").write_text(json.dumps(LOCK))
    assert read_lockfile(tmp_path) is not None


def test_a_link_to_another_file_is_not_followed(tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_text(json.dumps(LOCK))
    src = tmp_path / "src"
    src.mkdir()
    (src / "package-lock.json").symlink_to(elsewhere)
    assert read_lockfile(src) is None


def test_a_fifo_does_not_block_the_worker(tmp_path: Path) -> None:
    os.mkfifo(tmp_path / "package-lock.json")
    result: list[object] = []
    reader = threading.Thread(target=lambda: result.append(read_lockfile(tmp_path)), daemon=True)
    reader.start()
    reader.join(timeout=5)
    assert result == [None], "reading blocked on the FIFO"


def test_an_oversized_lockfile_is_not_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "package-lock.json").write_text(json.dumps(LOCK))
    monkeypatch.setattr(npm_lockfile, "MAX_LOCKFILE_BYTES", 10)
    assert read_lockfile(tmp_path) is None


def test_a_linked_package_json_is_not_followed(tmp_path: Path) -> None:
    v1 = {"lockfileVersion": 1, "dependencies": {"lodash": {"version": "4.17.21"}}}
    (tmp_path / "package-lock.json").write_text(json.dumps(v1))
    secret = tmp_path / "secret.json"
    secret.write_text(json.dumps({"devDependencies": {"lodash": "*"}}))
    (tmp_path / "package.json").symlink_to(secret)
    assert npm_lockfile._read_manifest(tmp_path) is None


def test_the_type_check_refuses_a_fifo_on_its_own(tmp_path: Path) -> None:
    """Asserted on the helper: through ``read_lockfile`` an empty FIFO read would
    also fail to parse, which would hide a missing type check."""
    fifo = tmp_path / "package-lock.json"
    os.mkfifo(fifo)
    assert npm_lockfile._read_regular_file(fifo, 1024) is None


def test_the_size_check_refuses_before_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The size on the open descriptor decides, before any byte is read (the
    read is also capped, which alone would hide a missing size check)."""
    from structlog.testing import capture_logs

    path = tmp_path / "package-lock.json"
    path.write_text(json.dumps(LOCK))
    with capture_logs() as logs:
        assert npm_lockfile._read_regular_file(path, 10) is None
    assert [e["event"] for e in logs] == ["npm_lockfile_not_read"]

