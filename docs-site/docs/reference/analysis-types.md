---
id: analysis-types
title: Analysis types
description: The kinds of analysis TRUSCA runs — source SBOM scan, container image scan, policy gate, reachability — what each consumes, the tool behind it, and what it produces.
sidebar_label: Analysis types
sidebar_position: 6
---

# Analysis types

TRUSCA runs several distinct **kinds** of analysis over your code and its dependencies. Each kind consumes a different input, runs a different tool, and produces a different result — a set of findings, a quality score, or a pass/fail build verdict. This page is the entry-point matrix for deciding **what to run**: pick the analysis that answers your question, then follow the cross-link to the page that documents it in depth.

:::note Audience
New adopters and platform owners choosing which analysis to run, and reviewers mapping TRUSCA's capabilities to an internal checklist. Familiarity with SBOMs (Software Bill of Materials — the dependency inventory of a build), CVEs (Common Vulnerabilities and Exposures), and CI build gates helps. For term definitions see the [glossary](./glossary.md).
:::

:::note This is a matrix of pipelines, not data signals
This page lists the **analysis pipelines** — source scan, container scan, policy gate, reachability. The [Data sources](./data-sources.md#analysis-types) page has a similarly named `## Analysis types` section, but that one is a matrix of the per-finding **data signals** the Trivy DB exposes (NVD · OSV · GHSA · EPSS · KEV, plus CVSS, CWE, fixed version, and so on). Read the two together: this page is *which analysis runs*; that page is *what data each vulnerability finding carries*.
:::

## The matrix

| Analysis | Consumes (input) | Tool | Produces | When to use | Details |
|---|---|---|---|---|---|
| **Source SBOM scan** | A Git repository (or an uploaded source archive) | `cdxgen` → scancode → `trivy sbom` | A CycloneDX SBOM, declared + detected licenses with legal-tier classification, and matched CVEs | The default. You want the full component inventory, licenses, and vulnerabilities for a project's dependency tree. | [Scans → Scan kinds](../user-guide/scans.md#scan-kinds), [SBOM](../user-guide/sbom.md) |
| **Container image scan** | A built container image reference (`name:tag`) | Trivy | OS-package CVEs and a base-image OS end-of-life (EOSL) verdict | You ship a container and want to know about vulnerabilities in the OS layer, not just your application dependencies. | [Scans → Scan a container image](../user-guide/scans.md#scan-a-container-image), [Container OS end-of-life](../user-guide/scans.md#container-os-eol) |
| **Policy gate** | The findings and licenses from a completed scan | Portal gate evaluator | A pass/fail build verdict (CI exit code `0` or `1`) | You want a build to fail automatically on a forbidden license or a vulnerability over threshold — the CI enforcement point. | [Approvals](../user-guide/approvals.md), [License policies](./license-policies.md), [GitHub Actions](../ci-integration/github-actions.md) |
| **Reachability analysis** | (Planned) analyser output for a finding's call graph | (Planned) `govulncheck` and peers | A per-finding reachability signal — reachable / not reachable / not analysed | You want to prioritise findings whose vulnerable code is actually called. **See the status note below before relying on this.** | [Comparison → reachability](../comparison.md) |

The first three are analysis pipelines that ship and run today. The fourth, reachability, is a planned capability with partial UI plumbing — read the note below so you do not overstate it in an evaluation.

## Source SBOM scan {#source-detail}

A source scan is the default analysis. `cdxgen` (a CycloneDX SBOM generator covering 30+ ecosystems) walks the repository and emits an SBOM of the dependency tree with **declared** licenses read from each package's metadata. scancode then reads your own first-party source for **detected** licenses (best-effort). Finally `trivy sbom` matches the SBOM against the local Trivy DB to produce CVE findings, and the built-in classifier assigns each license a legal tier (`permissive` / `conditional` / `forbidden` / `unknown`).

The result feeds every project tab — Components, Licenses, Vulnerabilities, SBOM. See [Architecture → Scan pipeline](./architecture.md#scan-pipeline) for the stage-by-stage flow and [Scans](../user-guide/scans.md) for how to trigger one.

An **uploaded SBOM** (an SBOM your own build already produced) is a variant of this kind: TRUSCA scores its conformance, persists its components, and runs the same `trivy sbom` matching, without cloning or building your source. See [Received SBOMs](../user-guide/scans.md#received-sboms-uploaded).

## Container image scan {#container-detail}

A container scan targets a **built image** rather than source. Trivy inspects the image's OS packages (Alpine `apk`, Debian `deb`, RHEL `rpm`, and so on) for known CVEs — complementary to a source scan, which covers your application's dependency tree. It also reports whether the image's base OS release is past its **end-of-service-life**: a release that no longer receives upstream security fixes will never be patched for CVEs disclosed after it retired, so the recommendation is to rebuild on a supported release.

See [Scan a container image](../user-guide/scans.md#scan-a-container-image) and [Base-image OS end-of-life](../user-guide/scans.md#container-os-eol).

## Policy gate {#gate-detail}

The policy gate is not a scanner — it **evaluates** the output of a completed scan against your rules to reach a build verdict. It counts components whose license resolves to `forbidden` and vulnerabilities over the configured thresholds, then returns a pass/fail result. In CI, a failing gate exits with code `1` to block the build. Thresholds and posture are set through `GATE_*` environment variables (see [Environment variables](./env-variables.md)) and, per team or organization, through a [license policy](./license-policies.md) that re-classifies licenses dynamically before counting.

See [Approvals](../user-guide/approvals.md) for the human workflow around conditional licenses and [GitHub Actions](../ci-integration/github-actions.md) for the CI wiring.

## Reachability analysis {#reachability-detail}

:::caution Planned — not a shipped analysis pipeline
Reachability is a **planned, best-effort** capability. The UI surfaces a reachability signal where source data provides it, but **no dedicated reachability scan pipeline ships today**. The backend and worker do not run `govulncheck` (or any call-graph analyser) as a first-class analysis type — there is UI plumbing (a reachability badge, a `?reachable=` filter, a `sort=reachable` ranking, and a `reachability_source` field) that displays a signal *when a finding carries one*, but in this release findings are shown in full rather than ranked by whether vulnerable code is reachable. Every matched CVE is presented as "potentially affected".

This matches the honest characterization elsewhere in the docs: [Comparison](../comparison.md) lists reachability as **Planned** with *no reachability prioritization*, and [Data sources](./data-sources.md) states the portal does **not** consume reachability analysis. Do not represent TRUSCA as running `govulncheck` as a first-class analysis type.
:::

When reachability lands, it will layer over the existing finding list as a prioritisation signal — the UI affordances (badge, filter, sort) are already in place to receive it. Track the status on the [comparison page](../comparison.md) and the [roadmap](https://github.com/trustedoss/trusca/blob/main/ROADMAP.md).

## Verify it worked

<!-- docs-uat: id=analysis-types-pipelines-match-scans kind=manual tier=manual -->
1. The three shipped analysis kinds on this page (source, container, policy gate) match the scan kinds and gate documented in [Scans → Scan kinds](../user-guide/scans.md#scan-kinds) and [License policies → Dynamic gate evaluation](./license-policies.md#dynamic-gate-evaluation) — no pipeline appears here that is not documented there.

<!-- docs-uat: id=analysis-types-reachability-planned kind=manual tier=manual -->
2. The reachability row and its status note describe a **planned, best-effort** signal, consistent with [Comparison](../comparison.md) ("Planned", "No reachability prioritization") and [Data sources](./data-sources.md) ("does not consume reachability analysis") — this page does not claim a shipped `govulncheck` pipeline.

## Troubleshooting

- **"Which analysis do I run for licenses and CVEs?"** A source scan — it produces both in one run. The policy gate then turns those results into a build verdict.
- **"My container scan found no application-dependency CVEs."** Container scans cover OS packages only. Run a source scan for the application dependency tree; the two are complementary.
- **"The reachability badge is blank on every finding."** Expected in this release: no reachability pipeline runs, so findings carry no reachability signal and the compact list renders nothing for the "not analysed" state. See the [status note](#reachability-detail).

## See also

- [Scans](../user-guide/scans.md) — trigger source and container scans, watch progress.
- [SBOM](../user-guide/sbom.md) — export and read the SBOM a source scan produces.
- [Architecture](./architecture.md) — services, scan pipeline stages, Trivy matching.
- [Data sources](./data-sources.md) — the per-finding data signals (NVD · OSV · GHSA · EPSS · KEV) behind each vulnerability.
- [License policies](./license-policies.md) — how the policy gate classifies and gates licenses.
- [Comparison](../comparison.md) — where reachability and other planned items stand.
