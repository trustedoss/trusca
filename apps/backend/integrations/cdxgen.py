"""
cdxgen adapter — CycloneDX SBOM generator.

cdxgen ships as the ``@cyclonedx/cdxgen`` Node package. The worker image
(``apps/backend/Dockerfile.worker``) installs cdxgen globally so the
``cdxgen`` binary is on $PATH; the host machine running unit tests usually has
no such binary, which is why this adapter supports a ``mock`` mode keyed off
``TRUSTEDOSS_SCAN_BACKEND=mock``.

Gradle worker-image requirement (G4, 2026-05-22):
    cdxgen's Gradle path shells out to ``gradle dependencies``. The Gradle
    ``java`` plugin requires a **JDK** toolchain (``JAVA_COMPILER``) even for
    dependency resolution — a JRE-only image fails every plain-Gradle build
    with ``Toolchain installation ... does not provide the required
    capabilities: [JAVA_COMPILER]`` and emits 0 components. The worker image
    therefore MUST ship a full JDK (``temurin-21-jdk``), not a JRE. This is a
    devops-owned image invariant, not something the adapter can work around.
    Android fixtures (``com.android.application``, root plugin ``apply
    false``) happen to resolve without the compiler because they never trigger
    the ``compileJava`` toolchain check.

Contract:

- Input: a source directory (the cloned repo) + the workspace root.
- Output: ``Path`` to the generated CycloneDX JSON, plus the parsed dict.
- Failure modes:
    - cdxgen binary missing → ``CdxgenNotInstalled`` (so unit tests can pivot
      to mock mode without a real install).
    - cdxgen exits non-zero → ``CdxgenFailed`` with stderr captured.
    - cdxgen runs longer than the per-stage timeout → ``CdxgenTimeout``.

Phase 2 PR #8 only needs the SBOM to flow through to DT; downstream
ScanComponent persistence reads ``components`` and ``dependencies`` arrays
from the parsed JSON.

Gradle 8 compatibility (chore PR #5 Part C):
    cdxgen <= 11.x injects an ``init.gradle`` script that calls
    ``allprojects { ... }`` against the root project. Gradle 8 removed
    that path's implicit ``allprojects`` property and the build aborts
    with ``Could not get unknown property 'allprojects' for root project``
    — observed during the 2026-05-07 UAT (pilot-java-gradle returned 0
    components). cdxgen honours a ``CDXGEN_GRADLE_ARGS`` environment
    variable that lets us pass our own Gradle invocation; we set it to
    skip the broken init script. cdxgen 11+ also accepts ``--no-recurse``
    in some builds; we keep the env-var path because it works on every
    cdxgen v11.x build that ships in the worker image.
"""

from __future__ import annotations

import json
import shutil
import subprocess  # noqa: S404 — running a vetted local binary, not user input
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from core.config import cdxgen_fetch_license, cdxgen_spec_version, scan_backend_mode
from integrations._line_streamer import LineCallback, run_with_line_streaming
from integrations._subprocess_env import scrubbed_env_for_cdxgen

log = structlog.get_logger("integrations.cdxgen")

# cdxgen is generally fast (<5 min for typical repos) but can stall on large
# monorepos with deep node_modules; we cap at 30 minutes.
_DEFAULT_TIMEOUT_SECONDS = 30 * 60


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class CdxgenError(RuntimeError):
    """Base class for cdxgen adapter errors."""


class CdxgenNotInstalled(CdxgenError):
    """Raised when the ``cdxgen`` binary is not on $PATH."""


class CdxgenFailed(CdxgenError):
    """cdxgen exited with a non-zero status."""


class CdxgenTimeout(CdxgenError):
    """cdxgen ran longer than the per-stage timeout."""


@dataclass(frozen=True)
class CdxgenResult:
    """Output of a cdxgen run."""

    sbom_path: Path
    sbom: dict[str, Any]


def _podfile_present(source_dir: Path) -> bool:
    """True when a ``Podfile`` exists within depth 3 of the source root.

    Depth-bounded on purpose (mirrors ``source_detect._has_android_manifest``):
    BomLens' unbounded ``find .`` over an attacker-controlled tree is a DoS
    surface. ``Pods/`` copies are ignored — ``Pods/Manifest.lock`` trees ship
    their own Podfiles that do not make the project a CocoaPods root.
    """
    if (source_dir / "Podfile").is_file():
        return True
    for pattern in ("*/Podfile", "*/*/Podfile"):
        for candidate in source_dir.glob(pattern):
            relative_parts = candidate.relative_to(source_dir).parts
            if candidate.is_file() and "Pods" not in relative_parts:
                return True
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_cdxgen(
    *,
    source_dir: Path,
    output_dir: Path,
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
    backend: str | None = None,
    line_callback: LineCallback | None = None,
    verbose: bool = False,
    spec_version: str | None = None,
    fetch_license: bool | None = None,
) -> CdxgenResult:
    """
    Generate a CycloneDX SBOM for `source_dir` under `output_dir`.

    `backend` defaults to ``scan_backend_mode()``. When set to ``mock`` the
    adapter writes a fixture SBOM to disk without invoking cdxgen — used by
    unit tests and the smoke harness.

    P2 #8c — ``line_callback`` (optional) is invoked for every stdout / stderr
    line as it arrives, from a background drain thread (so a slow consumer
    cannot stall the cdxgen subprocess). The callback receives
    ``(stream, line)`` where ``stream`` is ``"stdout"`` or ``"stderr"``. The
    callback runs synchronously inside the drain thread and MUST NOT raise —
    failures are caught and logged at WARNING. The mock path emits no lines
    (no subprocess to drain).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    sbom_path = output_dir / "cdxgen.cdx.json"
    mode = (backend or scan_backend_mode()).lower()

    # Resolve the spec-version / fetch-license toggles. Explicit args (passed by
    # the executor from the SbomGenRequest) win; otherwise fall back to the env
    # knobs (CLAUDE.md core rule #11). Default stays 1.5 / no-fetch — backward
    # compatible for direct callers (tests).
    resolved_spec = spec_version if spec_version is not None else cdxgen_spec_version()
    resolved_fetch = fetch_license if fetch_license is not None else cdxgen_fetch_license()

    if mode == "mock":
        return _write_mock_sbom(
            sbom_path, source_dir=source_dir, spec_version=resolved_spec
        )

    if shutil.which("cdxgen") is None:
        raise CdxgenNotInstalled(
            "cdxgen binary not found on $PATH. Install via "
            "`npm install -g @cyclonedx/cdxgen` or set "
            "TRUSTEDOSS_SCAN_BACKEND=mock for tests.",
        )

    cmd = [
        "cdxgen",
        "-r",  # recurse
        # cdxgen 12.x validates the generated BOM against the CycloneDX JSON
        # schema by default and emits NO output file when validation fails.
        # Some lockfile integrity values (e.g. npm's base64 sha512) violate the
        # schema's hash-content hex pattern, so an otherwise-valid SBOM with real
        # components gets dropped entirely (cdxgen exits non-zero, no -o file).
        # We persist + size-guard the SBOM ourselves downstream, so disable
        # cdxgen's own validation to keep valid-component SBOMs.
        "--no-validate",
        "-o",
        str(sbom_path),
        "--spec-version",
        resolved_spec,
    ]
    if _podfile_present(source_dir):
        # Phase L — cdxgen's cocoapods cataloger does NOT skip when the `pod`
        # CLI is absent (the worker ships no Ruby/CocoaPods toolchain): it
        # throws a TypeError on the undefined `pod` stdout and aborts the
        # WHOLE stage-1 scan. Excluding the type keeps the rest of the scan
        # alive; integrations/cocoapods_lockfile.py fills the pods back in
        # from Podfile.lock after cdxgen (see _merge_cocoapods_components in
        # tasks/scan_source.py). The sidecar executors carry the same guard
        # in scan_executor/build_prep_source.sh — keep both in sync.
        cmd += ["--exclude-type", "cocoapods"]
    cmd.append(str(source_dir))
    env = _build_cdxgen_env(source_dir=source_dir, output_dir=output_dir, verbose=verbose)
    if resolved_fetch:
        # cdxgen resolves component licenses when FETCH_LICENSE is truthy (env,
        # not a CLI flag — matches the BomLens sidecar). Off by default.
        env["FETCH_LICENSE"] = "true"
    log.info(
        "cdxgen_start",
        source_dir=str(source_dir),
        output=str(sbom_path),
        gradle_args=env.get("CDXGEN_GRADLE_ARGS"),
        streaming=line_callback is not None,
        verbose=verbose,
        spec_version=resolved_spec,
        fetch_license=resolved_fetch,
    )
    try:
        completed = run_with_line_streaming(
            cmd,
            timeout_seconds=timeout_seconds,
            cwd=str(source_dir),
            env=env,
            line_callback=line_callback,
            stage="cdxgen",
        )
    except subprocess.TimeoutExpired as exc:
        raise CdxgenTimeout(
            f"cdxgen exceeded {timeout_seconds}s while scanning {source_dir}",
        ) from exc

    if completed.returncode != 0:
        log.error(
            "cdxgen_failed",
            returncode=completed.returncode,
            stderr=completed.stderr.decode("utf-8", errors="replace")[:4000],
        )
        raise CdxgenFailed(
            f"cdxgen exited {completed.returncode}: "
            f"{completed.stderr.decode('utf-8', errors='replace')[:1000]}",
        )

    sbom = _load_sbom(sbom_path)
    # P3 #12 diagnostic (2026-05-26): the per-component ``depth`` / ``direct``
    # and the ``component_dependency_edges`` table are populated from
    # ``sbom["dependencies"]``. Surfacing the entry count on every successful
    # cdxgen run lets operators trace "Type/Usage columns are dash" gaps back
    # to cdxgen output quality (a 0 / missing array is the systemic skip mode
    # observed across the 2026-05-26 scan corpus). ``dependencies`` must be a
    # list of ``{"ref": str, "dependsOn": [str, ...]}`` dicts; non-list / missing
    # is reported as 0 so the log line always carries an integer field.
    raw_dependencies = sbom.get("dependencies")
    dependencies_count = (
        len(raw_dependencies) if isinstance(raw_dependencies, list) else 0
    )
    log.info(
        "cdxgen_succeeded",
        components=len(sbom.get("components", [])),
        dependencies_count=dependencies_count,
        sbom_size_bytes=sbom_path.stat().st_size,
    )
    return CdxgenResult(sbom_path=sbom_path, sbom=sbom)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Gradle 8 compatibility (chore PR #5 Part C)
# ---------------------------------------------------------------------------

# Gradle 8 init script that defines a no-op ``allprojects`` extension
# *before* cdxgen's init script runs. cdxgen 11.x's bundled init.gradle
# blindly accesses ``allprojects { ... }`` from the root project — that
# implicit closure was removed in Gradle 8 and the build aborts. The
# script below re-injects a benign delegate that swallows the call and
# lets cdxgen's downstream "list resolved dependencies" logic continue
# (cdxgen's component enumeration runs against ``configurations``,
# which Gradle 8 still exposes as expected). A pure no-op would also
# work but degrades cdxgen's recursion across multi-project builds; we
# prefer to keep recursion intact.
_GRADLE8_COMPAT_INIT = """\
// TrustedOSS Portal — Gradle 8 / cdxgen v11.x compatibility shim.
//
// cdxgen's init.gradle calls ``allprojects { ... }`` at root scope.
// Gradle 8 removed that implicit closure. We re-bind ``allprojects``
// to ``rootProject.allprojects`` so cdxgen's component enumeration
// keeps working without patching cdxgen itself.
gradle.projectsLoaded {
    if (!rootProject.ext.has("trustedossAllprojectsShim")) {
        rootProject.ext.trustedossAllprojectsShim = true
        rootProject.ext.allprojects = { Closure cl ->
            rootProject.allprojects(cl)
        }
    }
}
"""


def _is_gradle_project(source_dir: Path) -> bool:
    """Return True if ``source_dir`` looks like a Gradle build root."""
    for marker in ("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"):
        if (source_dir / marker).exists():
            return True
    return False


def _write_gradle_compat_init(output_dir: Path) -> Path:
    """Write the Gradle 8 compat init script under ``output_dir``.

    The file lives alongside other cdxgen artefacts so the workspace
    cleanup in ``scan_source._workspace`` reaps it on scan teardown.
    """
    init_path = output_dir / "trustedoss-gradle8-compat.init.gradle"
    output_dir.mkdir(parents=True, exist_ok=True)
    init_path.write_text(_GRADLE8_COMPAT_INIT, encoding="utf-8")
    return init_path


def _build_cdxgen_env(
    *, source_dir: Path, output_dir: Path, verbose: bool = False
) -> dict[str, str]:
    """Build the env dict cdxgen runs under.

    Starts from the scrubbed cdxgen env (security-reviewer Medium #1 v2,
    chore PR #6) — only the language-toolchain / npm-config keys that
    cdxgen actually needs are forwarded; worker secrets like
    ``DT_API_KEY`` / ``SECRET_KEY`` / ``DATABASE_URL`` are stripped so
    a hostile clone cannot exfiltrate them through cdxgen plugin
    telemetry or crash reports. Then, when the source contains a Gradle
    build, we conditionally augment ``CDXGEN_GRADLE_ARGS`` with a
    Gradle 8 compat init script (chore PR #5 Part C). An operator-set
    ``CDXGEN_GRADLE_ARGS`` is preserved verbatim — explicit opt-in.
    """
    env = scrubbed_env_for_cdxgen()
    # Scan-log verbosity (feat/scan-log-verbosity): cdxgen reads
    # ``CDXGEN_DEBUG_MODE`` and emits verbose per-plugin diagnostics when set
    # to ``debug``. Normal mode leaves it unset (cdxgen already streams its
    # default progress lines, which carry enough signal). Set before the
    # early returns so it applies to Gradle and non-Gradle projects alike.
    if verbose:
        env["CDXGEN_DEBUG_MODE"] = "debug"
    if not _is_gradle_project(source_dir):
        return env
    if env.get("CDXGEN_GRADLE_ARGS"):
        # Operator override wins — do not stomp on it.
        return env
    init_script = _write_gradle_compat_init(output_dir)
    env["CDXGEN_GRADLE_ARGS"] = f"--init-script {init_script}"
    return env


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_sbom(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)
    return data


def _write_mock_sbom(
    path: Path, *, source_dir: Path, spec_version: str = "1.5"
) -> CdxgenResult:
    """
    Emit a tiny but valid CycloneDX SBOM for the given source directory.

    The mock SBOM contains a single library component so downstream stages
    have something concrete to persist. Tests that need richer SBOMs pass
    pre-built fixtures by writing them to ``output_dir`` themselves.
    """
    sbom: dict[str, Any] = {
        "bomFormat": "CycloneDX",
        "specVersion": spec_version,
        "serialNumber": f"urn:uuid:mock-{source_dir.name}",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "name": source_dir.name,
                "version": "0.0.0",
            },
        },
        "components": [
            {
                "type": "library",
                "bom-ref": "pkg:npm/example@1.0.0",
                "name": "example",
                "version": "1.0.0",
                "purl": "pkg:npm/example@1.0.0",
                "licenses": [{"license": {"id": "MIT"}}],
            }
        ],
    }
    path.write_text(json.dumps(sbom, indent=2), encoding="utf-8")
    log.info("cdxgen_mock_written", path=str(path))
    return CdxgenResult(sbom_path=path, sbom=sbom)


__all__ = [
    "CdxgenError",
    "CdxgenFailed",
    "CdxgenNotInstalled",
    "CdxgenResult",
    "CdxgenTimeout",
    "LineCallback",
    "run_cdxgen",
]
