---
id: analysis-types
title: Analysis types
description: The kinds of analysis TRUSCA runs — source SBOM scan, container image scan, build gate, reachability — what each consumes, the tool behind it, and what it produces.
sidebar_label: Analysis types
sidebar_position: 6
---

# Analysis types

TRUSCA runs several distinct **kinds** of analysis over your code and its dependencies. Each kind consumes a different input, runs a different tool, and produces a different result — a set of findings, a quality score, or a pass/fail build verdict. This page is the entry-point matrix for deciding **what to run**: pick the analysis that answers your question, then follow the cross-link to the page that documents it in depth.

:::note Audience
New adopters and platform owners choosing which analysis to run, and reviewers mapping TRUSCA's capabilities to an internal checklist. Familiarity with SBOMs (Software Bill of Materials — the dependency inventory of a build), CVEs (Common Vulnerabilities and Exposures), and CI build gates helps. For term definitions see the [glossary](./glossary.md).
:::

:::note This is a matrix of pipelines, not data signals
This page lists the **analysis pipelines** — source scan, container scan, build gate, reachability. The [Data sources](./data-sources.md#analysis-types) page has a similarly named `## Analysis types` section, but that one is a matrix of the per-finding **data signals** the Trivy DB exposes (NVD · OSV · GHSA · EPSS · KEV, plus CVSS, CWE, fixed version, and so on). Read the two together: this page is *which analysis runs*; that page is *what data each vulnerability finding carries*.
:::

## The matrix

| Analysis | Consumes (input) | Tool | Produces | When to use | Details |
|---|---|---|---|---|---|
| **Source SBOM scan** | A Git repository (or an uploaded source archive) | `cdxgen` → scancode → `trivy sbom` | A CycloneDX SBOM, declared + detected licenses with legal-tier classification, and matched CVEs | The default. You want the full component inventory, licenses, and vulnerabilities for a project's dependency tree. | [Scans → Scan kinds](../user-guide/scans.md#scan-kinds), [SBOM](../user-guide/sbom.md) |
| **Container image scan** | A built container image reference (`name:tag`) | Trivy | OS-package CVEs and a base-image OS end-of-life (EOSL) verdict | You ship a container and want to know about vulnerabilities in the OS layer, not just your application dependencies. | [Scans → Scan a container image](../user-guide/scans.md#scan-a-container-image), [Container OS end-of-life](../user-guide/scans.md#container-os-eol) |
| **Build gate** | The findings and licenses from a completed scan | Portal gate evaluator | A pass/fail build verdict (CI exit code `0` or `1`) | You want a build to fail automatically on a forbidden license or a vulnerability over threshold — the CI enforcement point. | [Approvals](../user-guide/approvals.md), [License policies](./license-policies.md), [GitHub Actions](../ci-integration/github-actions.md) |
| **Reachability analysis** | Preserved Go source of a scanned module | `govulncheck` (Go) | A per-finding reachable / not-reachable / not-analysed signal (Go findings only) | You want to prioritise findings whose vulnerable code is actually called. **Go only today — see the status note below.** | [Comparison → reachability](../comparison.md) |

All four ship and run today. Reachability ships **for Go** via `govulncheck` — read the note below for its scope: it covers Go modules only, and findings in every other ecosystem are not yet analysed.

## Source SBOM scan {#source-detail}

A source scan is the default analysis. `cdxgen` (a CycloneDX SBOM generator covering 30+ ecosystems) walks the repository and emits an SBOM of the dependency tree with **declared** licenses read from each package's metadata. scancode then reads your own first-party source for **detected** licenses (best-effort). Finally `trivy sbom` matches the SBOM against the local Trivy DB to produce CVE findings, and the built-in classifier assigns each license a legal tier (`permissive` / `conditional` / `forbidden` / `unknown`).

The result feeds every project tab — Components, Licenses, Vulnerabilities, SBOM. See [Architecture → Scan pipeline](./architecture.md#scan-pipeline) for the stage-by-stage flow and [Scans](../user-guide/scans.md) for how to trigger one.

An **uploaded SBOM** (an SBOM your own build already produced) is a variant of this kind: TRUSCA scores its conformance, persists its components, and runs the same `trivy sbom` matching, without cloning or building your source. See [SBOM upload](../user-guide/scans.md#received-sboms-uploaded).

## Container image scan {#container-detail}

A container scan targets a **built image** rather than source. Trivy inspects the image's OS packages (Alpine `apk`, Debian `deb`, RHEL `rpm`, and so on) for known CVEs — complementary to a source scan, which covers your application's dependency tree. It also reports whether the image's base OS release is past its **end-of-service-life**: a release that no longer receives upstream security fixes will never be patched for CVEs disclosed after it retired, so the recommendation is to rebuild on a supported release.

See [Scan a container image](../user-guide/scans.md#scan-a-container-image) and [Base-image OS end-of-life](../user-guide/scans.md#container-os-eol).

## Build gate {#gate-detail}

The build gate is not a scanner — it **evaluates** the output of a completed scan against your rules to reach a build verdict. It counts components whose license resolves to `forbidden` and vulnerabilities over the configured thresholds, then returns a pass/fail result. In CI, a failing gate exits with code `1` to block the build. Thresholds and posture are set through `GATE_*` environment variables (see [Environment variables](./env-variables.md)) and, per team or organization, through a [license policy](./license-policies.md) that re-classifies licenses dynamically before counting.

See [Approvals](../user-guide/approvals.md) for the human workflow around conditional licenses and [GitHub Actions](../ci-integration/github-actions.md) for the CI wiring.

## Reachability analysis {#reachability-detail}

:::note Ships for Go; other ecosystems not yet analysed
Reachability ships today **for Go**. After every successful source scan, the worker runs `govulncheck` as a best-effort follow-up (`scan_reachability.py`, dispatched from `scan_source`; `govulncheck` is built into the worker image). It is on by default and can be turned off with `REACHABILITY_ENABLED=false` to shed worker load. It never fails the originating scan — if the source is not preserved, the project is not a Go module, or `govulncheck` is missing or times out, findings simply stay "not analysed".

For each **Go** finding (a `pkg:golang/` component whose CVE / GHSA / GO id `govulncheck` reported), the analyser stamps a verdict: `reachable = true` (the vulnerable symbol is reachable on the call graph), `reachable = false` (the analyser ran but the symbol is not reachable), or `reachable = null` (not analysed). The signal is surfaced by the reachability badge, the `?reachable=true|false|unknown` filter, and the `sort=reachable` ranking, and it can inform the policy/build gate.

**Findings in other ecosystems (Java, JS/TS, Python, and so on) are not yet analysed** — they stay `reachable = null` and are shown as "potentially affected". Multi-language reachability is the current commercial gap: Black Duck and Snyk run proprietary multi-language reachability, whereas TRUSCA ships Go-only reachability via `govulncheck`.
:::

Track the multi-language roadmap on the [comparison page](../comparison.md) and the [roadmap](https://github.com/trustedoss/trusca/blob/main/ROADMAP.md).

## Verify it worked

<!-- docs-uat: id=analysis-types-pipelines-match-scans kind=manual tier=manual -->
1. The three shipped analysis kinds on this page (source, container, build gate) match the scan kinds and gate documented in [Scans → Scan kinds](../user-guide/scans.md#scan-kinds) and [License policies → Dynamic gate evaluation](./license-policies.md#dynamic-gate-evaluation) — no pipeline appears here that is not documented there.

<!-- docs-uat: id=analysis-types-reachability-go kind=manual tier=manual -->
2. The reachability row and its status note describe a **Go-only, best-effort** signal that ships today: a Go finding can carry a `reachable = true / false / null` verdict from `govulncheck`, while non-Go findings stay "not analysed". This is consistent with [Comparison](../comparison.md) ("Go only", reachability prioritization ships for Go) and [Data sources](./data-sources.md) (reachability is not a Trivy-DB signal but a separate `govulncheck` pipeline for Go).

## Troubleshooting

- **"Which analysis do I run for licenses and CVEs?"** A source scan — it produces both in one run. The build gate then turns those results into a build verdict.
- **"My container scan found no application-dependency CVEs."** Container scans cover OS packages only. Run a source scan for the application dependency tree; the two are complementary.
- **"The reachability badge is blank on every finding."** Expected on non-Go findings, and on Go findings when reachability could not run (source not preserved, `REACHABILITY_ENABLED=false`, or `govulncheck` missing or timed out): the finding stays "not analysed" and the compact list renders nothing for that state. Go findings that were analysed show a reachable / not-reachable badge. See the [status note](#reachability-detail).

## See also

- [Scans](../user-guide/scans.md) — trigger source and container scans, watch progress.
- [SBOM](../user-guide/sbom.md) — export and read the SBOM a source scan produces.
- [Architecture](./architecture.md) — services, scan pipeline stages, Trivy matching.
- [Data sources](./data-sources.md) — the per-finding data signals (NVD · OSV · GHSA · EPSS · KEV) behind each vulnerability.
- [License policies](./license-policies.md) — how the build gate classifies and gates licenses.
- [Comparison](../comparison.md) — reachability scope (Go today) and where other planned items stand.
