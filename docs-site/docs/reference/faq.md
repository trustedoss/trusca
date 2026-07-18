---
id: faq
title: FAQ
description: Frequently asked questions about TRUSCA — setup, scanning, air-gapped operation, the Trivy database, licenses and policy, the build gate, and CI integration, each pointing to the page with the full answer.
sidebar_label: FAQ
sidebar_position: 12
---

# Frequently asked questions

This page answers the questions new adopters ask most often, and points to the page that covers each in full. It is a map, not a replacement for the detailed guides — follow the link under each answer.

## Getting started {#getting-started}

### What does TRUSCA actually do? {#what-is-trusca}

It scans your projects for open-source components, classifies their licenses, matches known vulnerabilities (CVEs) against them, and can fail a CI build on a forbidden license or a Critical vulnerability. It is a self-hosted software composition analysis (SCA) portal. See the [Introduction](../intro.md) and the [Comparison](../comparison.md) with commercial tools.

### How do I install it? {#install}

The supported path is Docker Compose on a single host; a Helm chart is available for Kubernetes. See [Installation → Docker Compose](../installation/docker-compose.md) and [Installation → Helm](../installation/helm.md).

### What is the fastest way to see it working? {#quickstart}

Follow the [Quickstart](../quickstart.md) — it brings up the stack, creates a project, and runs a first scan end to end.

### What kinds of analysis can it run? {#analysis-types}

Four: a source SBOM scan, a container image scan, the build gate, and a reachability signal (Go modules today, via `govulncheck`). The [Analysis types](./analysis-types.md) reference lays out what each consumes and produces in one matrix.

## Scanning {#scanning}

### How do I scan a project? {#how-to-scan}

Register the project, then trigger a **source** scan (from a Git URL or an uploaded archive) or a **container** scan (from an image reference). See [Scans](../user-guide/scans.md).

### Which languages and ecosystems are supported? {#languages}

Component detection uses cdxgen, which covers 30+ language and build systems. License enrichment additionally resolves declared licenses for several registries (PyPI, Maven, crates.io, Go, RubyGems, NuGet). See [Components and licenses](../user-guide/components-and-licenses.md).

### My scan finished but there are no vulnerabilities — is that a bug? {#no-vulns}

Most often the Trivy database had not finished downloading when the scan ran. The findings appear automatically once the database arrives (the re-match beat re-scans existing SBOMs); no re-scan is needed. See [Admin → Vulnerability data](../admin-guide/vulnerability-data.md) and the [on-call runbook, Scenario 1](../admin-guide/oncall-runbook.md).

### How long does a scan take? {#scan-duration}

A source scan is dominated by the cdxgen walk and scancode; a container scan is dominated by image-pull time. The Trivy matching stage itself is sub-second. See [Scans → Average duration](../user-guide/scans.md#average-duration).

### How often should I scan? {#scan-frequency}

Scan on source change (every PR/push via CI); let the re-match beat handle new CVEs on unchanged code. See [Best practices → Scan frequency](../best-practices/scan-frequency.md).

## Air-gapped and offline {#air-gapped}

### Can TRUSCA run fully offline / air-gapped? {#offline}

Yes. Vulnerability matching uses Trivy's bundled database, which you can mirror internally (`TRIVY_DB_REPOSITORY`). Features that would reach the network — fingerprint-based snippet matching, license enrichment fetches — are gated and can be turned off. See [Admin → Vulnerability data](../admin-guide/vulnerability-data.md) and [Environment variables](./env-variables.md).

### Does anything leave my network during a scan? {#egress}

By default, no scan data leaves your network. License enrichment can fetch declared licenses from public registries; set `LICENSE_FETCH_ENABLED=false` to disable it in an air-gapped deployment. See [Components and licenses](../user-guide/components-and-licenses.md) and [Environment variables](./env-variables.md).

### How do I keep the Trivy database current in an air-gapped install? {#airgap-db}

Point `TRIVY_DB_REPOSITORY` at an internal mirror and refresh it on your own schedule; the worker re-matches existing SBOMs after each refresh. See [Admin → Vulnerability data](../admin-guide/vulnerability-data.md) and [Best practices → Upgrade cadence](../best-practices/upgrade-cadence.md#trivy-db).

## Licenses and policy {#licenses-policy}

### How are licenses classified? {#license-tiers}

Into three tiers — **forbidden** (blocks the build), **conditional** (needs review/approval), and **permitted**. See [License policies](./license-policies.md) and [Best practices → Policy design](../best-practices/policy-design.md#license-tiers).

### Can I change which licenses are allowed? {#change-policy}

Yes, per team at runtime — promote or demote a license tier without a redeploy. See [License policies → Dynamic gate evaluation](./license-policies.md#dynamic-gate-evaluation).

### What is the component approval workflow for? {#approvals}

For dispositioning components that carry a **conditional** license (Pending → Under Review → Approved / Rejected). Note that the approval verdict is recorded for audit but does **not** itself gate the build. See [Approvals](../user-guide/approvals.md) and [Triage](../user-guide/triage.md#approval-does-not-gate).

### Where do license obligations (e.g. NOTICE requirements) come from? {#obligations}

From a built-in catalogue keyed by SPDX identifier, surfaced per project with a downloadable NOTICE file. See [Obligation catalogue](./obligation-catalog.md).

## The build gate and CI {#gate-ci}

### What makes a build fail? {#gate-fail}

Exactly two conditions: an open Critical CVE, or a component whose license resolves to the `forbidden` tier. See [Triage → Where each decision reaches the build gate](../user-guide/triage.md#gate-reach).

### A rejected component did not block CI — why? {#rejected-not-blocked}

By design: the gate reads the `forbidden` license tier and open Critical CVEs, never the approval verdict. To block a license, promote its tier to `forbidden`. See [Triage → Component approval does not gate the build](../user-guide/triage.md#approval-does-not-gate).

### Can I fail a build on a non-Critical but high-risk CVE? {#epss-gate}

Yes — add the optional EPSS dimension so a high-probability CVE fails regardless of severity. See [GitHub Actions → Gate the build on EPSS](../ci-integration/github-actions.md#gate-the-build-on-epss-optional).

### How do I wire a scan into CI? {#ci-wiring}

Use the REST API with an API key, or the ready-made CI templates. See [CI integration → GitHub Actions](../ci-integration/github-actions.md), [GitLab CI](../ci-integration/gitlab-ci.md), and [Jenkins](../ci-integration/jenkins.md).

### How do I get an API key? {#api-key}

An admin creates one from the API-key management screen. See [Admin → API keys](../admin-guide/api-keys.md).

## Triage {#triage}

### What is VEX? {#vex}

VEX (Vulnerability Exploitability eXchange) is the standard vocabulary for recording whether a CVE genuinely affects your product — the states a finding moves through during triage. See [Vulnerabilities → VEX state machine](../user-guide/vulnerabilities.md#vex-state-machine).

### How do I mark a CVE as not affecting us? {#not-affected}

Move the finding to an excluded VEX state (`Not affected`, `False positive`, `Suppressed`, or `Fixed`); it drops out of the build-gate count on the next scan. See [Triage](../user-guide/triage.md) and [Vulnerabilities](../user-guide/vulnerabilities.md#vex-state-machine).

### Can I generate or import a VEX document? {#vex-export}

Yes — VEX can be exported and re-imported. See [Vulnerabilities](../user-guide/vulnerabilities.md).

## SBOM and reports {#sbom-reports}

### What SBOM formats are supported? {#sbom-formats}

CycloneDX (JSON/XML) and SPDX (JSON/Tag-Value), with optional policy-annotated and policy-filtered export profiles. See [SBOM](../user-guide/sbom.md).

### Can I verify an SBOM was signed by TRUSCA? {#sbom-verify}

Yes — default SBOM exports are signed with cosign and can be verified offline. Note that export profiles are unsigned. See [SBOM signature verification](./sbom-signature-verification.md).

## Administration and operations {#admin-ops}

### What are the roles? {#roles}

Super Admin (whole deployment), Team Admin (a team's settings and members), and Developer (run scans, read results). See [Admin → Users and teams](../admin-guide/users-and-teams.md) and [Best practices → Team structure](../best-practices/team-structure.md).

### How do I back up and restore? {#backup}

Automatic daily backups run by default; manual backup/restore is available from the admin UI. Always back up before an upgrade — migrations are forward-only. See [Admin → Backup and restore](../admin-guide/backup-and-restore.md) and [Best practices → Upgrade cadence](../best-practices/upgrade-cadence.md).

### How do I upgrade TRUSCA? {#upgrade}

Read the release notes, back up, then run the upgrade — migrations apply forward-only. See [Installation → Upgrade](../installation/upgrade.md) and [Best practices → Upgrade cadence](../best-practices/upgrade-cadence.md).

### Something is broken in production — where do I start? {#incident}

The [on-call runbook](../admin-guide/oncall-runbook.md) has step-by-step recovery for the common incidents (stale Trivy DB, failed backups, stuck scans, disk pressure).

## See also {#see-also}

- [Quickstart](../quickstart.md) — the fastest end-to-end path
- [Analysis types](./analysis-types.md) — what each kind of scan produces
- [Triage](../user-guide/triage.md) — how findings become build-gate decisions
- [Best practices](../best-practices/scan-frequency.md) — operating decisions (frequency, policy, teams, upgrades)
- [Comparison](../comparison.md) — how TRUSCA relates to commercial SCA tools
