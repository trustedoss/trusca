---
id: comparison
title: How TrustedOSS Portal compares
sidebar_label: Comparison
description: An honest comparison of TrustedOSS Portal versus commercial SCA (Black Duck, Snyk), Dependency-Track, and SW360 — strengths and current limits.
---

# How TrustedOSS Portal compares

:::note Audience
Engineers, platform owners, and legal/compliance leads deciding whether
TrustedOSS Portal fits their organization. This page is deliberately honest:
it lists what the portal does well **and** what it does not do yet. For the
roadmap behind the "planned" rows, see [`ROADMAP.md`](https://github.com/trustedoss/trustedoss-portal/blob/main/ROADMAP.md).
:::

TrustedOSS Portal's core idea is to wrap several best-of-breed open-source
tools — cdxgen, scancode, and Trivy (single vulnerability engine from v2.4.0) —
in one self-hosted UI with teams, roles, approvals, and CI gating. The
comparisons below frame that idea against three common alternatives. They
describe shipped capabilities through the **v2.4** release (with in-progress
v2.x items marked); they are not benchmarks and they do not disparage other
projects, several of which TrustedOSS Portal builds on.

For term definitions (SCA, SBOM, VEX, EPSS, reachability), see the
[glossary](./reference/glossary.md).

## At a glance

| | TrustedOSS Portal | Commercial SCA (Black Duck / Snyk) | Dependency-Track | Eclipse SW360 |
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
| Auto remediation / PRs | In progress (v2.2) | Yes | No | No |
| EPSS prioritization | **Shipped (v2.1)** | Yes | Partial | No |
| VEX consumption | **Shipped (v2.1)** | Yes | Partial | No |
| Reachability analysis | Planned | Yes (some) | No | No |
| Signed SBOM / provenance | Planned | Partial | No | No |

## vs commercial SCA (Black Duck, Snyk)

**Choose TrustedOSS Portal when** you want to own your data and avoid per-seat
licensing, you are comfortable self-hosting, and a unified open-source portal
covering detection, licenses, SBOM, approvals, and CI gating meets your needs.

**Where commercial tools lead today:**

- **Curated vulnerability and license intelligence.** Commercial vendors
  maintain proprietary databases and dedicated research teams. TrustedOSS
  Portal relies on public feeds (NVD + OSV + GHSA + EPSS + KEV) delivered via
  the Trivy DB.
- **Automated remediation.** Snyk and others open fix pull requests
  automatically. In TrustedOSS Portal this is in progress (v2.2): per-finding
  `fixed_version` and dependency-graph depth are shipped, and suggested upgrades
  are being built. Today the portal detects and gates but does not yet open
  upgrade pull requests.
- **Prioritization signals.** First-class EPSS prioritization is shipped
  (v2.1) — column, sort, filter, and a policy-gate threshold. Reachability
  analysis is still planned, not shipped.

**Where TrustedOSS Portal is competitive:** self-hosting with no seat cost,
Apache-2.0 licensing, a single portal instead of several consoles, a built-in
component approval workflow, build-blocking CI gates, and a fully bilingual
(EN/KO) UI and documentation.

## vs Dependency-Track

Dependency-Track (DT) is excellent at what it does — a focused vulnerability
intelligence platform for SBOMs you supply. TrustedOSS Portal used DT as its
vulnerability engine through v2.3 and replaced it with Trivy at v2.4.0 (see
[ADR-0001](https://github.com/trustedoss/trustedoss-portal/blob/main/docs/decisions/0001-replace-dt-with-trivy.md)).
The question is what shape of platform fits your team.

**TrustedOSS Portal differs from running DT directly:**

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

**TrustedOSS Portal leads in:** an integrated scan pipeline (cdxgen / scancode /
Trivy) out of the box, container scanning, first-class CI build gates,
byte-stable CycloneDX **and** SPDX export, a modern single-page UI, and EN/KO
bilingual support. SW360 typically expects SBOMs/components to be supplied and
emphasizes clearing over scanning.

**Use SW360 when** deep, formal license clearing is your primary need and you
already have a process built around it.

## Current limitations (be aware before you adopt)

These are real and intentional gaps. Each is on the
[roadmap](https://github.com/trustedoss/trustedoss-portal/blob/main/ROADMAP.md):

- **Automated remediation is in progress.** The portal detects and gates, and
  surfaces per-finding `fixed_version` and dependency-graph depth (v2.2), but
  does not yet open upgrade pull requests — suggested upgrades are being built
  (v2.2).
- **Vulnerability data depends on the Trivy DB.** Signals are limited to
  what NVD + OSV + GHSA + EPSS + KEV expose, augmented by first-class EPSS
  prioritization (shipped v2.1), KEV in-the-wild badges, and imported VEX.
- **No reachability prioritization.** Findings are listed in full rather than
  ranked by whether vulnerable code is reachable (planned, v2.3, best-effort).
- **Static license policy.** Classification uses a fixed catalog; per-team /
  per-org editable policy is planned (v2.2).
- **No signed SBOMs / provenance.** SBOM signing and SLSA provenance are planned
  (v2.3).
- **No native Jenkins plugin.** GitHub Actions and GitLab CI are first-class;
  Jenkins is supported via a worked `Jenkinsfile` example.
- **No SSO / OIDC.** Password and OAuth (GitHub / Google, demo only) auth ship
  today; SSO / OIDC is backlog.

## See also

- [Introduction](./intro.md) — what the portal does and does not do
- [Glossary](./reference/glossary.md) — SCA, SBOM, VEX, EPSS, and more
- [Roadmap](https://github.com/trustedoss/trustedoss-portal/blob/main/ROADMAP.md) — where the "planned" items land
