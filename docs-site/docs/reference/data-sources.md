---
id: data-sources
title: Vulnerability data sources
description: Per-source coverage matrix for the Trivy DB — NVD, OSV, GHSA, EPSS, KEV — refresh cadence, ecosystem coverage, and analysis types.
sidebar_label: Data sources
sidebar_position: 5
---

# Vulnerability data sources

TRUSCA correlates SBOMs against CVEs using the **Trivy DB**, a compiled bundle of five public vulnerability feeds. This page is the reference for **what** each source contributes, **when** it refreshes, and **which ecosystems** it covers.

:::note Audience
Security leads, auditors, and operators answering "where does this CVE come from?" or "why is this ecosystem missing?". For the operator-facing lifecycle (download, refresh, air-gapped mirror) see [Vulnerability data (Trivy DB)](../admin-guide/vulnerability-data.md).
:::

## Source matrix

| Source | Owner | Refresh upstream | What it contributes |
|---|---|---|---|
| **NVD** — National Vulnerability Database | NIST | ~6 hours | CVE IDs, CVSS v3 vectors, CPE matching, references. The CVE backbone. |
| **OSV** — Open Source Vulnerabilities | Google | Continuous | Per-ecosystem advisories with precise version ranges (`introduced` / `fixed` / `last_affected`). |
| **GHSA** — GitHub Security Advisories | GitHub | Continuous | Advisory metadata, fix versions, withdrawal status. Often the first to publish for npm / pip / Maven. |
| **EPSS** — Exploit Prediction Scoring System | FIRST | Daily | 30-day exploit probability `0.0–1.0` and percentile. Drives the optional EPSS gate. |
| **KEV** — Known Exploited Vulnerabilities | CISA | As published | Boolean flag: this CVE has confirmed in-the-wild exploitation. Highest-priority triage signal. |

All five live in the same Trivy DB bundle — the portal does **not** call any of these APIs at scan time. Per-scan matching reads from `/var/lib/trivy/db/` on the worker, which the [Trivy DB refresh task](../admin-guide/vulnerability-data.md) keeps current.

## Refresh cadence in the portal

| Layer | Cadence | Knob |
|---|---|---|
| Upstream Trivy DB rebuild | ~6 hours (Aqua publishes a new OCI tag) | — |
| Portal worker pulls the new DB | Weekly | `TRIVY_DB_REFRESH_HOURS` (default `168`) |
| Per-scan match against local DB | Per scan (no network) | — |
| Automatic re-match of existing SBOMs | After every successful DB refresh | Celery beat task `tasks.rematch.run_rematch` |

The **automatic re-match beat** is the killer feature DT could not deliver in our deployment: when a new CVE lands in the refreshed DB, the beat task walks every project's most-recent SBOM and writes new `vulnerability_findings` rows where they match. Users see fresh findings on the Vulnerabilities tab without re-triggering a scan.

For the user-facing view of re-detection (banner, notification triggers), see [Re-detection](../user-guide/vulnerabilities.md#re-detection).

## Ecosystem coverage

The Trivy DB matches components by their **package URL** (`purl`). Coverage is dense for the ecosystems below — each has a dedicated OSV stream plus GHSA contributions.

| Ecosystem | `purl` type | Primary feed(s) | Notes |
|---|---|---|---|
| npm (JavaScript / Node) | `pkg:npm/*` | OSV + GHSA + NVD | First-class — most CVEs land in GHSA first. |
| PyPI (Python) | `pkg:pypi/*` | OSV + GHSA + NVD | First-class. |
| Maven (Java / Kotlin) | `pkg:maven/*` | OSV + GHSA + NVD | First-class. Classifier-aware as of v0.10.0. |
| Go modules | `pkg:golang/*` | OSV + GHSA + NVD | First-class. Vulnerability DB at `vuln.go.dev`. |
| RubyGems | `pkg:gem/*` | OSV + GHSA | First-class. |
| crates.io (Rust) | `pkg:cargo/*` | OSV + GHSA | First-class. |
| Packagist (PHP) | `pkg:composer/*` | OSV + GHSA | First-class. |
| NuGet (.NET) | `pkg:nuget/*` | OSV + GHSA + NVD | First-class. |
| Hex (Elixir / Erlang) | `pkg:hex/*` | OSV | Solid. |
| Pub (Dart / Flutter) | `pkg:pub/*` | OSV | Solid. |
| Conan (C / C++) | `pkg:conan/*` | OSV | Sparser than the above — many C/C++ CVEs are OS-package CVEs, see below. |
| OS packages (Alpine, Debian, RHEL, …) | `pkg:apk/*`, `pkg:deb/*`, `pkg:rpm/*` | NVD + per-distro security advisories | Used by the **container scan** pipeline (`scan_container.py`). Source scans on a repo of C/C++ code do not produce these. |

If a component's `purl` does not match a feed entry, no finding is created — silently, by design. Two common reasons:

- **Non-canonical `purl`.** `cdxgen` is conservative: a malformed `package.json` may yield a `purl` that doesn't normalize. File an issue with the scan ID; we tighten the generator over time.
- **Ecosystem is not in OSV / GHSA yet.** Coverage grows monthly. The [OSV ecosystem list](https://ossf.github.io/osv-schema/) is the upstream source of truth.

## Analysis types

Compared to a Dependency-Track-class tool, the Trivy DB exposes the following per-finding signals. The portal surfaces all of them on the Vulnerabilities tab and through the API.

| Signal | Source | Where in the portal |
|---|---|---|
| **CVE ID** | NVD / OSV / GHSA | Row identifier, header chip. |
| **Severity** (`critical` / `high` / `medium` / `low`) | NVD CVSS v3 (preferred) → GHSA → OSV | Row badge, distribution card, severity filter. |
| **CVSS v3 vector** | NVD | Finding drawer → Summary. |
| **Description / title** | NVD / GHSA | Finding drawer → Summary. |
| **CWE** | NVD | Finding drawer → Summary. |
| **Fixed version** | GHSA → OSV (`fixed` range) | Finding drawer → Affected component → "Fixed in". |
| **Affected version ranges** | OSV (`introduced` / `last_affected` / `fixed`) | Used by the matcher; not surfaced directly. |
| **EPSS score / percentile** | EPSS | Finding drawer → Summary; sortable column; gate via `GATE_EPSS_THRESHOLD`. |
| **KEV (in the wild)** | KEV catalogue | Finding row badge; can drive the gate (post-GA roadmap). |
| **References** | NVD / GHSA / OSV (deduplicated by URL) | Finding drawer → References. |

The portal **does not** consume:

- Curated vulnerability research from commercial feeds (Black Duck KnowledgeBase, Snyk DB) — by design, we ship the open feeds only.
- Vendor-specific advisory feeds (Oracle CPU, Microsoft MSRC) beyond what flows into NVD. These can be added as additional Trivy data sources in a future release.
- Reachability analysis. The portal does not parse call graphs; every matched CVE is shown as "potentially affected".

## What this means for triage

The matrix above is what is **automatically** in front of every analyst. Two practices keep triage signal-to-noise high:

1. **Layer EPSS over CVSS.** A `medium` CVE with EPSS percentile `> 95` deserves a faster look than a `critical` with EPSS `< 5`. Use the column sort or the `GATE_EPSS_THRESHOLD` env to gate.
2. **Filter by KEV.** The Vulnerabilities tab carries a KEV filter — anything in the CISA catalogue has confirmed exploitation and should be patched ahead of severity-only ranking.

For the user-facing flow (drawer, VEX state machine, suppression), see [Vulnerabilities](../user-guide/vulnerabilities.md).

## See also

- [Vulnerability data (Trivy DB)](../admin-guide/vulnerability-data.md) — operator-facing lifecycle, air-gapped mirror, troubleshooting.
- [Vulnerabilities](../user-guide/vulnerabilities.md) — analyst-facing flow.
- [Glossary](./glossary.md#vulnerabilities) — CVE, CWE, NVD, EPSS, KEV definitions.
- [ADR-0001 — Dependency-Track removal](https://github.com/trustedoss/trusca/blob/main/docs/decisions/0001-replace-dt-with-trivy.md) — why Trivy is the single engine from v0.10.0.
