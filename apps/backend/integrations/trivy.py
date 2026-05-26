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
import shutil
import subprocess  # noqa: S404 — running a vetted local binary, not user input
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from core.config import scan_backend_mode

log = structlog.get_logger("integrations.trivy")

# Container scans are typically faster than source scans (no JVM, no
# transitive resolver), but the first run on a fresh worker pulls Trivy's
# vulnerability DB which can take several minutes. Cap at 30 minutes.
_DEFAULT_TIMEOUT_SECONDS = 30 * 60


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class TrivyError(RuntimeError):
    """Base class for Trivy adapter errors."""


class TrivyNotInstalled(TrivyError):
    """Raised when the ``trivy`` binary is not on $PATH."""


class TrivyFailed(TrivyError):
    """Trivy exited with a non-zero status."""


class TrivyTimeout(TrivyError):
    """Trivy ran longer than the per-stage timeout."""


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
) -> TrivyResult:
    """
    Scan `image_ref` (e.g. ``alpine:3.19`` or ``ghcr.io/foo/bar@sha256:...``).

    Returns a parsed Trivy JSON report. The caller is responsible for
    converting Trivy's findings into ``VulnerabilityFinding`` rows.
    """
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
        # Disable interactive output and the welcome banner.
        "--quiet",
        # Limit to vuln scanners to keep the runtime predictable; license +
        # secret scanning land in Phase 4.
        "--scanners",
        "vuln",
        image_ref,
    ]
    log.info("trivy_start", image=image_ref, output=str(report_path))
    try:
        completed = subprocess.run(  # noqa: S603 — fixed args list
            cmd,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise TrivyTimeout(
            f"trivy image exceeded {timeout_seconds}s scanning {image_ref}",
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
        TrivyNotInstalled: ``trivy`` binary not on ``$PATH`` in real mode.
        TrivyFailed: Trivy exited with a non-zero status. The stderr is
            captured (first 1000 chars) into the exception message and the
            full stderr is logged at ERROR.
        TrivyTimeout: Trivy exceeded ``timeout_seconds``.
        FileNotFoundError: The SBOM file does not exist (caught early so
            we do not waste a Trivy process on a missing input).
    """
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
        # Match the image-scan adapter: silence the welcome banner / progress
        # bar so worker logs stay parseable.
        "--quiet",
        str(sbom_path),
    ]
    log.info("trivy_sbom_start", sbom=str(sbom_path), output=str(report_path))
    try:
        completed = subprocess.run(  # noqa: S603 — fixed args list
            cmd,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise TrivyTimeout(
            f"trivy sbom exceeded {timeout_seconds}s scanning {sbom_path}",
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
        )

    report = _load_json(report_path)
    vuln_count = sum(len(r.get("Vulnerabilities", []) or []) for r in report.get("Results", []))
    log.info("trivy_sbom_succeeded", sbom=str(sbom_path), vulnerabilities=vuln_count)
    return TrivyResult(report_path=report_path, report=report)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> dict[str, Any]:
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


__all__ = [
    "TrivyError",
    "TrivyFailed",
    "TrivyNotInstalled",
    "TrivyResult",
    "TrivyTimeout",
    "run_trivy_image",
    "run_trivy_sbom",
]
