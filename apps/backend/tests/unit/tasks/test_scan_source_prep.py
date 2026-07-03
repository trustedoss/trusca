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


def test_prepare_npm_generates_lockfile_only_when_no_lock(
    tmp_path: Path,
    captured_prep_calls: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lockless npm project gets ``npm install --package-lock-only`` so cdxgen
    reads a lock instead of full-installing node_modules — which would pull in
    spurious ``pkg:nix/*`` components from dependency-shipped flake.lock files
    (fixtures e2e finding)."""
    from tasks import scan_source

    (tmp_path / "package.json").write_text('{"name":"x","dependencies":{"lodash":"4.17.21"}}')
    monkeypatch.setattr("tasks.scan_source.shutil.which", lambda _b: "/usr/bin/npm")
    scan_source._prepare_for_cdxgen(source_dir=tmp_path, scan_uuid=uuid.uuid4())

    npm_calls = [c for c in captured_prep_calls if c["cmd"][:2] == ["npm", "install"]]
    assert len(npm_calls) == 1
    assert "--package-lock-only" in npm_calls[0]["cmd"]
    assert "--ignore-scripts" in npm_calls[0]["cmd"]


@pytest.mark.parametrize(
    "lock",
    ["package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml"],
)
def test_prepare_npm_skipped_when_any_lock_present(
    tmp_path: Path,
    captured_prep_calls: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    lock: str,
) -> None:
    from tasks import scan_source

    (tmp_path / "package.json").write_text('{"name":"x"}')
    (tmp_path / lock).write_text("{}")
    monkeypatch.setattr("tasks.scan_source.shutil.which", lambda _b: "/usr/bin/npm")
    scan_source._prepare_for_cdxgen(source_dir=tmp_path, scan_uuid=uuid.uuid4())

    assert [c for c in captured_prep_calls if c["cmd"][:2] == ["npm", "install"]] == []


def test_prepare_npm_skipped_without_npm_binary(
    tmp_path: Path,
    captured_prep_calls: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tasks import scan_source

    (tmp_path / "package.json").write_text('{"name":"x"}')
    monkeypatch.setattr("tasks.scan_source.shutil.which", lambda _b: None)
    scan_source._prepare_for_cdxgen(source_dir=tmp_path, scan_uuid=uuid.uuid4())

    assert [c for c in captured_prep_calls if c["cmd"][:2] == ["npm", "install"]] == []


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
# _prepare_yarn — empty-lock heal (G4)
# ---------------------------------------------------------------------------


def test_prepare_yarn_removes_empty_lock_so_cdxgen_uses_package_json(
    tmp_path: Path,
) -> None:
    """An empty ``yarn.lock`` shadows ``package.json`` in cdxgen — remove it.

    cdxgen prefers a present yarn.lock and never falls back to the manifest,
    so a 0-byte lock yields 0 components. Healing = delete the empty lock.
    """
    from tasks.scan_source import _prepare_for_cdxgen

    (tmp_path / "package.json").write_text(
        '{"name":"x","dependencies":{"lodash":"4.17.21"}}'
    )
    (tmp_path / "yarn.lock").write_text("")  # 0 bytes — the broken case.

    _prepare_for_cdxgen(source_dir=tmp_path, scan_uuid=uuid.uuid4())

    assert not (tmp_path / "yarn.lock").exists()
    assert (tmp_path / "package.json").exists()


def test_prepare_yarn_removes_whitespace_only_lock(tmp_path: Path) -> None:
    from tasks.scan_source import _prepare_for_cdxgen

    (tmp_path / "package.json").write_text('{"name":"x"}')
    (tmp_path / "yarn.lock").write_text("\n  \n\t\n")

    _prepare_for_cdxgen(source_dir=tmp_path, scan_uuid=uuid.uuid4())

    assert not (tmp_path / "yarn.lock").exists()


def test_prepare_yarn_keeps_populated_lock(tmp_path: Path) -> None:
    """A real yarn.lock carries the transitive graph — never delete it."""
    from tasks.scan_source import _prepare_for_cdxgen

    (tmp_path / "package.json").write_text('{"name":"x"}')
    lock = tmp_path / "yarn.lock"
    lock.write_text('lodash@4.17.21:\n  version "4.17.21"\n')

    _prepare_for_cdxgen(source_dir=tmp_path, scan_uuid=uuid.uuid4())

    assert lock.exists()
    assert "lodash" in lock.read_text()


def test_prepare_yarn_noop_without_package_json(tmp_path: Path) -> None:
    """An orphan empty yarn.lock with no manifest is left alone."""
    from tasks.scan_source import _prepare_for_cdxgen

    (tmp_path / "yarn.lock").write_text("")

    _prepare_for_cdxgen(source_dir=tmp_path, scan_uuid=uuid.uuid4())

    assert (tmp_path / "yarn.lock").exists()


def test_prepare_yarn_swallows_unlink_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A filesystem error removing the empty lock is logged, never raised."""
    from tasks.scan_source import _prepare_for_cdxgen

    (tmp_path / "package.json").write_text('{"name":"x"}')
    (tmp_path / "yarn.lock").write_text("")

    def _boom(self: Path) -> None:
        raise OSError("read-only filesystem")

    monkeypatch.setattr(Path, "unlink", _boom)

    # Must return cleanly despite the unlink failure.
    _prepare_for_cdxgen(source_dir=tmp_path, scan_uuid=uuid.uuid4())


# ---------------------------------------------------------------------------
# _prepare_poetry — requirements.txt synthesis (G4)
# ---------------------------------------------------------------------------


def test_prepare_poetry_synthesizes_requirements_for_pinned_deps(
    tmp_path: Path,
) -> None:
    """Legacy [tool.poetry.dependencies] + no lock → requirements.txt.

    The worker ships no `poetry` binary, so cdxgen cannot resolve the legacy
    table. We translate exact pins into a requirements.txt cdxgen can read.
    """
    from tasks.scan_source import _prepare_for_cdxgen

    (tmp_path / "pyproject.toml").write_text(
        "[tool.poetry]\n"
        'name = "x"\n'
        'version = "1.0.0"\n'
        "[tool.poetry.dependencies]\n"
        'python = "^3.11"\n'
        'requests = "2.31.0"\n'
    )

    _prepare_for_cdxgen(source_dir=tmp_path, scan_uuid=uuid.uuid4())

    req = tmp_path / "requirements.txt"
    assert req.exists()
    body = req.read_text()
    assert "requests==2.31.0" in body
    # The python interpreter constraint is not a package.
    assert "python" not in body


def test_prepare_poetry_skips_when_lock_present(tmp_path: Path) -> None:
    """A poetry.lock means cdxgen has the full graph — do nothing."""
    from tasks.scan_source import _prepare_for_cdxgen

    (tmp_path / "pyproject.toml").write_text(
        "[tool.poetry.dependencies]\nrequests = \"2.31.0\"\n"
    )
    (tmp_path / "poetry.lock").write_text("[[package]]\nname = \"requests\"\n")

    _prepare_for_cdxgen(source_dir=tmp_path, scan_uuid=uuid.uuid4())

    assert not (tmp_path / "requirements.txt").exists()


def test_prepare_poetry_does_not_clobber_existing_requirements(
    tmp_path: Path,
) -> None:
    from tasks.scan_source import _prepare_for_cdxgen

    (tmp_path / "pyproject.toml").write_text(
        "[tool.poetry.dependencies]\nrequests = \"2.31.0\"\n"
    )
    (tmp_path / "requirements.txt").write_text("flask==3.0.0\n")

    _prepare_for_cdxgen(source_dir=tmp_path, scan_uuid=uuid.uuid4())

    assert (tmp_path / "requirements.txt").read_text() == "flask==3.0.0\n"


def test_prepare_poetry_skips_pep621_project_table(tmp_path: Path) -> None:
    """PEP-621 [project.dependencies] is parsed by cdxgen natively — skip."""
    from tasks.scan_source import _prepare_for_cdxgen

    (tmp_path / "pyproject.toml").write_text(
        "[project]\n"
        'name = "x"\n'
        'dependencies = ["requests==2.31.0"]\n'
    )

    _prepare_for_cdxgen(source_dir=tmp_path, scan_uuid=uuid.uuid4())

    assert not (tmp_path / "requirements.txt").exists()


def test_prepare_poetry_skips_unreadable_pyproject(tmp_path: Path) -> None:
    """Malformed TOML is logged and swallowed, never raised."""
    from tasks.scan_source import _prepare_for_cdxgen

    (tmp_path / "pyproject.toml").write_text("this is = not valid toml [[[")

    _prepare_for_cdxgen(source_dir=tmp_path, scan_uuid=uuid.uuid4())

    assert not (tmp_path / "requirements.txt").exists()


def test_prepare_poetry_noop_when_no_pinned_deps(tmp_path: Path) -> None:
    """A poetry table with only range specs yields no requirements file."""
    from tasks.scan_source import _prepare_for_cdxgen

    (tmp_path / "pyproject.toml").write_text(
        "[tool.poetry.dependencies]\n"
        'python = "^3.11"\n'
        'requests = "^2.0"\n'  # range — cannot pin offline
    )

    _prepare_for_cdxgen(source_dir=tmp_path, scan_uuid=uuid.uuid4())

    assert not (tmp_path / "requirements.txt").exists()


def test_prepare_poetry_swallows_write_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A write failure on requirements.txt is logged, never raised."""
    from tasks.scan_source import _prepare_for_cdxgen

    (tmp_path / "pyproject.toml").write_text(
        "[tool.poetry.dependencies]\nrequests = \"2.31.0\"\n"
    )

    real_write_text = Path.write_text

    def _selective_boom(self: Path, *args: Any, **kwargs: Any) -> int:
        if self.name == "requirements.txt":
            raise OSError("disk full")
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", _selective_boom)

    _prepare_for_cdxgen(source_dir=tmp_path, scan_uuid=uuid.uuid4())

    assert not (tmp_path / "requirements.txt").exists()


@pytest.mark.parametrize(
    "spec,expected",
    [
        ("2.31.0", ["requests==2.31.0"]),  # bare exact pin
        ("==2.31.0", ["requests==2.31.0"]),  # explicit ==
        ("^1.0.0", []),  # caret range — cannot pin offline
        ("~=2.0", []),  # compatible-release range
        (">=1.0,<2.0", []),  # bounded range
        ("*", []),  # wildcard
    ],
)
def test_poetry_deps_to_requirements_only_emits_exact_pins(
    spec: str, expected: list[str]
) -> None:
    from tasks.scan_source import _poetry_deps_to_requirements

    assert _poetry_deps_to_requirements({"requests": spec}) == expected


def test_poetry_deps_to_requirements_skips_table_form_and_python(
    tmp_path: Path,
) -> None:
    """Table-form deps (git/path/extras) have no offline-installable version."""
    from tasks.scan_source import _poetry_deps_to_requirements

    deps = {
        "python": "^3.11",
        "requests": "2.31.0",
        "internal": {"git": "https://example.com/x.git"},
        "local": {"path": "../local"},
    }
    assert _poetry_deps_to_requirements(deps) == ["requests==2.31.0"]


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
# _scrubbed_env / _run_prep secret allowlist (security-reviewer Medium #1)
# ---------------------------------------------------------------------------


def test_run_prep_passes_only_allowlisted_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Worker secrets must NOT inherit into prep subprocesses.

    A hostile clone could otherwise tunnel ``DT_API_KEY`` /
    ``SECRET_KEY`` / ``DATABASE_URL`` through resolver telemetry or
    a malicious NuGet feed. We pin that the env handed to
    ``subprocess.run`` excludes those keys and includes only the
    documented allowlist.
    """
    from tasks.scan_source import _run_prep

    monkeypatch.setenv("DT_API_KEY", "super-secret-dt-key")
    monkeypatch.setenv("SECRET_KEY", "super-secret-jwt-signing-key")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://trustedoss:hunter2@postgres:5432/trustedoss",
    )
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/secret")
    monkeypatch.setenv("GOPROXY", "https://proxy.golang.org,direct")

    captured: dict[str, Any] = {}

    class _FakeResult:
        returncode = 0
        stdout = ""
        stderr = ""

    def _capture(*args: Any, **kwargs: Any) -> Any:
        captured["env"] = kwargs.get("env")
        return _FakeResult()

    monkeypatch.setattr("tasks.scan_source.subprocess.run", _capture)
    _run_prep("go mod tidy", ["go", "mod", "tidy"], tmp_path, 60, uuid.uuid4())

    env = captured["env"]
    assert env is not None, "subprocess.run must receive a scrubbed env, not inherit os.environ"
    # Secrets must not leak into the resolver subprocess.
    assert "DT_API_KEY" not in env
    assert "SECRET_KEY" not in env
    assert "DATABASE_URL" not in env
    assert "SLACK_WEBHOOK_URL" not in env
    # Documented allowlisted vars are forwarded.
    assert env.get("GOPROXY") == "https://proxy.golang.org,direct"


def test_run_prep_seeds_dotnet_telemetry_optout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the worker leaves .NET telemetry vars unset, prep seeds them.

    Otherwise ``dotnet restore`` phones home on first invocation, which
    is both noisy and a covert exfil channel for any env we ship later.
    """
    from tasks.scan_source import _run_prep

    monkeypatch.delenv("DOTNET_CLI_TELEMETRY_OPTOUT", raising=False)
    monkeypatch.delenv("DOTNET_NOLOGO", raising=False)

    captured: dict[str, Any] = {}

    class _FakeResult:
        returncode = 0
        stdout = ""
        stderr = ""

    def _capture(*args: Any, **kwargs: Any) -> Any:
        captured["env"] = kwargs.get("env")
        return _FakeResult()

    monkeypatch.setattr("tasks.scan_source.subprocess.run", _capture)
    _run_prep("dotnet restore", ["dotnet", "restore"], tmp_path, 60, uuid.uuid4())

    env = captured["env"]
    assert env["DOTNET_CLI_TELEMETRY_OPTOUT"] == "1"
    assert env["DOTNET_NOLOGO"] == "1"


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
        # Allowed — Phase E catalog expansion
        ("BSL-1.0", "allowed"),
        ("Artistic-2.0", "allowed"),
        ("PostgreSQL", "allowed"),
        ("UPL-1.0", "allowed"),
        ("AFL-3.0", "allowed"),
        ("MS-PL", "allowed"),
        ("BSD-4-Clause", "allowed"),
        ("CC-BY-4.0", "allowed"),
        ("MIT-0", "allowed"),
        # Conditional
        ("LGPL-2.1-or-later", "conditional"),
        ("MPL-2.0", "conditional"),
        ("EPL-2.0", "conditional"),
        ("CDDL-1.0", "conditional"),
        # Conditional — Phase E catalog expansion (share-alike / reciprocal)
        ("OFL-1.1", "conditional"),
        ("CC-BY-SA-4.0", "conditional"),
        ("MS-RL", "conditional"),
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


@pytest.mark.parametrize(
    ("spdx_id", "expected"),
    [
        # Compound expressions are resolved with correct per-operator semantics
        # (via services.license_expression): AND/WITH = most-restrictive,
        # OR = LEAST-restrictive ("either license satisfies"). They must not
        # degrade to 'unknown'.
        ("GPL-3.0-or-later AND GPL-3.0-only", "forbidden"),
        ("MIT AND ISC AND BSD-3-Clause", "allowed"),
        ("MIT OR Apache-2.0", "allowed"),
        ("Apache-2.0 AND MPL-2.0", "conditional"),
        ("(MIT OR Apache-2.0) AND GPL-3.0-only", "forbidden"),
        ("Apache-2.0 WITH LLVM-exception", "allowed"),
        ("MPL-2.0 OR LGPL-2.1-or-later", "conditional"),
        # OR is disjunctive (pick one): a forbidden operand does NOT poison the
        # expression when a non-forbidden alternative exists. Regression for the
        # dogfood finding (pyphen: GPL-2.0-or-later OR LGPL-2.1+ OR MPL-1.1 was
        # wrongly flagged forbidden when OR was treated like AND).
        ("GPL-2.0-or-later OR MPL-1.1", "conditional"),
        ("GPL-2.0-or-later OR LGPL-2.1-or-later OR MPL-1.1", "conditional"),
        ("GPL-3.0-only OR MIT", "allowed"),
        # No recognised operand → still unknown.
        ("Custom-A AND Custom-B", "unknown"),
    ],
)
def test_classify_license_category_resolves_compound_expressions(
    spdx_id: str, expected: str
) -> None:
    from tasks.scan_source import _classify_license_category

    assert _classify_license_category(spdx_id) == expected


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


def test_extract_spdx_ids_keeps_compound_expression() -> None:
    """A compound SPDX expression is now KEPT (previously dropped).

    Dropping it silently lost a package's disjunctive license (the dogfood
    pyphen finding). It is stored as-is and resolved by
    ``_classify_license_category`` with correct OR=least-restrictive semantics.
    """
    from tasks.scan_source import _extract_spdx_ids

    component = {"licenses": [{"expression": "MIT OR Apache-2.0"}]}
    assert _extract_spdx_ids(component) == [("MIT OR Apache-2.0", None)]


def test_extract_spdx_ids_skips_oversized_expression() -> None:
    """An expression longer than the License.spdx_id column (64) is skipped."""
    from tasks.scan_source import _extract_spdx_ids

    long_expr = " OR ".join(["GPL-3.0-or-later"] * 10)  # > 64 chars
    component = {"licenses": [{"expression": long_expr}]}
    assert _extract_spdx_ids(component) == []


def test_extract_spdx_ids_joins_multiple_licenses_with_or() -> None:
    """Multiple declared licenses on one component → joined with OR (disjunctive).

    When cdxgen emits a component's full multi-license set (e.g. GPL/LGPL/MPL),
    it is "pick one", so it must classify conditional, not forbidden.
    """
    from tasks.scan_source import _classify_license_category, _extract_spdx_ids

    component = {
        "licenses": [
            {"license": {"id": "GPL-2.0-or-later"}},
            {"license": {"id": "LGPL-2.1-or-later"}},
            {"license": {"id": "MPL-1.1"}},
        ]
    }
    extracted = _extract_spdx_ids(component)
    assert extracted == [
        ("GPL-2.0-or-later OR LGPL-2.1-or-later OR MPL-1.1", None),
    ]
    # And the disjunctive set resolves to conditional (OR = least-restrictive).
    assert _classify_license_category(extracted[0][0]) == "conditional"


def test_extract_spdx_ids_keeps_first_reference_url() -> None:
    """When joining multiple licenses, the first available url is kept."""
    from tasks.scan_source import _extract_spdx_ids

    component = {
        "licenses": [
            {"license": {"id": "MIT", "url": "https://mit"}},
            {"license": {"id": "Apache-2.0", "url": "https://apache"}},
        ]
    }
    assert _extract_spdx_ids(component) == [("MIT OR Apache-2.0", "https://mit")]


def test_extract_spdx_ids_skips_unrecognized_freetext_license_name() -> None:
    """A `name`-only entry the alias normalizer does not recognize is skipped —
    persisting a raw free-text name would pollute the license surfaces."""
    from tasks.scan_source import _extract_spdx_ids

    component = {"licenses": [{"license": {"name": "Acme Proprietary 2.0"}}]}
    assert _extract_spdx_ids(component) == []


def test_extract_spdx_ids_recovers_recognized_freetext_name() -> None:
    """Phase E: a `name`-only entry for a well-known alias is recovered as its
    canonical SPDX id (so it classifies instead of landing as unknown)."""
    from tasks.scan_source import _extract_spdx_ids

    component = {
        "licenses": [
            {"license": {"name": "Apache License, Version 2.0", "url": "https://apache"}}
        ]
    }
    assert _extract_spdx_ids(component) == [("Apache-2.0", "https://apache")]


def test_extract_spdx_ids_prefers_explicit_id_over_name_normalization() -> None:
    """An explicit SPDX ``id`` is authoritative; the ``name`` is not consulted."""
    from tasks.scan_source import _extract_spdx_ids

    component = {"licenses": [{"license": {"id": "MIT", "name": "The Apache License"}}]}
    assert _extract_spdx_ids(component) == [("MIT", None)]


def test_extract_spdx_ids_normalized_name_classifies_not_unknown() -> None:
    """The recovered id flows through the classifier to a real category."""
    from tasks.scan_source import _classify_license_category, _extract_spdx_ids

    component = {"licenses": [{"license": {"name": "Boost Software License 1.0"}}]}
    extracted = _extract_spdx_ids(component)
    assert extracted == [("BSL-1.0", None)]
    assert _classify_license_category(extracted[0][0]) == "allowed"


def test_extract_spdx_ids_handles_missing_licenses_field() -> None:
    from tasks.scan_source import _extract_spdx_ids

    assert _extract_spdx_ids({}) == []
    assert _extract_spdx_ids({"licenses": None}) == []
    assert _extract_spdx_ids({"licenses": "not-a-list"}) == []
