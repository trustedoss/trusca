---
id: postgres-tuning
title: Postgres sizing and connection tuning
description: Size Postgres max_connections against TRUSCA's own pool knobs across the backend, worker, and beat processes, using the formula the backend enforces at boot.
sidebar_label: Postgres sizing and tuning
sidebar_position: 8.6
---

# Postgres sizing and connection tuning

:::note Audience
`super_admin` operators sizing Postgres for a production deployment, or diagnosing connection exhaustion on an existing one. Assumes familiarity with `.env` / Helm values and basic Postgres administration.
:::

TRUSCA does not run one connection pool against Postgres - it runs several, one per process, and every one of them is a separate SQLAlchemy engine with its own `pool_size` + `max_overflow`. Sizing Postgres correctly means adding all of them up, not tuning any single one in isolation. This page covers that arithmetic and where TRUSCA-side tuning ends and your own Postgres instance's tuning begins.

## Which processes hold a pool

| Process | Pool knobs | Multiplies by |
|---|---|---|
| Backend (FastAPI, async) | `DB_POOL_SIZE` (default 5) + `DB_MAX_OVERFLOW` (default 3) | uvicorn workers per container (`UVICORN_WORKERS`, default 4) × backend containers/pods |
| Celery worker (sync) | `DB_SYNC_POOL_SIZE` (default 3) + `DB_SYNC_MAX_OVERFLOW` (default 3) | worker containers/pods, summed across `worker-scan` and `worker-default` if you run the split queues |
| Celery beat (sync) | same `DB_SYNC_*` knobs as the worker | always 1 - beat is a singleton by design, never scaled |
| Migration job (`alembic upgrade head`) | none - runs with SQLAlchemy's `NullPool`, one connection at a time | not part of the pool formula; covered by the fixed admin allowance below |

Each uvicorn worker is a separate OS process with its own engine and its own pool, which is why the backend row multiplies by worker count *and* container count rather than just one of the two. A single backend container running the default 4 uvicorn workers already opens up to `4 x (5 + 3) = 32` connections on its own, before a second container or a Celery process enters the picture.

## The formula

```
backend_conns = backend_replicas x uvicorn_workers x (DB_POOL_SIZE + DB_MAX_OVERFLOW)
worker_conns  = worker_replicas  x (DB_SYNC_POOL_SIZE + DB_SYNC_MAX_OVERFLOW)
beat_conns    = 1                x (DB_SYNC_POOL_SIZE + DB_SYNC_MAX_OVERFLOW)

total = backend_conns + worker_conns + beat_conns + 5 (admin/migration headroom)

require: total <= Postgres max_connections
```

`worker_replicas` is the sum of every Celery worker container you run - `worker-scan` and `worker-default` both open the same per-process connection count (they share the one `DB_SYNC_*` config), so the formula treats them as one pool of replicas rather than tracking the two queues separately. The fixed 5-connection admin headroom covers the migration job's `NullPool` connection plus a `psql` session or two from an operator's shell; it is not itself a knob.

This is the same formula the backend's own boot-time check runs against Postgres' actual `SHOW max_connections`, and the same one the Helm chart's `NOTES.txt` computes in template arithmetic - a test cross-checks the two stay in agreement, so this page is not describing a separate, docs-only model.

## Worked examples

**Default production Compose** (1 backend container × 4 uvicorn workers, 1 `worker-scan` + 1 `worker-default` replica):

```
backend = 1 x 4 x (5 + 3) = 32
worker  = (1 + 1) x (3 + 3) = 12
beat    = 1 x (3 + 3) = 6
total   = 32 + 12 + 6 + 5 = 55   (fits Postgres' default max_connections=100)
```

**Default Helm chart** (2 backend pods × 4 uvicorn workers, `worker.scan.replicaCount=2` + `worker.default.replicaCount=1`):

```
backend = 2 x 4 x (5 + 3) = 64
worker  = (2 + 1) x (3 + 3) = 18
beat    = 1 x (3 + 3) = 6
total   = 64 + 18 + 6 + 5 = 93   (fits max_connections=100, little headroom left)
```

**Dev Compose** (1 backend container, no `--workers` flag, one shared `celery-worker` service - dev is not split into `worker-scan`/`worker-default`):

```
backend = 1 x 1 x (5 + 3) = 8
worker  = 1 x (3 + 3) = 6
beat    = 1 x (3 + 3) = 6
total   = 8 + 6 + 6 + 5 = 25   (fits max_connections=100 comfortably)
```

## Size your own deployment

1. Count your actual process shape: backend containers/pods, `UVICORN_WORKERS`, and the sum of every Celery worker container you run (both queues if you use the split).
2. Run the formula above with your `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` / `DB_SYNC_POOL_SIZE` / `DB_SYNC_MAX_OVERFLOW` (defaults if unset).
3. Compare `total` against your Postgres instance's `max_connections`. If it does not fit, either raise `max_connections` (roughly 10 MB of RAM per connection) or bring the fleet shape down before scaling further.
4. If you cannot read your own replica count from an env var (plain Compose has no such variable), set `CONN_BUDGET_BACKEND_REPLICAS` / `CONN_BUDGET_WORKER_REPLICAS` to match how you actually deploy, so the backend's own boot-time warning stays accurate instead of assuming its 1-container default. These two knobs only feed that estimate - they do not change how many containers run.

Each pool knob is independently clamped (200 for a pool size, 200 for overflow) so a single fat-fingered value cannot exhaust `max_connections` on its own; raising a knob past that ceiling is logged at WARNING and clamped down rather than applied.

## What TRUSCA tunes, and what it leaves to you

Neither the production `docker-compose.yml` nor the Helm chart's bundled Postgres applies any `shared_buffers` / `work_mem` / `effective_cache_size` tuning - both run Postgres with its own upstream defaults, sized only by the container resource limits (`docker-compose.yml`: 2 CPU / 2 GB; the chart's `postgres.resources`: 250m–2 CPU / 256Mi–2Gi). The one place a tuned `command:` exists in this repository is the **demo overlay** (`docker-compose.demo.yml`), which sets `shared_buffers=256MB`, `effective_cache_size=768MB`, and `max_connections=60` for a deliberately small 4 GB evaluation box - those numbers are sized to that overlay's own resource limits, not a general recommendation, and are not meant to be copied onto a production instance with different RAM.

Sizing Postgres' own memory parameters for your instance is standard Postgres administration, not something specific to this application: the common rule of thumb is `shared_buffers` around a quarter of available RAM and `effective_cache_size` around half to three-quarters of it, tuned from there against your own observed workload. Treat that as a starting point to benchmark, not a number this project asserts as correct for your deployment.

### Managed / external Postgres

The Helm chart's bundled Postgres is a single Pod with no replication - suitable for evaluation, not production HA (`postgres.bundled: true`, see [Install on Kubernetes with Helm](../installation/helm.md#self-hosted-ha)). For production, that guide recommends `postgres.bundled: false` against a managed instance (Cloud SQL, RDS) or a dedicated in-cluster operator (CloudNativePG, the Zalando operator). In that shape, memory tuning, storage IOPS, replication, and failover are the managed service's or the operator's job, not this chart's. What stays TRUSCA's job either way is the connection-budget math above: hand your DBA (or your managed-instance console) the `total` you calculated and ask for `max_connections` at least that high, with headroom for future replica growth.

## Verify it worked

- `docker-compose -f docker-compose.yml logs --tail=100 backend | grep connection_budget` - a clean boot logs nothing from this check; an over-budget deployment logs `connection_budget.over_max_connections` with every input value it computed from, so you can see exactly which term is too large.
- Connect to Postgres and confirm the ceiling you expect:

  ```sql
  SHOW max_connections;
  ```

- Under normal load, active + idle connections should sit well under that ceiling:

  ```sql
  SELECT count(*) FROM pg_stat_activity;
  ```

  A count that regularly sits near `max_connections` means the fleet has grown past what you last sized for - re-run the formula against your current replica counts.

## Troubleshooting

### Boot log shows `connection_budget.over_max_connections`

The logged fields (`backend_conns`, `worker_conns`, `beat_conns`, `total_with_headroom`, `max_connections`) show exactly which term pushed the total over. Either raise Postgres' `max_connections` or reduce the offending replica count / pool size before scaling further. This is a warning, not a boot failure - the backend still starts, because the estimate is based on the `CONN_BUDGET_*` hints an operator sets, not a live count, and a wrong hint should not take the deployment down.

### `QueuePool limit of size X overflow Y reached` at runtime

The application-level pool for one process is genuinely exhausted - every connection is checked out and the queue is waiting past `DB_POOL_TIMEOUT` (default 30s). Check whether Postgres itself is also near `max_connections` first (see Verify above); if there is headroom on the Postgres side, raise `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` for that process. If there is no headroom on the Postgres side, raising the pool only moves the exhaustion from the application queue to Postgres' own connection limit.

### Migration job fails to connect during a rolling upgrade

The migration job uses `NullPool` (one connection, not a pool), so it is rarely the exhausted side - but if the application fleet is already at its ceiling when the job starts, Postgres can still have nothing left to hand out. Scale down (or briefly pause) application replicas before a migration that lands on an already-saturated instance, rather than raising the admin headroom.

## See also

- [Environment variables - Database](../reference/env-variables.md#database) - `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_SYNC_POOL_SIZE`, `DB_SYNC_MAX_OVERFLOW`, and the composed-DSN alternative to `DATABASE_URL`.
- [Install on Kubernetes with Helm](../installation/helm.md#self-hosted-ha) - bundled vs. external Postgres, and the operator-based path for self-hosted HA.
- [Docker Compose - Scan capacity](../installation/docker-compose.md#scan-capacity-sizing-and-scaling) - sizing `worker-scan` replicas for scan throughput, which is the same replica count this page's `worker_conns` term uses.
- [Hardening](./hardening.md) - the L1 database role-separation model (`DATABASE_URL_OWNER` / `DATABASE_URL_APP`), which sits next to this page's sizing model but is about privilege, not connection count.
- [Backup and restore](./backup-and-restore.md)
