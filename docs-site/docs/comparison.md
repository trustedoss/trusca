---
id: comparison
title: How TRUSCA compares
sidebar_label: Comparison
description: An honest comparison of TRUSCA versus commercial SCA (Black Duck, Snyk), Dependency-Track, and SW360 — strengths and current limits.
---

# How TRUSCA compares

:::note Audience
Engineers, platform owners, and legal/compliance leads deciding whether
TRUSCA fits their organization. This page is deliberately honest:
it lists what the portal does well **and** what it does not do yet. For the
roadmap behind the "planned" rows, see [`ROADMAP.md`](https://github.com/trustedoss/trusca/blob/main/ROADMAP.md).
:::

TRUSCA's core idea is to wrap several best-of-breed open-source
tools — cdxgen, scancode, and Trivy (the single vulnerability engine) — in
one self-hosted UI with teams, roles, approvals, and CI gating. The
comparisons below frame that idea against three common alternatives. They
describe shipped capabilities in the current release (with planned items
marked); they are not benchmarks and they do not disparage other projects,
several of which TRUSCA builds on.

For term definitions (SCA, SBOM, VEX, EPSS, reachability), see the
[glossary](./reference/glossary.md).

## At a glance

| | TRUSCA | Commercial SCA (Black Duck / Snyk) | Dependency-Track | Eclipse SW360 |
|---|---|---|---|---|
| License | Apache-2.0 | Proprietary | Apache-2.0 | EPL-2.0 |
| Hosting | Self-hosted (Docker / Helm) | SaaS or self-managed | Self-hosted | Self-hosted |
| Pricing model | Free, no per-seat | Per-seat / per-project | Free | Free |
| Component detection | cdxgen (30+ ecosystems) | Broad, proprietary | Consumes SBOMs | Consumes SBOMs |
| License detection | declared + detected (scancode) | Deep, curated | Limited | Strong (license clearing) |
| Vulnerability data | Trivy DB (NVD + OSV + GHSA + EPSS + KEV) | Curated proprietary feeds | NVD / OSV / GHSA | via add-ons |
| Container scanning | Trivy (OS packages) | Yes | No | No |
| SBOM export | CycloneDX + SPDX, byte-stable | Yes | CycloneDX | SPDX / CycloneDX |
| RBAC | 3 roles (super / team / developer) | Rich | Teams + permissions | LDAP roles |
| Approval workflow | Built in | Yes | No | Clearing workflow |
| CI build gate | exit 1 on Critical CVE / forbidden license | Yes | Via API | No |
| Bilingual UI (EN/KO) | Yes | Partial | No | No |
| Auto remediation / PRs | Planned | Yes | No | No |
| EPSS prioritization | **Yes** | Yes | Partial | No |
| VEX consumption | **Yes** | Yes | Partial | No |
| Reachability analysis | Go only (govulncheck) | Yes (some) | No | No |
| Signed SBOM / provenance | Planned | Partial | No | No |

## vs commercial SCA (Black Duck, Snyk)

**Choose TRUSCA when** you want to own your data and avoid per-seat
licensing, you are comfortable self-hosting, and a unified open-source portal
covering detection, licenses, SBOM, approvals, and CI gating meets your needs.

**Where commercial tools lead today:**

- **Curated vulnerability and license intelligence.** Commercial vendors
  maintain proprietary databases and dedicated research teams. TRUSCA
  relies on public feeds (NVD + OSV + GHSA + EPSS + KEV) delivered via
  the Trivy DB.
- **Automated remediation.** Snyk and others open fix pull requests
  automatically. TRUSCA surfaces per-finding `fixed_version` and
  dependency-graph depth but does not yet open upgrade pull requests —
  suggested upgrades are planned.
- **Prioritization signals.** EPSS prioritization is first-class — column,
  sort, filter, and a policy-gate threshold. Reachability analysis ships
  for Go (via `govulncheck`); reachability for other ecosystems is planned.

**Where TRUSCA is competitive:** self-hosting with no seat cost,
Apache-2.0 licensing, a single portal instead of several consoles, a built-in
component approval workflow, build-blocking CI gates, and a fully bilingual
(EN/KO) UI and documentation.

## vs Dependency-Track

Dependency-Track (DT) is excellent at what it does — a focused vulnerability
intelligence platform for SBOMs you supply. TRUSCA uses Trivy as
its single embedded vulnerability engine (see
[ADR-0001](https://github.com/trustedoss/trusca/blob/main/docs/decisions/0001-replace-dt-with-trivy.md)
for the decision). The question is what shape of platform fits your team.

**TRUSCA differs from running DT directly:**

- **Scan orchestration.** It runs cdxgen, scancode, and Trivy automatically and
  feeds results in, rather than expecting you to produce and upload SBOMs.
- **License compliance.** Allowed / conditional / forbidden classification,
  obligations tracking, and automatic `NOTICE` generation — outside DT's scope.
- **Workflow and governance.** Component approvals, a build-blocking gate, an
  append-only audit log, and a 3-role RBAC model.
- **Operational footprint.** ~500 MB Trivy DB vs DT's 4 GB JVM + H2 — fits on
  a 4 GB host.
- **Bilingual UI.** English and Korean.
- **Triage signals.** EPSS is a first-class signal (column, sort, filter,
  policy-gate threshold), KEV badges flag in-the-wild exploitation, and external
  VEX (OpenVEX / CycloneDX VEX) can be imported to auto-suppress findings.

**Use Dependency-Track directly when** you want DT's native features (its UI,
its policy engine, its existing integrations), already have DT operationalised,
or need a per-organisation per-component-graph governance model only DT
offers.

## vs Eclipse SW360

SW360 is a mature open-source platform focused on **license clearing** and
component cataloging.

**SW360 leads in:** depth of license clearing workflows, a large component
clearing catalog, and established enterprise integration patterns.

**TRUSCA leads in:** an integrated scan pipeline (cdxgen / scancode /
Trivy) out of the box, container scanning, first-class CI build gates,
byte-stable CycloneDX **and** SPDX export, a modern single-page UI, and EN/KO
bilingual support. SW360 typically expects SBOMs/components to be supplied and
emphasizes clearing over scanning.

**Use SW360 when** deep, formal license clearing is your primary need and you
already have a process built around it.

## Current limitations (be aware before you adopt)

These are real and intentional gaps. Each is on the
[roadmap](https://github.com/trustedoss/trusca/blob/main/ROADMAP.md):

- **Automated remediation pull requests are planned.** The portal detects
  and gates, and surfaces per-finding `fixed_version` and dependency-graph
  depth, but does not yet open upgrade pull requests — suggested upgrades
  are planned.
- **Vulnerability data depends on the Trivy DB.** Signals are limited to
  what NVD + OSV + GHSA + EPSS + KEV expose, augmented by first-class EPSS
  prioritization, KEV in-the-wild badges, and imported VEX.
- **Multi-language reachability is limited.** Reachability prioritization
  ships for **Go** (via `govulncheck`) — a reachable / not-reachable badge,
  the `?reachable=` filter, `sort=reachable`, and a gate signal. Findings in
  other ecosystems (Java, JS/TS, Python, and so on) are not yet analysed and
  are shown in full. Proprietary multi-language reachability is where the
  commercial tools still lead.
- **Static license policy.** Classification uses a fixed catalog; per-team /
  per-org editable policy is planned.
- **No signed SBOMs / provenance.** SBOM signing and SLSA provenance are
  planned.
- **No native Jenkins plugin.** GitHub Actions and GitLab CI are first-class;
  Jenkins is supported via a worked `Jenkinsfile` example.
- **No SSO / OIDC.** Password and OAuth (GitHub / Google, demo only) auth ship
  today; SSO / OIDC is backlog.

## See also

- [Introduction](./intro.md) — what the portal does and does not do
- [Glossary](./reference/glossary.md) — SCA, SBOM, VEX, EPSS, and more
- [Roadmap](https://github.com/trustedoss/trusca/blob/main/ROADMAP.md) — where the "planned" items land
