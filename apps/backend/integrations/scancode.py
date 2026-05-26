"""
scancode-toolkit adapter — first-party detected-license scanner (PR-A2).

PR-A2 replaced the broken ORT ``evaluate`` stage with a scancode pass that
runs over the **first-party** source tree only (the cloned / uploaded
workspace). Third-party dependency licenses stay *declared* (from cdxgen's
package metadata) — we deliberately do NOT download dependency sources, which
would push per-scan runtime past the budget. scancode therefore gives us
*detected* SPDX licenses for code the team actually wrote, complementing the
declared licenses for everything they pulled in.

scancode-toolkit is a pure-Python tool installed into an isolated virtualenv
in the worker image (``apps/backend/Dockerfile.worker``); the ``scancode``
launcher is symlinked onto ``$PATH``. The host running unit tests usually has
no such binary, which is why this adapter supports a ``mock`` mode keyed off
``TRUSTEDOSS_SCAN_BACKEND=mock`` and raises :class:`ScancodeNotInstalled` in
real mode when the binary is absent (so unit tests can pivot to mock — the
same pivot the cdxgen adapter offers).

Contract
--------
- Input: a first-party source directory + a workspace subdirectory for output.
- Output: a :class:`ScancodeResult` carrying the on-disk JSON path and a list
  of :class:`DetectedLicense` ``(spdx_id, source_path)`` tuples — the per-file
  detected SPDX identifiers the scan persists as ``kind='detected'``
  ``license_findings``.
- Guards (all resolved from env at call time — CLAUDE.md core rule #11):
    - ``SCANCODE_MAX_FILES``: a pre-scan walk counts eligible files (after the
      exclude filter); over the ceiling we skip with :class:`ScancodeTooLarge`
      so a giant monorepo cannot starve the budget.
    - ``SCANCODE_TIMEOUT_SECONDS``: hard subprocess wall-clock limit.
    - ``SCANCODE_MAX_DETECTIONS``: caps the number of returned tuples so a
      pathological tree cannot balloon ``license_findings``.

Exclusion
---------
The detection scope is first-party only. Vendored / build-output / VCS
directories (``node_modules`` / ``vendor`` / ``.git`` / ``dist`` / ``build``
/ ``target`` / ``.venv`` …) are excluded two ways for defence in depth:
  1. A pre-filter in :func:`_count_eligible_files` skips them when counting
     against ``SCANCODE_MAX_FILES`` (so an excluded ``node_modules`` does not
     trip the ceiling).
  2. scancode ``--ignore '<glob>'`` flags so scancode itself never reads them.

Both must agree — a path counted as eligible but scanned-and-ignored (or vice
versa) would make the ceiling lie. The single source of truth is
:data:`EXCLUDED_DIR_NAMES`.

Output parsing
--------------
scancode 32.x ``--json`` emits ``{"files": [{"path": ..., "type": "file",
"detected_license_expression_spdx": "MIT", "license_detections": [...]}, ...]}``
(compact, not the pretty ``--json-pp`` form — see :func:`_build_command`). The
result is size-capped (``SCANCODE_MAX_RESULT_BYTES``) before deserialization so
an attacker-controlled tree cannot OOM the worker via a giant document.
We read ``detected_license_expression_spdx`` per file and split simple
expressions into individual SPDX ids; compound expressions (AND / OR / WITH)
are recorded verbatim as a single token (downstream classification treats an
unrecognised token as ``unknown``). Free-text / ``LicenseRef-*`` detections
are kept too — they are legitimate *detected* findings even when not a clean
SPDX id.
"""

from __future__ import annotations

import json
import shutil
import subprocess  # noqa: S404 — running a vetted local binary, not user input
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from core.config import (
    scan_backend_mode,
    scancode_max_detections,
    scancode_max_files,
    scancode_max_result_bytes,
    scancode_timeout_seconds,
)
from integrations._line_streamer import LineCallback, run_with_line_streaming
from integrations._subprocess_env import scrubbed_env_for_scancode

log = structlog.get_logger("integrations.scancode")


# Width of ``licenses.spdx_id`` (models/scan.py — ``String(64)``). A detected
# SPDX token (which comes from attacker-controlled file content) longer than
# this would raise ``StringDataRightTruncation`` on INSERT and roll back the
# WHOLE persistence transaction — destroying the declared findings and the
# component graph this scan just built (the cache the UI shows when DT is down).
# We therefore cap the token at the adapter boundary: anything over the column
# width is dropped (detected licenses are auxiliary; declared from cdxgen is the
# authoritative set). The persistence layer caps again, defensively.
SPDX_ID_MAX_LENGTH = 64

# Cap on the ``source_path`` we carry into ``license_findings.source_path``
# (a ``Text`` column, so not a truncation risk, but an attacker-controlled deep
# path is unbounded telemetry / UI noise). Over this we keep a head+tail slice.
SOURCE_PATH_MAX_LENGTH = 1024


# Directory names excluded from first-party detection. Single source of truth
# for BOTH the eligible-file pre-count and the scancode --ignore flags so the
# SCANCODE_MAX_FILES ceiling and the actual scan scope can never disagree.
# These are vendored deps (node_modules / vendor / .venv / virtualenv),
# build output (dist / build / target / out / .next / __pycache__), and VCS /
# tooling metadata (.git / .hg / .svn / .tox / .mypy_cache / .pytest_cache).
EXCLUDED_DIR_NAMES: frozenset[str] = frozenset(
    {
        # vendored third-party sources (NOT first-party — declared via cdxgen)
        "node_modules",
        "vendor",
        "bower_components",
        ".venv",
        "venv",
        "virtualenv",
        "site-packages",
        # build / packaging output
        "dist",
        "build",
        "target",
        "out",
        ".next",
        ".nuxt",
        "__pycache__",
        ".gradle",
        # VCS + tooling metadata
        ".git",
        ".hg",
        ".svn",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".idea",
        ".vscode",
    }
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ScancodeError(RuntimeError):
    """Base class for scancode adapter errors."""


class ScancodeNotInstalled(ScancodeError):
    """Raised when the ``scancode`` binary is not on $PATH (test pivot point)."""


class ScancodeFailed(ScancodeError):
    """scancode exited with a non-zero status."""


class ScancodeTimeout(ScancodeError):
    """scancode ran longer than the per-stage timeout."""


class ScancodeTooLarge(ScancodeError):
    """First-party tree exceeds ``SCANCODE_MAX_FILES`` — detection skipped."""


@dataclass(frozen=True)
class DetectedLicense:
    """One detected (spdx_id, relative source path) pairing from scancode."""

    spdx_id: str
    source_path: str


@dataclass(frozen=True)
class ScancodeResult:
    """Output of a scancode first-party run."""

    result_path: Path
    detections: list[DetectedLicense]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_scancode(
    *,
    source_dir: Path,
    output_dir: Path,
    timeout_seconds: int | None = None,
    max_files: int | None = None,
    max_detections: int | None = None,
    backend: str | None = None,
    line_callback: LineCallback | None = None,
) -> ScancodeResult:
    """
    Run scancode over the **first-party** ``source_dir`` and return detected
    SPDX licenses.

    Args:
        source_dir: First-party source root (the cloned / uploaded workspace).
        output_dir: Workspace subdirectory for the scancode JSON artefact.
        timeout_seconds: Override ``SCANCODE_TIMEOUT_SECONDS``.
        max_files: Override ``SCANCODE_MAX_FILES`` (eligible-file ceiling).
        max_detections: Override ``SCANCODE_MAX_DETECTIONS`` (returned cap).
        backend: Override ``scan_backend_mode()`` (``mock`` writes a fixture).
        line_callback: P2 #8c — invoked from a background drain thread for
            every stdout / stderr line. ``(stream, line)`` where ``stream`` is
            ``"stdout"`` or ``"stderr"``. The callback runs in the drain
            thread; failures are caught and logged. The mock path emits no
            lines.

    Raises:
        ScancodeTooLarge: when the eligible-file count exceeds the ceiling.
            The caller (scan_source) treats this as best-effort: it logs and
            continues with declared (cdxgen) licenses only — a degraded but
            non-fatal outcome, mirroring the prep-stage philosophy.
        ScancodeNotInstalled / ScancodeFailed / ScancodeTimeout: real-mode
            subprocess failure modes (see the per-class docstrings).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / "scancode.json"
    mode = (backend or scan_backend_mode()).lower()
    cap = max_detections if max_detections is not None else scancode_max_detections()

    if mode == "mock":
        return _write_mock_result(result_path, source_dir=source_dir, cap=cap)

    if shutil.which("scancode") is None:
        raise ScancodeNotInstalled(
            "scancode binary not found on $PATH. Install scancode-toolkit "
            "(`pip install scancode-toolkit`) or set "
            "TRUSTEDOSS_SCAN_BACKEND=mock for tests.",
        )

    ceiling = max_files if max_files is not None else scancode_max_files()
    eligible = _count_eligible_files(source_dir, ceiling=ceiling)
    if eligible > ceiling:
        raise ScancodeTooLarge(
            f"first-party tree has >{ceiling} eligible files "
            f"(SCANCODE_MAX_FILES); skipping detected-license scan",
        )

    timeout = timeout_seconds if timeout_seconds is not None else scancode_timeout_seconds()
    cmd = _build_command(source_dir=source_dir, result_path=result_path)
    log.info(
        "scancode_start",
        source_dir=str(source_dir),
        output=str(result_path),
        eligible_files=eligible,
        timeout_seconds=timeout,
        streaming=line_callback is not None,
    )
    try:
        completed = run_with_line_streaming(
            cmd,
            timeout_seconds=timeout,
            cwd=str(source_dir),
            env=scrubbed_env_for_scancode(),
            line_callback=line_callback,
            stage="scancode",
        )
    except subprocess.TimeoutExpired as exc:
        raise ScancodeTimeout(
            f"scancode exceeded {timeout}s while scanning {source_dir}",
        ) from exc

    if completed.returncode != 0:
        log.error(
            "scancode_failed",
            returncode=completed.returncode,
            stderr=completed.stderr.decode("utf-8", errors="replace")[:4000],
        )
        raise ScancodeFailed(
            f"scancode exited {completed.returncode}: "
            f"{completed.stderr.decode('utf-8', errors='replace')[:1000]}",
        )

    detections = _parse_detections(result_path, cap=cap)
    log.info(
        "scancode_succeeded",
        detections=len(detections),
        result_size_bytes=result_path.stat().st_size if result_path.exists() else 0,
    )
    return ScancodeResult(result_path=result_path, detections=detections)


# ---------------------------------------------------------------------------
# Command construction
# ---------------------------------------------------------------------------


def _build_command(*, source_dir: Path, result_path: Path) -> list[str]:
    """Build the scancode argv.

    ``--license`` enables license detection; we deliberately omit copyright /
    package / email scanning to keep the runtime predictable (first-party
    license detection is the only thing this stage needs — third-party package
    metadata is cdxgen's job). ``--quiet`` suppresses the progress bar (noise in
    worker logs).

    Ignore globs (security-reviewer Medium #2): scancode's ``*`` does NOT cross
    ``/``, so ``*/<name>/*`` only matches a directory exactly one level deep —
    a ``a/b/node_modules/x`` (two levels) would slip through and be scanned even
    though :func:`_count_eligible_files` (which prunes by *name* at any depth)
    already excluded it. That mismatch let a hostile clone bury a giant
    ``node_modules`` two levels down to dodge the ``SCANCODE_MAX_FILES`` ceiling.
    We emit the **bare** ``<name>`` ignore, which scancode treats as "ignore a
    directory of this name anywhere in the tree" (matching the pre-count's
    name-based pruning at any depth), and keep the explicit ``*/<name>/*`` /
    ``<name>/*`` globs for defence in depth.

    Symlinks (security-reviewer Low): scancode does NOT follow symlinks by
    default, so a symlink pointing outside ``source_dir`` (e.g. ``-> /etc``) is
    not traversed — this matches :func:`_count_eligible_files`, which skips
    symlinks. We rely on that default rather than passing a follow flag; there
    is no scancode option that would make traversal *safer* than the default of
    not following, and ``--ignore`` would not help (the link target is outside
    the scanned root). A unit test pins that an out-of-tree symlink target is
    never read.

    ``--json`` writes COMPACT JSON (not ``--json-pp`` pretty-print) — the
    pretty form roughly doubles the on-disk footprint with indentation we never
    read, working against the result-size ceiling (Medium #3).
    """
    cmd: list[str] = [
        "scancode",
        "--license",
        "--quiet",
        "--strip-root",
    ]
    for name in sorted(EXCLUDED_DIR_NAMES):
        # Bare name: ignore this directory at ANY depth (scancode semantics) —
        # keeps the scan scope consistent with the eligible-file pre-count.
        cmd.extend(["--ignore", name])
        # Explicit path globs for defence in depth (root-level + one level).
        cmd.extend(["--ignore", f"*/{name}/*"])
        cmd.extend(["--ignore", f"{name}/*"])
    cmd.extend(["--json", str(result_path), str(source_dir)])
    return cmd


# ---------------------------------------------------------------------------
# Eligible-file pre-count (SCANCODE_MAX_FILES guard)
# ---------------------------------------------------------------------------


def _count_eligible_files(source_dir: Path, *, ceiling: int) -> int:
    """Count files under ``source_dir`` that scancode would actually scan.

    Walks the tree, pruning :data:`EXCLUDED_DIR_NAMES` so an excluded
    ``node_modules`` does not inflate the count. Stops early once the count
    exceeds ``ceiling`` (the caller only needs the boolean "over the ceiling?",
    so there is no value in walking the rest of a giant tree). Symlinks are not
    followed — a symlink loop would otherwise spin the walk forever.
    """
    count = 0
    # os.walk-style manual walk so we can prune excluded dirs in-place.
    stack: list[Path] = [source_dir]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            # Unreadable directory (permissions / race with cleanup) — skip it
            # rather than aborting the whole count.
            continue
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir():
                    if entry.name in EXCLUDED_DIR_NAMES:
                        continue
                    stack.append(entry)
                elif entry.is_file():
                    count += 1
                    if count > ceiling:
                        return count
            except OSError:
                continue
    return count


# ---------------------------------------------------------------------------
# Output parsing
# ---------------------------------------------------------------------------


def _parse_detections(result_path: Path, *, cap: int) -> list[DetectedLicense]:
    """Extract (spdx_id, source_path) tuples from scancode JSON.

    Best-effort: a missing / unparseable result file yields an empty list
    rather than raising — scancode having "succeeded" (exit 0) but produced no
    parseable JSON is degraded, not fatal (the declared cdxgen licenses still
    stand). Per-file we read ``detected_license_expression_spdx``; binary /
    unlicensed files simply carry ``null`` there and are skipped. Results are
    de-duplicated on (spdx_id, source_path) and capped at ``cap``.
    """
    if not result_path.exists():
        log.warning("scancode_result_missing", path=str(result_path))
        return []

    # Result-size ceiling (security-reviewer Medium #3): the JSON is keyed off
    # the attacker-controlled tree, and ``json.load`` materialises the whole
    # document — an unbounded result is an OOM vector. Skip parsing (degraded,
    # non-fatal: declared cdxgen licenses still stand) when it is too large.
    try:
        size = result_path.stat().st_size
    except OSError as exc:
        log.warning("scancode_result_unstattable", error=str(exc)[:300])
        return []
    limit = scancode_max_result_bytes()
    if size > limit:
        log.warning(
            "scancode_result_too_large",
            size_bytes=size,
            limit_bytes=limit,
            note="result exceeds SCANCODE_MAX_RESULT_BYTES; detection skipped",
        )
        return []

    try:
        data = _load_json(result_path)
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("scancode_result_unparseable", error=str(exc)[:300])
        return []

    files = data.get("files")
    if not isinstance(files, list):
        return []

    seen: set[tuple[str, str]] = set()
    out: list[DetectedLicense] = []
    capped = False
    for entry in files:
        if not isinstance(entry, dict):
            continue
        if entry.get("type") not in (None, "file"):
            # scancode marks directories with type='directory'; only files
            # carry detections.
            continue
        path = entry.get("path")
        if not isinstance(path, str) or not path:
            continue
        spdx_expr = entry.get("detected_license_expression_spdx")
        if not isinstance(spdx_expr, str) or not spdx_expr.strip():
            continue
        safe_path = _truncate_source_path(path)
        for spdx_id in _split_spdx_expression(spdx_expr, source_path=path):
            key = (spdx_id, safe_path)
            if key in seen:
                continue
            seen.add(key)
            out.append(DetectedLicense(spdx_id=spdx_id, source_path=safe_path))
            if len(out) >= cap:
                capped = True
                break
        if capped:
            break

    if capped:
        log.warning(
            "scancode_detections_capped",
            cap=cap,
            note="excess detected licenses dropped; scan still succeeds",
        )
    return out


def _split_spdx_expression(expression: str, *, source_path: str | None = None) -> list[str]:
    """Split a detected SPDX expression into individual identifiers.

    A simple single-id expression (``MIT``) yields ``["MIT"]``. A compound
    expression (``MIT AND Apache-2.0`` / ``GPL-2.0-only WITH Classpath-...``)
    is kept verbatim as a single token so downstream classification can flag
    it for review (an unrecognised compound token classifies as ``unknown``).
    We do NOT attempt to fully parse SPDX expression grammar here — that is the
    classifier's concern; the adapter only normalises whitespace.

    Security (security-reviewer High): the expression is derived from
    attacker-controlled file content. A token longer than
    :data:`SPDX_ID_MAX_LENGTH` (the ``licenses.spdx_id`` column width) would,
    if persisted, raise ``StringDataRightTruncation`` and roll back the entire
    scan-persistence transaction. We drop over-length tokens here (with a
    structured WARNING) so a single hostile file cannot poison the cached
    declared findings / component graph. Detected licenses are auxiliary; the
    declared cdxgen set is authoritative, so skip-over-truncate is the right
    trade-off.
    """
    expr = expression.strip()
    if not expr:
        return []
    if len(expr) > SPDX_ID_MAX_LENGTH:
        log.warning(
            "scancode_spdx_too_long",
            length=len(expr),
            limit=SPDX_ID_MAX_LENGTH,
            source_path=_truncate_source_path(source_path) if source_path else None,
            preview=expr[:80],
            note="detected SPDX token exceeds column width; dropped (declared stands)",
        )
        return []
    # Compound vs simple: both currently kept verbatim as a single token (the
    # classifier resolves grammar). Kept as one branch for clarity now that the
    # length cap above is the only filtering this function performs.
    return [expr]


def _truncate_source_path(path: str) -> str:
    """Bound an attacker-controlled source path for storage / logging.

    ``license_findings.source_path`` is a ``Text`` column (no truncation risk),
    but a pathological deep path is unbounded UI noise and log bloat. Over
    :data:`SOURCE_PATH_MAX_LENGTH` we keep a head + tail slice with an explicit
    elision marker so the file is still recognisable.
    """
    if len(path) <= SOURCE_PATH_MAX_LENGTH:
        return path
    keep = SOURCE_PATH_MAX_LENGTH - len("...<truncated>...")
    head = keep // 2
    tail = keep - head
    log.warning(
        "scancode_source_path_too_long",
        length=len(path),
        limit=SOURCE_PATH_MAX_LENGTH,
    )
    return f"{path[:head]}...<truncated>...{path[-tail:]}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)
    return data


def _write_mock_result(
    path: Path, *, source_dir: Path, cap: int
) -> ScancodeResult:
    """
    Emit a deterministic mock scancode result.

    The mock scans the first-party tree for real first-party-looking files
    (so tests can assert the exclude filter works) but assigns every file a
    detected ``MIT`` license — a well-known shape unit tests can pin without a
    real scancode install. Excluded directories are skipped here too, mirroring
    the real ``--ignore`` flags so a mock-mode integration test exercises the
    same scope contract.
    """
    files: list[dict[str, Any]] = []
    detections: list[DetectedLicense] = []
    stack: list[Path] = [source_dir]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir():
                    if entry.name in EXCLUDED_DIR_NAMES:
                        continue
                    stack.append(entry)
                    continue
                if not entry.is_file():
                    continue
            except OSError:
                continue
            rel = str(entry.relative_to(source_dir))
            files.append(
                {
                    "path": rel,
                    "type": "file",
                    "detected_license_expression_spdx": "MIT",
                    "license_detections": [{"license_expression_spdx": "MIT"}],
                }
            )
            if len(detections) < cap:
                detections.append(DetectedLicense(spdx_id="MIT", source_path=rel))

    result: dict[str, Any] = {
        "headers": [{"tool_name": "scancode-toolkit", "tool_version": "mock"}],
        "files": files,
    }
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    log.info("scancode_mock_written", path=str(path), detections=len(detections))
    return ScancodeResult(result_path=path, detections=detections)


__all__ = [
    "EXCLUDED_DIR_NAMES",
    "SOURCE_PATH_MAX_LENGTH",
    "SPDX_ID_MAX_LENGTH",
    "DetectedLicense",
    "LineCallback",
    "ScancodeError",
    "ScancodeFailed",
    "ScancodeNotInstalled",
    "ScancodeResult",
    "ScancodeTimeout",
    "ScancodeTooLarge",
    "run_scancode",
]
