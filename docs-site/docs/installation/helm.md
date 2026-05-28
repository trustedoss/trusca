---
id: helm
title: Install on Kubernetes with Helm
description: Deploy TrustedOSS Portal on Kubernetes with the production-grade Helm chart — bundled or external PostgreSQL & Redis, Ingress TLS, and a migration Job.
sidebar_label: Helm / Kubernetes
sidebar_position: 3
---

# Install on Kubernetes with Helm

:::note Audience
Operators running Kubernetes who want to deploy TrustedOSS Portal with the
production-grade Helm chart. Assume `kubectl`, Helm 3, and basic cluster
administration (Ingress, StorageClasses, cert-manager) proficiency. If you run a
single host, the [Docker Compose install](./docker-compose.md) is simpler.
:::

The Helm chart (`charts/trustedoss`, chart version **0.3.0**) deploys the full
portal: the FastAPI backend, the Celery worker and beat scheduler, the React
frontend, an Ingress with TLS, and a database migration Job. PostgreSQL and
Redis can either be bundled in-cluster (for evaluation) or pointed at external
managed datastores (recommended for production).

:::info Vulnerability matching ships in-chart (v0.10.0+)
The worker pod ships with the Trivy DB and downloads / refreshes it from `ghcr.io/aquasecurity/trivy-db` (or a mirror via `env.trivy.dbRepository`). No external vulnerability engine is required. See [Vulnerability data (Trivy DB)](../admin-guide/vulnerability-data.md).
:::

:::caution Chart 0.3.0 dropped Dependency-Track
Prior chart versions (≤ 0.2.x) optionally deployed Dependency-Track as a sub-chart and exposed `env.dt.*` values. Chart 0.3.0 removes that path — see [v0.10.0 release notes — Helm chart 0.3.0](../release-notes/v0.10.0.md#helm-chart-030) before upgrading from 0.2.x.
:::

## What the chart deploys

| Workload | Kind | Notes |
|---|---|---|
| backend | Deployment | FastAPI API. `AUTO_MIGRATE=false` — migrations run in the Job. |
| worker | Deployment (+ optional HPA) | Celery worker (cdxgen / scancode / Trivy). |
| beat | Deployment (replicas: 1) | Celery scheduler — singleton. |
| frontend | Deployment | React SPA on nginx (`:8080`). |
| postgres | StatefulSet | Optional bundle (`postgres.bundled`). |
| redis | Deployment | Optional bundle (`redis.bundled`). |
| migrate | Job (pre-install / pre-upgrade hook) | `alembic upgrade head` as the owner role. |
| ingress | Ingress | cert-manager TLS; API + SPA routing. |

## Prerequisites

- A Kubernetes cluster and a `kubectl` context with permission to create the
  namespace and workloads.
- Helm 3.
- An **ingress controller** (the chart defaults to class `nginx`).
- **cert-manager** with a `ClusterIssuer` named `letsencrypt-prod` for the
  default TLS configuration (override via `ingress.annotations`).
- On multi-node clusters, a **`ReadWriteMany` StorageClass** for the shared scan
  workspace (`workspace.persistence.storageClassName`). A single-node cluster
  can use the per-pod `emptyDir` fallback.

## Quick start (bundled datastores, evaluation)

This runs PostgreSQL and Redis in-cluster — fast to stand up, but **not**
recommended for production data.

```bash
helm install trustedoss oci://ghcr.io/trustedoss/charts/trustedoss \
  --version 0.3.0 \
  --namespace trustedoss --create-namespace \
  --set env.secret.secretKey="$(openssl rand -hex 32)" \
  --set postgres.auth.password="$(openssl rand -hex 24)" \
  --set ingress.host=trustedoss.example.com \
  --set env.corsAllowedOrigins=https://trustedoss.example.com
```

Replace `trustedoss.example.com` with your own hostname, and make sure DNS for
that host points at your ingress controller.

:::caution Bundled datastores are for evaluation
The in-cluster PostgreSQL and Redis have modest defaults and a single replica.
For anything beyond a trial, use external managed datastores (below).
:::

## Production (external managed datastores — recommended)

Prefer Cloud SQL / RDS for PostgreSQL and Memorystore / ElastiCache for Redis
over the in-cluster bundles. Provide a values file:

```yaml
# values.prod.yaml
postgres:
  bundled: false
redis:
  bundled: false
env:
  database:
    url: postgresql+asyncpg://app:***@cloudsql-proxy:5432/trustedoss
    # if you separate the DDL/owner role from the runtime role:
    ownerUrl: postgresql+asyncpg://owner:***@cloudsql-proxy:5432/trustedoss
  redis:
    url: redis://memorystore:6379/0
  secret:
    # pre-created Secret carrying all four keys (see below)
    existingSecret: trustedoss-prod-secrets
  corsAllowedOrigins: https://trustedoss.example.com
ingress:
  host: trustedoss.example.com
```

Then install:

```bash
helm install trustedoss oci://ghcr.io/trustedoss/charts/trustedoss \
  --version 0.3.0 \
  --namespace trustedoss --create-namespace \
  -f values.prod.yaml
```

:::warning Secret contents are mandatory
When `env.secret.existingSecret` is set, the chart renders **no** Secret of its
own. The referenced Secret **must** carry all four keys, or the pods will not
start:

- `DATABASE_URL_APP`
- `DATABASE_URL_OWNER`
- `REDIS_URL`
- `SECRET_KEY` (at least 32 characters)
:::

:::note CORS in production
`env.corsAllowedOrigins` **must** enumerate the exact origins that serve the SPA
— no wildcard in production. List every scheme + host that browsers will use.
:::

## How migrations run

A Helm `pre-install` + `pre-upgrade` hook Job runs `alembic upgrade head` **once**
as the **owner** DB role (`DATABASE_URL_OWNER`). The application pods run with
`AUTO_MIGRATE=false`, so the Job is the sole migrator.

Backend pods stay `NotReady` (`/health/ready` returns `503`) until the schema is
at HEAD, so traffic only ever reaches a migrated schema. Migrations are
forward-only — the Job never downgrades. Hook ordering for the bundled case is:
Secrets → Postgres Service / StatefulSet → migration Job, and the Job's init
container waits for Postgres to accept connections before alembic runs.

## Upgrade

```bash
helm upgrade trustedoss oci://ghcr.io/trustedoss/charts/trustedoss \
  --version <new-chart-version> \
  --namespace trustedoss \
  -f values.prod.yaml
```

The pre-upgrade migration Job applies any new schema before the new pods roll
out. Because migrations are forward-only, take a database backup before
upgrading — see [Backup & restore](../admin-guide/backup-and-restore.md).

## Key values

The full table lives in the [chart README](https://github.com/trustedoss/trustedoss-portal/blob/main/charts/trustedoss/README.md).
The values you most often set:

| Key | Default | Purpose |
|---|---|---|
| `image.tag` | `2.4.0` | Image tag for backend / worker / frontend (never `:latest`). |
| `ingress.host` | `""` | **Required.** Public hostname. |
| `env.corsAllowedOrigins` | `""` | **Required in prod.** Allowed browser origins (no wildcard). |
| `env.secret.secretKey` | `""` | `SECRET_KEY` (≥32 chars). Required unless `existingSecret`. |
| `env.secret.existingSecret` | `""` | Pre-created Secret with all four keys; disables the chart Secret. |
| `postgres.bundled` | `true` | `false` → use `env.database.*` (external). |
| `redis.bundled` | `true` | `false` → use `env.redis.url` (external). |
| `env.trivy.dbRepository` | `ghcr.io/aquasecurity/trivy-db` | Override for an air-gapped internal mirror — see [Air-gapped operation](../admin-guide/vulnerability-data.md#air-gapped). |
| `env.trivy.dbRefreshHours` | `168` | Weekly Trivy DB refresh; lower for fresher feeds. |
| `worker.trivyDbPersistence.enabled` | `true` | Mount a PVC at `/var/lib/trivy` so the worker doesn't re-download on every restart. |
| `workspace.persistence.storageClassName` | `""` | RWX class for the shared scan volume on multi-node clusters. |
| `worker.replicaCount` | `2` | Prefer scaling worker pods over per-pod `concurrency`. |

## Verify it worked

1. The migration Job completed:

   ```bash
   kubectl -n trustedoss get jobs
   # the trustedoss migrate Job should show COMPLETIONS 1/1
   ```

2. All pods are `Running` and backend pods are `Ready`:

   ```bash
   kubectl -n trustedoss get pods
   # backend pods Ready means /health/ready returned 200 (schema at HEAD)
   ```

3. The readiness probe passes from inside the cluster:

   ```bash
   kubectl -n trustedoss exec deploy/trustedoss-backend -- \
     curl -fsS http://localhost:8000/health/ready
   # → {"status":"ready"}
   ```

4. The Ingress has an address and a valid certificate, then open
   `https://<ingress.host>/` in a browser and sign in.

## Troubleshooting

- **Backend pods stuck `NotReady`.** `/health/ready` returns `503` until the
  schema is at HEAD. Check the migration Job logs:

  ```bash
  kubectl -n trustedoss logs job/trustedoss-migrate
  ```

  A failed Job usually means the owner DSN (`DATABASE_URL_OWNER`) lacks DDL
  privileges or cannot reach the database.

- **Pods `CreateContainerConfigError` with an existing Secret.** The referenced
  Secret is missing one of the four required keys. Confirm:

  ```bash
  kubectl -n trustedoss get secret trustedoss-prod-secrets -o jsonpath='{.data}' | tr ',' '\n'
  # expect DATABASE_URL_APP, DATABASE_URL_OWNER, REDIS_URL, SECRET_KEY
  ```

- **Scans fail on multi-node clusters.** The backend and worker share the scan
  workspace. Without a `ReadWriteMany` StorageClass the worker cannot read what
  the backend wrote. Set `workspace.persistence.storageClassName` to an RWX
  class (nfs / efs / filestore / longhorn).

- **TLS certificate never issues.** The default annotations expect a
  cert-manager `ClusterIssuer` named `letsencrypt-prod`. Inspect the
  Certificate:

  ```bash
  kubectl -n trustedoss describe certificate
  ```

If you hit a chart bug, open an issue using the
[bug report template](https://github.com/trustedoss/trustedoss-portal/issues/new/choose).

## See also

- [Install with Docker Compose](./docker-compose.md) — single-host install
- [Upgrade](./upgrade.md) — Docker Compose upgrade path
- [Backup & restore](../admin-guide/backup-and-restore.md) — back up before upgrading
- [Environment variables](../reference/env-variables.md) — every setting the chart maps
- [Architecture](../reference/architecture.md) — services, Trivy DB lifecycle, and the migration model
- [Vulnerability data (Trivy DB)](../admin-guide/vulnerability-data.md) — air-gapped operation and DB refresh
- [v0.10.0 release notes](../release-notes/v0.10.0.md) — chart 0.3.0 breaking changes
