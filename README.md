# TrustedOSS Portal

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Status](https://img.shields.io/badge/status-pre--alpha-orange.svg)](docs/v2-execution-plan.md)

> Open-source enterprise SCA portal — manage CVEs, license compliance, and SBOMs in one self-hosted UI.

**TrustedOSS Portal** is an Apache-2.0 licensed, self-hosted alternative to commercial Software Composition Analysis (SCA) products. It unifies vulnerability tracking (CVE), license compliance, and Software Bill of Materials (SBOM) management for engineering and legal teams.

> **Status:** Pre-alpha. Active development on the v2 rewrite. See [`docs/v2-execution-plan.md`](docs/v2-execution-plan.md) for the live roadmap.

---

## Why TrustedOSS Portal

- **Self-hosted, no vendor lock-in.** Apache-2.0, deployable via `docker-compose` or Helm.
- **Unified risk view.** CVEs, licenses, and SBOM in one project page — no context switching.
- **CI/CD native.** REST API + GitHub/GitLab webhooks + build-blocking gate (Critical CVE / forbidden license → exit 1).
- **Enterprise-grade workflows.** Component approval, license obligations + auto-NOTICE generation, audit log, RBAC.
- **Internationalized from day one.** English and Korean UI shipped together at GA.

## Feature Highlights

- Component detection across 30+ language ecosystems (cdxgen)
- License classification with allowed / conditional / forbidden tiers (ORT rules)
- Vulnerability detection from NVD / OSV / GitHub Advisory (Dependency-Track)
- Container image scanning (Trivy)
- SBOM export — CycloneDX (JSON/XML) + SPDX (JSON/Tag-Value)
- Excel / PDF reports
- Obligations tracking + auto-generated `NOTICE` files
- Re-detection of new CVEs against existing projects
- Component approval workflow (Pending → Under Review → Approved / Rejected)
- Notifications: Email (SMTP), Slack, Microsoft Teams
- Admin: user/team management, DT health monitoring, scan queue, disk dashboard, audit log
- CI integrations: GitHub Action, GitLab CI template, Jenkinsfile example

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI · SQLAlchemy 2.0 · Alembic |
| Database | PostgreSQL 17 |
| Async | Celery + Redis |
| Frontend | React 18 · Vite · shadcn/ui · Tailwind CSS |
| Server state | TanStack Query |
| Client state | Zustand |
| Realtime | WebSocket (scan progress streaming) |
| Auth | FastAPI-Users (JWT + OAuth2) |
| i18n | react-i18next |
| Tests | pytest · Playwright (harness pattern) |
| Docs | Docusaurus |
| CI/CD | GitHub Actions |
| Containers | Docker Compose (dev/prod split), Helm chart (Phase B) |

## Quick Start (development)

> ⚠️ Bootstrap is in progress. The commands below describe the target experience after Phase 0 PR #2 lands.

```bash
git clone https://github.com/trustedoss/trustedoss-portal.git
cd trustedoss-portal
cp .env.example .env

docker-compose -f docker-compose.dev.yml up
# → http://localhost:5173 (frontend) · http://localhost:8000/docs (API) · http://localhost:8080 (Dependency-Track)
```

Production deployment uses the bundled `docker-compose.yml` with Traefik. Helm chart and demo SaaS (Cloud Run) ship later in Phase 7–8.

## Repository Layout

```
trustedoss-portal/
├── apps/
│   ├── backend/         FastAPI app (api, core, models, services, tasks, integrations)
│   └── frontend/        React + Vite + shadcn/ui app
├── charts/trustedoss/   Helm chart (Phase B)
├── docs/                Docusaurus site + execution plan + session handoffs
├── scripts/             install / upgrade / backup / restore
└── .github/             workflows, issue templates, PR template
```

See [`CLAUDE.md`](CLAUDE.md) for the full architecture and rules.

## Documentation

- [`CLAUDE.md`](CLAUDE.md) — project rules, architecture decisions, quality / security / ops standards
- [`docs/v2-execution-plan.md`](docs/v2-execution-plan.md) — single source of truth for the v2 roadmap, micro-tasks, harness operations, and definitions of done
- [`docs/sessions/`](docs/sessions/) — per-session handoff notes
- [`docs/_v1-reference/`](docs/_v1-reference/) — read-only references kept from the v1 prototype

## Contributing

Contribution guidelines, issue templates, and PR templates land in Phase 0 PR #4 (`CONTRIBUTING.md`, `.github/ISSUE_TEMPLATE/*`, `.github/pull_request_template.md`). Until then, please open a discussion before submitting a PR.

## License

Apache License 2.0 — see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
