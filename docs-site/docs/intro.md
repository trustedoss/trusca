---
id: intro
title: Introduction
description: TrustedOSS Portal is a self-hosted, Apache-2.0 SCA portal that unifies CVEs, license compliance, and SBOM in one UI.
sidebar_label: Introduction
sidebar_position: 1
slug: /intro
---

# TrustedOSS Portal

:::tip Version 2.0.0 (GA) — 2026-05-09
TrustedOSS Portal is now generally available! See the [v2.0.0 release notes](./release-notes/v2.0.0.md) for what's new.
:::

**TrustedOSS Portal** is a self-hosted, open-source Software Composition Analysis (SCA) platform. It unifies vulnerability tracking, license compliance, and Software Bill of Materials (SBOM) management in a single web UI — without the per-seat licensing of commercial products.

:::note Audience
This page is for engineers, platform owners, and legal/compliance leads evaluating an SCA portal for their organization. If you are ready to install, jump to [Install with Docker Compose](./installation/docker-compose.md).
:::

## What's new in 2.0.0

- **Authentication UX** — `/forgot-password` + `/reset-password` flow, OAuth on `/login` (GitHub + Google), and an `i18next-parser` drift gate that keeps EN / KO in lockstep.
- **`/integrations` page** — self-service API keys with one-time plaintext reveal, revoke confirmation, and inline GitHub / GitLab webhook URL info.
- **Backup automation** — daily Celery Beat backup at 00:00 UTC with 7-day auto-retention, plus a `/admin/backup` UI that supports manual trigger, streaming download, and a typing-gated Upload + Restore.
- **SAST hard-fail in CI** — `bandit`, `semgrep`, and Trivy image-scan now block merges on High / ERROR / CRITICAL respectively.
- **SCA self-scan** — a nightly workflow that scans the portal's own dependencies and opens / closes a GitHub issue automatically; the project eats its own dog food.

Full details in the [v2.0.0 release notes](./release-notes/v2.0.0.md).

## What it does

| Capability | Detail |
|---|---|
| Component detection | `cdxgen` (CycloneDX generator) discovers packages across 30+ ecosystems (npm, Maven, PyPI, Go, Cargo, NuGet, Composer, RubyGems, Gradle, Hex, …). |
| License classification | Every license is tagged **Allowed**, **Conditional**, or **Forbidden**; declared licenses come from `cdxgen` and detected first-party licenses from scancode. Forbidden licenses block the build. |
| Vulnerability detection | Dependency-Track (DT) correlates components against NVD, OSV, and the GitHub Advisory Database. |
| Container scanning | Trivy (Aqua Security container scanner) detects OS-package CVEs (Common Vulnerabilities and Exposures) in container images. |
| SBOM export | CycloneDX (JSON / XML) and SPDX (JSON / Tag-Value), byte-stable for diffing. |
| Obligations & NOTICE | Per-license obligations are tracked, and a `NOTICE` file is generated automatically from the latest scan. |
| CI/CD integration | REST API + API key auth, GitHub & GitLab webhooks, GitHub Action, GitLab CI template, Jenkinsfile. The build gate exits 1 on Critical CVE or forbidden license. |
| Notifications | Email (SMTP), Slack, and Microsoft Teams webhooks for six trigger kinds — scan completed / scan failed / CVE detected / license violation / approval pending / policy gate failed. (Producer-side emit-points for most kinds land in v2.1; the inbox UI is functional today.) |
| Audit log | Append-only record of every write operation — actor, action, target, request ID. |
| Internationalization | English and Korean shipped together. The UI, error messages, and this documentation site are all bilingual. |

## What it is not

- **Not a SAST scanner.** No source-code analysis for custom code; the portal focuses on third-party components.
- **Not a vulnerability database.** It consumes feeds (NVD, OSV, GitHub Advisory) via Dependency-Track but does not curate them.
- **Not a hosted service.** The primary distribution is a `docker-compose` install (or the Helm chart) you run on your own infrastructure. A public **read-only** live demo is supported — `DEMO_READ_ONLY` mode plus a nightly dataset reset shipped in v2.1; see [Live demo](./installation/live-demo.md).

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
 ┌───────────┐       ┌──────────┐           ┌────────────────────────────┐
 │ Postgres  │       │ Celery   │ → tasks → │ cdxgen / scancode / Trivy /│
 │   (17)    │       │ + Redis  │           │ Dependency-Track           │
 └───────────┘       └──────────┘           └────────────────────────────┘
```

Seven container services run in production: **traefik**, **postgres**, **redis**, **backend**, **worker**, **beat** (Celery scheduler), and **frontend**. An optional Dependency-Track overlay adds bundled vulnerability data.

The full architecture, decision log, and pipeline detail are in the [architecture reference](./reference/architecture.md).

## License & governance

- **License:** Apache-2.0 — see [`LICENSE`](https://github.com/trustedoss/trustedoss-portal/blob/main/LICENSE).
- **Source:** [github.com/trustedoss/trustedoss-portal](https://github.com/trustedoss/trustedoss-portal).
- **Roadmap:** [`ROADMAP.md`](https://github.com/trustedoss/trustedoss-portal/blob/main/ROADMAP.md) — public summary of post-GA work, with the detailed plan in [`docs/post-ga-roadmap.md`](https://github.com/trustedoss/trustedoss-portal/blob/main/docs/post-ga-roadmap.md). The pre-GA [`docs/v2-execution-plan.md`](https://github.com/trustedoss/trustedoss-portal/blob/main/docs/v2-execution-plan.md) is kept as a completed record.
- **Security disclosures:** [`SECURITY.md`](https://github.com/trustedoss/trustedoss-portal/blob/main/SECURITY.md).

## Where to go next

- **Install on your own host** → [Install with Docker Compose](./installation/docker-compose.md)
- **Run your first scan** → [Scans](./user-guide/scans.md)
- **Wire it into CI** → [GitHub Actions](./ci-integration/github-actions.md), [GitLab CI](./ci-integration/gitlab-ci.md), [Jenkins](./ci-integration/jenkins.md)
- **Operate it** → [Users & teams](./admin-guide/users-and-teams.md), [Backup & restore](./admin-guide/backup-and-restore.md)
- **API consumers** → [API overview](./reference/api-overview.md)
