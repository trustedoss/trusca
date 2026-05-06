"""
cdxgen adapter — CycloneDX SBOM generator.

cdxgen ships as the ``@cyclonedx/cdxgen`` Node package. The worker image
(``apps/backend/Dockerfile.worker``) installs version 11.11.0 globally so the
``cdxgen`` binary is on $PATH; the host machine running unit tests usually has
no such binary, which is why this adapter supports a ``mock`` mode keyed off
``TRUSTEDOSS_SCAN_BACKEND=mock``.

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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_cdxgen(
    *,
    source_dir: Path,
    output_dir: Path,
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
    backend: str | None = None,
) -> CdxgenResult:
    """
    Generate a CycloneDX SBOM for `source_dir` under `output_dir`.

    `backend` defaults to ``scan_backend_mode()``. When set to ``mock`` the
    adapter writes a fixture SBOM to disk without invoking cdxgen — used by
    unit tests and the smoke harness.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    sbom_path = output_dir / "cdxgen.cdx.json"
    mode = (backend or scan_backend_mode()).lower()

    if mode == "mock":
        return _write_mock_sbom(sbom_path, source_dir=source_dir)

    if shutil.which("cdxgen") is None:
        raise CdxgenNotInstalled(
            "cdxgen binary not found on $PATH. Install via "
            "`npm install -g @cyclonedx/cdxgen` or set "
            "TRUSTEDOSS_SCAN_BACKEND=mock for tests.",
        )

    cmd = [
        "cdxgen",
        "-r",  # recurse
        "-o",
        str(sbom_path),
        "--spec-version",
        "1.5",
        str(source_dir),
    ]
    log.info("cdxgen_start", source_dir=str(source_dir), output=str(sbom_path))
    try:
        completed = subprocess.run(  # noqa: S603 — args are a fixed list, no shell
            cmd,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
            cwd=str(source_dir),
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
    log.info(
        "cdxgen_succeeded",
        components=len(sbom.get("components", [])),
        sbom_size_bytes=sbom_path.stat().st_size,
    )
    return CdxgenResult(sbom_path=sbom_path, sbom=sbom)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_sbom(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)
    return data


def _write_mock_sbom(path: Path, *, source_dir: Path) -> CdxgenResult:
    """
    Emit a tiny but valid CycloneDX SBOM for the given source directory.

    The mock SBOM contains a single library component so downstream stages
    have something concrete to persist. Tests that need richer SBOMs pass
    pre-built fixtures by writing them to ``output_dir`` themselves.
    """
    sbom: dict[str, Any] = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
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
    "run_cdxgen",
]
