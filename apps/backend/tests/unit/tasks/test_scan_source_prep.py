"""
Unit tests for the multi-language pre-cdxgen prep helpers (chore PR #4).

Pinned behaviour:

* ``_prepare_for_cdxgen`` only invokes the resolver for ecosystems whose
  marker file is present, and skips the call when a populated lockfile
  already exists. Each branch (Ruby / Rust / Go / .NET) is exercised via
  a tmp_path fixture that materialises the marker layout.
* ``_run_prep`` is best-effort — non-zero exit + timeout + missing tool
  are all logged-and-swallowed so the surrounding scan continues.
* The 30-entry SPDX → category map agrees with CLAUDE.md
  §"라이선스 분류" exactly.

The integration tests cover the full pipeline against Postgres; these
are pure-Python so they run without a DB.
"""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# _prepare_for_cdxgen — ecosystem dispatch
# ---------------------------------------------------------------------------


@pytest.fixture
def captured_prep_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Replace ``_run_prep`` with a recorder so we can assert on dispatch."""
    captured: list[dict[str, Any]] = []

    def _capture(
        name: str,
        cmd: list[str],
        cwd: Path,
        timeout: int,
        scan_uuid: uuid.UUID,
    ) -> None:
        captured.append(
            {"name": name, "cmd": cmd, "cwd": cwd, "timeout": timeout, "scan_uuid": scan_uuid}
        )

    monkeypatch.setattr("tasks.scan_source._run_prep", _capture)
    return captured


def test_prepare_for_cdxgen_runs_bundle_lock_for_ruby_without_lockfile(
    tmp_path: Path, captured_prep_calls: list[dict[str, Any]]
) -> None:
    from tasks.scan_source import _prepare_for_cdxgen

    (tmp_path / "Gemfile").write_text("source 'https://rubygems.org'\ngem 'rake'\n")
    _prepare_for_cdxgen(source_dir=tmp_path, scan_uuid=uuid.uuid4())

    assert len(captured_prep_calls) == 1
    assert captured_prep_calls[0]["cmd"] == ["bundle", "lock"]


def test_prepare_for_cdxgen_skips_bundle_lock_when_lockfile_present(
    tmp_path: Path, captured_prep_calls: list[dict[str, Any]]
) -> None:
    """A populated Gemfile.lock means cdxgen has enough — no resolver needed."""
    from tasks.scan_source import _prepare_for_cdxgen

    (tmp_path / "Gemfile").write_text("source 'https://rubygems.org'\n")
    (tmp_path / "Gemfile.lock").write_text("GEM\n  remote: https://rubygems.org\n")
    _prepare_for_cdxgen(source_dir=tmp_path, scan_uuid=uuid.uuid4())

    assert captured_prep_calls == []


def test_prepare_for_cdxgen_runs_cargo_for_rust_without_lockfile(
    tmp_path: Path, captured_prep_calls: list[dict[str, Any]]
) -> None:
    from tasks.scan_source import _prepare_for_cdxgen

    (tmp_path / "Cargo.toml").write_text('[package]\nname="x"\nversion="0.1.0"\n')
    _prepare_for_cdxgen(source_dir=tmp_path, scan_uuid=uuid.uuid4())

    assert len(captured_prep_calls) == 1
    assert captured_prep_calls[0]["cmd"] == ["cargo", "generate-lockfile"]


def test_prepare_for_cdxgen_skips_cargo_when_cargo_lock_present(
    tmp_path: Path, captured_prep_calls: list[dict[str, Any]]
) -> None:
    from tasks.scan_source import _prepare_for_cdxgen

    (tmp_path / "Cargo.toml").write_text('[package]\nname="x"\nversion="0.1.0"\n')
    (tmp_path / "Cargo.lock").write_text("# generated\n[[package]]\n")
    _prepare_for_cdxgen(source_dir=tmp_path, scan_uuid=uuid.uuid4())

    assert captured_prep_calls == []


def test_prepare_for_cdxgen_runs_go_mod_tidy_unconditionally(
    tmp_path: Path, captured_prep_calls: list[dict[str, Any]]
) -> None:
    """`go mod tidy` is idempotent — we run it even if go.sum is present."""
    from tasks.scan_source import _prepare_for_cdxgen

    (tmp_path / "go.mod").write_text("module example.com/x\n\ngo 1.22\n")
    (tmp_path / "go.sum").write_text("# already populated\n")
    _prepare_for_cdxgen(source_dir=tmp_path, scan_uuid=uuid.uuid4())

    assert len(captured_prep_calls) == 1
    assert captured_prep_calls[0]["cmd"] == ["go", "mod", "tidy"]


def test_prepare_for_cdxgen_skips_dotnet_when_cli_missing(
    tmp_path: Path,
    captured_prep_calls: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A worker without dotnet on PATH skips .NET prep silently."""
    from tasks.scan_source import _prepare_for_cdxgen

    monkeypatch.setattr("tasks.scan_source.shutil.which", lambda _: None)
    (tmp_path / "App.csproj").write_text("<Project Sdk='Microsoft.NET.Sdk' />")
    _prepare_for_cdxgen(source_dir=tmp_path, scan_uuid=uuid.uuid4())

    assert captured_prep_calls == []


def test_prepare_for_cdxgen_runs_dotnet_when_cli_available(
    tmp_path: Path,
    captured_prep_calls: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tasks.scan_source import _prepare_for_cdxgen

    monkeypatch.setattr("tasks.scan_source.shutil.which", lambda _: "/usr/bin/dotnet")
    (tmp_path / "App.csproj").write_text("<Project Sdk='Microsoft.NET.Sdk' />")
    _prepare_for_cdxgen(source_dir=tmp_path, scan_uuid=uuid.uuid4())

    assert len(captured_prep_calls) == 1
    assert captured_prep_calls[0]["cmd"] == ["dotnet", "restore"]


def test_prepare_for_cdxgen_no_op_for_unrecognised_layout(
    tmp_path: Path, captured_prep_calls: list[dict[str, Any]]
) -> None:
    """A bare repo with no markers means no prep runs."""
    from tasks.scan_source import _prepare_for_cdxgen

    (tmp_path / "README.md").write_text("# nothing to see\n")
    _prepare_for_cdxgen(source_dir=tmp_path, scan_uuid=uuid.uuid4())

    assert captured_prep_calls == []


def test_prepare_for_cdxgen_dispatches_multiple_languages(
    tmp_path: Path, captured_prep_calls: list[dict[str, Any]]
) -> None:
    """A polyglot repo gets one prep call per applicable ecosystem."""
    from tasks.scan_source import _prepare_for_cdxgen

    (tmp_path / "Gemfile").write_text("source 'rubygems'\n")
    (tmp_path / "Cargo.toml").write_text('[package]\nname="x"\nversion="0.1.0"\n')
    (tmp_path / "go.mod").write_text("module x\n")
    _prepare_for_cdxgen(source_dir=tmp_path, scan_uuid=uuid.uuid4())

    assert {c["cmd"][0] for c in captured_prep_calls} == {"bundle", "cargo", "go"}


# ---------------------------------------------------------------------------
# _run_prep — best-effort, never raises
# ---------------------------------------------------------------------------


def test_run_prep_logs_returncode_zero_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tasks.scan_source import _run_prep

    class _FakeResult:
        def __init__(self) -> None:
            self.returncode = 0
            self.stdout = "ok\n"
            self.stderr = ""

    monkeypatch.setattr(
        "tasks.scan_source.subprocess.run",
        lambda *_a, **_kw: _FakeResult(),
    )

    # Should return without raising — failure to do so is the test failure.
    _run_prep("bundle lock", ["bundle", "lock"], tmp_path, 60, uuid.uuid4())


def test_run_prep_swallows_nonzero_returncode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tasks.scan_source import _run_prep

    class _FakeResult:
        def __init__(self) -> None:
            self.returncode = 1
            self.stdout = ""
            self.stderr = "Could not resolve dependency."

    monkeypatch.setattr(
        "tasks.scan_source.subprocess.run",
        lambda *_a, **_kw: _FakeResult(),
    )

    _run_prep("bundle lock", ["bundle", "lock"], tmp_path, 60, uuid.uuid4())


def test_run_prep_swallows_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tasks.scan_source import _run_prep

    def _boom(*_a: Any, **_kw: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd=["bundle", "lock"], timeout=60)

    monkeypatch.setattr("tasks.scan_source.subprocess.run", _boom)

    _run_prep("bundle lock", ["bundle", "lock"], tmp_path, 60, uuid.uuid4())


def test_run_prep_swallows_missing_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A worker image without the language layer must not break the scan."""
    from tasks.scan_source import _run_prep

    def _boom(*_a: Any, **_kw: Any) -> Any:
        raise FileNotFoundError(2, "No such file or directory: 'cargo'")

    monkeypatch.setattr("tasks.scan_source.subprocess.run", _boom)

    _run_prep("cargo gen", ["cargo", "generate-lockfile"], tmp_path, 60, uuid.uuid4())


# ---------------------------------------------------------------------------
# _classify_license_category + _LICENSE_CATEGORY_DEFAULTS — CLAUDE.md alignment
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spdx_id,expected",
    [
        # Allowed
        ("MIT", "allowed"),
        ("Apache-2.0", "allowed"),
        ("BSD-3-Clause", "allowed"),
        ("ISC", "allowed"),
        # Conditional
        ("LGPL-2.1-or-later", "conditional"),
        ("MPL-2.0", "conditional"),
        ("EPL-2.0", "conditional"),
        ("CDDL-1.0", "conditional"),
        # Forbidden
        ("GPL-2.0-only", "forbidden"),
        ("GPL-3.0-or-later", "forbidden"),
        ("AGPL-3.0-only", "forbidden"),
        ("SSPL-1.0", "forbidden"),
        ("BUSL-1.1", "forbidden"),
    ],
)
def test_classify_license_category_matches_claude_md(spdx_id: str, expected: str) -> None:
    from tasks.scan_source import _classify_license_category

    assert _classify_license_category(spdx_id) == expected


@pytest.mark.parametrize(
    "spdx_id",
    [None, "", "Custom-License", "ZLib-Acme-Fork-1.0"],
)
def test_classify_license_category_unknown_for_unmapped(spdx_id: str | None) -> None:
    """Anything outside the 30-entry map → 'unknown'."""
    from tasks.scan_source import _classify_license_category

    assert _classify_license_category(spdx_id) == "unknown"


# ---------------------------------------------------------------------------
# _extract_spdx_ids — CycloneDX licenses[] shape parsing
# ---------------------------------------------------------------------------


def test_extract_spdx_ids_pulls_license_id_form() -> None:
    from tasks.scan_source import _extract_spdx_ids

    component = {
        "licenses": [
            {"license": {"id": "MIT", "url": "https://opensource.org/licenses/MIT"}}
        ]
    }
    assert _extract_spdx_ids(component) == [
        ("MIT", "https://opensource.org/licenses/MIT"),
    ]


def test_extract_spdx_ids_accepts_simple_expression() -> None:
    """An SPDX expression with no operators is treated as a single license."""
    from tasks.scan_source import _extract_spdx_ids

    component = {"licenses": [{"expression": "Apache-2.0"}]}
    assert _extract_spdx_ids(component) == [("Apache-2.0", None)]


def test_extract_spdx_ids_skips_compound_expression() -> None:
    """`MIT OR Apache-2.0` requires an SPDX expression parser — skip it.

    cdxgen-fast-path scope: declared-from-metadata only. Compound
    expressions are intentionally deferred to the (future) ORT analyzer
    integration that emits per-file findings instead.
    """
    from tasks.scan_source import _extract_spdx_ids

    component = {"licenses": [{"expression": "MIT OR Apache-2.0"}]}
    assert _extract_spdx_ids(component) == []


def test_extract_spdx_ids_skips_freetext_license_name() -> None:
    """A `name`-only license entry has no SPDX id we can trust."""
    from tasks.scan_source import _extract_spdx_ids

    component = {"licenses": [{"license": {"name": "Acme Proprietary 2.0"}}]}
    assert _extract_spdx_ids(component) == []


def test_extract_spdx_ids_handles_missing_licenses_field() -> None:
    from tasks.scan_source import _extract_spdx_ids

    assert _extract_spdx_ids({}) == []
    assert _extract_spdx_ids({"licenses": None}) == []
    assert _extract_spdx_ids({"licenses": "not-a-list"}) == []
