---
id: architecture
title: Architecture
description: TrustedOSS Portal architecture — services, data flow, scan pipeline, ORT rules, DT integration, and operational primitives.
sidebar_label: Architecture
sidebar_position: 1
---

# Architecture

This page explains how TrustedOSS Portal is wired up under the hood. It is the place to start if you want to extend the portal, integrate it into an existing platform, or evaluate it against an internal architecture review.

:::note Audience
Architects, platform engineers, and security reviewers. Familiarity with FastAPI, PostgreSQL, Celery, and Docker.
:::

## Services

The production stack runs seven container services (plus an optional eighth — Dependency-Track):

| Service | Image | Role |
|---|---|---|
| `traefik` | `traefik:v3.2.1` | Edge proxy. TLS termination via Let's Encrypt HTTP-01. HTTP→HTTPS redirect. |
| `postgres` | `postgres:17.2-alpine` | Primary store. All persistent state. |
| `redis` | `redis:7.4-alpine` | Celery broker + result backend. WebSocket pub/sub. |
| `backend` | `trustedoss/backend:<tag>` | FastAPI + uvicorn (4 workers). Reachable via Traefik on `/api`, `/health`, `/metrics`. |
| `worker` | `trustedoss/backend-worker:<tag>` | Celery worker with `cdxgen`, ORT, Trivy, JRE bundled. |
| `beat` | `trustedoss/backend-worker:<tag>` | Celery Beat scheduler. DT heartbeat (60 s), DT resync (1 h), orphan cleanup (6 h), backup (daily). |
| `frontend` | `trustedoss/frontend:<tag>` | nginx serving the Vite build. Reachable via Traefik on `/`. |
| `dt` (overlay) | `dependencytrack/apiserver:4.13.2` | Optional bundled Dependency-Track. Brought up via `docker-compose.dt.yml`. |

Image tags are pinned (`CLAUDE.md` rule #9 — never `:latest`).

## Network

```
                       :80 / :443
                          │
                       ┌──────────┐
                       │ Traefik  │  TLS termination, HTTP→HTTPS
                       └────┬─────┘
                            │ trustedoss network (bridge)
              ┌─────────────┼─────────────┐
              ↓             ↓             ↓
       ┌──────────┐  ┌──────────┐  ┌──────────┐
       │ frontend │  │ backend  │  │ DT (opt) │
       └──────────┘  └────┬─────┘  └──────────┘
                          │
            ┌─────────────┼─────────────┬──────────┐
            ↓             ↓             ↓          ↓
      ┌──────────┐  ┌──────────┐  ┌──────────┐ ┌──────┐
      │ postgres │  │  redis   │  │  worker  │ │ beat │
      └──────────┘  └──────────┘  └──────────┘ └──────┘
                            └──── shared `workspace` volume ────┘
```

Only `traefik` exposes ports to the host (`80`, `443`). Every other service is reachable inside the compose network only.

## Data layout

PostgreSQL is the single source of truth. Significant tables:

| Table | Purpose |
|---|---|
| `users`, `teams`, `team_memberships` | Identity + RBAC. |
| `api_keys` | Service-account credentials (bcrypt-hashed). |
| `projects` | One row per project. Owns scans, components, findings. |
| `scans` | Scan lifecycle records (queued → terminal). |
| `components`, `component_licenses` | Per-scan SBOM rows + license attribution. |
| `vuln_findings` | CVEs with VEX state + justification. |
| `vuln_cache` | DT-mirror cache for offline / breaker-OPEN reads. |
| `obligations`, `obligation_kinds` | License obligations per component. |
| `approvals` | Conditional-license approval workflow. |
| `audit_log` | Append-only write history. CHECK-constrained immutable. |
| `dt_health` | DT heartbeat results (last 24 h). |
| `webhook_deliveries` | `(source, delivery_id)` for idempotency. |
| `notifications` | Outbound notification log + dedup keys. |
| `backups` | Backup manifest history (read-only by application). |

Migrations are forward-only Alembic. Schema and data migrations live in separate revisions.

## Scan pipeline

A scan is a Celery task chain. Source scan stages:

```
1. bootstrapping            (workspace setup, locks the per-project lock)
2. fetching_source          (git clone / fetch / checkout)
3. detecting_components     (cdxgen → CycloneDX SBOM)
4. analyzing_licenses       (ORT consumes the SBOM, emits findings + obligations)
5. resolving_vulnerabilities (DT correlation OR cache fallback when breaker is OPEN)
6. persisting               (write to PostgreSQL in one transaction per scan)
```

Container scan stages:

```
1. bootstrapping
2. fetching_image           (skopeo pull or worker-cache hit)
3. trivy                    (OS-package CVE detection)
4. persisting
```

Stage transitions emit WebSocket events (`scan.<id>.progress`) so the UI updates in real time. Completion fires the appropriate notification triggers.

## ORT rules {#ort-rules}

License classification is rule-driven. The rules live in `ort/rules.kts` and are mounted read-only into the worker:

```kotlin
// Excerpt — see ort/rules.kts for the canonical version.
val forbidden = setOf(
    "AGPL-3.0-only", "AGPL-3.0-or-later",
    "GPL-2.0-only",  "GPL-2.0-or-later",
    "GPL-3.0-only",  "GPL-3.0-or-later",
    "SSPL-1.0",      "BUSL-1.1",
)

val conditional = setOf(
    "LGPL-2.1-only", "LGPL-2.1-or-later",
    "LGPL-3.0-only", "LGPL-3.0-or-later",
    "MPL-2.0", "EPL-1.0", "EPL-2.0", "CDDL-1.0",
)

// Allowed = anything else that's a recognized SPDX identifier; unknown
// expressions surface as `Unknown` and need human review.
```

Editing the rules is supported. After editing:

1. Restart the worker (`docker-compose restart worker beat`).
2. Re-scan affected projects to apply the new classification.

The portal does not auto-re-classify historical scans — the historical record is preserved with the rules that were in effect at scan time.

## Dependency-Track integration {#dependency-track}

The DT connector is more than an HTTP client. It adds:

- **Health monitor** (60 s heartbeat) — surfaces DT state on `/admin/dt`.
- **Circuit breaker** (CLOSED / HALF_OPEN / OPEN) — protects the worker from DT outages.
- **PostgreSQL vulnerability cache** — read fallback when the breaker is OPEN.
- **Orphan cleanup** (every 6 h) — reconciles the portal's project list against DT.
- **Forward-resync** (every 1 h) — re-correlates new CVEs against existing scans.

See [DT connector](../admin-guide/dt-connector.md) for operational detail.

## Authentication & sessions

- **Password** — bcrypt cost 12, NIST 800-63B banned-password list, ≥ 12 chars, no PII reuse.
- **Access token** — JWT, 30-minute lifetime, `HS256` signed (symmetric, `SECRET_KEY`), in-app memory only.
- **Refresh token** — 7-day lifetime, **rotation with reuse detection**. HttpOnly + Secure + SameSite=Lax cookie.
- **API keys** — `tos_<prefix>_<secret>` accepted via `Authorization: Bearer …`. bcrypt-hashed; full key shown once at creation.
- **CSRF posture** — the SPA uses bearer tokens (CSRF-immune by construction). The refresh cookie is HttpOnly + Secure + SameSite=Lax, which blocks the cross-site POST attack class without an explicit CSRF token. No separate CSRF token endpoint exists at v2.0.0.
- **Rate limit** — IP-keyed 5/minute on login and forgot-password, 429 with `Retry-After`. Per-address cooldown on password-reset emails.

## Authorization (RBAC)

`super_admin` (org), `team_admin` (per team), `developer` (per team). See [Users & teams → roles](../admin-guide/users-and-teams.md#roles).

A request's effective role is derived from `(user, target_team)`. Cross-team API calls are 403.

Admin endpoints additionally use the **404-existence-hide** pattern: a `developer` requesting an admin URL receives 404, not 403, so the URL surface is not enumerable.

## Errors — RFC 7807

Every 4xx / 5xx response uses `application/problem+json`:

```json
{
  "type":     "https://trustedoss.io/problems/last-super-admin",
  "title":    "Cannot demote the last super_admin",
  "status":   409,
  "detail":   "At least one super_admin must remain in the organization.",
  "instance": "/api/v1/admin/users/01H…/role"
}
```

Domain-specific extensions are `snake_case` and modelled in OpenAPI.

## Logging

`structlog` JSON lines, one event per line. The middleware seeds `request_id` (from `X-Request-ID` or a UUIDv7), `user_id`, `team_id`, and (in Celery) `task_id`. PII is masked through the `mask_pii` helper before log emission — passwords, tokens, API keys, and full email addresses never appear in logs.

## Observability

Out of the box:

- **Logs** — `docker-compose logs <service>` (structured JSON, `structlog`).
- **Health** — `/health` (backend), `/healthz` (frontend container), `/admin/health` UI for the operator dashboard.
- **Metrics** — basic service-health metrics are shipped at the Traefik level via its access log. A backend `/metrics` endpoint with a Prometheus exporter is on the roadmap (Phase 6).

OpenTelemetry tracing exporter and a bundled Jaeger overlay are on the roadmap (Phase 9) — there is no `docker-compose.tracing.yml` at v2.0.0.

## Deployment topologies

The reference deployment is a **single-host docker-compose** install. Two variations are supported:

- **Single-host with bundled DT** — add `docker-compose.dt.yml`. DT runs alongside.
- **Single-host with external DT** — leave DT off, point `DT_URL` at the external instance.

A **Helm chart** lands in Phase B (post-GA). It adds:

- Per-component HPA (worker scales by queue depth).
- StatefulSet for PostgreSQL with PVC.
- ServiceMonitor for the Prometheus operator.
- Ingress + cert-manager for TLS.

Multi-host docker-compose (e.g. workers on separate machines) is technically possible but not the supported path; use the Helm chart for that scale.

## Backup model

`pg_dump --clean --if-exists | gzip` for the database, `tar.gz` for the workspace, plus a manifest with the Alembic head. See [Backup & restore](../admin-guide/backup-and-restore.md) for the full procedure.

## Security posture summary

- Apache-2.0 licensed; SBOM published at GA.
- OWASP Top 10 reviewed in Phase 8 (`security-reviewer` agent + manual audit).
- Dependencies pinned via `pip-tools` (backend) and `package-lock.json` (frontend); `pip-audit` and `npm audit` run in CI.
- Trivy scan on every image build.
- TLS-only in production (Traefik enforces HTTPS).
- Secrets never logged; `mask_pii` enforced via test fixtures.

## See also

- [Environment variables](./env-variables.md)
- [API overview](./api-overview.md)
- [DT connector](../admin-guide/dt-connector.md)
- [Glossary](https://github.com/trustedoss/trustedoss-portal/blob/main/docs/glossary.md)
