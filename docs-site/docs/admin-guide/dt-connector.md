---
id: dt-connector
title: Dependency-Track connector
description: Operate the Dependency-Track integration — health monitor, circuit breaker, vulnerability cache, and orphan cleanup.
sidebar_label: DT connector
sidebar_position: 2
---

# Dependency-Track connector

[Dependency-Track](https://dependencytrack.org/) (DT) is the upstream vulnerability database the portal correlates SBOMs against. The connector adds reliability primitives the bare DT API does not provide: health monitoring, a circuit breaker, a PostgreSQL vulnerability cache, and orphan cleanup.

:::note Audience
`super_admin` operating the deployment. The screens described live under `/admin/dt`.
:::

## Why a connector?

DT has historically suffered three classes of operational pain:

1. **Slow startup** — DT can take 5–10 minutes to come back from a cold start while it rebuilds indexes. Calls during that window time out.
2. **Stale projects** — when projects are deleted in the portal but the connector did not flush DT, DT accumulates "ghost" projects that confuse future scans.
3. **Sync windows** — DT periodically refreshes its NVD / OSV mirrors, during which write operations are rejected.

The connector wraps every DT call with the layers below to keep the portal usable across all three cases.

## Operational layers

```
Portal API call → Circuit breaker → DT health probe (60s heartbeat)
                       │
              CLOSED ──┴── OPEN ──► PostgreSQL vulnerability cache
```

### 1. Health monitor

A Celery Beat task pings `${DT_URL}/api/version` every 60 seconds and writes the result to PostgreSQL (`dt_health` table). Three consecutive failures flip the state from `healthy` to `degraded`; the next failure flips it to `down`.

The dashboard at **/admin/dt** shows:

- Current state (`healthy` / `degraded` / `down`).
- Last successful probe timestamp.
- Last error message, when not `healthy`.
- Probe history (last 24h sparkline).

When the state hits `down`, the connector tries `docker restart dt` once and waits 90 seconds. If DT recovers, state goes back to `healthy`. If not, the **circuit breaker** opens.

### 2. Circuit breaker

The breaker is a three-state machine: `CLOSED` (normal), `HALF_OPEN` (probing), `OPEN` (rejecting).

- `CLOSED` — calls go through.
- `OPEN` — calls return cached data immediately. No DT round-trip.
- `HALF_OPEN` — once every 30 seconds while OPEN, the breaker lets one call through. Success → `CLOSED`. Failure → back to `OPEN`.

The current state is visible at **/admin/dt** and via `GET /api/v1/admin/dt/state`.

### 3. PostgreSQL vulnerability cache

Every successful DT response is mirrored into `vuln_cache` (project / component / cve triple, plus severity, summary, fix availability). The cache is the source of truth when the breaker is OPEN.

The cache is best-effort: it can lag DT by up to one hour (the resync interval). New CVEs that DT learned about during a portal-side outage will not appear until the next successful resync.

### 4. Orphan cleanup

Every 6 hours, a Celery Beat task lists DT projects and compares them against the portal's `projects` table:

- DT projects with no matching portal row are **orphans** — they get deleted from DT (with operator confirmation if `DT_ORPHAN_AUTODELETE=false`, default in prod).
- Portal projects with no DT counterpart are **missing** — they get auto-created on next scan.

The list of orphans is shown at **/admin/dt → Orphan projects** with a **Delete selected** button.

## First-time bootstrap (bundled DT)

If you bring up the bundled DT overlay (`docker-compose.dt.yml`), the API server starts with default credentials.

1. **Bring up the stack with the DT overlay:**

   ```bash
   docker-compose -f docker-compose.yml -f docker-compose.dt.yml up -d
   ```

2. **Open `http://localhost:8080`** (or the DT route on your reverse proxy).

3. **Sign in as `admin / admin`** and set a new password when prompted.

4. **Enable the eight OSV ecosystems:**

   Administration → Vulnerability Sources → enable each:
   - npm
   - Maven
   - PyPI
   - RubyGems
   - crates.io
   - Go
   - Packagist
   - NuGet

   The mirror sync runs in the background. Maven takes ~1 hour on a cold sync; the others finish in 5–15 minutes each.

5. **Create the API key:**

   Administration → Access Management → Teams → **Automation** → copy the API key.

6. **Wire into `.env`** (do not commit):

   ```bash
   DT_API_KEY=<the-key-you-just-copied>
   ```

   Restart the affected services:

   ```bash
   docker-compose -f docker-compose.yml -f docker-compose.dt.yml \
     restart backend worker beat
   ```

7. **Verify** — visit **/admin/dt**. The state should be `healthy` within 60 seconds. The orphan-projects list should be empty.

## Connecting to an external DT

If your organization runs DT centrally and you want the portal to point at it instead of the bundled instance:

1. Set `DT_URL` to the external URL in `.env`.
2. Set `DT_API_KEY` to a key issued by the external DT's Automation team.
3. Do **not** bring up `docker-compose.dt.yml` — leave the local DT services off.
4. Restart `backend`, `worker`, `beat`.

The connector behaves identically. The orphan-cleanup task will list any DT projects you do not own — keep it on a manual-confirm policy (`DT_ORPHAN_AUTODELETE=false`) to avoid clobbering other teams' projects.

## Manual probes

For runbook use:

```bash
# DT health (no auth)
curl -fsS https://trustedoss.example.com/api/v1/admin/dt/probe \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"

# Trigger an orphan-cleanup pass right now
curl -sS -X POST \
  https://trustedoss.example.com/api/v1/admin/dt/orphans/cleanup \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

Both endpoints require `super_admin`.

## Notifications {#notifications}

The five notification triggers are configured at **/notifications**:

| Trigger | Default |
|---|---|
| Scan finished | Off |
| Build gate failed | On |
| New CVE on existing project (re-detection) | On |
| Approval request | On (team admins) |
| Disk pressure (≥ 90%) | On (super-admins) |

Channels: email (SMTP), Slack webhook, MS Teams webhook. Configure the webhook URLs in `.env` (`SMTP_*`, `SLACK_WEBHOOK_URL`, `TEAMS_WEBHOOK_URL`).

## Troubleshooting

### `/admin/dt` shows `down` but DT is reachable from a browser

The portal's worker container reaches DT via the compose network, not via the public URL. Confirm:

```bash
docker-compose -f docker-compose.yml exec worker \
  curl -fsS http://dtrack-api:8080/api/version
```

If that fails, the network is misconfigured (different compose networks for backend vs DT). Re-bring-up with both files:

```bash
docker-compose -f docker-compose.yml -f docker-compose.dt.yml up -d
```

### Breaker stuck OPEN

The breaker only closes on a successful HALF_OPEN probe. If DT is flapping, the breaker will oscillate. You can force a reset:

```bash
curl -sS -X POST \
  https://trustedoss.example.com/api/v1/admin/dt/breaker/reset \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

Use sparingly — repeatedly forcing reset while DT is unhealthy will spam the worker with timeouts.

### Vulnerabilities not refreshing after DT comes back

The hourly resync task is the path that re-runs correlation against existing scans. Trigger it manually:

```bash
curl -sS -X POST \
  https://trustedoss.example.com/api/v1/admin/dt/resync \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

Resync is idempotent — running twice produces the same result.

### "Orphan list shows projects I don't recognize"

You are pointed at a shared external DT. Other teams may have created projects there. Set `DT_ORPHAN_AUTODELETE=false` (the default) and only delete orphans you own.

## See also

- [System health dashboard](./disk-and-health.md)
- [Backup & restore](./backup-and-restore.md)
- [Architecture — DT integration](../reference/architecture.md#dependency-track)
