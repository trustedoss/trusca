# TrustedOSS Portal

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Release](https://img.shields.io/badge/release-v2.0.0_GA-2563eb.svg)](CHANGELOG.md)
[![Docs](https://img.shields.io/badge/docs-trustedoss.github.io-0f172a.svg)](https://trustedoss.github.io/trustedoss-portal/)

> Open-source enterprise SCA portal — manage CVEs, license compliance, and SBOMs in one self-hosted UI.

**TrustedOSS Portal** is an Apache-2.0 licensed, self-hosted alternative to commercial Software Composition Analysis (SCA) products. It unifies vulnerability tracking (CVE), license compliance, and Software Bill of Materials (SBOM) management for engineering and legal teams.

> **Status:** Generally available — **v2.0.0** ([CHANGELOG](CHANGELOG.md)). See the published [documentation site](https://trustedoss.github.io/trustedoss-portal/) to get started, and [`ROADMAP.md`](ROADMAP.md) for what comes next.

> **🔭 Live demo:** read-only demo mode (`DEMO_READ_ONLY`) and the nightly dataset reset are shipped (v2.1); a public hosted instance URL is published once the demo host is provisioned. Read-only mode and the daily reset are documented in [Live demo](https://trustedoss.github.io/trustedoss-portal/docs/installation/live-demo). You can also run a read-only demo locally — see the doc.

> **v1 → v2 transition (2026-05-05):** `main` tracks the v2 rewrite. The previous v1 codebase is preserved on the [`legacy/v1`](https://github.com/trustedoss/trustedoss-portal/tree/legacy/v1) branch (read-only, not maintained). v2 is a clean re-implementation — there is no automatic data migration from v1.

---

## Why TrustedOSS Portal

- **Self-hosted, no vendor lock-in.** Apache-2.0, deployable via `docker-compose` or Helm. No per-seat licensing.
- **Unified risk view.** CVEs, licenses, and SBOM in one project page — no context switching.
- **CI/CD native.** REST API + GitHub/GitLab webhooks + build-blocking gate (Critical CVE / forbidden license → exit 1).
- **Enterprise-grade workflows.** Component approval, license obligations + auto-NOTICE generation, append-only audit log, RBAC.
- **Internationalized from day one.** English and Korean UI — and this documentation — shipped together at GA.

![Project list](docs-site/static/img/screenshots/user-projects-list.png)
*Project list — risk roll-up across every scanned project.*

![Vulnerabilities](docs-site/static/img/screenshots/user-vulns-list.png)
*Vulnerability list — CVEs correlated by Dependency-Track with a 7-state VEX triage workflow.*

![SBOM export](docs-site/static/img/screenshots/user-sbom-tab.png)
*SBOM tab — CycloneDX and SPDX export in JSON, XML, and Tag-Value.*

![Admin health](docs-site/static/img/screenshots/admin-health-cards.png)
*Admin System Health — service status, scan queue, disk, and Dependency-Track connectivity at a glance.*

## Feature highlights

- Component detection across 30+ language ecosystems (cdxgen, CycloneDX generator), with direct vs. transitive dependency-graph depth
- License classification with allowed / conditional / forbidden tiers — declared licenses from cdxgen, detected first-party licenses from scancode, scored against a fixed classification catalog (dynamic per-team policy editing is on the [roadmap](ROADMAP.md))
- Vulnerability detection from NVD / OSV / GitHub Advisory (Dependency-Track) with a circuit breaker + PostgreSQL cache, 7-state VEX triage, EPSS prioritization (column / sort / filter / policy-gate threshold), per-finding `fixed_version`, and automatic re-detection of new CVEs
- Container image scanning for OS-package CVEs (Trivy)
- SBOM export — CycloneDX (JSON/XML) + SPDX (JSON/Tag-Value), byte-stable; VEX export **and** VEX consumption (import OpenVEX / CycloneDX VEX to auto-suppress findings)
- Vulnerability report as PDF (`GET /v1/projects/{id}/vulnerability-report.pdf`) — Excel and compliance-PDF reports are on the [roadmap](ROADMAP.md)
- Obligations tracking + auto-generated `NOTICE` files (text / markdown / HTML)
- Component approval workflow (Pending → Under Review → Approved / Rejected)
- Notifications: Email (SMTP), Slack, Microsoft Teams
- Admin: user/team management, DT health monitoring + orphan cleanup, scan queue, disk dashboard, audit log
- CI integrations: GitHub Action, GitLab CI template, Jenkinsfile example (Jenkins has no native plugin — the Jenkinsfile is a worked example)
- Hosted OpenAPI reference on the docs site, a `/health/ready` schema-gated readiness probe, an evaluation Compose profile, a read-only live-demo mode, and a production-grade Helm chart

## Tech stack

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
| Containers | Docker Compose (dev/prod split), Helm chart |

## Quick start (development)

```bash
git clone https://github.com/trustedoss/trustedoss-portal.git
cd trustedoss-portal
cp .env.example .env

docker-compose -f docker-compose.dev.yml up
# → http://localhost:5173 (frontend) · http://localhost:8000/docs (API) · http://localhost:8080 (Dependency-Track)
```

After roughly 30 seconds the dev containers (`postgres`, `redis`, `backend`, `celery-worker`, `frontend`) are healthy.

### Other ways to run it

- **Evaluate on a small host** — one command brings the portal up on a 2 vCPU / 4 GB machine and seeds a realistic dataset (no Dependency-Track required):

  ```bash
  ./scripts/eval-up.sh        # add --no-prompt for CI / automation
  ```

  Details in the [evaluation section of the installation guide](https://trustedoss.github.io/trustedoss-portal/docs/installation/docker-compose).

- **Production (Docker Compose)** — use the bundled `docker-compose.yml` (Traefik + Let's Encrypt). See the [installation guide](https://trustedoss.github.io/trustedoss-portal/docs/installation/docker-compose).
- **Production (Kubernetes / Helm)** — the production-grade chart (`charts/trustedoss`, 0.2.0) ships bundled-or-external PostgreSQL & Redis, an Ingress with cert-manager TLS, and a migration Job. See the [Helm / Kubernetes guide](https://trustedoss.github.io/trustedoss-portal/docs/installation/helm).
- **Read-only live demo** — run any deploy with `DEMO_READ_ONLY=true`. See [Live demo](https://trustedoss.github.io/trustedoss-portal/docs/installation/live-demo).
- **API reference** — the hosted OpenAPI reference is at [`/reference/api`](https://trustedoss.github.io/trustedoss-portal/reference/api).

## Repository layout

```
trustedoss-portal/
├── apps/
│   ├── backend/         FastAPI app (api, core, models, services, tasks, integrations)
│   └── frontend/        React + Vite + shadcn/ui app
├── charts/trustedoss/   Helm chart
├── terraform/           GCP infrastructure (hosted demo)
├── docs-site/           Docusaurus documentation site (EN/KO) + static assets
├── docs/                Internal execution plans, roadmap detail, session handoffs
├── scripts/             install / upgrade / backup / restore
└── .github/             workflows, issue templates, PR template, CODEOWNERS
```

## Documentation

- **[Documentation site](https://trustedoss.github.io/trustedoss-portal/)** — install, scan, operate, and integrate (English + Korean)
- [`ROADMAP.md`](ROADMAP.md) — public roadmap after the v2.0.0 GA release
- [`CHANGELOG.md`](CHANGELOG.md) — release history

### For contributors

- [`CONTRIBUTING.md`](CONTRIBUTING.md) — local setup, conventions, and the PR process
- [`GOVERNANCE.md`](GOVERNANCE.md) — decision-making model and maintainer responsibilities
- [`MAINTAINERS.md`](MAINTAINERS.md) — current maintainers and areas of ownership
- [`SUPPORT.md`](SUPPORT.md) — where to ask questions and report problems
- [`CLAUDE.md`](CLAUDE.md) — architecture decisions and runtime rules
- [`docs/post-ga-roadmap.md`](docs/post-ga-roadmap.md) — detailed, PR-level post-GA execution plan
- [`docs/v2-execution-plan.md`](docs/v2-execution-plan.md) — the execution plan up to GA (kept as a record)

## Contributing

Contributions are welcome — code, documentation, translations, bug reports, and design feedback. Start with [`CONTRIBUTING.md`](CONTRIBUTING.md) for local setup and the PR process, and [`SUPPORT.md`](SUPPORT.md) if you have a question first. All participants are expected to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## SCA self-scan

[![SCA self-scan](https://github.com/trustedoss/trustedoss-portal/actions/workflows/sca-self.yml/badge.svg)](https://github.com/trustedoss/trustedoss-portal/actions/workflows/sca-self.yml)

The portal dog-foods its own toolchain. A nightly GitHub Actions workflow ([`.github/workflows/sca-self.yml`](.github/workflows/sca-self.yml)) generates a CycloneDX SBOM with cdxgen, runs Trivy against it, and auto-opens / closes a labelled GitHub issue when Critical CVEs appear in our dependency tree.

## Security

Please do not open a public issue for an unpatched vulnerability. See [`SECURITY.md`](SECURITY.md) for the private disclosure process.

## License

Apache License 2.0 — see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
