---
id: intro
title: Introduction
description: TrustedOSS Portal is a self-hosted, Apache-2.0 SCA portal that unifies CVEs, license compliance, and SBOM in one UI.
sidebar_label: Introduction
sidebar_position: 1
slug: /intro
---

# TrustedOSS Portal

**TrustedOSS Portal** is a self-hosted, open-source Software Composition Analysis (SCA) platform. It unifies vulnerability tracking, license compliance, and Software Bill of Materials (SBOM) management in a single web UI — without the per-seat licensing of commercial products.

:::note Audience
This page is for engineers, platform owners, and legal/compliance leads evaluating an SCA portal for their organization. If you are ready to install, jump to [Install with Docker Compose](./installation/docker-compose.md).
:::

## What it does

| Capability | Detail |
|---|---|
| Component detection | `cdxgen` discovers packages across 30+ ecosystems (npm, Maven, PyPI, Go, Cargo, NuGet, Composer, RubyGems, Gradle, Hex, …). |
| License classification | ORT rules tag every license as **Allowed**, **Conditional**, or **Forbidden**. Forbidden licenses block the build. |
| Vulnerability detection | Dependency-Track correlates components against NVD, OSV, and the GitHub Advisory Database. |
| Container scanning | Trivy detects OS-package CVEs in container images. |
| SBOM export | CycloneDX (JSON / XML) and SPDX (JSON / Tag-Value), byte-stable for diffing. |
| Obligations & NOTICE | Per-license obligations are tracked, and a `NOTICE` file is generated automatically from the latest scan. |
| CI/CD integration | REST API + API key auth, GitHub & GitLab webhooks, GitHub Action, GitLab CI template, Jenkinsfile. The build gate exits 1 on Critical CVE or forbidden license. |
| Notifications | Email (SMTP), Slack, and Microsoft Teams webhooks for the five core triggers (scan finished, gate failed, new CVE, approval request, disk pressure). |
| Audit log | Append-only record of every write operation — actor, action, target, request ID. |
| Internationalization | English and Korean shipped together. The UI, error messages, and this documentation site are all bilingual. |

## What it is not

- **Not a SAST scanner.** No source-code analysis for custom code; the portal focuses on third-party components.
- **Not a vulnerability database.** It consumes feeds (NVD, OSV, GitHub Advisory) via Dependency-Track but does not curate them.
- **Not a hosted service** (yet). The primary distribution is a `docker-compose` install you run on your own infrastructure. A demo SaaS on GCP follows in Phase 8.

## Architecture at a glance

```
┌────────────┐   ┌────────────────────────────────┐   ┌──────────────────┐
│  Browser   │ → │  Traefik (TLS, HTTP→HTTPS)     │ → │  Frontend (Vite) │
└────────────┘   └────────────────────────────────┘   └──────────────────┘
                            │
                            ↓
                   ┌────────────────┐
                   │ FastAPI backend│
                   └────────────────┘
                            │
       ┌────────────────────┼────────────────────────┐
       ↓                    ↓                        ↓
 ┌───────────┐       ┌──────────┐           ┌────────────────────────┐
 │ Postgres  │       │ Celery   │ → tasks → │ cdxgen / ORT / Trivy / │
 │   (17)    │       │ + Redis  │           │ Dependency-Track       │
 └───────────┘       └──────────┘           └────────────────────────┘
```

Six container services run in production: **traefik**, **postgres**, **redis**, **backend**, **worker**, **beat** (Celery scheduler), and **frontend**. Optional Dependency-Track and Jaeger overlays add bundled vulnerability data and tracing respectively.

The full architecture, decision log, and pipeline detail are in the [architecture reference](./reference/architecture.md).

## License & governance

- **License:** Apache-2.0 — see [`LICENSE`](https://github.com/trustedoss/trustedoss-portal/blob/main/LICENSE).
- **Source:** [github.com/trustedoss/trustedoss-portal](https://github.com/trustedoss/trustedoss-portal).
- **Roadmap:** [`docs/v2-execution-plan.md`](https://github.com/trustedoss/trustedoss-portal/blob/main/docs/v2-execution-plan.md) — single source of truth for in-flight work.
- **Security disclosures:** [`SECURITY.md`](https://github.com/trustedoss/trustedoss-portal/blob/main/SECURITY.md).

## Where to go next

- **Install on your own host** → [Install with Docker Compose](./installation/docker-compose.md)
- **Run your first scan** → [Scans](./user-guide/scans.md)
- **Wire it into CI** → [GitHub Actions](./ci-integration/github-actions.md), [GitLab CI](./ci-integration/gitlab-ci.md), [Jenkins](./ci-integration/jenkins.md)
- **Operate it** → [Users & teams](./admin-guide/users-and-teams.md), [Backup & restore](./admin-guide/backup-and-restore.md)
- **API consumers** → [API overview](./reference/api-overview.md)
