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


__all__ = [
    "TrivyError",
    "TrivyFailed",
    "TrivyNotInstalled",
    "TrivyResult",
    "TrivyTimeout",
    "run_trivy_image",
]
