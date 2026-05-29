"""
Trivy adapter — container image vulnerability scanner.

Trivy 0.70.0 ships with the worker image and can pull images directly from
any reachable registry (with credentials from ``~/.docker/config.json``).
This adapter wraps a single ``trivy image --format json --output ...`` call.

Output JSON shape::

    {
        "ArtifactName": "alpine:3.19",
        "Results": [
            {
                "Target": "alpine:3.19 (alpine 3.19.1)",
                "Class": "os-pkgs",
                "Vulnerabilities": [
                    {"VulnerabilityID": "CVE-2024-...", "PkgName": "musl",
                     "InstalledVersion": "1.2.4", "Severity": "HIGH", ...}
                ]
            }
        ]
    }

Phase 2 PR #8 only persists the parsed dict; component / vulnerability
upserts happen in the persistence helpers. ``mock`` mode emits a tiny fixture
so unit tests can drive the whole container scan task without Docker.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess  # noqa: S404 — running a vetted local binary, not user input
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import structlog

from core.config import scan_backend_mode, workspace_root
from integrations._line_streamer import LineCallback, run_with_line_streaming
from integrations._subprocess_env import scrubbed_env_for_trivy

log = structlog.get_logger("integrations.trivy")

# Container scans are typically faster than source scans (no JVM, no
# transitive resolver), but the first run on a fresh worker pulls Trivy's
# vulnerability DB which can take several minutes. Cap at 30 minutes.
_DEFAULT_TIMEOUT_SECONDS = 30 * 60

# Defense-in-depth size cap on the on-disk Trivy report (security-reviewer L2
# on PR #196). A normal Trivy JSON for even a sprawling monorepo with thousands
# of components sits in the low tens of MB; 256 MiB is comfortably above any
# realistic real-world output. A report past this cap is almost certainly a
# corrupt write, a disk-fill DoS, or a fault-injected payload and must not be
# loaded into the worker process (where it could OOM the box or be deserialised
# into adversarial nested structures). The adapter loads JSON eagerly via
# ``json.load`` so we cannot stream-validate; bounding the file size is the
# only safe gate.
_MAX_REPORT_SIZE_BYTES = 256 * 1024 * 1024  # 256 MiB


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class TrivyError(RuntimeError):
    """Base class for Trivy adapter errors."""


class TrivyNotInstalled(TrivyError):
    """Raised when the ``trivy`` binary is not on $PATH."""


class TrivyFailed(TrivyError):
    """Trivy exited with a non-zero status.

    ``safe_detail`` is a path-redacted message safe to surface in an RFC 7807
    ``problem+json`` ``detail`` field exposed to API callers (security-reviewer
    L4 on PR #196). The main message keeps the absolute path / full stderr
    slice so ops engineers still get the diagnostic value in worker logs.
    Callers that build an HTTP response from a caught ``TrivyFailed`` should
    surface ``safe_detail`` to clients and log ``str(exc)`` for diagnostics.
    """

    def __init__(self, message: str, *, safe_detail: str | None = None) -> None:
        super().__init__(message)
        self.safe_detail = safe_detail or "Vulnerability scan failed"


class TrivyTimeout(TrivyError):
    """Trivy ran longer than the per-stage timeout.

    Like :class:`TrivyFailed`, carries a ``safe_detail`` attribute the caller
    can surface in an RFC 7807 response without leaking absolute workspace
    paths or full image references.
    """

    def __init__(self, message: str, *, safe_detail: str | None = None) -> None:
        super().__init__(message)
        self.safe_detail = safe_detail or "Vulnerability scan timed out"


# ---------------------------------------------------------------------------
# Internal: workspace boundary guard (security-reviewer L1 on PR #196)
# ---------------------------------------------------------------------------


def _ensure_inside_workspace(p: Path, *, label: str) -> Path:
    """Resolve ``p`` and reject if it escapes :func:`workspace_root`.

    Defense-in-depth: the only callers today are Celery tasks that build paths
    from ``workspace_root() / <scan_uuid>``, so the inputs are trusted. But if
    a future API caller or admin tooling passes a path verbatim from external
    input (e.g. an operator-supplied output directory in an upcoming "rerun
    against this SBOM" admin endpoint), parent-relative traversal (``..``) or
    a symlink could land Trivy writes outside the workspace, where they
    bypass workspace disk quotas, audit retention, and the cleanup path in
    ``scan_container.py`` that ``shutil.rmtree(workspace, ignore_errors=True)``.

    The check uses ``Path.resolve()`` (which follows symlinks) on both sides
    so a symlink pointing outside the workspace is rejected, not just a
    literal ``..`` traversal.

    ``workspace_root()`` is read at call time per CLAUDE.md core rule #11, so
    tests / operators can retune ``WORKSPACE_HOST_PATH`` without a rebuild.
    """
    resolved = p.resolve()
    root = Path(workspace_root()).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(
            f"{label} {resolved} escapes workspace root {root}",
        )
    return resolved


@dataclass(frozen=True)
class TrivyResult:
    """Output of a Trivy image scan."""

    report_path: Path
    report: dict[str, Any]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_trivy_image(
    *,
    image_ref: str,
    output_dir: Path,
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
    backend: str | None = None,
    line_callback: LineCallback | None = None,
    verbose: bool = False,
) -> TrivyResult:
    """
    Scan `image_ref` (e.g. ``alpine:3.19`` or ``ghcr.io/foo/bar@sha256:...``).

    Returns a parsed Trivy JSON report. The caller is responsible for
    converting Trivy's findings into ``VulnerabilityFinding`` rows.

    Raises:
        ValueError: ``output_dir`` resolves outside ``WORKSPACE_HOST_PATH``.
            See :func:`_ensure_inside_workspace` for the threat model.
        TrivyNotInstalled: ``trivy`` binary not on ``$PATH`` in real mode.
        TrivyFailed: Trivy exited non-zero, or the on-disk report exceeds
            :data:`_MAX_REPORT_SIZE_BYTES`. The ``safe_detail`` attribute
            carries a path-redacted message safe for an RFC 7807 ``detail``
            field; the main message keeps the absolute path for ops logs.
        TrivyTimeout: Trivy exceeded ``timeout_seconds``. Same
            ``safe_detail`` contract as :class:`TrivyFailed`.
    """
    output_dir = _ensure_inside_workspace(output_dir, label="output_dir")
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "trivy.json"
    mode = (backend or scan_backend_mode()).lower()

    if mode == "mock":
        return _write_mock_report(report_path, image_ref=image_ref)

    if shutil.which("trivy") is None:
        raise TrivyNotInstalled(
            "trivy binary not found on $PATH. Install Trivy 0.50+ from "
            "https://aquasecurity.github.io/trivy/ or set "
            "TRUSTEDOSS_SCAN_BACKEND=mock for tests.",
        )

    cmd = [
        "trivy",
        "image",
        "--format",
        "json",
        "--output",
        str(report_path),
        # Limit to vuln scanners to keep the runtime predictable; license +
        # secret scanning land in Phase 4.
        "--scanners",
        "vuln",
    ]
    # Scan-log verbosity (feat/scan-log-verbosity): the report goes to
    # ``--output <file>`` regardless, so Trivy's stdout/stderr carry ONLY
    # human-readable progress/diagnostic lines we can stream to the scan log.
    # Normal mode drops ``--quiet`` (previously suppressed the DB-update +
    # progress banner) so the user sees Trivy is alive; verbose mode adds
    # ``--debug`` for full matcher diagnostics.
    if verbose:
        cmd.append("--debug")
    cmd.append(image_ref)
    log.info("trivy_start", image=image_ref, output=str(report_path), verbose=verbose)
    try:
        completed = run_with_line_streaming(
            cmd,
            timeout_seconds=timeout_seconds,
            cwd=None,
            env=scrubbed_env_for_trivy(),
            line_callback=line_callback,
            stage="trivy",
        )
    except subprocess.TimeoutExpired as exc:
        # image_ref is caller-supplied and visible in the API response that
        # spawned the scan, so it's safe to echo back to the client.
        raise TrivyTimeout(
            f"trivy image exceeded {timeout_seconds}s scanning {image_ref}",
            safe_detail=f"Container image scan timed out: {image_ref}",
        ) from exc

    if completed.returncode != 0:
        log.error(
            "trivy_failed",
            returncode=completed.returncode,
            image=image_ref,
            stderr=completed.stderr.decode("utf-8", errors="replace")[:4000],
        )
        raise TrivyFailed(
            f"trivy exited {completed.returncode}: "
            f"{completed.stderr.decode('utf-8', errors='replace')[:1000]}",
            safe_detail=f"Container image scan failed: {image_ref}",
        )

    report = _load_json(report_path)
    vuln_count = sum(len(r.get("Vulnerabilities", []) or []) for r in report.get("Results", []))
    log.info("trivy_succeeded", image=image_ref, vulnerabilities=vuln_count)
    return TrivyResult(report_path=report_path, report=report)


def run_trivy_sbom(
    sbom_path: Path,
    output_dir: Path,
    *,
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
    backend: str | None = None,
    line_callback: LineCallback | None = None,
    verbose: bool = False,
) -> TrivyResult:
    """
    Match CVEs against a CycloneDX SBOM produced by cdxgen.

    This is the W6 replacement for the DT upload + poll cycle: cdxgen still
    enumerates components and writes a CycloneDX JSON SBOM, but Trivy now
    performs vulnerability matching against the same Trivy DB used for image
    scans (NVD + GHSA + redhat + ...). The caller passes the SBOM file path
    produced by ``run_cdxgen`` and gets back a parsed Trivy JSON report whose
    shape mirrors ``run_trivy_image`` so a single persistence path can consume
    both source and container scans.

    Output JSON shape (Trivy 0.50+ ``trivy sbom`` mode)::

        {
            "ArtifactName": "<sbom-path>",
            "ArtifactType": "cyclonedx",
            "Results": [
                {
                    "Target": "<purl-or-pkg>",
                    "Class": "lang-pkgs",
                    "Type": "npm",
                    "Vulnerabilities": [
                        {"VulnerabilityID": "CVE-...", "PkgName": "...",
                         "InstalledVersion": "...", "Severity": "HIGH", ...}
                    ]
                }
            ]
        }

    Args:
        sbom_path: CycloneDX JSON SBOM file produced by cdxgen. Must exist.
        output_dir: Directory the worker owns for this scan. The report is
            written as ``<output_dir>/trivy-sbom.json`` so a single workspace
            can hold both image and SBOM reports without collision.
        timeout_seconds: Per-stage timeout (default 30 min — SBOM scans are
            CPU-bound, no network pull, so they finish well under image
            scans, but the first run still has to load the vuln DB).
        backend: Override the scan backend (``real`` or ``mock``). Tests pass
            ``mock`` directly; production callers leave this as ``None`` and
            let ``scan_backend_mode()`` resolve from the env at call time
            (per CLAUDE.md core rule #11 — no module-level caching).

    Returns:
        A ``TrivyResult`` whose ``report`` field is the parsed JSON dict.

    Raises:
        ValueError: ``sbom_path`` or ``output_dir`` resolves outside
            ``WORKSPACE_HOST_PATH`` (defense-in-depth path-traversal guard;
            see :func:`_ensure_inside_workspace`).
        TrivyNotInstalled: ``trivy`` binary not on ``$PATH`` in real mode.
        TrivyFailed: Trivy exited with a non-zero status, or the on-disk
            report exceeds :data:`_MAX_REPORT_SIZE_BYTES`. The stderr is
            captured (first 1000 chars) into the exception message and the
            full stderr is logged at ERROR. The ``safe_detail`` attribute
            carries a path-redacted message safe for an RFC 7807 ``detail``
            field; the main message keeps the absolute path for ops logs.
        TrivyTimeout: Trivy exceeded ``timeout_seconds``. Same
            ``safe_detail`` contract as :class:`TrivyFailed`.
        FileNotFoundError: The SBOM file does not exist (caught early so
            we do not waste a Trivy process on a missing input).
    """
    output_dir = _ensure_inside_workspace(output_dir, label="output_dir")
    sbom_path = _ensure_inside_workspace(sbom_path, label="sbom_path")

    if not sbom_path.exists():
        raise FileNotFoundError(
            f"SBOM file not found: {sbom_path}. cdxgen must run before "
            "run_trivy_sbom and write a CycloneDX JSON file.",
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "trivy-sbom.json"
    mode = (backend or scan_backend_mode()).lower()

    if mode == "mock":
        return _write_mock_sbom_report(report_path, sbom_path=sbom_path)

    if shutil.which("trivy") is None:
        raise TrivyNotInstalled(
            "trivy binary not found on $PATH. Install Trivy 0.50+ from "
            "https://aquasecurity.github.io/trivy/ or set "
            "TRUSTEDOSS_SCAN_BACKEND=mock for tests.",
        )

    cmd = [
        "trivy",
        "sbom",
        "--format",
        "json",
        "--output",
        str(report_path),
        # Match run_trivy_image: explicitly restrict scanners to vuln so a
        # future trivy default flip to also-on license/secret scanning does
        # not start exfiltrating SBOM component data or matching internal
        # paths against secret patterns. (security-reviewer L3 on PR #196.)
        "--scanners",
        "vuln",
    ]
    # Scan-log verbosity (feat/scan-log-verbosity): see run_trivy_image. The
    # JSON report still lands in ``--output <file>``; dropping ``--quiet`` lets
    # the DB-update / matching progress stream to the scan log, and ``--debug``
    # adds full diagnostics in verbose mode.
    if verbose:
        cmd.append("--debug")
    cmd.append(str(sbom_path))
    log.info(
        "trivy_sbom_start", sbom=str(sbom_path), output=str(report_path), verbose=verbose
    )
    try:
        completed = run_with_line_streaming(
            cmd,
            timeout_seconds=timeout_seconds,
            cwd=None,
            env=scrubbed_env_for_trivy(),
            line_callback=line_callback,
            stage="trivy",
        )
    except subprocess.TimeoutExpired as exc:
        # ``sbom_path.name`` (basename only) — never expose the absolute
        # workspace path in a message the API may surface to clients.
        raise TrivyTimeout(
            f"trivy sbom exceeded {timeout_seconds}s scanning {sbom_path}",
            safe_detail=f"Vulnerability scan timed out for SBOM {sbom_path.name}",
        ) from exc

    if completed.returncode != 0:
        log.error(
            "trivy_sbom_failed",
            returncode=completed.returncode,
            sbom=str(sbom_path),
            stderr=completed.stderr.decode("utf-8", errors="replace")[:4000],
        )
        raise TrivyFailed(
            f"trivy sbom exited {completed.returncode}: "
            f"{completed.stderr.decode('utf-8', errors='replace')[:1000]}",
            safe_detail=f"Vulnerability scan failed for SBOM {sbom_path.name}",
        )

    report = _load_json(report_path)
    vuln_count = sum(len(r.get("Vulnerabilities", []) or []) for r in report.get("Results", []))
    log.info("trivy_sbom_succeeded", sbom=str(sbom_path), vulnerabilities=vuln_count)
    return TrivyResult(report_path=report_path, report=report)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> dict[str, Any]:
    """Load a Trivy JSON report from disk.

    Enforces a 256 MiB size cap on the file before reading it into memory
    (security-reviewer L2 on PR #196). Real Trivy output for even the largest
    realistic SBOM sits in the low tens of MB; anything past the cap is almost
    certainly a corrupt write, a disk-fill DoS, or a fault-injected payload.
    Loading it eagerly via :func:`json.load` could OOM the worker or
    deserialise into adversarial nested structures, so we refuse instead.

    Raises:
        TrivyFailed: file size exceeds :data:`_MAX_REPORT_SIZE_BYTES`.
    """
    size = path.stat().st_size
    if size > _MAX_REPORT_SIZE_BYTES:
        log.error(
            "trivy_report_oversize",
            path=str(path),
            size=size,
            cap=_MAX_REPORT_SIZE_BYTES,
        )
        raise TrivyFailed(
            f"trivy output too large: {size} bytes > "
            f"{_MAX_REPORT_SIZE_BYTES} cap (corrupt or fault-injected at "
            f"{path})",
            safe_detail="Vulnerability scan produced an oversized report",
        )
    with path.open("r", encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)
    return data


def _write_mock_report(path: Path, *, image_ref: str) -> TrivyResult:
    """Produce a small but realistic Trivy report for unit tests."""
    report: dict[str, Any] = {
        "SchemaVersion": 2,
        "ArtifactName": image_ref,
        "ArtifactType": "container_image",
        "Results": [
            {
                "Target": f"{image_ref} (alpine 3.19.1)",
                "Class": "os-pkgs",
                "Type": "alpine",
                "Vulnerabilities": [
                    {
                        "VulnerabilityID": "CVE-2024-MOCK-0001",
                        "PkgName": "example-pkg",
                        "InstalledVersion": "1.0.0",
                        "FixedVersion": "1.0.1",
                        "Severity": "HIGH",
                        "Title": "Mock vulnerability for tests",
                        "Description": "Synthetic CVE used by the mock scan backend.",
                        "References": ["https://example.invalid/CVE-2024-MOCK-0001"],
                    }
                ],
            }
        ],
    }
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    log.info("trivy_mock_written", path=str(path), image=image_ref)
    return TrivyResult(report_path=path, report=report)


def _write_mock_sbom_report(path: Path, *, sbom_path: Path) -> TrivyResult:
    """Produce a small but realistic Trivy ``sbom`` report for unit tests.

    Mirrors ``_write_mock_report`` but uses the ``lang-pkgs`` class / ``npm``
    type pair that ``trivy sbom`` emits for CycloneDX inputs from cdxgen,
    so downstream persisters can route source-scan vulnerabilities the same
    way regardless of backend mode.
    """
    report: dict[str, Any] = {
        "SchemaVersion": 2,
        "ArtifactName": str(sbom_path),
        "ArtifactType": "cyclonedx",
        "Results": [
            {
                "Target": "pkg:npm/example-pkg@1.0.0",
                "Class": "lang-pkgs",
                "Type": "npm",
                "Vulnerabilities": [
                    {
                        "VulnerabilityID": "CVE-2024-MOCK-SBOM-0001",
                        "PkgName": "example-pkg",
                        "InstalledVersion": "1.0.0",
                        "FixedVersion": "1.0.1",
                        "Severity": "HIGH",
                        "Title": "Mock SBOM vulnerability for tests",
                        "Description": "Synthetic CVE used by the mock scan backend.",
                        "References": ["https://example.invalid/CVE-2024-MOCK-SBOM-0001"],
                    }
                ],
            }
        ],
    }
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    log.info("trivy_sbom_mock_written", path=str(path), sbom=str(sbom_path))
    return TrivyResult(report_path=path, report=report)


# ---------------------------------------------------------------------------
# DB lifecycle — W6-#44 (worker bootstrap + weekly refresh beat)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrivyDbDownloadResult:
    """Outcome of one ``trivy --download-db-only`` invocation.

    Returned by :func:`download_db_only`. Callers (the worker-ready bootstrap
    hook + the weekly Celery beat task) inspect ``status`` to decide whether
    to log INFO or WARNING and whether to fire an operator notification.

    ``status`` values:
      - ``"downloaded"`` — Trivy fetched a fresh manifest (incremental or
        full). The on-disk metadata is now current.
      - ``"skipped"``    — The pre-flight check decided no work was needed
        (e.g. ``trivy`` binary not on $PATH in dev / mock mode). Not a failure.
      - ``"timeout"``    — The subprocess hit the wall-clock cap. The prior
        DB stays intact because Trivy swaps the manifest only on success.
      - ``"failed"``     — Trivy exited non-zero or could not be launched.
        The prior DB stays intact. ``error`` holds a redacted detail string.

    ``duration_seconds`` is wall time of the subprocess (0.0 for ``skipped``).
    ``stderr_tail`` captures the last ~1000 chars of Trivy's stderr on
    failure / timeout for the admin notification body. We never log full
    stderr (it can include attacker-controlled mirror response text, even
    though the DB OCI tag is operator-set).
    """

    status: Literal["downloaded", "skipped", "timeout", "failed"]
    duration_seconds: float
    error: str | None = None
    stderr_tail: str | None = None


def download_db_only(*, timeout_seconds: int) -> TrivyDbDownloadResult:
    """Run ``trivy --download-db-only`` and return a structured outcome.

    Used by both the worker-ready bootstrap hook (W6-#44, single fire at
    boot) and the weekly Celery Beat refresh task. The function NEVER
    raises — every failure mode degrades to a ``TrivyDbDownloadResult``
    with the failure status so the caller can decide between INFO / WARNING
    log levels and whether to dispatch a notification, without a separate
    try/except wrapper.

    Idempotency: Trivy's own download path is idempotent — re-running with
    a current on-disk manifest is a no-op (it stats the manifest, compares
    to upstream, and exits 0 without overwriting). Trivy also takes a file
    lock under ``cache_dir/db/`` for the duration of the download, so two
    concurrent invocations (e.g. a beat tick racing a manual ad-hoc call)
    serialise cleanly instead of corrupting the manifest.

    Air-gapped operation: if ``TRIVY_DB_REPOSITORY`` points at a mirror that
    is unreachable, the subprocess will exit non-zero. The caller logs the
    failure but the prior cached DB stays valid for ``trivy sbom`` /
    ``trivy image`` invocations — graceful degradation is owned at the
    caller layer, not here.

    Args:
        timeout_seconds: Wall-clock cap on the subprocess.

    Returns:
        A :class:`TrivyDbDownloadResult` describing the outcome.
    """
    # Pre-flight: in dev (TRUSTEDOSS_SCAN_BACKEND=mock) or on a developer
    # laptop without trivy installed, we MUST NOT block the worker boot or
    # log an ERROR. The mock backend has no use for a real DB anyway.
    backend = scan_backend_mode().lower()
    if backend == "mock":
        log.info("trivy_db_download_skipped", reason="mock_backend")
        return TrivyDbDownloadResult(status="skipped", duration_seconds=0.0)

    if shutil.which("trivy") is None:
        log.info("trivy_db_download_skipped", reason="trivy_not_installed")
        return TrivyDbDownloadResult(status="skipped", duration_seconds=0.0)

    cmd = [
        "trivy",
        "image",
        "--download-db-only",
        # ``--quiet`` keeps the worker log free of the progress bar that
        # Trivy emits to stderr (one line per Mb). The completion message
        # still lands at INFO level if Trivy prints it on success.
        "--quiet",
        # ``--no-progress`` is the older alias; harmless if Trivy picks the
        # quiet flag, but defensive against a version that splits the two.
        "--no-progress",
    ]
    log.info("trivy_db_download_start", timeout_seconds=timeout_seconds)
    started = datetime.now(tz=UTC)
    try:
        completed = subprocess.run(  # noqa: S603 — fixed args list
            cmd,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
            env=scrubbed_env_for_trivy(),
        )
    except subprocess.TimeoutExpired as exc:
        elapsed = (datetime.now(tz=UTC) - started).total_seconds()
        tail = ""
        if exc.stderr:
            tail = exc.stderr.decode("utf-8", errors="replace")[-1000:]
        log.warning(
            "trivy_db_download_timeout",
            duration_seconds=elapsed,
            timeout_seconds=timeout_seconds,
        )
        return TrivyDbDownloadResult(
            status="timeout",
            duration_seconds=elapsed,
            error=f"trivy --download-db-only exceeded {timeout_seconds}s",
            stderr_tail=tail or None,
        )
    except OSError as exc:
        # Covers ENOENT / permission denied on the cache dir / exec failures.
        # Treat as a non-fatal "failed" — the prior DB is still good.
        elapsed = (datetime.now(tz=UTC) - started).total_seconds()
        log.warning(
            "trivy_db_download_oserror",
            duration_seconds=elapsed,
            error=str(exc)[:300],
        )
        return TrivyDbDownloadResult(
            status="failed",
            duration_seconds=elapsed,
            error=str(exc)[:300],
        )

    elapsed = (datetime.now(tz=UTC) - started).total_seconds()
    if completed.returncode != 0:
        tail = completed.stderr.decode("utf-8", errors="replace")[-1000:]
        log.warning(
            "trivy_db_download_failed",
            returncode=completed.returncode,
            duration_seconds=elapsed,
        )
        return TrivyDbDownloadResult(
            status="failed",
            duration_seconds=elapsed,
            error=f"trivy --download-db-only exited {completed.returncode}",
            stderr_tail=tail or None,
        )

    log.info("trivy_db_download_complete", duration_seconds=elapsed)
    return TrivyDbDownloadResult(
        status="downloaded",
        duration_seconds=elapsed,
    )


# ---------------------------------------------------------------------------
# DB lifecycle status — W6-#43e (admin/health Trivy DB panel)
# ---------------------------------------------------------------------------


# Match the Trivy default refresh cadence configured in the worker boot path
# (W6-#44). The admin panel surfaces this so operators see the configured
# cadence next to the actual ``UpdatedAt`` from the on-disk metadata.
_DEFAULT_DB_REFRESH_HOURS = 24 * 7  # 7 days

# Freshness boundaries — pair with the badge colour the FE renders.
# < 7 days  : fresh
# < 14 days : stale
# >= 14 days: very_stale
_STALE_AFTER = timedelta(days=7)
_VERY_STALE_AFTER = timedelta(days=14)

TrivyDbFreshness = Literal["fresh", "stale", "very_stale", "unknown"]


def trivy_cache_dir() -> Path:
    """Resolve the Trivy cache directory at call time.

    Reads ``TRIVY_CACHE_DIR`` at each call (per CLAUDE.md core rule #11) and
    falls back to Trivy's documented default ``$HOME/.cache/trivy`` so the
    admin panel works on a stock worker image with no overrides.
    """
    override = os.getenv("TRIVY_CACHE_DIR")
    if override:
        return Path(override)
    return Path.home() / ".cache" / "trivy"


def trivy_db_repository() -> str:
    """Resolve the configured Trivy DB OCI repository at call time."""
    return os.getenv("TRIVY_DB_REPOSITORY", "ghcr.io/aquasecurity/trivy-db")


def trivy_db_refresh_interval_hours() -> int:
    """Hours between Trivy DB refreshes (admin panel display)."""
    raw = os.getenv("TRIVY_DB_REFRESH_HOURS")
    if not raw:
        return _DEFAULT_DB_REFRESH_HOURS
    try:
        value = int(raw)
    except ValueError:
        log.warning("trivy_db_refresh_hours_invalid", raw=raw)
        return _DEFAULT_DB_REFRESH_HOURS
    # Lower bound 1h — anything below that is almost certainly a misconfig
    # (Trivy itself caps to 24h cadence in production guidance). Upper bound
    # 168h (1 week) — we still want the panel to surface "next refresh" even
    # if the operator over-spaced the cadence.
    return max(1, value)


@dataclass(frozen=True)
class TrivyDbStatus:
    """Read-only snapshot of the Trivy vulnerability DB.

    Returned by :func:`get_trivy_db_status` and consumed by the admin/health
    panel (W6-#43e). All fields are optional so the "not yet downloaded"
    case can render without raising.
    """

    last_update: datetime | None
    """``UpdatedAt`` field of the on-disk ``metadata.json``."""

    next_refresh_at: datetime | None
    """``last_update + refresh_interval_hours``."""

    vuln_count: int | None
    """Total advisories tracked — best-effort from ``trivy --version`` or DB."""

    db_version: str | None
    """``"trivy-db v0.6.123"`` — pulled from ``metadata.json`` schema."""

    db_size_bytes: int | None
    """Sum of file sizes inside ``cache_dir/db/``."""

    refresh_interval_hours: int
    cache_dir: str
    repository: str
    freshness: TrivyDbFreshness


def _classify_freshness(last_update: datetime | None, *, now: datetime) -> TrivyDbFreshness:
    """Bucket ``last_update`` into fresh / stale / very_stale.

    ``unknown`` is reserved for the "no metadata.json yet" case so the panel
    can render an explicit empty state instead of mis-classifying as stale.
    """
    if last_update is None:
        return "unknown"
    # If ``last_update`` is naive (no tz), treat it as UTC — the Trivy schema
    # documents UpdatedAt as RFC3339, so this is defensive.
    if last_update.tzinfo is None:
        last_update = last_update.replace(tzinfo=UTC)
    age = now - last_update
    if age < _STALE_AFTER:
        return "fresh"
    if age < _VERY_STALE_AFTER:
        return "stale"
    return "very_stale"


def _parse_trivy_metadata(metadata_path: Path) -> dict[str, Any] | None:
    """Read & parse Trivy's ``metadata.json``.

    Returns ``None`` if the file does not exist (DB not yet downloaded) or
    cannot be parsed (corrupt). Either case renders the same empty state in
    the panel — we deliberately don't surface the parse error to admins
    because the only remediation is "wait for the next refresh" and the
    worker logs already carry the diagnostic.
    """
    if not metadata_path.exists():
        return None
    try:
        with metadata_path.open("r", encoding="utf-8") as fh:
            data: dict[str, Any] = json.load(fh)
        return data
    except (OSError, json.JSONDecodeError) as exc:
        log.warning(
            "trivy_db_metadata_unreadable",
            path=str(metadata_path),
            error=str(exc),
        )
        return None


def _parse_iso(value: Any) -> datetime | None:
    """Parse Trivy's RFC3339 timestamps; tolerate ``Z`` suffix and ``None``."""
    if not isinstance(value, str) or not value:
        return None
    # ``fromisoformat`` accepts ``+00:00`` but not ``Z`` until 3.11+; the
    # worker image runs on 3.12 so ``Z`` is accepted, but we normalise for
    # defensive parity with older Python builds in tests.
    normalised = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalised)
    except ValueError:
        log.warning("trivy_db_metadata_bad_timestamp", value=value)
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _sum_db_size(db_dir: Path) -> int | None:
    """Sum file sizes inside ``cache_dir/db/`` for the admin panel."""
    if not db_dir.exists() or not db_dir.is_dir():
        return None
    total = 0
    try:
        for entry in db_dir.iterdir():
            if entry.is_file():
                total += entry.stat().st_size
    except OSError as exc:
        log.warning("trivy_db_size_unreadable", path=str(db_dir), error=str(exc))
        return None
    return total


def get_trivy_db_status(*, now: datetime | None = None) -> TrivyDbStatus:
    """Read the on-disk Trivy DB state and return a snapshot.

    Best-effort: every per-field probe is wrapped so a missing / corrupt
    artefact returns a partial snapshot instead of raising. The admin panel
    decides what to render for ``None`` fields.

    ``now`` is injectable for deterministic freshness tests; production
    callers leave it as ``None`` and we read wall clock.
    """
    now = now or datetime.now(tz=UTC)
    cache_dir = trivy_cache_dir()
    db_dir = cache_dir / "db"
    metadata_path = db_dir / "metadata.json"
    repository = trivy_db_repository()
    refresh_hours = trivy_db_refresh_interval_hours()

    metadata = _parse_trivy_metadata(metadata_path)

    last_update: datetime | None = None
    db_version: str | None = None
    vuln_count: int | None = None
    if metadata is not None:
        last_update = _parse_iso(metadata.get("UpdatedAt"))
        # Trivy DB schema version pivots the on-disk format. We expose it as
        # ``trivy-db v0.<Version>.<NextUpdate counter>`` — operators can map
        # this to aquasecurity/trivy-db releases.
        version_field = metadata.get("Version")
        if isinstance(version_field, int):
            db_version = f"trivy-db schema v{version_field}"
        # Some Trivy versions surface ``VulnerabilityID`` count under a
        # ``VulnerabilityCount`` / ``Count`` key; tolerate either.
        for candidate_key in ("VulnerabilityCount", "Count", "AdvisoryCount"):
            candidate = metadata.get(candidate_key)
            if isinstance(candidate, int):
                vuln_count = candidate
                break

    next_refresh_at: datetime | None = None
    if last_update is not None:
        next_refresh_at = last_update + timedelta(hours=refresh_hours)

    db_size = _sum_db_size(db_dir)
    freshness = _classify_freshness(last_update, now=now)

    return TrivyDbStatus(
        last_update=last_update,
        next_refresh_at=next_refresh_at,
        vuln_count=vuln_count,
        db_version=db_version,
        db_size_bytes=db_size,
        refresh_interval_hours=refresh_hours,
        cache_dir=str(cache_dir),
        repository=repository,
        freshness=freshness,
    )


__all__ = [
    "TrivyDbDownloadResult",
    "TrivyDbFreshness",
    "TrivyDbStatus",
    "TrivyError",
    "TrivyFailed",
    "TrivyNotInstalled",
    "TrivyResult",
    "TrivyTimeout",
    "download_db_only",
    "get_trivy_db_status",
    "run_trivy_image",
    "run_trivy_sbom",
    "trivy_cache_dir",
    "trivy_db_refresh_interval_hours",
    "trivy_db_repository",
]
