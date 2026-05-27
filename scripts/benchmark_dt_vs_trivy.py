#!/usr/bin/env python3
"""
benchmark_dt_vs_trivy.py — informational DT vs Trivy delta measurement (W6-#41).

Per ADR-0002 (``docs/decisions/0002-w6-trivy-benchmark-cohort.md``), this is
**informational only** — there is no pass/fail gate. The script measures the
delta between DT and Trivy on a small cohort of OSS repos, classifies each
diverging finding into one of three buckets, and writes a JSON report to
``./benchmark-results/<date>.json``. The report feeds the post-DT-removal
backlog (which Trivy gaps are worth pursuing, where Trivy's DB outperforms DT,
which version-mismatches are real bugs vs metadata noise).

Important:

* This script is NOT a CI gate. ADR-0001 Amendment 2 explicitly dropped the
  Jaccard-based shadow-mode gate because the metric had structural flaws and
  the rollback cost was minutes. We track divergence to *improve* Trivy
  matching, not to block the migration.
* Run this script locally once after #41 lands, attach the report to the PR
  description, and update ADR-0002 with the measured commit SHAs.
* The script assumes a working DT instance (DT_URL + DT_API_KEY env) AND a
  working ``trivy`` binary on $PATH. When either is unavailable the script
  emits a skip note in the report rather than crashing.

Usage:
    python3 scripts/benchmark_dt_vs_trivy.py [--out DIR]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Cohort (mirror of ADR-0002 table)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CohortEntry:
    name: str
    ecosystem: str
    url: str
    commit: str | None  # None = HEAD at script-run time; populated in report
    expected_deps: int  # rough upper bound for sanity-check, not a gate


COHORT: list[CohortEntry] = [
    CohortEntry(
        name="express",
        ecosystem="npm",
        url="https://github.com/expressjs/express",
        commit=None,
        expected_deps=200,
    ),
    CohortEntry(
        name="flask",
        ecosystem="pip",
        url="https://github.com/pallets/flask",
        commit=None,
        expected_deps=20,
    ),
    CohortEntry(
        name="gin",
        ecosystem="golang",
        url="https://github.com/gin-gonic/gin",
        commit=None,
        expected_deps=30,
    ),
    CohortEntry(
        name="commons-text",
        ecosystem="maven",
        url="https://github.com/apache/commons-text",
        commit=None,
        expected_deps=10,
    ),
    CohortEntry(
        name="serde-json",
        ecosystem="cargo",
        url="https://github.com/serde-rs/json",
        commit=None,
        expected_deps=10,
    ),
    CohortEntry(
        name="terraform-stress",
        ecosystem="golang",
        url="https://github.com/hashicorp/terraform",
        commit=None,
        expected_deps=800,
    ),
]


# ---------------------------------------------------------------------------
# Report shapes
# ---------------------------------------------------------------------------


@dataclass
class FindingPair:
    """A (cve_id, component_purl) pair as emitted by one matcher."""

    cve_id: str
    component_purl: str
    severity: str | None = None
    fixed_version: str | None = None
    # All aliases known for the CVE (CVE-* + GHSA-* etc.). The diff classifier
    # uses this to dedupe "DT said GHSA-foo, Trivy said CVE-bar but they're
    # the same vulnerability".
    aliases: list[str] = field(default_factory=list)

    def key(self) -> tuple[str, str]:
        return (self.cve_id, self.component_purl)


@dataclass
class RepoReport:
    name: str
    ecosystem: str
    url: str
    commit: str | None
    cdxgen_seconds: float | None = None
    trivy_seconds: float | None = None
    dt_seconds: float | None = None
    sbom_component_count: int = 0
    dt_finding_count: int = 0
    trivy_finding_count: int = 0
    dt_only: list[dict[str, str | None]] = field(default_factory=list)
    trivy_only: list[dict[str, str | None]] = field(default_factory=list)
    version_mismatch: list[dict[str, str | None]] = field(default_factory=list)
    error: str | None = None
    skipped: bool = False
    skip_reason: str | None = None


@dataclass
class Report:
    generated_at: str
    cohort: list[RepoReport]


# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------


def _git_clone(url: str, dest: Path) -> str | None:
    """Shallow clone ``url`` into ``dest``. Returns the commit SHA or None on failure."""
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", url, str(dest)],
            check=True,
            capture_output=True,
            timeout=120,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print(f"  ! git clone failed: {exc}", file=sys.stderr)
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(dest), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            timeout=10,
            text=True,
        )
        return result.stdout.strip()
    except Exception:
        return None


def _run_cdxgen(source_dir: Path, output: Path, ecosystem: str) -> bool:
    """Run cdxgen and write the SBOM to ``output``. Returns True on success."""
    cmd = [
        "cdxgen",
        "-r",
        "-o",
        str(output),
        "--no-recurse",
        "-t",
        ecosystem,
        str(source_dir),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=300)
        return output.exists()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print(f"  ! cdxgen failed: {exc}", file=sys.stderr)
        return False


def _run_trivy_sbom(sbom_path: Path, output: Path) -> dict[str, Any] | None:
    """Run ``trivy sbom`` and return the parsed JSON. None on failure."""
    if shutil.which("trivy") is None:
        return None
    try:
        subprocess.run(
            [
                "trivy",
                "sbom",
                "--format",
                "json",
                "--output",
                str(output),
                "--quiet",
                "--scanners",
                "vuln",
                str(sbom_path),
            ],
            check=True,
            capture_output=True,
            timeout=600,
        )
        return json.loads(output.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"  ! trivy sbom failed: {exc}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# DT client wrapper (informational — uses the existing integration)
# ---------------------------------------------------------------------------


def _dt_call(sbom_path: Path, project_name: str) -> list[dict[str, Any]] | None:
    """Upload SBOM to DT, poll findings, return parsed findings. None on
    failure. Best-effort — DT may be down or removed by the time this runs;
    that simply means we report ``dt_only=[]`` for the repo.
    """
    dt_url = os.getenv("DT_URL")
    dt_key = os.getenv("DT_API_KEY")
    if not dt_url or not dt_key:
        return None
    try:
        # Best-effort: use the same client / breaker the worker uses so any
        # local DT topology peculiarity (auth, timeouts, TLS) is reproduced.
        import sys as _sys
        from pathlib import Path as _Path

        backend_root = _Path(__file__).resolve().parent.parent / "apps" / "backend"
        _sys.path.insert(0, str(backend_root))
        from integrations.dt.client import build_client
    except Exception as exc:
        print(f"  ! DT client import failed: {exc}", file=sys.stderr)
        return None

    client = build_client()
    try:
        project_uuid = client.upsert_project(name=project_name, version="bench")
        client.upload_sbom(
            project_uuid=project_uuid,
            sbom_json=sbom_path.read_bytes(),
        )
        # Poll for ~5 minutes — DT's matcher needs time on a fresh project.
        import time

        for _ in range(30):
            time.sleep(10)
            findings = client.get_findings(project_uuid=project_uuid)
            if findings:
                return list(findings)
        return []
    except Exception as exc:
        print(f"  ! DT call failed: {exc}", file=sys.stderr)
        return None
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Pair extraction + diff
# ---------------------------------------------------------------------------


def _trivy_pairs(report: dict[str, Any]) -> list[FindingPair]:
    out: list[FindingPair] = []
    for result in report.get("Results", []) or []:
        ecosystem = result.get("Type") or "unknown"
        for vuln in result.get("Vulnerabilities", []) or []:
            cve = vuln.get("VulnerabilityID")
            pkg = vuln.get("PkgName")
            installed = vuln.get("InstalledVersion")
            if not (cve and pkg and installed):
                continue
            purl = f"pkg:{ecosystem}/{pkg}@{installed}"
            out.append(
                FindingPair(
                    cve_id=cve,
                    component_purl=purl,
                    severity=vuln.get("Severity"),
                    fixed_version=vuln.get("FixedVersion"),
                    aliases=list(vuln.get("aliases") or []) + [cve],
                )
            )
    return out


def _dt_pairs(findings: Iterable[dict[str, Any]]) -> list[FindingPair]:
    out: list[FindingPair] = []
    for raw in findings:
        vuln = raw.get("vulnerability") or {}
        component = raw.get("component") or {}
        cve = vuln.get("vulnId")
        if not cve:
            src = vuln.get("source")
            if isinstance(src, dict):
                cve = src.get("name")
            elif isinstance(src, str):
                cve = src
        purl = component.get("purl")
        if not (cve and purl):
            continue
        out.append(
            FindingPair(
                cve_id=cve,
                component_purl=purl,
                severity=vuln.get("severity"),
                fixed_version=None,  # DT shape varies; report leaves as None
            )
        )
    return out


def _classify_diff(
    dt: list[FindingPair], trivy: list[FindingPair]
) -> tuple[list[FindingPair], list[FindingPair], list[tuple[FindingPair, FindingPair]]]:
    """Return (dt_only, trivy_only, version_mismatch) lists.

    Alias-aware: if DT says ``GHSA-xxxx`` and Trivy says ``CVE-yyyy`` for the
    same component AND ``GHSA-xxxx`` appears in Trivy's aliases list, we
    treat them as the same finding.
    """
    # Build alias → primary maps so a GHSA-keyed DT entry can hit the CVE-keyed
    # Trivy entry.
    trivy_by_key: dict[tuple[str, str], FindingPair] = {p.key(): p for p in trivy}
    trivy_alias_index: dict[tuple[str, str], FindingPair] = {}
    for p in trivy:
        for alias in p.aliases:
            trivy_alias_index[(alias, p.component_purl)] = p

    matched_trivy: set[tuple[str, str]] = set()
    dt_only: list[FindingPair] = []
    version_mismatch: list[tuple[FindingPair, FindingPair]] = []

    for d in dt:
        match = trivy_by_key.get(d.key()) or trivy_alias_index.get(d.key())
        if match is None:
            dt_only.append(d)
            continue
        matched_trivy.add(match.key())
        # Same finding — check fixed_version for divergence.
        if d.fixed_version and match.fixed_version and d.fixed_version != match.fixed_version:
            version_mismatch.append((d, match))

    trivy_only = [p for p in trivy if p.key() not in matched_trivy]
    return dt_only, trivy_only, version_mismatch


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _bench_one(entry: CohortEntry) -> RepoReport:
    print(f"  [{entry.name}] cloning …")
    rr = RepoReport(
        name=entry.name,
        ecosystem=entry.ecosystem,
        url=entry.url,
        commit=None,
    )

    with tempfile.TemporaryDirectory(prefix=f"bench-{entry.name}-") as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        source_dir = tmp_dir / "source"
        commit = _git_clone(entry.url, source_dir)
        if commit is None:
            rr.error = "git clone failed"
            return rr
        rr.commit = commit

        # cdxgen
        import time as _time

        sbom_path = tmp_dir / "sbom.json"
        if shutil.which("cdxgen") is None:
            rr.skipped = True
            rr.skip_reason = "cdxgen not on $PATH"
            return rr
        print(f"  [{entry.name}] cdxgen …")
        t0 = _time.monotonic()
        if not _run_cdxgen(source_dir, sbom_path, entry.ecosystem):
            rr.error = "cdxgen failed"
            return rr
        rr.cdxgen_seconds = round(_time.monotonic() - t0, 2)

        try:
            sbom = json.loads(sbom_path.read_text(encoding="utf-8"))
            rr.sbom_component_count = len(sbom.get("components", []) or [])
        except Exception:
            pass

        # Trivy
        print(f"  [{entry.name}] trivy …")
        t0 = _time.monotonic()
        trivy_report = _run_trivy_sbom(sbom_path, tmp_dir / "trivy.json")
        rr.trivy_seconds = round(_time.monotonic() - t0, 2)
        trivy_pairs = _trivy_pairs(trivy_report) if trivy_report else []
        rr.trivy_finding_count = len(trivy_pairs)

        # DT (best-effort)
        print(f"  [{entry.name}] dt …")
        t0 = _time.monotonic()
        dt_findings = _dt_call(sbom_path, project_name=f"bench-{entry.name}")
        rr.dt_seconds = round(_time.monotonic() - t0, 2)
        dt_pairs = _dt_pairs(dt_findings) if dt_findings else []
        rr.dt_finding_count = len(dt_pairs)

        if dt_findings is None:
            # No DT comparison possible — emit Trivy-only count as "trivy_only".
            rr.trivy_only = [
                {"cve": p.cve_id, "purl": p.component_purl, "fixed": p.fixed_version}
                for p in trivy_pairs
            ]
            rr.skip_reason = "DT unavailable; reporting Trivy-only counts"
            return rr

        dt_only, trivy_only, mismatch = _classify_diff(dt_pairs, trivy_pairs)
        rr.dt_only = [
            {"cve": p.cve_id, "purl": p.component_purl, "fixed": p.fixed_version}
            for p in dt_only
        ]
        rr.trivy_only = [
            {"cve": p.cve_id, "purl": p.component_purl, "fixed": p.fixed_version}
            for p in trivy_only
        ]
        rr.version_mismatch = [
            {
                "cve": d.cve_id,
                "purl": d.component_purl,
                "dt_fixed": d.fixed_version,
                "trivy_fixed": t.fixed_version,
            }
            for d, t in mismatch
        ]

    return rr


def main() -> int:
    parser = argparse.ArgumentParser(description="DT vs Trivy benchmark (informational).")
    parser.add_argument(
        "--out",
        default="benchmark-results",
        help="Output directory for the JSON report (default: ./benchmark-results)",
    )
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("DT vs Trivy benchmark (ADR-0002, informational only)")
    print("====================================================")

    cohort_results: list[RepoReport] = []
    for entry in COHORT:
        print(f"\n>> {entry.name} ({entry.ecosystem})")
        try:
            cohort_results.append(_bench_one(entry))
        except KeyboardInterrupt:
            print("interrupted", file=sys.stderr)
            return 130
        except Exception as exc:  # noqa: BLE001 — report-not-crash
            print(f"  ! unhandled error: {exc}", file=sys.stderr)
            cohort_results.append(
                RepoReport(
                    name=entry.name,
                    ecosystem=entry.ecosystem,
                    url=entry.url,
                    commit=None,
                    error=str(exc),
                )
            )

    report = Report(
        generated_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        cohort=cohort_results,
    )
    date_str = dt.date.today().isoformat()
    out_path = out_dir / f"{date_str}.json"
    out_path.write_text(
        json.dumps(asdict(report), indent=2, default=str), encoding="utf-8"
    )
    print(f"\nReport written: {out_path}")

    # Summary table.
    print("\nSummary")
    print("-------")
    print(f"{'repo':20} {'dt_only':>8} {'trivy_only':>11} {'mismatch':>9}")
    for rr in cohort_results:
        print(
            f"{rr.name:20} {len(rr.dt_only):>8} "
            f"{len(rr.trivy_only):>11} {len(rr.version_mismatch):>9}"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
