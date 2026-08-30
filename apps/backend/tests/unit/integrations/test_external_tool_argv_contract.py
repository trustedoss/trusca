"""
argv contract tests for the external-tool adapters (testing-hardening-plan-2026-08.md,
Wave 1, unit C1).

Why this file exists
---------------------
Before this file, argv assertions were scattered per adapter and only checked the
handful of flags that particular test happened to write (``test_trivy_sbom.py``
checked ``--format`` / ``--output`` and nothing else). There was no single place
that declared the FULL required-flag set for an adapter, so a flag silently
dropped from one code path could still pass every existing test. This file is
that place: one table, one required/forbidden/conditional declaration per
adapter, parametrized.

Interception boundary
----------------------
Every adapter that shells out through the shared streaming helper is patched at
``integrations.<module>.run_with_line_streaming``, not at ``subprocess.run``.
``run_with_line_streaming`` falls back to a bare ``subprocess.run`` only when
``line_callback`` is ``None`` (see ``_line_streamer.py``); every production
caller (``tasks/scan_source.py``, ``tasks/scan_container.py``,
``tasks/ingest_sbom.py``) always passes a callback, so production NEVER takes
that fast path. Patching ``subprocess.run`` directly (the older style, still
used by ``test_trivy_sbom.py`` et al. before F1 lands) exercises a branch
production never runs. Patching the helper boundary instead means these tests
walk the same code path scan_source.py does.

``govulncheck`` and the ``cosign`` sign/attest/verify family call
``subprocess.run`` directly, never routing through the streaming helper,
so those two are patched at ``integrations.<module>.subprocess.run``, matching
what production actually calls. Trivy's DB-download path
(``download_db_only``) is the same shape: it also calls ``subprocess.run``
directly (it has no line-by-line progress consumer), so it is patched the same
way even though it lives in ``trivy.py`` alongside the two streaming-helper
Trivy entry points.

``--skip-db-update`` / offline mode
------------------------------------
Air-gapped operation passes ``--skip-db-update`` to Trivy so a disconnected
worker does not stall retrying a DB fetch it cannot complete (bug 3,
testing-hardening-plan-2026-08.md §2 C1). ``run_trivy_image`` /
``run_trivy_sbom`` add the flag when
``core.config.trivy_db_bootstrap_on_start()`` is ``False`` (the operator's
``TRIVY_DB_BOOTSTRAP_ON_START=false`` signal that the cache is populated by a
separate process and the worker must never attempt a network pull); the base
case in the table below is the default (bootstrap on, online), where the
flag stays absent. The two dedicated conditional-flag tests below the table
cover the offline case. ``trivy_db_download`` (``download_db_only``) never
takes the flag, in either mode - that call's entire job is to update the DB,
so skipping the update would defeat its purpose.

Census defense
--------------
``test_every_subprocess_call_site_is_covered`` walks the top-level modules
under ``integrations/`` with the stdlib ``ast`` module and finds every direct
call to ``run_with_line_streaming(`` or ``subprocess.run(``. A new adapter (or
a new call site in an existing one) that is not in ``_ADAPTER_CALL_SITES``
below fails that test immediately, before anyone has to remember to update
this file by hand.

``integrations/scan_executor/local_docker.py`` (the Docker-sidecar SBOM
executor) and ``integrations/_line_streamer.py`` (the helper itself) are
deliberately out of the census: the plan's eight-adapter list names the direct
CLI tool adapters, not the sidecar that shells out to ``docker run`` on their
behalf.
"""

from __future__ import annotations

import ast
import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

_INTEGRATIONS_DIR = Path(__file__).resolve().parents[3] / "integrations"


# ---------------------------------------------------------------------------
# Per-adapter argv builders
#
# Each builder forces the "real" backend, pretends the binary is on $PATH,
# patches the adapter's subprocess boundary to capture argv and write just
# enough output for the success path to complete, then returns the captured
# argv list.
# ---------------------------------------------------------------------------


def _invoke_trivy_image(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, verbose: bool = False, offline: bool = False
) -> list[str]:
    from integrations import trivy as trivy_adapter

    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "real")
    monkeypatch.setattr("integrations.trivy.shutil.which", lambda _n: "/usr/local/bin/trivy")
    if offline:
        monkeypatch.setenv("TRIVY_DB_BOOTSTRAP_ON_START", "false")

    captured: dict[str, list[str]] = {}

    def _fake(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        captured["cmd"] = list(cmd)
        out_path = Path(cmd[cmd.index("--output") + 1])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps({"SchemaVersion": 2, "ArtifactName": "alpine:3.19", "Results": []}),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr("integrations.trivy.run_with_line_streaming", _fake)
    trivy_adapter.run_trivy_image(
        image_ref="alpine:3.19", output_dir=tmp_path / "out", verbose=verbose
    )
    return captured["cmd"]


def _invoke_trivy_sbom(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, verbose: bool = False, offline: bool = False
) -> list[str]:
    from integrations import trivy as trivy_adapter

    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "real")
    monkeypatch.setattr("integrations.trivy.shutil.which", lambda _n: "/usr/local/bin/trivy")
    if offline:
        monkeypatch.setenv("TRIVY_DB_BOOTSTRAP_ON_START", "false")

    sbom_path = tmp_path / "sbom.cdx.json"
    sbom_path.write_text('{"bomFormat":"CycloneDX"}', encoding="utf-8")

    captured: dict[str, list[str]] = {}

    def _fake(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        captured["cmd"] = list(cmd)
        out_path = Path(cmd[cmd.index("--output") + 1])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps({"SchemaVersion": 2, "ArtifactName": str(sbom_path), "Results": []}),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr("integrations.trivy.run_with_line_streaming", _fake)
    trivy_adapter.run_trivy_sbom(
        sbom_path=sbom_path, output_dir=tmp_path / "out", verbose=verbose
    )
    return captured["cmd"]


def _invoke_trivy_db_download(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[str]:
    from integrations import trivy as trivy_adapter

    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "real")
    monkeypatch.setattr("integrations.trivy.shutil.which", lambda _n: "/usr/local/bin/trivy")

    captured: dict[str, list[str]] = {}

    def _fake(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        captured["cmd"] = list(cmd)
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr("integrations.trivy.subprocess.run", _fake)
    trivy_adapter.download_db_only(timeout_seconds=60)
    return captured["cmd"]


def _invoke_cdxgen(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, podfile: bool = False
) -> list[str]:
    from integrations import cdxgen as cdxgen_adapter

    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "real")
    monkeypatch.setattr("integrations.cdxgen.shutil.which", lambda _n: "/usr/local/bin/cdxgen")

    source_dir = tmp_path / "src"
    source_dir.mkdir()
    if podfile:
        (source_dir / "Podfile").write_text("platform :ios, '13.0'\n", encoding="utf-8")

    captured: dict[str, list[str]] = {}

    def _fake(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        captured["cmd"] = list(cmd)
        out_path = Path(cmd[cmd.index("-o") + 1])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps({"bomFormat": "CycloneDX", "specVersion": "1.5", "components": []}),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr("integrations.cdxgen.run_with_line_streaming", _fake)
    cdxgen_adapter.run_cdxgen(source_dir=source_dir, output_dir=tmp_path / "out")
    return captured["cmd"]


def _invoke_scancode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, verbose: bool = False
) -> list[str]:
    from integrations import scancode as scancode_adapter

    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "real")
    monkeypatch.setenv("SCANCODE_ENABLED", "true")
    monkeypatch.setattr(
        "integrations.scancode.shutil.which", lambda _n: "/usr/local/bin/scancode"
    )

    source_dir = tmp_path / "src"
    source_dir.mkdir()
    (source_dir / "a.py").write_text("print('hi')\n", encoding="utf-8")

    captured: dict[str, list[str]] = {}

    def _fake(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        captured["cmd"] = list(cmd)
        out_path = Path(cmd[cmd.index("--json") + 1])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps({"files": []}), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr("integrations.scancode.run_with_line_streaming", _fake)
    scancode_adapter.run_scancode(
        source_dir=source_dir, output_dir=tmp_path / "out", verbose=verbose
    )
    return captured["cmd"]


def _invoke_scanoss(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, api_key: str | None = None
) -> list[str]:
    from integrations import scanoss as scanoss_adapter

    monkeypatch.setenv("SCANOSS_ENABLED", "true")
    monkeypatch.setattr(
        "integrations.scanoss.shutil.which", lambda _n: "/usr/local/bin/scanoss-py"
    )
    if api_key:
        monkeypatch.setenv("SCANOSS_API_KEY", api_key)
    else:
        monkeypatch.delenv("SCANOSS_API_KEY", raising=False)

    source_dir = tmp_path / "src"
    source_dir.mkdir()

    captured: dict[str, list[str]] = {}

    def _fake(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        captured["cmd"] = list(cmd)
        out_path = Path(cmd[cmd.index("--output") + 1])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("{}", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr("integrations.scanoss.run_with_line_streaming", _fake)
    scanoss_adapter.run_scanoss(source_dir=source_dir, output_dir=tmp_path / "out")
    return captured["cmd"]


def _invoke_cosign(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, keyless: bool = False
) -> list[str]:
    from cryptography.fernet import Fernet

    from core.crypto import encrypt_secret
    from integrations import cosign as cosign_adapter

    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "real")
    monkeypatch.setattr("integrations.cosign.shutil.which", lambda _n: "/usr/local/bin/cosign")

    blob = tmp_path / "sbom.cdx.json"
    blob.write_bytes(b'{"bomFormat":"CycloneDX"}')

    if not keyless:
        key = tmp_path / "cosign.key"
        key.write_text(
            "-----BEGIN ENCRYPTED COSIGN PRIVATE KEY-----\nx\n-----END ENCRYPTED "
            "COSIGN PRIVATE KEY-----\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("COSIGN_KEY_PATH", str(key))
        monkeypatch.setenv("GITHUB_APP_ENCRYPTION_KEY", Fernet.generate_key().decode())
        monkeypatch.setenv("COSIGN_KEY_PASSWORD_ENCRYPTED", encrypt_secret("sup3r-secret"))

    captured: dict[str, list[str]] = {}

    def _fake(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        captured["cmd"] = list(cmd)
        for i, tok in enumerate(cmd):
            if tok in ("--output-signature", "--output-certificate"):
                out_path = Path(cmd[i + 1])
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text("sig-bytes", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr("integrations.cosign.subprocess.run", _fake)
    cosign_adapter.sign_blob(blob_path=blob, output_dir=tmp_path / "out", keyless=keyless)
    return captured["cmd"]


def _invoke_govulncheck(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[str]:
    from integrations import govulncheck as govulncheck_adapter

    module_dir = tmp_path / "gomod"
    module_dir.mkdir()
    (module_dir / "go.mod").write_text("module example.invalid/x\n\ngo 1.21\n", encoding="utf-8")
    monkeypatch.setattr(
        "integrations.govulncheck.shutil.which", lambda _n: "/usr/local/bin/govulncheck"
    )

    captured: dict[str, list[str]] = {}

    def _fake(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = list(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("integrations.govulncheck.subprocess.run", _fake)
    govulncheck_adapter.run_govulncheck(module_dir=module_dir)
    return captured["cmd"]


# ---------------------------------------------------------------------------
# Table: base-case required / forbidden flag sets
#
# "Base case" means the adapter's default invocation (verbose=False, no
# Podfile, no SCANOSS key, key-based cosign). Conditional-flag behaviour
# (the toggles that add/remove a flag) is exercised by the dedicated tests
# below the table, one per adapter that actually has a conditional flag.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArgvCase:
    key: str
    invoke: Callable[[pytest.MonkeyPatch, Path], list[str]]
    required: frozenset[str]
    forbidden: frozenset[str]


_CASES: list[ArgvCase] = [
    ArgvCase(
        key="trivy_image",
        invoke=_invoke_trivy_image,
        required=frozenset(
            {"trivy", "image", "--format", "json", "--output", "--scanners", "vuln"}
        ),
        # --skip-db-update: only added in offline mode
        # (TRIVY_DB_BOOTSTRAP_ON_START=false); the base case here is the
        # online default, so it stays absent. See
        # test_trivy_image_offline_mode_adds_skip_db_update_flag below.
        forbidden=frozenset({"--debug", "--skip-db-update", "--quiet"}),
    ),
    ArgvCase(
        key="trivy_sbom",
        invoke=_invoke_trivy_sbom,
        required=frozenset({"trivy", "sbom", "--format", "json", "--output", "--scanners", "vuln"}),
        # --skip-db-update: only added in offline mode, same as trivy_image.
        # See test_trivy_sbom_offline_mode_adds_skip_db_update_flag below.
        forbidden=frozenset({"--debug", "--skip-db-update", "--quiet"}),
    ),
    ArgvCase(
        key="trivy_db_download",
        invoke=_invoke_trivy_db_download,
        required=frozenset({"trivy", "image", "--download-db-only", "--quiet", "--no-progress"}),
        # --skip-db-update: permanently forbidden here, in every mode -
        # this call's entire job is to update the DB, so skipping the
        # update would defeat its purpose. Unlike trivy_image/trivy_sbom,
        # there is no conditional variant of this case.
        forbidden=frozenset({"--skip-db-update", "--format"}),
    ),
    ArgvCase(
        key="cdxgen",
        invoke=_invoke_cdxgen,
        required=frozenset({"cdxgen", "-r", "--no-validate", "-o", "--spec-version"}),
        forbidden=frozenset({"--exclude-type", "cocoapods"}),
    ),
    ArgvCase(
        key="scancode",
        invoke=_invoke_scancode,
        required=frozenset(
            {"scancode", "--license", "--strip-root", "--json", "--quiet", "--ignore"}
        ),
        forbidden=frozenset({"--verbose"}),
    ),
    ArgvCase(
        key="scanoss",
        invoke=_invoke_scanoss,
        required=frozenset({"scanoss-py", "scan", "--output", "--apiurl"}),
        forbidden=frozenset({"--key"}),
    ),
    ArgvCase(
        key="cosign",
        invoke=_invoke_cosign,
        required=frozenset(
            {
                "cosign",
                "sign-blob",
                "--yes",
                "--new-bundle-format=false",
                "--use-signing-config=false",
                "--key",
                "--output-signature",
                "--",
            }
        ),
        forbidden=frozenset({"--output-certificate"}),
    ),
    ArgvCase(
        key="govulncheck",
        invoke=_invoke_govulncheck,
        required=frozenset({"govulncheck", "-json", "./..."}),
        # govulncheck has no verbose / debug knob in this adapter at all; the
        # forbidden set records that absence so a future accidental flag add
        # is caught here rather than in production.
        forbidden=frozenset({"--debug", "-v", "--json-pp"}),
    ),
]


@pytest.mark.parametrize("case", _CASES, ids=lambda c: c.key)
def test_required_flags_are_present(
    case: ArgvCase, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cmd = case.invoke(monkeypatch, tmp_path)
    missing = case.required - set(cmd)
    assert not missing, f"{case.key}: argv missing required flags {sorted(missing)}: {cmd}"


@pytest.mark.parametrize("case", _CASES, ids=lambda c: c.key)
def test_forbidden_flags_are_absent(
    case: ArgvCase, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cmd = case.invoke(monkeypatch, tmp_path)
    present = case.forbidden & set(cmd)
    assert not present, f"{case.key}: argv carries forbidden flags {sorted(present)}: {cmd}"


# ---------------------------------------------------------------------------
# Conditional flags: one dedicated test per adapter that has a toggle.
# ---------------------------------------------------------------------------


def test_trivy_image_verbose_adds_debug_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cmd = _invoke_trivy_image(monkeypatch, tmp_path, verbose=True)
    assert "--debug" in cmd


def test_trivy_image_offline_mode_adds_skip_db_update_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """bug 3 - TRIVY_DB_BOOTSTRAP_ON_START=false must add --skip-db-update
    so an air-gapped worker never tries a network DB pull mid-scan."""
    cmd = _invoke_trivy_image(monkeypatch, tmp_path, offline=True)
    assert "--skip-db-update" in cmd


def test_trivy_sbom_offline_mode_adds_skip_db_update_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """bug 3 - same offline signal as trivy_image, see that test."""
    cmd = _invoke_trivy_sbom(monkeypatch, tmp_path, offline=True)
    assert "--skip-db-update" in cmd


def test_trivy_db_download_offline_mode_never_adds_skip_db_update_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """download_db_only's entire job is to update the DB, so the offline
    signal must not reach it - --skip-db-update there would make the
    Path B "populate while connected" bootstrap silently do nothing."""
    monkeypatch.setenv("TRIVY_DB_BOOTSTRAP_ON_START", "false")
    cmd = _invoke_trivy_db_download(monkeypatch, tmp_path)
    assert "--skip-db-update" not in cmd


def test_trivy_sbom_verbose_adds_debug_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cmd = _invoke_trivy_sbom(monkeypatch, tmp_path, verbose=True)
    assert "--debug" in cmd


def test_cdxgen_podfile_adds_cocoapods_exclude(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cmd = _invoke_cdxgen(monkeypatch, tmp_path, podfile=True)
    assert "--exclude-type" in cmd
    assert cmd[cmd.index("--exclude-type") + 1] == "cocoapods"


def test_scancode_verbose_swaps_quiet_for_verbose(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cmd = _invoke_scancode(monkeypatch, tmp_path, verbose=True)
    assert "--verbose" in cmd
    assert "--quiet" not in cmd


def test_scanoss_key_configured_adds_key_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cmd = _invoke_scanoss(monkeypatch, tmp_path, api_key="sk-secret-123")
    assert "--key" in cmd
    assert cmd[cmd.index("--key") + 1] == "sk-secret-123"


def test_cosign_keyless_drops_key_flag_and_adds_certificate_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cmd = _invoke_cosign(monkeypatch, tmp_path, keyless=True)
    assert "--key" not in cmd
    assert "--output-certificate" in cmd
    assert "--output-signature" in cmd


# ---------------------------------------------------------------------------
# Census defense: a new call site must be declared here or this test fails.
# ---------------------------------------------------------------------------

# (filename, top-level-function-name) -> the table key above. Only files
# directly under integrations/ (not scan_executor/, oauth/, license_fetcher/,
# remediation/) are in scope; see module docstring for why.
_ADAPTER_CALL_SITES: dict[tuple[str, str], str] = {
    ("trivy.py", "run_trivy_image"): "trivy_image",
    ("trivy.py", "run_trivy_sbom"): "trivy_sbom",
    ("trivy.py", "download_db_only"): "trivy_db_download",
    ("cdxgen.py", "run_cdxgen"): "cdxgen",
    ("scancode.py", "run_scancode"): "scancode",
    ("scanoss.py", "run_scanoss"): "scanoss",
    ("cosign.py", "_run_cosign"): "cosign",
    ("govulncheck.py", "_run"): "govulncheck",
}

# Files that legitimately spawn subprocesses but are out of scope for this
# unit (see module docstring): the streaming helper itself, and the Docker
# sidecar executor (a different call shape: `docker run <adapter-image>` on
# the tool's behalf, not the tool binary itself).
_CENSUS_EXCLUDE_FILES = {"_line_streamer.py"}


def _census_call_sites() -> set[tuple[str, str]]:
    """AST-walk the top-level ``integrations/*.py`` files for direct calls to
    ``run_with_line_streaming(`` or ``subprocess.run(`` inside a top-level
    function, returning ``(filename, function_name)`` pairs.
    """

    def _is_target_call(node: ast.AST) -> bool:
        if not isinstance(node, ast.Call):
            return False
        func = node.func
        if isinstance(func, ast.Name) and func.id == "run_with_line_streaming":
            return True
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "run"
            and isinstance(func.value, ast.Name)
            and func.value.id == "subprocess"
        ):
            return True
        return False

    sites: set[tuple[str, str]] = set()
    for path in sorted(_INTEGRATIONS_DIR.glob("*.py")):
        if path.name in _CENSUS_EXCLUDE_FILES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for inner in ast.walk(node):
                if _is_target_call(inner):
                    sites.add((path.name, node.name))
                    break
    return sites


def test_every_subprocess_call_site_is_covered() -> None:
    """A new adapter (or a new call site in an existing one) must be added to
    ``_ADAPTER_CALL_SITES``, and therefore to the table above, or this
    fails. Guards against the C1 gap regressing silently the way argv
    assertions did before this file existed.
    """
    found = _census_call_sites()
    declared = set(_ADAPTER_CALL_SITES)

    missing = found - declared
    assert not missing, (
        f"integrations/ has subprocess call sites not covered by this contract "
        f"test: {sorted(missing)}; add a table entry (and an invoke helper) "
        f"for each"
    )
    stale = declared - found
    assert not stale, (
        f"_ADAPTER_CALL_SITES declares call sites that no longer exist in "
        f"integrations/: {sorted(stale)}; the adapter was removed/renamed "
        f"and this table was not updated"
    )


def test_the_case_table_keys_match_the_census_values() -> None:
    """The ``_CASES`` table and ``_ADAPTER_CALL_SITES`` must name the same
    eight adapters, so every census entry actually has argv coverage above.
    """
    table_keys = {case.key for case in _CASES}
    census_keys = set(_ADAPTER_CALL_SITES.values())
    assert table_keys == census_keys
    assert len(_CASES) == 8, f"expected 8 adapters, the table has {len(_CASES)}"
