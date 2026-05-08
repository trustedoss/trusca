# Changelog

All notable changes to TrustedOSS Portal v2 are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- SAST CI workflow (`bandit` + `semgrep`) — advisory mode in this release;
  flips to HARD FAIL on High+ findings in a follow-up.

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
