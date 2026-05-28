---
id: quickstart
title: Quickstart
description: Bring TrustedOSS Portal up on your laptop in 5 minutes with the dev Docker Compose stack and a seeded demo dataset.
sidebar_label: Quickstart
sidebar_position: 1
slug: /quickstart
---

# Quickstart

Run TrustedOSS Portal on your laptop in about 5 minutes. This page gives you a
populated dashboard you can click through. For a real deployment, see
[Install with Docker Compose](./installation/docker-compose.md) or the
[Helm chart](./installation/helm.md).

## Prerequisites

- Docker + `docker-compose` (V1, hyphenated) — V2 plugin also works.
- 4 vCPU / 8 GB RAM free, 10 GB free disk.

## 1. Start the stack

```bash
git clone https://github.com/trustedoss/trustedoss-portal.git
cd trustedoss-portal
cp .env.example .env

docker-compose -f docker-compose.dev.yml up -d
```

About 30 seconds in, `postgres`, `redis`, `backend`, `celery-worker`, and
`frontend` are healthy.

## 2. Seed the demo dataset

```bash
docker-compose -f docker-compose.dev.yml exec backend \
  python -m scripts.seed_demo
```

This creates one organization, three teams, five users, five projects, plus
a realistic mix of CVEs, license findings, and obligations — about
10 seconds.

## 3. Sign in

Open **http://localhost:5173** and sign in:

| Account | Email | Password |
|---|---|---|
| Super admin | `admin@demo.trustedoss.dev` | `DemoTest2026!` |
| Team admin | `frontend-admin@demo.trustedoss.dev` | `DemoTest2026!` |
| Developer | `dev@demo.trustedoss.dev` | `DemoTest2026!` |

The demo password is set in `.env.example` and is intentionally weak — never
reuse it on a host that anyone else can reach.

## 4. Look around

- **Dashboard** (`/`) — org-wide severity tiles + recent scans.
- **Projects → frontend-admin's project** — the richest dataset; click the
  **Vulnerabilities** tab to see the 7-state VEX triage flow.
- **Components & licenses** — the donut shows the allowed / conditional /
  forbidden mix.
- **SBOM** — download CycloneDX or SPDX.

![Project list — five seeded projects with severity roll-up](/img/screenshots/user-projects-list.png)

## What next

- Wire it into CI → [GitHub Actions](./ci-integration/github-actions.md), [GitLab CI](./ci-integration/gitlab-ci.md), or [Jenkins](./ci-integration/jenkins.md).
- Trigger your own scan → [Scans](./user-guide/scans.md).
- Operate it for a team → [Users & teams](./admin-guide/users-and-teams.md), [Backup & restore](./admin-guide/backup-and-restore.md).
- Move to production → [Install with Docker Compose](./installation/docker-compose.md).

## Stop the stack

```bash
docker-compose -f docker-compose.dev.yml down
```

Add `-v` to also drop the database volume.
