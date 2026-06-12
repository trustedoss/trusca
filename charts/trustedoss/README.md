# TrustedOSS Portal Helm chart

Production-complete Kubernetes deployment of [TrustedOSS Portal](https://github.com/trustedoss/trusca)
— enterprise open-source risk management (SCA: CVE, license compliance, SBOM).

- **Chart version:** see `Chart.yaml` `version`
- **App version:** `appVersion` (the published container image tag)
- **License:** Apache-2.0

## What this chart deploys

| Workload | Kind | Notes |
|---|---|---|
| backend | Deployment | FastAPI API. `AUTO_MIGRATE=false` — migrations are run by the Job. |
| worker | Deployment (+ optional HPA) | Celery worker (cdxgen / scancode / Trivy). |
| beat | Deployment (replicas: 1) | Celery scheduler — singleton. |
| frontend | Deployment | React SPA on nginx (`:8080`). |
| postgres | StatefulSet | **Optional bundle** (`postgres.bundled`). |
| redis | Deployment | **Optional bundle** (`redis.bundled`). |
| migrate | Job (pre-install/pre-upgrade hook) | `alembic upgrade head` as the owner role. |
| ingress | Ingress | cert-manager TLS; API + SPA routing. |

Vulnerability matching runs from the worker via `trivy sbom` against the
locally-cached Trivy DB — no external engine. Air-gapped sites override the
upstream OCI registry with `env.trivy.dbRepository`. See
[ADR-0001 — Dependency-Track removal](https://github.com/trustedoss/trusca/blob/main/docs/decisions/0001-replace-dt-with-trivy.md).

## Quick start (bundled datastores, evaluation)

```bash
helm install trustedoss oci://ghcr.io/trustedoss/charts/trustedoss \
  --version <chart-version> \
  --namespace trustedoss --create-namespace \
  --set env.secret.secretKey="$(openssl rand -hex 32)" \
  --set postgres.auth.password="$(openssl rand -hex 24)" \
  --set ingress.host=trustedoss.example.com \
  --set env.corsAllowedOrigins=https://trustedoss.example.com
```

Prerequisites for the defaults: cert-manager installed with a `ClusterIssuer`
named `letsencrypt-prod` (override via `ingress.annotations`), an ingress
controller (default class `nginx`), and a `ReadWriteMany` StorageClass for the
shared scan workspace on multi-node clusters (`workspace.persistence.storageClassName`).

## Production (external managed datastores — recommended)

Prefer Cloud SQL / RDS + Memorystore / ElastiCache over the in-cluster bundles:

```yaml
postgres:
  bundled: false
redis:
  bundled: false
env:
  database:
    url: postgresql+asyncpg://app:***@cloudsql-proxy:5432/trustedoss
    ownerUrl: postgresql+asyncpg://owner:***@cloudsql-proxy:5432/trustedoss  # if role-separated
  redis:
    url: redis://memorystore:6379/0
  secret:
    existingSecret: trustedoss-prod-secrets   # carries DATABASE_URL_APP, DATABASE_URL_OWNER, REDIS_URL, SECRET_KEY
  corsAllowedOrigins: https://trustedoss.example.com
ingress:
  host: trustedoss.example.com
```

When `env.secret.existingSecret` is set, the chart renders **no** Secret; the
referenced Secret **must** carry all four keys: `DATABASE_URL_APP`,
`DATABASE_URL_OWNER`, `REDIS_URL`, `SECRET_KEY`.

## Migrations (B3 design)

A Helm `pre-install` + `pre-upgrade` hook Job runs `alembic upgrade head` **once**
as the **owner** DB role (`DATABASE_URL_OWNER`). The application pods run with
`AUTO_MIGRATE=false`, so the Job is the sole migrator; the Postgres advisory lock
in `alembic/env.py` is only a safety net. Migrations are forward-only — the Job
never downgrades. Backend pods stay `NotReady` (`/health/ready` → 503) until the
schema is at HEAD, so traffic only reaches a migrated schema.

Hook ordering (bundled): Secrets (`-20`) → Postgres Service/StatefulSet (`-10`) →
migration Job (`-5`). The Job's init container waits for Postgres to accept
connections before alembic runs.

## Values reference

### Images

| Key | Default | Description |
|---|---|---|
| `image.backendRepository` | `ghcr.io/trustedoss/trusca-backend` | API image. |
| `image.workerRepository` | `ghcr.io/trustedoss/trusca-backend-worker` | Worker/beat/migrate image (ships alembic). |
| `image.frontendRepository` | `ghcr.io/trustedoss/trusca-frontend` | SPA image. |
| `image.tag` | `0.11.0` | Tag for all three (lock-step with `appVersion`). Never `:latest`. |
| `image.pullPolicy` | `IfNotPresent` | |
| `imagePullSecrets` | `[]` | Private-registry pull secrets. |

### Application env

| Key | Default | Description |
|---|---|---|
| `env.appEnv` | `prod` | `APP_ENV`. |
| `env.logLevel` | `INFO` | `LOG_LEVEL`. |
| `env.corsAllowedOrigins` | `""` | `CORS_ALLOWED_ORIGINS` — **must** enumerate origins in prod (no wildcard). |
| `env.accessTokenExpireMinutes` | `30` | JWT access TTL. |
| `env.refreshTokenExpireDays` | `7` | JWT refresh TTL. |
| `env.database.url` | `""` | External runtime (app) DSN. Used when `postgres.bundled=false`. |
| `env.database.ownerUrl` | `""` | External owner/DDL DSN (migration Job). Falls back to `url`. |
| `env.redis.url` | `""` | External `REDIS_URL`. Used when `redis.bundled=false`. |
| `env.secret.existingSecret` | `""` | Pre-created Secret with all four keys; disables the chart Secret. |
| `env.secret.secretKey` | `""` | `SECRET_KEY` (>=32 chars). Required unless `existingSecret`. |
| `env.trivy.dbRepository` | `ghcr.io/aquasecurity/trivy-db` | `TRIVY_DB_REPOSITORY` — OCI registry the worker pulls the cached DB from. Override for an air-gapped mirror. |
| `env.trivy.dbRefreshHours` | `168` | `TRIVY_DB_REFRESH_HOURS` — beat cadence for the DB refresh task. |
| `env.trivy.dbCacheDir` | `/var/lib/trivy` | `TRIVY_DB_CACHE_DIR` — in-container path the cached DB lands at. |
| `env.trivy.timeoutSeconds` | `300` | `TRIVY_TIMEOUT_SECONDS` — per-invocation wall clock for `trivy sbom`. |
| `env.dbPool.*` | see `values.yaml` | Async + sync connection-pool sizing (B1). |
| `env.scan.*` | see `values.yaml` | Scan rate limit / concurrency cap / time limits (B1+A1). |
| `env.scancode.*` | see `values.yaml` | scancode license-detection guards (A2). |

### Workspace (shared scan volume)

| Key | Default | Description |
|---|---|---|
| `workspace.mountPath` | `/workspace` | `WORKSPACE_HOST_PATH`; mounted by backend + worker. |
| `workspace.persistence.enabled` | `true` | Provision a PVC. `false` → per-pod `emptyDir` (single-node only). |
| `workspace.persistence.accessMode` | `ReadWriteMany` | RWX is required on multi-node clusters. |
| `workspace.persistence.size` | `20Gi` | |
| `workspace.persistence.storageClassName` | `""` | Set an RWX class (nfs/efs/filestore/longhorn). |
| `workspace.persistence.existingClaim` | `""` | Use a pre-created PVC. |

### Bundled PostgreSQL (`postgres.*`)

| Key | Default | Description |
|---|---|---|
| `postgres.bundled` | `true` | Run Postgres in-cluster. `false` → use `env.database.*`. |
| `postgres.image.tag` | `17.2-alpine` | Pinned (CLAUDE.md #9). |
| `postgres.auth.username` / `password` / `database` | `trustedoss` / `""` / `trustedoss` | Owner role. `password` required when bundled. |
| `postgres.auth.roleSeparation` | `false` | L1 split: DML-only app role + owner role for DDL. |
| `postgres.auth.appUsername` / `appPassword` | `trustedoss_app` / `""` | Runtime role (required when role-separated). |
| `postgres.service.port` | `5432` | |
| `postgres.persistence.*` | enabled, `10Gi`, RWO | StatefulSet PVC. |
| `postgres.resources` | see `values.yaml` | |

### Bundled Redis (`redis.*`)

| Key | Default | Description |
|---|---|---|
| `redis.bundled` | `true` | Run Redis in-cluster. `false` → use `env.redis.url`. |
| `redis.image.tag` | `7.4-alpine` | Pinned (CLAUDE.md #9). |
| `redis.service.port` | `6379` | |
| `redis.persistence.enabled` | `false` | Broker queue is transient; enable for at-least-once across restarts. |
| `redis.resources` | see `values.yaml` | |

### Migration Job (`migrationJob.*`)

| Key | Default | Description |
|---|---|---|
| `migrationJob.enabled` | `true` | Run the pre-install/pre-upgrade alembic Job. |
| `migrationJob.backoffLimit` | `3` | Retries (alembic upgrade head is idempotent). |
| `migrationJob.activeDeadlineSeconds` | `1800` | Ceiling so a wedged migration cannot block a release. |
| `migrationJob.ttlSecondsAfterFinished` | `600` | Job GC TTL. |
| `migrationJob.resources` | see `values.yaml` | |

### Workloads

| Key | Default | Description |
|---|---|---|
| `backend.replicaCount` | `2` | |
| `backend.port` | `8000` | |
| `backend.healthPath` / `readyPath` | `/health` / `/health/ready` | Liveness / readiness (schema-gated). |
| `worker.replicaCount` | `2` | Prefer scaling pods over `concurrency`. |
| `worker.concurrency` | `2` | Prefork slots per pod. |
| `worker.autoscaling.enabled` | `false` | Optional HPA (off by default). |
| `beat.resources` | see `values.yaml` | Singleton scheduler. |
| `frontend.replicaCount` | `2` | |
| `frontend.port` / `healthPath` | `8080` / `/healthz` | |
| `*.resources` | see `values.yaml` | `requests` + `limits` set on every container. |

### Ingress

| Key | Default | Description |
|---|---|---|
| `ingress.enabled` | `true` | |
| `ingress.className` | `nginx` | |
| `ingress.host` | `""` | **Required** when enabled. |
| `ingress.annotations` | cert-manager + nginx timeouts | `cert-manager.io/cluster-issuer` drives TLS issuance. |
| `ingress.tls.enabled` | `true` | |
| `ingress.tls.secretName` | `""` | Defaults to `<fullname>-tls`. |

### Scheduling

`nodeSelector`, `tolerations`, `affinity`, `podAnnotations`, `serviceAccount.create`,
`serviceAccount.annotations` apply to the workloads.

## Verify locally

```bash
helm lint charts/trustedoss \
  --set env.secret.secretKey=x..32.. --set postgres.auth.password=pw --set ingress.host=h.example.com
helm template trustedoss charts/trustedoss \
  --set env.secret.secretKey=x..32.. --set postgres.auth.password=pw --set ingress.host=h.example.com
```
