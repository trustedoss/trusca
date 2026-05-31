# Changelog

All notable changes to TrustedOSS Portal are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.10.0] — 2026-05-31

First public release of TrustedOSS Portal.

### Scope

A self-hosted, Apache-2.0 SCA portal covering vulnerability tracking,
license compliance, SBOM generation, and CI/CD integration in one UI.

### Highlights

#### Scanning
- **Source scans** — `cdxgen` generates a CycloneDX SBOM across 30+ language
  ecosystems; Trivy correlates components against its unified vulnerability DB
  (NVD + OSV + GitHub Advisory + EPSS + KEV).
- **Container scans** — Trivy on OS packages of an image reference.
- **Vulnerability re-detection** — weekly Trivy DB refresh + a Celery beat
  re-matches existing SBOMs against the refreshed feed, with notification
  channels firing on new criticals.
- **Air-gapped support** — `TRIVY_DB_REPOSITORY` can point at a private OCI
  mirror of the Trivy DB.
- **Scan retention** — results are keyed by project ref so each ref keeps its
  latest scan + findings (superseded scans retired automatically, a beat
  reclaims orphans, and manual `DELETE` is available) — no unbounded growth.

#### Compliance
- **License classification** — allowed / conditional / forbidden tiers,
  scored against a fixed catalog.
- **Obligations** — auto-generated `NOTICE` files (text / markdown / HTML).
- **Component approval workflow** — Pending → Under Review → Approved / Rejected.
- **VEX** — export and consumption (OpenVEX + CycloneDX VEX), 7-state triage.
- **SBOM export** — CycloneDX (JSON/XML) and SPDX (JSON/Tag-Value), byte-stable,
  with per-component license and version fields populated.
- **Forbidden-license waivers** — time-boxed waivers from the Compliance tab,
  capped by `LICENSE_WAIVE_MAX_DAYS` so a waiver cannot outlive its review.

#### CI/CD
- **GitHub Actions composite action** (`actions/scan/`) — trigger a scan and
  gate the build on Critical CVEs or forbidden licenses (`exit 1`).
- **GitHub & GitLab webhooks** — auto-trigger scans on push / PR events with
  inline PR/MR comments.
- **REST API + API Keys** — for Jenkins and other CI systems without a native
  integration; a Jenkinsfile example is shipped.
- **EPSS prioritization** — column, sort, filter, and a policy-gate threshold
  (`GATE_EPSS_THRESHOLD`).
- **API key expiry presets** — pick a TTL when minting a key from the
  Integrations form; keys carry an explicit expiry.
- **Self-scan hardened via dogfooding** — running our own scan-action against
  this repo surfaced and fixed an API-key scope rejection on trigger/poll
  (`401`) and a disjunctive-`OR` license misclassification.

#### Operations
- **Multi-tenant teams + RBAC** — `super_admin` / `team_admin` / `developer`.
- **Append-only audit log** — every write surfaced with diff + actor; SQL-level
  immutability via a `plpgsql` trigger.
- **Notifications** — email (SMTP), Slack, Microsoft Teams.
- **Admin UI** — user/team management, Trivy DB monitoring + weekly refresh,
  scan queue, disk dashboard, audit-log search/filter/CSV export.
- **Backups** — daily auto-backup via Celery beat + manual backup/restore from
  the Admin UI.
- **Self-hosted demo mode** — `DEMO_READ_ONLY=true` makes the deploy read-only.

#### Experience
- **EN + KO i18n** — every UI string and every documentation page is shipped
  in both languages from the first public release.
- **Modern enterprise design system** — light theme, WCAG AA contrast,
  compact 40 px tables, drawer + page navigation dual surfaces.
- **Filter URL persistence** — every filter facet (severity, license category,
  search, status, page) lives in the URL so reload / share / back-button
  restores the exact view.
- **Global ⌘K palette** — keyboard-first navigation across projects, vulns,
  components, and admin surfaces.
- **Portfolio Dashboard** — KPI cards + severity / license distribution +
  recent scans / activity, on `/`.
- **Collapsible sidebar + responsive shell** — the sidebar toggles to a 64 px
  icon rail (persisted) and collapses to a hamburger drawer below `lg`.

#### Distribution
- **Docker Compose** (dev + prod with Traefik + Let's Encrypt).
- **Helm chart** (`charts/trustedoss`) — bundled-or-external PostgreSQL &
  Redis, Ingress with cert-manager TLS, migration Job.
- **Hosted OpenAPI reference** at `/reference/api` on the docs site.
- **`/health/ready`** — schema-gated readiness probe; `503` until the Alembic
  schema is at HEAD.
- **Chart image tags pinned to the release** — `image.tag` defaults track
  `appVersion` (`0.10.0`) so a default `helm install` pulls matching images.

#### Quality
- **Documentation UAT harness** — the user/admin/CI guides are exercised
  end-to-end ("does it work as written?") with 38 auto-executed assertions
  across 23 enrolled docs, run nightly.
- **CI gates re-enabled** — SAST (Semgrep / Bandit), the Playwright e2e matrix,
  and supply-chain self-scan run on every change or nightly, with `main`
  branch protection enforcing the required checks.
