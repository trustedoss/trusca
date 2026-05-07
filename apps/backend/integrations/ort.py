"""
ORT (OSS Review Toolkit) adapter — license evaluator.

ORT is a JVM tool. The worker image installs Eclipse Temurin JRE 21 and the
ORT 85.0.0 distribution; ORT runs in three steps that we collapse into one
``evaluate`` call here:

1. ``analyze``  — parse package metadata (already done by cdxgen for SBOM,
   but ORT needs its own ``analyzer-result.yml``).
2. ``evaluate`` — apply the rules in ``ORT_RULES_PATH`` (defaults to the
   bundled ``ort/rules.kts``) to classify each license as
   ``allowed`` / ``conditional`` / ``forbidden`` / ``unknown``.
3. We do NOT run ``ort scan`` here — full source license scanning would push
   per-scan runtime past the 60-minute ceiling. cdxgen's declared-license
   metadata is sufficient for Phase 2; Phase 4 introduces optional deep scan.

The output is a JSON file (``evaluation-result.json``) with the structure::

    {
        "violations": [...],
        "evaluated_packages": [
            {"id": "Maven:com.example:foo:1.2.3",
             "concluded_license": "Apache-2.0",
             "category": "allowed", ...}
        ]
    }

The ``mock`` backend short-circuits all of this and emits a pre-canned result
keyed off the SBOM's components — useful for unit tests and the
TRUSTEDOSS_SCAN_BACKEND=mock smoke run.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess  # noqa: S404 — running a vetted local binary, not user input
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from core.config import scan_backend_mode
from integrations._subprocess_env import scrubbed_env_for_ort

log = structlog.get_logger("integrations.ort")

# ORT can take 5–20 minutes for a real repo (JVM warmup + dependency
# resolution). Cap at 45 minutes to fit inside the 1h soft Celery limit.
_DEFAULT_TIMEOUT_SECONDS = 45 * 60


def _ort_rules_path() -> str:
    """Resolved at call time so the worker picks up env changes (rule #11)."""
    return os.getenv("ORT_RULES_PATH", "/opt/trustedoss/ort/rules.kts")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class OrtError(RuntimeError):
    """Base class for ORT adapter errors."""


class OrtNotInstalled(OrtError):
    """Raised when the ``ort`` binary is not on $PATH."""


class OrtFailed(OrtError):
    """ORT exited with a non-zero status."""


class OrtTimeout(OrtError):
    """ORT ran longer than the per-stage timeout."""


@dataclass(frozen=True)
class OrtResult:
    """Output of an ORT evaluate run."""

    result_path: Path
    evaluation: dict[str, Any]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_ort(
    *,
    source_dir: Path,
    sbom_path: Path,
    output_dir: Path,
    rules_path: str | None = None,
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
    backend: str | None = None,
) -> OrtResult:
    """
    Run ORT analyze + evaluate against `source_dir` and persist the result.

    Args:
        source_dir: Cloned repo root.
        sbom_path: cdxgen output — used as the analyzer's input shortcut.
        output_dir: Workspace subdirectory for ORT artefacts.
        rules_path: Override the ruleset path (default: env / fallback).
        timeout_seconds: Hard wall-clock limit for the subprocess.
        backend: Override ``scan_backend_mode()``.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / "evaluation-result.json"
    mode = (backend or scan_backend_mode()).lower()

    if mode == "mock":
        return _write_mock_evaluation(result_path, sbom_path=sbom_path)

    if shutil.which("ort") is None:
        raise OrtNotInstalled(
            "ort binary not found on $PATH. Install ORT 85+ from "
            "https://github.com/oss-review-toolkit/ort/releases or set "
            "TRUSTEDOSS_SCAN_BACKEND=mock for tests.",
        )

    rules = rules_path or _ort_rules_path()
    cmd = [
        "ort",
        "evaluate",
        "--rules-file",
        rules,
        "--ort-file",
        str(sbom_path),
        "--output-dir",
        str(output_dir),
        "--output-formats",
        "JSON",
    ]
    log.info(
        "ort_start",
        source_dir=str(source_dir),
        rules=rules,
        sbom=str(sbom_path),
    )
    # ORT runs as a JVM subprocess. Both inputs (``--rules-file`` and
    # ``--ort-file``) are worker-controlled — ``rules_path`` resolves to
    # the operator's ``ORT_RULES_PATH`` env or the bundled
    # ``/opt/trustedoss/ort/rules.kts`` under the worker image, and
    # ``sbom_path`` is the cdxgen output we just wrote under the
    # workspace. ``cwd`` is the cloned source tree but ORT does not
    # ingest manifests from it (we hand it the SBOM directly), so the
    # clone cannot smuggle secrets through ORT's analyzer. Even so we
    # scrub the env (security-reviewer Medium #1 v2, chore PR #6): a
    # malicious ORT plugin or upstream JVM CVE would otherwise have
    # access to ``DT_API_KEY`` / ``SECRET_KEY`` / ``DATABASE_URL``.
    try:
        completed = subprocess.run(  # noqa: S603 — fixed args, no shell
            cmd,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
            cwd=str(source_dir),
            env=scrubbed_env_for_ort(),
        )
    except subprocess.TimeoutExpired as exc:
        raise OrtTimeout(
            f"ort evaluate exceeded {timeout_seconds}s while scanning {source_dir}",
        ) from exc

    if completed.returncode != 0:
        log.error(
            "ort_failed",
            returncode=completed.returncode,
            stderr=completed.stderr.decode("utf-8", errors="replace")[:4000],
        )
        raise OrtFailed(
            f"ort exited {completed.returncode}: "
            f"{completed.stderr.decode('utf-8', errors='replace')[:1000]}",
        )

    if not result_path.exists():
        # Some ORT releases name the file ``evaluation-result.json``; others
        # put it in a subdirectory. Try a couple of well-known fallbacks
        # before giving up.
        candidates = list(output_dir.rglob("evaluation-result.json"))
        if candidates:
            result_path = candidates[0]
        else:
            raise OrtFailed(f"ort succeeded but no evaluation-result.json under {output_dir}")

    evaluation = _load_json(result_path)
    log.info(
        "ort_succeeded",
        violations=len(evaluation.get("violations", [])),
        packages=len(evaluation.get("evaluated_packages", [])),
    )
    return OrtResult(result_path=result_path, evaluation=evaluation)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)
    return data


def _write_mock_evaluation(path: Path, *, sbom_path: Path) -> OrtResult:
    """
    Generate a deterministic mock evaluation from the SBOM's component list.

    Every component is classified ``allowed`` with license ``MIT`` so unit
    tests can assert on a well-known shape without running the JVM.
    """
    components: list[dict[str, Any]] = []
    if sbom_path.exists():
        try:
            sbom = _load_json(sbom_path)
            components = sbom.get("components", []) or []
        except (json.JSONDecodeError, OSError):
            components = []

    evaluated: list[dict[str, Any]] = []
    for comp in components:
        evaluated.append(
            {
                "id": comp.get("purl") or comp.get("bom-ref") or comp.get("name", "unknown"),
                "name": comp.get("name", "unknown"),
                "version": comp.get("version", "0.0.0"),
                "concluded_license": "MIT",
                "declared_license": "MIT",
                "category": "allowed",
            }
        )

    evaluation: dict[str, Any] = {
        "violations": [],
        "evaluated_packages": evaluated,
    }
    path.write_text(json.dumps(evaluation, indent=2), encoding="utf-8")
    log.info("ort_mock_written", path=str(path), packages=len(evaluated))
    return OrtResult(result_path=path, evaluation=evaluation)


__all__ = [
    "OrtError",
    "OrtFailed",
    "OrtNotInstalled",
    "OrtResult",
    "OrtTimeout",
    "run_ort",
]
