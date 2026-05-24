# Changelog

All notable changes to TrustedOSS Portal v2 are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added — v2.1 (Triage Confidence)
- **EPSS prioritization** — Exploit Prediction Scoring System surfaced as a
  first-class signal: column, sort, filter, and a policy-gate threshold.
- **VEX consumption** — import OpenVEX / CycloneDX VEX to auto-suppress
  findings (export already shipped in 2.0.0); findings are suppressed
  automatically from the imported assertions, with UI surfacing.
- **`/health/ready`** — schema-gated readiness probe; returns `503` until the
  Alembic schema is at HEAD, so traffic only reaches a migrated schema.
- **Evaluation profile** — `docker-compose.eval.yml` + `scripts/eval-up.sh`
  stand the portal up on a 2 vCPU / 4 GB host and seed a realistic dataset in
  one command (no Dependency-Track required).
- **Production-grade Helm chart** (`charts/trustedoss` 0.2.0) — bundled-or-
  external PostgreSQL & Redis, a frontend Deployment, an Ingress with
  cert-manager TLS, a pre-install/pre-upgrade migration Job (`alembic upgrade
  head` as the owner role), and OCI (ghcr) publication with ArtifactHub
  metadata.
- **Hosted API reference** — redocusaurus renders a committed `openapi.json`
  snapshot at `/reference/api` on the docs site.
- **Live read-only demo** — `DEMO_READ_ONLY` middleware (allow-list, blocks all
  non-auth mutations API-wide) plus a GCP nightly demo reset (Cloud Scheduler →
  Cloud Run Job) that drops and reseeds only the demo dataset.

### Added — v2.2 (Remediation & Policy, in progress)
- **Per-finding `fixed_version`** — real fixed-version data surfaced on each
  vulnerability finding (#153).
- **Dependency-graph depth** — direct vs. transitive classification with depth
  on components / findings (#154).
- Suggested dependency upgrades are in progress; automated upgrade PRs and a
  dynamic license-policy engine are planned. See
  [`ROADMAP.md`](ROADMAP.md).

## [2.0.0] — 2026-05-09

First general-availability release. Promotes `2.0.0-rc.1` and absorbs the
post-rc cleanup wave (PRs #28 ~ #31).

### Added
- **Authentication UX** (PR #28): wire `/forgot-password` to backend,
  add new `/reset-password` page with `?token=` flow, `i18next-parser`
  drift gate, and OAuth (`/login` GitHub + Google buttons + 7-error-code
  i18n mapping + `redirect_after` pass-through).
- **`/integrations` page** (PR #28): API Key list + create dialog with
  one-time plaintext reveal + revoke confirmation; webhook URL info
  (GitHub HMAC + GitLab token).
- **Backup automation** (PR #29): daily Celery Beat backup at 00:00 UTC
  with 7-day auto-retention, `/admin/backup` UI (manual trigger,
  streaming download, type-"restore" upload+restore, delete with
  auto-* protection), `useScanWebSocket` immediate reconnect on tab
  focus.
- **Locust load harness** (PR #30): `tests/load/` + `docker-compose.load.yml`
  for staging-only p95 < 1s SLO validation. Not wired to CI.
- **SCA self-scan** (PR #30): nightly `sca-self.yml` workflow generates
  CycloneDX SBOM via cdxgen, scans with Trivy, opens / closes a GitHub
  issue automatically when CRITICAL vulns are detected.
- **API Keys + Webhooks test coverage** (PR #31): 125 tests
  (52 unit + 30 + 23 + 20 integration) bringing
  `services/api_key_service.py` from 0% to 88.24%.

### Changed
- **SAST CI is now HARD FAIL** (PR #30): `bandit` blocks on High+,
  `semgrep` blocks on ERROR, Trivy image-scan blocks on CRITICAL. HIGH
  Trivy findings remain advisory until Phase 8 worker-image refresh.

### Fixed
- `tasks.backup` lazy `scripts/` resolution so the module imports
  cleanly inside `/app/tasks/` containers (previous `parents[3]` lookup
  exceeded container path depth) (PR #29).
- `.semgrepignore` and inline justifications for 21 semgrep ERROR
  findings — none represent real risk; documented in
  `.semgrepignore` and per-line `# nosemgrep` comments (PR #30).
- Pin `setuptools<78` in SAST workflow so `pkg_resources` stays
  importable for opentelemetry transitively pulled in by semgrep (PR #30).

### Known issues / deferred
- Chore A2 (in-app notification center) — backend `/v1/notifications/*`
  not yet shipped; `/notifications` page deferred.
- Chore L2 — 13 webhook + api-key tests `xfail` pending fixture
  `webhook_secret` commit-on-update fix.
- Chore F + G (Demo SaaS Terraform + admin OAuth identity unlink UI)
  scheduled for next session.

## [2.0.0-rc.1] — 2026-05-09

First release candidate of TrustedOSS Portal v2.

### Added — Phase 1 ~ Phase 4 (foundation)
- PostgreSQL 17 schema with Alembic forward-only migrations (0001 → 0010).
- FastAPI + SQLAlchemy 2.0 backend with structlog JSON logging.
- React 18 + Vite + shadcn/ui frontend with TanStack Query + Zustand.
- Auth: bcrypt cost 12, JWT (access 30 min / refresh 7 d with rotation),
  rate-limited login.
- RBAC: Super Admin / Team Admin / Developer.
- Project, Component, Vulnerability, License, Obligation domains.
- WebSocket scan progress streaming.
- Admin Panel (7 screens): Users, Teams, DT Connector, Scan Queue, Disk,
  Audit Log, System Health — `require_super_admin_or_404` (existence-hide).
- Component approval workflow (`/approvals`) with state machine
  pending → under_review → approved / rejected and ETag optimistic
  concurrency.

### Added — Phase 3 backend
- SBOM Export: CycloneDX JSON / XML 1.5 + SPDX JSON / Tag-Value 2.3.
- Cross-project `/v1/scans` listing with team-scope clamp.

### Added — Phase 5 (CI / CD)
- API Keys: scoped (org / team / project), `Authorization: Bearer tos_...`
  middleware, bcrypt-hashed storage, soft-delete revocation.
- GitHub & GitLab webhook receivers with HMAC / token verification and
  `webhook_deliveries(provider, delivery_id)` idempotency.
- Policy gate (`GET /v1/projects/{id}/gate-result`) — Critical CVE +
  forbidden license counts → `gate=pass|fail`.
- SCA PR-comment service (create-or-update via `<!-- trustedoss-sca-bot -->`
  marker, dry-run by default).
- Composite GitHub Action `trustedoss/scan-action` (5-step flow:
  trigger → poll → gate → comment → apply verdict).
- GitLab CI template + Jenkinsfile example.

### Added — Phase 6 (operations)
- Notifications module: SMTP email (aiosmtplib), Slack + MS Teams webhooks,
  Celery autoretry with exponential backoff (max 5).
- Forgot / reset password (CWE-204 uniform 204, 1-hour single-use tokens).
- Disk hard-limit guard — new scans 503 when workspace ≥ `DISK_HARD_LIMIT_PCT`
  (default 95%).
- React Error Boundary at the app root.

### Added — Phase 7 (deployment)
- `scripts/install.sh`, `upgrade.sh`, `backup.sh`, `restore.sh` —
  interactive wizard, automatic backup, manifest-validated restore.
- Production `docker-compose.yml` with Traefik v3.2 + Let's Encrypt HTTP-01,
  pinned images, restart policies, healthchecks, volumes.
- `apps/backend/scripts/create_super_admin.py` — env-piped credentials,
  idempotent.
- Docusaurus v3.6 documentation site (`docs-site/`) with EN/KO i18n
  parity and GitHub Pages deploy workflow.
- `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`.

### Added — Phase 8 (advanced)
- OAuth (GitHub + Google) with personal-team auto-provisioning,
  signed-state CSRF protection (5-min JWT), `oauth_identities` table with
  `UNIQUE (provider, provider_user_id)` to block account takeover.

### Security
- All 4xx / 5xx responses use RFC 7807 `application/problem+json`.
- PII (email / name / token / secret) never logged in plaintext —
  `mask_pii()` helper enforced; sha256 fingerprints stored in audit
  diffs.
- Adversarial input parametrize on every parser surface (registry
  metadata, webhook URLs, SPDX expressions).
- All cron / Celery tasks idempotent.
- Forward-only Alembic migrations; rollback path is `scripts/restore.sh`.

### Migration
- 10 Alembic revisions land in this release. Run `alembic upgrade head`
  inside the backend container after pulling new images.

## Notes for v1 → v2 migrators

v1 was an internal tool tracked in a separate codebase. v2 is a clean
rewrite — there is **no automatic migration path** from v1 data because
the team / RBAC model and the scan pipeline were redesigned. v1 data
should be re-imported via the new `POST /v1/projects` API + a fresh
scan trigger.
