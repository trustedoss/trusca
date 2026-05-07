#!/usr/bin/env python3
"""
UAT v2 — license-coverage spot-check for the multi-ecosystem fetcher
====================================================================

Why this exists
---------------
chore PR #5 introduced the per-ecosystem license fetcher (Maven /
PyPI / crates.io / pkg.go.dev) to fix the UAT v1 finding that
``cdxgen`` was emitting 91 / 39 / 164 unknown-license components for
pilot-java-maven / pilot-python / pilot-rust. chore PR #7 then
dropped the publisher-controlled ``reference_url`` (security-reviewer
Medium #2). The full UAT v1 reproduction (``git clone`` + ``cdxgen``
+ full-pipeline scan) is 30+ minutes per pilot — this lightweight
script answers the same question the matrix asks (does the fetcher
actually fill in unknowns?) in ~3 minutes by hitting each
ecosystem's fetcher with 8-12 known-popular PURLs drawn from each
pilot's published dependency list.

How to run
----------
Inside the worker container::

    docker-compose -f docker-compose.dev.yml exec celery-worker \
        python /app/scripts/uat_license_coverage.py

The script prints a Markdown table and exits non-zero if any
ecosystem fails the threshold from the chore PR #7 prompt:

    java-maven  — unknown ratio ≤ 20%
    python      — unknown ratio ≤ 20%
    rust        — unknown ratio ≤ 20%
    go          — unknown ratio ≤ 30%
    java-gradle — covered by the Maven Central fetcher (same source)

A pure-network failure (rate-limit, registry outage) shows up as
"unknown" in the same way — the threshold is generous enough to
absorb a one-off flake on a single PURL.

Caveats
-------
- The fetchers are run *without* the DB cache layer to avoid hiding
  per-network behaviour behind a cached entry.
- We disable redirect following (matches production) and use the
  same per-host rate limit (1 req/sec for crates.io).
- This script does NOT replace the full UAT matrix run when verifying
  cdxgen-Gradle-8 component counts; that path needs an actual scan.
  See ``docs/sessions/2026-05-XX-uat-multi-ecosystem-matrix-v2.md``
  for the full-scan protocol.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make the FastAPI source tree importable when run from the repo root
# *or* from inside the worker container (where the app lives at /app).
_CANDIDATES = (
    Path(__file__).resolve().parent.parent / "apps" / "backend",
    Path("/app"),
)
for _candidate in _CANDIDATES:
    if (_candidate / "integrations").is_dir() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))
        break

from integrations.license_fetcher.crates import CratesLicenseFetcher  # noqa: E402
from integrations.license_fetcher.maven import MavenLicenseFetcher  # noqa: E402
from integrations.license_fetcher.pkggo import PkgGoLicenseFetcher  # noqa: E402
from integrations.license_fetcher.pypi import PyPILicenseFetcher  # noqa: E402

# ---------------------------------------------------------------------------
# Sample PURLs per ecosystem (top deps drawn from the chore PR #7 pilots)
# ---------------------------------------------------------------------------
# Each ecosystem gets ≥8 PURLs so the threshold (≤20%, ≤30%) means at
# least 2 PURLs may be "unknown" before the threshold trips. We pick
# *transitive* PURLs in addition to direct deps, since UAT v1 showed
# transitives were the unknown-license offenders.

PILOT_JAVA_MAVEN: list[str] = [
    # spring-petclinic top-level
    "pkg:maven/org.springframework.boot/spring-boot-starter-web@3.2.4",
    "pkg:maven/org.springframework.boot/spring-boot-starter-data-jpa@3.2.4",
    "pkg:maven/org.springframework.boot/spring-boot-starter-thymeleaf@3.2.4",
    # transitive
    "pkg:maven/com.fasterxml.jackson.core/jackson-databind@2.15.4",
    "pkg:maven/org.hibernate.orm/hibernate-core@6.4.4.Final",
    "pkg:maven/org.thymeleaf/thymeleaf@3.1.2.RELEASE",
    "pkg:maven/io.netty/netty-codec-http@4.1.108.Final",
    "pkg:maven/com.h2database/h2@2.2.224",
    "pkg:maven/org.aspectj/aspectjweaver@1.9.21",
    "pkg:maven/org.apache.tomcat.embed/tomcat-embed-core@10.1.19",
]

PILOT_PYTHON: list[str] = [
    # flask top-level
    "pkg:pypi/flask@3.0.3",
    "pkg:pypi/click@8.1.7",
    "pkg:pypi/itsdangerous@2.2.0",
    "pkg:pypi/jinja2@3.1.4",
    "pkg:pypi/werkzeug@3.0.3",
    "pkg:pypi/blinker@1.7.0",
    # popular transitives in flask-adjacent stacks
    "pkg:pypi/markupsafe@2.1.5",
    "pkg:pypi/requests@2.32.0",
    "pkg:pypi/urllib3@2.2.1",
    "pkg:pypi/certifi@2024.2.2",
]

PILOT_RUST: list[str] = [
    # clap workspace + popular transitives
    "pkg:cargo/clap@4.5.4",
    "pkg:cargo/clap_builder@4.5.2",
    "pkg:cargo/clap_derive@4.5.4",
    "pkg:cargo/serde@1.0.219",
    "pkg:cargo/serde_json@1.0.115",
    "pkg:cargo/tokio@1.37.0",
    "pkg:cargo/anyhow@1.0.82",
    "pkg:cargo/regex@1.10.4",
    "pkg:cargo/log@0.4.21",
    "pkg:cargo/once_cell@1.19.0",
]

PILOT_GO: list[str] = [
    # cobra + spf13 + popular transitives
    "pkg:golang/github.com/spf13/cobra@v1.8.0",
    "pkg:golang/github.com/spf13/pflag@v1.0.5",
    "pkg:golang/github.com/inconshreveable/mousetrap@v1.1.0",
    "pkg:golang/github.com/spf13/viper@v1.18.2",
    "pkg:golang/github.com/stretchr/testify@v1.9.0",
    "pkg:golang/github.com/davecgh/go-spew@v1.1.1",
    "pkg:golang/github.com/pmezard/go-difflib@v1.0.0",
    "pkg:golang/gopkg.in/yaml.v3@v3.0.1",
]


THRESHOLDS: dict[str, float] = {
    "java-maven": 0.20,
    "python": 0.20,
    "rust": 0.20,
    "go": 0.30,
}


def run(name: str, fetcher_factory, purls: list[str]) -> tuple[int, int, list[str]]:
    """Run one ecosystem; return (resolved, unknown, [unknown_purls])."""
    resolved = 0
    unknown_purls: list[str] = []
    for purl in purls:
        fetcher = fetcher_factory()
        try:
            result = fetcher.fetch(purl)
        finally:
            close = getattr(fetcher, "close", None)
            if callable(close):
                close()
        if result is not None and result.spdx_id:
            resolved += 1
            assert result.reference_url is None, (
                f"{name}/{purl} returned reference_url={result.reference_url!r}; "
                "chore PR #7 requires uniform None"
            )
        else:
            unknown_purls.append(purl)
    return resolved, len(unknown_purls), unknown_purls


def main() -> int:
    plan: list[tuple[str, object, list[str]]] = [
        ("java-maven", MavenLicenseFetcher, PILOT_JAVA_MAVEN),
        ("python", PyPILicenseFetcher, PILOT_PYTHON),
        ("rust", CratesLicenseFetcher, PILOT_RUST),
        ("go", PkgGoLicenseFetcher, PILOT_GO),
    ]
    print("\n# UAT v2 — license-coverage spot-check\n")
    print("| ecosystem | resolved | unknown | unknown ratio | threshold | pass |")
    print("|-----------|---------:|--------:|--------------:|----------:|:----:|")

    failures: list[str] = []
    detail: list[str] = []
    for name, factory, purls in plan:
        resolved, unknown, miss_list = run(name, factory, purls)
        total = resolved + unknown
        ratio = unknown / total if total else 0.0
        threshold = THRESHOLDS[name]
        ok = ratio <= threshold
        mark = "✅" if ok else "❌"
        print(
            f"| {name:<10} | {resolved:>8} | {unknown:>7} | "
            f"{ratio*100:>13.1f}% | {threshold*100:>8.0f}% | {mark} |"
        )
        if miss_list:
            detail.append(f"\n## Unknowns — {name}\n")
            for purl in miss_list:
                detail.append(f"- `{purl}`")
        if not ok:
            failures.append(f"{name}: {ratio*100:.1f}% > {threshold*100:.0f}%")

    if detail:
        print("\n".join(detail))

    print()
    if failures:
        print("FAIL —", "; ".join(failures))
        return 1
    print("PASS — every ecosystem inside threshold.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
