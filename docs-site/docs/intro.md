---
id: intro
title: Introduction
description: TRUSCA is a self-hosted, Apache-2.0 SCA portal that unifies CVEs, license compliance, and SBOMs in one UI.
sidebar_label: Introduction
sidebar_position: 0
slug: /intro
---

# TRUSCA

**TRUSCA** — the SCA tool of the [TrustedOSS](https://trustedoss.github.io/)
initiative — is a self-hosted, Apache-2.0 Software Composition
Analysis (SCA) platform. It unifies CVE tracking, license compliance, and
SBOM management in a single web UI — without the per-seat licensing of
commercial products.

## Where to start

- **Try it in 5 minutes** → [Quickstart](./quickstart.md) — one command,
  preloaded with a realistic demo dataset.
- **Install on your own host** → [Docker Compose](./installation/docker-compose.md)
  or the [Helm chart](./installation/helm.md).
- **See how it compares** → [Comparison](./comparison.md) — versus commercial
  SCA, Dependency-Track, and SW360.

## What it does

| Capability | Detail |
|---|---|
| Component detection | `cdxgen` discovers packages across 30+ language ecosystems (npm, Maven, PyPI, Go, Cargo, NuGet, RubyGems, …). |
| License classification | Allowed / conditional / forbidden tiers, with auto-generated `NOTICE` files. Forbidden licenses block the build. |
| Vulnerability detection | Trivy matches components against NVD + OSV + GitHub Advisory + EPSS + KEV via a local DB. New CVEs are picked up automatically on the weekly DB refresh. |
| Container scanning | Trivy detects OS-package CVEs in container images. |
| SBOM export | CycloneDX (JSON / XML) and SPDX (JSON / Tag-Value), byte-stable. |
| CI/CD integration | GitHub Action, GitLab CI template, Jenkinsfile example, REST API + API keys. Build gate exits `1` on Critical CVE or forbidden license. |
| Workflow | Component approval, append-only audit log, notifications via email / Slack / Teams. |
| Bilingual | English and Korean — UI, error messages, and this documentation site. |

## What it is not

- **Not a SAST scanner.** No source-code analysis for your own code — the
  portal focuses on third-party components.
- **Not a vulnerability database.** It consumes feeds (NVD, OSV, GHSA, EPSS,
  KEV) via Trivy but does not curate them.
- **Not a hosted service by default.** Ships as `docker-compose` or a Helm
  chart you run yourself. A read-only [live demo](./installation/live-demo.md)
  is available.

## Project

- **License** — Apache-2.0.
- **Source** — [github.com/trustedoss/trusca](https://github.com/trustedoss/trusca).
- **Roadmap** — [`ROADMAP.md`](https://github.com/trustedoss/trusca/blob/main/ROADMAP.md).
- **Security disclosures** — [`SECURITY.md`](https://github.com/trustedoss/trusca/blob/main/SECURITY.md).
- **Architecture and decisions** — [Architecture reference](./reference/architecture.md).
