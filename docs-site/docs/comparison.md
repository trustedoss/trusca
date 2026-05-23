---
id: comparison
title: How TrustedOSS Portal compares
sidebar_label: Comparison
description: An honest comparison of TrustedOSS Portal versus commercial SCA (Black Duck, Snyk), Dependency-Track alone, and SW360 — strengths and current limits.
---

# How TrustedOSS Portal compares

:::note Audience
Engineers, platform owners, and legal/compliance leads deciding whether
TrustedOSS Portal fits their organization. This page is deliberately honest:
it lists what the portal does well **and** what it does not do yet. For the
roadmap behind the "planned" rows, see [`ROADMAP.md`](https://github.com/trustedoss/trustedoss-portal/blob/main/ROADMAP.md).
:::

TrustedOSS Portal's core idea is to wrap several best-of-breed open-source
tools — cdxgen, scancode, Trivy, and Dependency-Track — in one self-hosted UI
with teams, roles, approvals, and CI gating. The comparisons below frame that
idea against three common alternatives. They describe capabilities as of the
**v2.0.0** release; they are not benchmarks and they do not disparage other
projects, several of which TrustedOSS Portal builds on.

For term definitions (SCA, SBOM, VEX, EPSS, reachability), see the
[glossary](./reference/glossary.md).

## At a glance

| | TrustedOSS Portal | Commercial SCA (Black Duck / Snyk) | Dependency-Track alone | Eclipse SW360 |
|---|---|---|---|---|
| License | Apache-2.0 | Proprietary | Apache-2.0 | EPL-2.0 |
| Hosting | Self-hosted (Docker / Helm) | SaaS or self-managed | Self-hosted | Self-hosted |
| Pricing model | Free, no per-seat | Per-seat / per-project | Free | Free |
| Component detection | cdxgen (30+ ecosystems) | Broad, proprietary | Consumes SBOMs | Consumes SBOMs |
| License detection | declared + detected (scancode) | Deep, curated | Limited | Strong (license clearing) |
| Vulnerability data | via Dependency-Track | Curated proprietary feeds | NVD / OSV / GHSA | via add-ons |
| Container scanning | Trivy (OS packages) | Yes | No | No |
| SBOM export | CycloneDX + SPDX, byte-stable | Yes | CycloneDX | SPDX / CycloneDX |
| RBAC | 3 roles (super / team / developer) | Rich | Teams + permissions | LDAP roles |
| Approval workflow | Built in | Yes | No | Clearing workflow |
| CI build gate | exit 1 on Critical CVE / forbidden license | Yes | Via API | No |
| Bilingual UI (EN/KO) | Yes | Partial | No | No |
| Auto remediation / PRs | Planned | Yes | No | No |
| EPSS prioritization | Planned | Yes | Partial | No |
| VEX consumption | Planned (export today) | Yes | Partial | No |
| Reachability analysis | Planned | Yes (some) | No | No |
| Signed SBOM / provenance | Planned | Partial | No | No |

## vs commercial SCA (Black Duck, Snyk)

**Choose TrustedOSS Portal when** you want to own your data and avoid per-seat
licensing, you are comfortable self-hosting, and a unified open-source portal
covering detection, licenses, SBOM, approvals, and CI gating meets your needs.

**Where commercial tools lead today:**

- **Curated vulnerability and license intelligence.** Commercial vendors
  maintain proprietary databases and dedicated research teams. TrustedOSS
  Portal relies on public feeds (NVD, OSV, GitHub Advisory) through
  Dependency-Track.
- **Automated remediation.** Snyk and others open fix pull requests
  automatically. In TrustedOSS Portal this is on the roadmap; today the portal
  detects and gates but does not yet propose upgrades.
- **Prioritization signals.** Reachability analysis and first-class EPSS
  prioritization are planned, not shipped.

**Where TrustedOSS Portal is competitive:** self-hosting with no seat cost,
Apache-2.0 licensing, a single portal instead of several consoles, a built-in
component approval workflow, build-blocking CI gates, and a fully bilingual
(EN/KO) UI and documentation.

## vs Dependency-Track alone

Dependency-Track is excellent at what it does, and TrustedOSS Portal uses it as
its vulnerability engine. The question is whether you want the surrounding
portal.

**TrustedOSS Portal adds on top of Dependency-Track:**

- **Scan orchestration.** It runs cdxgen, scancode, and Trivy and feeds the
  results in, rather than expecting you to produce and upload SBOMs yourself.
- **License compliance.** Allowed / conditional / forbidden classification,
  obligations tracking, and automatic `NOTICE` generation — outside
  Dependency-Track's scope.
- **Resilience.** A circuit breaker plus a PostgreSQL cache keeps the portal
  usable while Dependency-Track restarts or is unreachable.
- **Workflow and governance.** Component approvals, a build-blocking gate, an
  append-only audit log, and a 3-role RBAC model.
- **Bilingual UI.** English and Korean.

**Use Dependency-Track directly when** you only need vulnerability tracking from
SBOMs you already produce, want the broadest set of Dependency-Track's native
features immediately, or need signals (such as raw EPSS columns) that the portal
has not surfaced yet. Bringing the portal to parity with running
Dependency-Track directly is an explicit roadmap goal.

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

These are real and intentional gaps as of v2.0.0. Each is on the
[roadmap](https://github.com/trustedoss/trustedoss-portal/blob/main/ROADMAP.md):

- **No automated remediation.** The portal detects and gates but does not yet
  open upgrade pull requests (planned for v2.2).
- **Vulnerability data depends on Dependency-Track.** Signals are limited to
  what Dependency-Track exposes; first-class EPSS prioritization is planned
  (v2.1).
- **No VEX consumption.** VEX is exported today, but importing external VEX to
  auto-suppress findings is planned (v2.1).
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
