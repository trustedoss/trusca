"""
scancode adapter unit tests (PR-A2).

scancode is a heavy pure-Python tool; unit tests must NEVER spawn it. We cover:

  - Mock backend: emits a real on-disk JSON keyed off first-party files, with
    every file detected as MIT (deterministic shape).
  - Exclusion: vendored / build / VCS dirs (node_modules, .git, dist, …) are
    skipped by BOTH the eligible-file pre-count and the mock walk.
  - Real-path subprocess: ``--ignore`` flags + scrubbed env; success parses
    detections, non-zero exit raises ScancodeFailed, timeout raises
    ScancodeTimeout, missing binary raises ScancodeNotInstalled.
  - Guards: SCANCODE_MAX_FILES (ScancodeTooLarge), SCANCODE_MAX_DETECTIONS cap.
  - Output parsing rare cases: malformed JSON, missing result file, binary
    files (null spdx), compound expressions, directory entries, empty tree.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_tree(root: Path) -> None:
    """A small first-party tree with some files to exclude."""
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "src" / "main.py").write_text("# code\n", encoding="utf-8")
    (root / "LICENSE").write_text("MIT License\n", encoding="utf-8")
    # Vendored + build + VCS dirs that MUST be excluded.
    (root / "node_modules" / "left-pad").mkdir(parents=True, exist_ok=True)
    (root / "node_modules" / "left-pad" / "index.js").write_text("x", encoding="utf-8")
    (root / "dist").mkdir(parents=True, exist_ok=True)
    (root / "dist" / "bundle.js").write_text("y", encoding="utf-8")
    (root / ".git").mkdir(parents=True, exist_ok=True)
    (root / ".git" / "config").write_text("z", encoding="utf-8")


def _write_scancode_json(path: Path, files: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"headers": [{"tool_name": "scancode-toolkit"}], "files": files}),
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# Mock backend
# ---------------------------------------------------------------------------


def test_mock_emits_detections_for_first_party_files(
    scan_backend_mock: None, tmp_path: Path
) -> None:
    from integrations.scancode import run_scancode

    source = tmp_path / "source"
    _seed_tree(source)

    result = run_scancode(source_dir=source, output_dir=tmp_path / "scancode")

    assert result.result_path.exists()
    paths = {d.source_path for d in result.detections}
    # First-party files are present.
    assert "src/main.py" in paths
    assert "LICENSE" in paths
    # Every detection is MIT in the mock.
    assert all(d.spdx_id == "MIT" for d in result.detections)


def test_mock_excludes_vendored_build_and_vcs_dirs(
    scan_backend_mock: None, tmp_path: Path
) -> None:
    """node_modules / dist / .git contents must NOT appear in detections."""
    from integrations.scancode import run_scancode

    source = tmp_path / "source"
    _seed_tree(source)

    result = run_scancode(source_dir=source, output_dir=tmp_path / "scancode")

    paths = {d.source_path for d in result.detections}
    for excluded in paths:
        assert "node_modules" not in excluded
        assert "dist" not in excluded
        assert ".git" not in excluded


def test_mock_empty_project_yields_no_detections(
    scan_backend_mock: None, tmp_path: Path
) -> None:
    from integrations.scancode import run_scancode

    source = tmp_path / "source"
    source.mkdir()

    result = run_scancode(source_dir=source, output_dir=tmp_path / "scancode")

    assert result.detections == []
    assert result.result_path.exists()


def test_mock_respects_max_detections_cap(
    scan_backend_mock: None, tmp_path: Path
) -> None:
    from integrations.scancode import run_scancode

    source = tmp_path / "source"
    source.mkdir()
    for i in range(10):
        (source / f"file{i}.py").write_text("code", encoding="utf-8")

    result = run_scancode(
        source_dir=source, output_dir=tmp_path / "scancode", max_detections=3
    )

    assert len(result.detections) == 3


# ---------------------------------------------------------------------------
# Eligible-file pre-count guard (SCANCODE_MAX_FILES → ScancodeTooLarge)
# ---------------------------------------------------------------------------


def test_count_eligible_files_prunes_excluded_dirs(tmp_path: Path) -> None:
    from integrations.scancode import _count_eligible_files

    source = tmp_path / "source"
    _seed_tree(source)
    # _seed_tree creates: src/main.py + LICENSE = 2 first-party files.
    # node_modules/left-pad/index.js, dist/bundle.js, .git/config are excluded.
    count = _count_eligible_files(source, ceiling=1000)
    assert count == 2


def test_count_eligible_files_prunes_deeply_nested_excluded_dir(tmp_path: Path) -> None:
    """security-reviewer Medium #2 — a 2-level-deep node_modules is excluded.

    The pre-count prunes by directory NAME at any depth, so a buried
    ``a/b/node_modules/x`` does not inflate the count. This MUST agree with the
    bare-name scancode ``--ignore`` so the SCANCODE_MAX_FILES ceiling cannot be
    bypassed by nesting the vendored tree deeper.
    """
    from integrations.scancode import _count_eligible_files

    source = tmp_path / "source"
    (source / "src").mkdir(parents=True)
    (source / "src" / "main.py").write_text("# code\n", encoding="utf-8")
    # node_modules buried two levels deep — the naive */node_modules/* glob
    # would miss this, but name-based pruning catches it.
    deep = source / "packages" / "ui" / "node_modules" / "left-pad"
    deep.mkdir(parents=True)
    for i in range(50):
        (deep / f"f{i}.js").write_text("x", encoding="utf-8")

    count = _count_eligible_files(source, ceiling=1000)
    # Only src/main.py is eligible; the 50 buried node_modules files are pruned.
    assert count == 1


def test_count_eligible_files_stops_early_over_ceiling(tmp_path: Path) -> None:
    from integrations.scancode import _count_eligible_files

    source = tmp_path / "source"
    source.mkdir()
    for i in range(50):
        (source / f"f{i}.txt").write_text("x", encoding="utf-8")
    # Ceiling of 5 — must return >5 without walking all 50.
    count = _count_eligible_files(source, ceiling=5)
    assert count > 5


def test_count_eligible_files_skips_symlinks(tmp_path: Path) -> None:
    from integrations.scancode import _count_eligible_files

    source = tmp_path / "source"
    source.mkdir()
    (source / "real.py").write_text("code", encoding="utf-8")
    # A symlink loop would spin a naive walk forever; assert it is skipped.
    (source / "loop").symlink_to(source, target_is_directory=True)
    count = _count_eligible_files(source, ceiling=1000)
    assert count == 1


def test_real_mode_too_large_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Over SCANCODE_MAX_FILES → ScancodeTooLarge, before any subprocess."""
    from integrations import scancode as sc

    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "real")
    monkeypatch.setattr("integrations.scancode.shutil.which", lambda _: "/usr/local/bin/scancode")

    def _no_subprocess(*args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        raise AssertionError("subprocess must not run when tree is too large")

    monkeypatch.setattr("integrations.scancode.subprocess.run", _no_subprocess)

    source = tmp_path / "source"
    source.mkdir()
    for i in range(5):
        (source / f"f{i}.py").write_text("x", encoding="utf-8")

    with pytest.raises(sc.ScancodeTooLarge):
        sc.run_scancode(
            source_dir=source, output_dir=tmp_path / "scancode", max_files=2
        )


# ---------------------------------------------------------------------------
# Real-path subprocess — success, scrubbed env, --ignore flags
# ---------------------------------------------------------------------------


@pytest.fixture
def captured_subprocess(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Force the real-mode branch and record the subprocess call."""
    captured: dict[str, Any] = {}

    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "real")
    monkeypatch.setattr(
        "integrations.scancode.shutil.which", lambda _: "/usr/local/bin/scancode"
    )

    class _FakeResult:
        returncode = 0
        stdout = b""
        stderr = b""

    def _capture(cmd: list[str], **kwargs: Any) -> _FakeResult:
        captured["cmd"] = list(cmd)
        captured["env"] = kwargs.get("env")
        captured["cwd"] = kwargs.get("cwd")
        captured["timeout"] = kwargs.get("timeout")
        # Drop a parseable result JSON where the adapter expects one.
        result_path = Path(captured["result_path"])
        _write_scancode_json(
            result_path,
            [
                {
                    "path": "src/app.py",
                    "type": "file",
                    "detected_license_expression_spdx": "Apache-2.0",
                },
            ],
        )
        return _FakeResult()

    monkeypatch.setattr("integrations.scancode.subprocess.run", _capture)
    return captured


def test_real_mode_success_parses_detections_and_scrubs_env(
    captured_subprocess: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from integrations.scancode import run_scancode

    monkeypatch.setenv("DT_API_KEY", "super-secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h/db")

    source = tmp_path / "source"
    source.mkdir()
    (source / "app.py").write_text("code", encoding="utf-8")
    output_dir = tmp_path / "scancode"
    captured_subprocess["result_path"] = str(output_dir / "scancode.json")

    result = run_scancode(source_dir=source, output_dir=output_dir)

    assert [(d.spdx_id, d.source_path) for d in result.detections] == [
        ("Apache-2.0", "src/app.py")
    ]
    # --ignore flags present for vendored dirs.
    cmd = captured_subprocess["cmd"]
    assert "--ignore" in cmd
    assert any("node_modules" in arg for arg in cmd)
    assert any(".git" in arg for arg in cmd)
    # Secrets stripped from the subprocess env.
    env = captured_subprocess["env"]
    assert "DT_API_KEY" not in env
    assert "DATABASE_URL" not in env


def test_real_mode_not_installed_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from integrations import scancode as sc

    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "real")
    monkeypatch.setattr("integrations.scancode.shutil.which", lambda _: None)

    source = tmp_path / "source"
    source.mkdir()

    with pytest.raises(sc.ScancodeNotInstalled):
        sc.run_scancode(source_dir=source, output_dir=tmp_path / "scancode")


def test_real_mode_nonzero_exit_raises_failed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from integrations import scancode as sc

    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "real")
    monkeypatch.setattr(
        "integrations.scancode.shutil.which", lambda _: "/usr/local/bin/scancode"
    )

    class _FakeResult:
        returncode = 2
        stdout = b""
        stderr = b"scancode: boom"

    monkeypatch.setattr(
        "integrations.scancode.subprocess.run", lambda *a, **k: _FakeResult()
    )

    source = tmp_path / "source"
    source.mkdir()

    with pytest.raises(sc.ScancodeFailed) as exc:
        sc.run_scancode(source_dir=source, output_dir=tmp_path / "scancode")
    assert "scancode" in str(exc.value).lower()


def test_real_mode_timeout_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import subprocess

    from integrations import scancode as sc

    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "real")
    monkeypatch.setattr(
        "integrations.scancode.shutil.which", lambda _: "/usr/local/bin/scancode"
    )

    def _raise_timeout(*a: Any, **k: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd="scancode", timeout=1)

    monkeypatch.setattr("integrations.scancode.subprocess.run", _raise_timeout)

    source = tmp_path / "source"
    source.mkdir()

    with pytest.raises(sc.ScancodeTimeout):
        sc.run_scancode(
            source_dir=source, output_dir=tmp_path / "scancode", timeout_seconds=1
        )


# ---------------------------------------------------------------------------
# Output parsing — rare cases
# ---------------------------------------------------------------------------


def test_parse_missing_result_file_returns_empty(tmp_path: Path) -> None:
    from integrations.scancode import _parse_detections

    assert _parse_detections(tmp_path / "nope.json", cap=100) == []


def test_parse_malformed_json_returns_empty(tmp_path: Path) -> None:
    from integrations.scancode import _parse_detections

    bad = tmp_path / "scancode.json"
    bad.write_text("{not valid json", encoding="utf-8")
    assert _parse_detections(bad, cap=100) == []


def test_parse_skips_binary_files_with_null_spdx(tmp_path: Path) -> None:
    from integrations.scancode import _parse_detections

    path = _write_scancode_json(
        tmp_path / "scancode.json",
        [
            {"path": "img.png", "type": "file", "detected_license_expression_spdx": None},
            {"path": "LICENSE", "type": "file", "detected_license_expression_spdx": "MIT"},
        ],
    )
    out = _parse_detections(path, cap=100)
    assert [(d.spdx_id, d.source_path) for d in out] == [("MIT", "LICENSE")]


def test_parse_skips_directory_entries(tmp_path: Path) -> None:
    from integrations.scancode import _parse_detections

    path = _write_scancode_json(
        tmp_path / "scancode.json",
        [
            {"path": "src", "type": "directory", "detected_license_expression_spdx": "MIT"},
            {"path": "src/a.py", "type": "file", "detected_license_expression_spdx": "MIT"},
        ],
    )
    out = _parse_detections(path, cap=100)
    assert [d.source_path for d in out] == ["src/a.py"]


def test_parse_keeps_compound_expression_verbatim(tmp_path: Path) -> None:
    """A compound SPDX expression is kept as a single token (classifies unknown)."""
    from integrations.scancode import _parse_detections

    path = _write_scancode_json(
        tmp_path / "scancode.json",
        [
            {
                "path": "src/a.py",
                "type": "file",
                "detected_license_expression_spdx": "MIT OR Apache-2.0",
            },
        ],
    )
    out = _parse_detections(path, cap=100)
    assert [d.spdx_id for d in out] == ["MIT OR Apache-2.0"]


def test_parse_dedupes_same_spdx_same_path(tmp_path: Path) -> None:
    from integrations.scancode import _parse_detections

    path = _write_scancode_json(
        tmp_path / "scancode.json",
        [
            {"path": "a.py", "type": "file", "detected_license_expression_spdx": "MIT"},
            {"path": "a.py", "type": "file", "detected_license_expression_spdx": "MIT"},
        ],
    )
    out = _parse_detections(path, cap=100)
    assert len(out) == 1


def test_parse_respects_cap(tmp_path: Path) -> None:
    from integrations.scancode import _parse_detections

    files = [
        {"path": f"f{i}.py", "type": "file", "detected_license_expression_spdx": "MIT"}
        for i in range(10)
    ]
    path = _write_scancode_json(tmp_path / "scancode.json", files)
    out = _parse_detections(path, cap=3)
    assert len(out) == 3


def test_parse_handles_files_not_a_list(tmp_path: Path) -> None:
    from integrations.scancode import _parse_detections

    path = tmp_path / "scancode.json"
    path.write_text(json.dumps({"files": "oops"}), encoding="utf-8")
    assert _parse_detections(path, cap=100) == []


def test_parse_skips_non_dict_and_empty_path_entries(tmp_path: Path) -> None:
    from integrations.scancode import _parse_detections

    path = tmp_path / "scancode.json"
    path.write_text(
        json.dumps(
            {
                "files": [
                    "not-a-dict",
                    {"path": "", "type": "file", "detected_license_expression_spdx": "MIT"},
                    {"type": "file", "detected_license_expression_spdx": "MIT"},  # no path
                    {"path": "ok.py", "type": "file", "detected_license_expression_spdx": "MIT"},
                ]
            }
        ),
        encoding="utf-8",
    )
    out = _parse_detections(path, cap=100)
    assert [d.source_path for d in out] == ["ok.py"]


def test_parse_skips_whitespace_only_spdx(tmp_path: Path) -> None:
    from integrations.scancode import _parse_detections

    path = _write_scancode_json(
        tmp_path / "scancode.json",
        [{"path": "a.py", "type": "file", "detected_license_expression_spdx": "   "}],
    )
    assert _parse_detections(path, cap=100) == []


# ---------------------------------------------------------------------------
# security-reviewer Medium #3 — result-size ceiling (OOM guard).
#
# The scancode JSON is keyed off the attacker-controlled tree, and json.load
# materialises the whole document. We stat() the file and skip parsing when it
# exceeds SCANCODE_MAX_RESULT_BYTES — degraded (no detected licenses) but never
# fatal (declared cdxgen licenses stand).
# ---------------------------------------------------------------------------


def test_parse_skips_oversized_result_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from integrations.scancode import _parse_detections

    # A tiny but valid result; the ceiling is forced below its size so the
    # guard trips without writing a real multi-MiB fixture.
    path = _write_scancode_json(
        tmp_path / "scancode.json",
        [{"path": "a.py", "type": "file", "detected_license_expression_spdx": "MIT"}],
    )
    monkeypatch.setenv("SCANCODE_MAX_RESULT_BYTES", "5")  # < the file size
    assert _parse_detections(path, cap=100) == []


def test_parse_returns_empty_when_stat_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An OSError from stat() (race with cleanup) is degraded, not fatal.

    ``.exists()`` is checked first and must pass; only the ``.st_size`` read
    on the (now-gone) file raises — so we let the first stat through and raise
    on the second.
    """
    from integrations import scancode as sc

    path = _write_scancode_json(
        tmp_path / "scancode.json",
        [{"path": "a.py", "type": "file", "detected_license_expression_spdx": "MIT"}],
    )

    real_stat = Path.stat
    calls = {"n": 0}

    def _boom(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self == path:
            calls["n"] += 1
            if calls["n"] >= 2:  # 1st = exists(), 2nd = st_size read
                raise OSError("stat blew up")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr("integrations.scancode.Path.stat", _boom)
    assert sc._parse_detections(path, cap=100) == []


def test_parse_accepts_result_within_ceiling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from integrations.scancode import _parse_detections

    path = _write_scancode_json(
        tmp_path / "scancode.json",
        [{"path": "a.py", "type": "file", "detected_license_expression_spdx": "MIT"}],
    )
    monkeypatch.setenv("SCANCODE_MAX_RESULT_BYTES", str(10 * 1024 * 1024))
    out = _parse_detections(path, cap=100)
    assert [d.source_path for d in out] == ["a.py"]


def test_scancode_max_result_bytes_default_and_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.config import scancode_max_result_bytes

    monkeypatch.delenv("SCANCODE_MAX_RESULT_BYTES", raising=False)
    assert scancode_max_result_bytes() == 256 * 1024 * 1024  # 256 MiB default
    monkeypatch.setenv("SCANCODE_MAX_RESULT_BYTES", "1048576")
    assert scancode_max_result_bytes() == 1048576  # read at call time (rule #11)


# ---------------------------------------------------------------------------
# security-reviewer Low — source_path truncation (unbounded telemetry).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path_len",
    [1, 64, 1024, 1025, 8192],
    ids=["tiny", "mid", "at-limit", "one-over", "huge"],
)
def test_source_path_truncation_bounds_length(path_len: int) -> None:
    from integrations.scancode import SOURCE_PATH_MAX_LENGTH, _truncate_source_path

    raw = "x" * path_len
    assert len(raw) == path_len
    out = _truncate_source_path(raw)
    assert len(out) <= SOURCE_PATH_MAX_LENGTH
    if path_len <= SOURCE_PATH_MAX_LENGTH:
        assert out == raw
    else:
        # Head + tail preserved so the file stays recognisable.
        assert "...<truncated>..." in out
        assert out.startswith(raw[:10])
        assert out.endswith(raw[-10:])


def test_parse_truncates_long_source_path(tmp_path: Path) -> None:
    from integrations.scancode import SOURCE_PATH_MAX_LENGTH, _parse_detections

    long_path = "deep/" * 1000 + "file.py"
    path = _write_scancode_json(
        tmp_path / "scancode.json",
        [{"path": long_path, "type": "file", "detected_license_expression_spdx": "MIT"}],
    )
    out = _parse_detections(path, cap=100)
    assert len(out) == 1
    assert len(out[0].source_path) <= SOURCE_PATH_MAX_LENGTH


# ---------------------------------------------------------------------------
# security-reviewer Low — symlink out of source_dir is never read.
#
# scancode does not follow symlinks by default; the eligible-file pre-count
# (and the mock walk) both skip them, so a symlink pointing OUTSIDE source_dir
# (e.g. -> /etc) cannot be traversed and cannot inflate the count.
# ---------------------------------------------------------------------------


def test_count_eligible_files_ignores_out_of_tree_symlink(tmp_path: Path) -> None:
    from integrations.scancode import _count_eligible_files

    outside = tmp_path / "outside"
    outside.mkdir()
    for i in range(20):
        (outside / f"secret{i}.txt").write_text("x", encoding="utf-8")

    source = tmp_path / "source"
    source.mkdir()
    (source / "real.py").write_text("code", encoding="utf-8")
    # A symlink whose target lives OUTSIDE source_dir — must not be traversed.
    (source / "escape").symlink_to(outside, target_is_directory=True)

    count = _count_eligible_files(source, ceiling=1000)
    # Only the one in-tree file; the 20 files behind the escaping symlink are
    # never read.
    assert count == 1


def test_mock_ignores_out_of_tree_symlink(
    scan_backend_mock: None, tmp_path: Path
) -> None:
    from integrations.scancode import run_scancode

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("x", encoding="utf-8")

    source = tmp_path / "source"
    source.mkdir()
    (source / "real.py").write_text("code", encoding="utf-8")
    (source / "escape").symlink_to(outside, target_is_directory=True)

    result = run_scancode(source_dir=source, output_dir=tmp_path / "scancode")
    paths = {d.source_path for d in result.detections}
    assert paths == {"real.py"}
    assert all("secret" not in p for p in paths)


def test_split_spdx_expression_empty_returns_empty() -> None:
    from integrations.scancode import _split_spdx_expression

    assert _split_spdx_expression("") == []
    assert _split_spdx_expression("   ") == []


def test_split_spdx_expression_simple_and_compound() -> None:
    from integrations.scancode import _split_spdx_expression

    assert _split_spdx_expression("MIT") == ["MIT"]
    assert _split_spdx_expression("GPL-2.0-only WITH Classpath-exception-2.0") == [
        "GPL-2.0-only WITH Classpath-exception-2.0"
    ]


# ---------------------------------------------------------------------------
# security-reviewer High — adversarial SPDX tokens must not reach the DB.
#
# The detected SPDX expression is derived from attacker-controlled FILE
# CONTENT. A token wider than ``licenses.spdx_id`` (String(64)) would, if
# persisted, raise StringDataRightTruncation and roll back the whole scan-
# persistence transaction (declared findings + component graph destroyed). The
# adapter must drop over-length tokens at the boundary. (Adversarial-input
# parametrize is a standing memory lesson for untrusted-input parsing.)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "expr",
    [
        "A" * 65,  # one over the 64-char column width
        "MIT" + "-x" * 40,  # plausible-looking but oversized id
        "LicenseRef-" + "z" * 100,  # oversized LicenseRef (free-text detection)
        "MIT OR " + "Apache-2.0 OR " * 20,  # oversized COMPOUND expression
        "GPL-2.0-only WITH " + "Classpath-exception-2.0-" * 10,  # oversized WITH
        " " * 70 + "MIT" + " " * 70,  # whitespace inflates length but strips short
    ],
    ids=[
        "65-char-token",
        "oversized-mit",
        "oversized-licenseref",
        "oversized-or-compound",
        "oversized-with-compound",
        "whitespace-padded-short",
    ],
)
def test_split_spdx_expression_drops_or_strips_oversized(expr: str) -> None:
    """Over-length tokens are dropped; whitespace-padded-but-short stays."""
    from integrations.scancode import SPDX_ID_MAX_LENGTH, _split_spdx_expression

    out = _split_spdx_expression(expr)
    if len(expr.strip()) > SPDX_ID_MAX_LENGTH:
        assert out == [], f"oversized token should be dropped: {expr[:40]!r}..."
    else:
        # The whitespace-padded "MIT" strips down to a 3-char id and survives.
        assert out == ["MIT"]


def test_split_spdx_expression_token_at_exactly_limit_kept() -> None:
    from integrations.scancode import SPDX_ID_MAX_LENGTH, _split_spdx_expression

    at_limit = "L" * SPDX_ID_MAX_LENGTH
    assert _split_spdx_expression(at_limit) == [at_limit]


def test_parse_drops_oversized_spdx_keeps_clean_siblings(tmp_path: Path) -> None:
    """A hostile file with a >64-char SPDX must not poison the clean detections."""
    from integrations.scancode import _parse_detections

    path = _write_scancode_json(
        tmp_path / "scancode.json",
        [
            {
                "path": "evil.py",
                "type": "file",
                "detected_license_expression_spdx": "A" * 200,
            },
            {
                "path": "good.py",
                "type": "file",
                "detected_license_expression_spdx": "MIT",
            },
        ],
    )
    out = _parse_detections(path, cap=100)
    # Only the clean MIT detection survives; the oversized token is dropped.
    assert [(d.spdx_id, d.source_path) for d in out] == [("MIT", "good.py")]


def test_command_includes_strip_root_and_license_flags(tmp_path: Path) -> None:
    from integrations.scancode import _build_command

    cmd = _build_command(
        source_dir=tmp_path / "src", result_path=tmp_path / "out.json"
    )
    assert cmd[0] == "scancode"
    assert "--license" in cmd
    assert "--strip-root" in cmd
    # Compact JSON (Medium #3) — NOT the pretty-printed --json-pp which roughly
    # doubles the on-disk footprint and works against the size ceiling.
    assert "--json" in cmd
    assert "--json-pp" not in cmd


def test_command_ignores_excluded_dirs_at_any_depth(tmp_path: Path) -> None:
    """security-reviewer Medium #2 — a bare-name ignore matches at any depth.

    scancode's ``*`` does not cross ``/``, so ``*/node_modules/*`` alone misses
    a two-level-deep ``a/b/node_modules/x``. We emit the bare ``node_modules``
    ignore (scancode: ignore a dir of this name anywhere) plus the path globs.
    """
    from integrations.scancode import _build_command

    cmd = _build_command(
        source_dir=tmp_path / "src", result_path=tmp_path / "out.json"
    )
    # The --ignore value following one of the flags must include the bare name.
    ignore_values = [cmd[i + 1] for i, tok in enumerate(cmd) if tok == "--ignore"]
    assert "node_modules" in ignore_values  # bare name → any depth
    assert "*/node_modules/*" in ignore_values  # path glob (defence in depth)
    assert "node_modules/*" in ignore_values  # root-level path glob
    # Same any-depth guarantee for the other vendored/build/VCS names.
    assert "vendor" in ignore_values
    assert ".git" in ignore_values
    assert "dist" in ignore_values


# ---------------------------------------------------------------------------
# Scan-log verbosity (feat/scan-log-verbosity)
# ---------------------------------------------------------------------------


def test_real_mode_default_uses_quiet(
    captured_subprocess: dict[str, Any], tmp_path: Path
) -> None:
    """Normal mode keeps --quiet (progress bar is carriage-return log noise)."""
    from integrations.scancode import run_scancode

    source = tmp_path / "source"
    source.mkdir()
    (source / "app.py").write_text("code", encoding="utf-8")
    output_dir = tmp_path / "scancode"
    captured_subprocess["result_path"] = str(output_dir / "scancode.json")

    run_scancode(source_dir=source, output_dir=output_dir)

    cmd = captured_subprocess["cmd"]
    assert "--quiet" in cmd
    assert "--verbose" not in cmd


def test_real_mode_verbose_swaps_to_verbose_flag(
    captured_subprocess: dict[str, Any], tmp_path: Path
) -> None:
    """verbose=True swaps --quiet for --verbose so scancode emits per-file lines."""
    from integrations.scancode import run_scancode

    source = tmp_path / "source"
    source.mkdir()
    (source / "app.py").write_text("code", encoding="utf-8")
    output_dir = tmp_path / "scancode"
    captured_subprocess["result_path"] = str(output_dir / "scancode.json")

    run_scancode(source_dir=source, output_dir=output_dir, verbose=True)

    cmd = captured_subprocess["cmd"]
    assert "--verbose" in cmd
    assert "--quiet" not in cmd
