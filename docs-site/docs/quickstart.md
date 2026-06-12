---
id: quickstart
title: Quickstart
description: Bring TRUSCA up on your laptop in 5 minutes with the dev Docker Compose stack and a seeded demo dataset.
sidebar_label: Quickstart
sidebar_position: 1
slug: /quickstart
---

# Quickstart

Run TRUSCA on your laptop in about 5 minutes. This page gives you a
populated dashboard you can click through. For a real deployment, see
[Install with Docker Compose](./installation/docker-compose.md) or the
[Helm chart](./installation/helm.md).

## Prerequisites

- Docker + `docker-compose` (V1, hyphenated) — V2 plugin also works.
- 4 vCPU / 8 GB RAM free, 10 GB free disk.

## 1. Start the stack

Clone the repository and create your env file:

<!-- docs-uat: id=qs-bootstrap kind=shell ctx=host tier=gate waiver=ci-uses-checkout-tree -->
```bash
git clone https://github.com/trustedoss/trusca.git
cd trusca
cp .env.example .env
```

The dev image runs `uvicorn --reload` directly, so — unlike the production
image — it does not auto-apply migrations on boot. Create the schema first, so
the backend reports healthy as soon as it starts (otherwise the
health-gated `celery-worker` blocks `up`):

<!-- docs-uat: id=qs-migrate kind=shell ctx=host expect=exit:0 retry=20x3s tier=gate -->
```bash
docker-compose -f docker-compose.dev.yml run --rm backend alembic upgrade head
```

Then bring the full stack up:

<!-- docs-uat: id=qs-up kind=shell ctx=host expect=exit:0 tier=gate -->
```bash
docker-compose -f docker-compose.dev.yml up -d
```

<!-- docs-uat: id=qs-health kind=api ctx=host url=/health/ready expect=status:200 retry=40x6s tier=gate -->
The schema is already applied, so `postgres`, `redis`, `backend`,
`celery-worker`, and `frontend` report healthy within about 30 seconds
(`docker-compose -f docker-compose.dev.yml ps`).

## 2. Seed the demo dataset

<!-- docs-uat: id=qs-seed kind=shell ctx=host expect=exit:0 fixture=seed_demo tier=gate -->
```bash
docker-compose -f docker-compose.dev.yml exec backend \
  python -m scripts.seed_demo
```

This creates one organization, three teams, five users, five projects, plus
a realistic mix of CVEs, license findings, and obligations — about
10 seconds.

## 3. Sign in

<!-- docs-uat: id=qs-login kind=ui harness=login(admin@demo.trustedoss.dev,DemoTest2026!) tier=gate -->
Open **http://localhost:5173** and sign in:

| Account | Email | Password |
|---|---|---|
| Super admin | `admin@demo.trustedoss.dev` | `DemoTest2026!` |
| Team admin | `frontend-admin@demo.trustedoss.dev` | `DemoTest2026!` |
| Developer | `dev@demo.trustedoss.dev` | `DemoTest2026!` |

The demo password is set in `.env.example` and is intentionally weak — never
reuse it on a host that anyone else can reach.

## 4. Look around

<!-- docs-uat: id=qs-dashboard kind=ui harness=expectMounted tier=gate -->
- **Dashboard** (`/`) — org-wide severity tiles + recent scans.
<!-- docs-uat: id=qs-projects kind=ui harness=expectVisibleProjectCount(5) tier=gate -->
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

<!-- docs-uat: id=qs-down kind=shell ctx=host expect=exit:0 tier=gate -->
```bash
docker-compose -f docker-compose.dev.yml down
```

Add `-v` to also drop the database volume.
