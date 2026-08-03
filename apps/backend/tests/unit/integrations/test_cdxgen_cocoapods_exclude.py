"""Unit tests — cdxgen CocoaPods crash guard (Phase L).

cdxgen's cocoapods cataloger does not skip when the ``pod`` CLI is absent —
it throws a TypeError on the undefined ``pod`` stdout and aborts the whole
stage-1 run (a terminal ``CdxgenFailed``). The guard passes
``--exclude-type cocoapods`` whenever a Podfile is present so the rest of the
scan survives; ``integrations/cocoapods_lockfile.py`` fills the pods back in.

Pinned here:
  * ``_podfile_present`` — depth-3 bounded detection, ``Pods/`` copies
    ignored (a vendored Pods tree must not flag a non-CocoaPods root).
  * ``run_cdxgen`` cmd assembly — the exclude flag appears exactly when a
    Podfile is detected, and the source dir stays the LAST argument.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from integrations import cdxgen

# ---------------------------------------------------------------------------
# _podfile_present
# ---------------------------------------------------------------------------


def test_podfile_at_root_detected(tmp_path: Path) -> None:
    (tmp_path / "Podfile").write_text("platform :ios, '15.0'\n", encoding="utf-8")
    assert cdxgen._podfile_present(tmp_path) is True


@pytest.mark.parametrize("depth", ["app", "app/ios"])
def test_podfile_nested_within_depth_three_detected(
    tmp_path: Path, depth: str
) -> None:
    nested = tmp_path / depth
    nested.mkdir(parents=True)
    (nested / "Podfile").write_text("platform :ios\n", encoding="utf-8")
    assert cdxgen._podfile_present(tmp_path) is True


def test_podfile_deeper_than_three_levels_not_detected(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)
    (nested / "Podfile").write_text("platform :ios\n", encoding="utf-8")
    assert cdxgen._podfile_present(tmp_path) is False  # DoS-bounded by design


def test_pods_directory_copy_ignored(tmp_path: Path) -> None:
    pods = tmp_path / "Pods"
    pods.mkdir()
    (pods / "Podfile").write_text("vendored copy\n", encoding="utf-8")
    assert cdxgen._podfile_present(tmp_path) is False


def test_no_podfile_not_detected(tmp_path: Path) -> None:
    (tmp_path / "Package.swift").write_text("// spm\n", encoding="utf-8")
    assert cdxgen._podfile_present(tmp_path) is False


# ---------------------------------------------------------------------------
# run_cdxgen cmd assembly
# ---------------------------------------------------------------------------


@pytest.fixture
def captured_cmd(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Stub the subprocess layer; capture cmd and fake a successful run."""
    calls: list[list[str]] = []

    def _fake_run(cmd: list[str], **kwargs: Any) -> SimpleNamespace:
        calls.append(cmd)
        out_path = Path(cmd[cmd.index("-o") + 1])
        out_path.write_text(
            json.dumps({"components": [], "dependencies": []}), encoding="utf-8"
        )
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(cdxgen, "run_with_line_streaming", _fake_run)
    monkeypatch.setattr(
        "integrations.cdxgen.shutil.which", lambda name: f"/usr/bin/{name}"
    )
    return calls


def test_exclude_type_present_iff_podfile_detected(
    tmp_path: Path, captured_cmd: list[list[str]]
) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "Podfile").write_text("platform :ios\n", encoding="utf-8")
    cdxgen.run_cdxgen(
        source_dir=source, output_dir=tmp_path / "out", backend="real"
    )
    cmd = captured_cmd[-1]
    exclude_index = cmd.index("--exclude-type")
    assert cmd[exclude_index + 1] == "cocoapods"
    assert cmd[-1] == str(source)  # source dir stays the last argument


def test_no_exclude_type_without_podfile(
    tmp_path: Path, captured_cmd: list[list[str]]
) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "package.json").write_text("{}", encoding="utf-8")
    cdxgen.run_cdxgen(
        source_dir=source, output_dir=tmp_path / "out", backend="real"
    )
    assert "--exclude-type" not in captured_cmd[-1]


def test_symlinked_podfile_not_detected(tmp_path: Path) -> None:
    # L3 — a symlinked Podfile must not steer the --exclude-type flag.
    outside = tmp_path / "outside-podfile"
    outside.write_text("platform :ios\n", encoding="utf-8")
    source = tmp_path / "src"
    source.mkdir()
    (source / "Podfile").symlink_to(outside)
    assert cdxgen._podfile_present(source) is False
