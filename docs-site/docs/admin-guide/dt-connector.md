---
id: dt-connector
title: Dependency-Track connector
description: Operate the Dependency-Track integration — health monitor, circuit breaker, vulnerability cache, and orphan cleanup.
sidebar_label: DT connector
sidebar_position: 2
---

# Dependency-Track connector

[Dependency-Track](https://dependencytrack.org/) (DT) is the upstream vulnerability database the portal correlates SBOMs against. The connector adds reliability primitives the bare DT API does not provide: health monitoring, a circuit breaker, a PostgreSQL vulnerability cache, and orphan cleanup.

The `/admin/dt` page surfaces the connector's runtime view — health status card, circuit-breaker badge, and refresh action:

![Admin DT connector — status card with breaker badge + refresh action](/img/screenshots/admin-dt-status.png)

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

A Celery Beat task pings `${DT_URL}/api/version` every 60 seconds. Three consecutive failures flip the state from `healthy` to `degraded`; the next failure flips it to `down`. DT probe outcomes are emitted as `dt_health_check` structlog events and consumed by the dashboard endpoint and the breaker; consult Loki / journald for the event stream (there is no `dt_health` SQL table at v2.0.0).

The dashboard at **/admin/dt** shows:

- Current state (`healthy` / `degraded` / `down`).
- Last successful probe timestamp.
- Last error message, when not `healthy`.
- Probe history (last 24h sparkline).

When the state hits `down`, the **circuit breaker** opens and subsequent calls return the cached vulnerabilities until the breaker probes successfully again. Automatic container restart on `down` is on the roadmap; until then, an operator restarts DT with `docker-compose restart dt` if the health probe stays red beyond a few minutes.

### 2. Circuit breaker

The breaker is a three-state machine: `CLOSED` (normal), `HALF_OPEN` (probing), `OPEN` (rejecting).

- `CLOSED` — calls go through.
- `OPEN` — calls return cached data immediately. No DT round-trip.
- `HALF_OPEN` — once every 30 seconds while OPEN, the breaker lets one call through. Success → `CLOSED`. Failure → back to `OPEN`.

The current state is visible at **/admin/dt** and via `GET /v1/admin/dt/status` (response schema `DTStatusOut`).

### 3. PostgreSQL vulnerability cache

Vulnerability data lives directly in the `vulnerabilities` and `vulnerability_findings` tables — the portal mirrors every successful DT response into these tables (CVE metadata + per-scan findings with severity, summary, fix availability). They are the source of truth when the breaker is OPEN; there is no separate `vuln_cache` table at v2.0.0.

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
# Force an immediate DT health probe (super_admin only)
curl -fsS -X POST \
  https://trustedoss.example.com/v1/admin/dt/health-check \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"

# Trigger an orphan-cleanup pass right now
curl -sS -X POST \
  https://trustedoss.example.com/v1/admin/dt/orphans/cleanup \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

Both endpoints require `super_admin`.

## Notifications {#notifications}

The notification triggers are configured at **/notifications**. The `kind` enum at v2.0.0 has six values, mirrored in `apps/backend/models/notification.py` and `apps/backend/schemas/notification.py`:

| `kind` | Trigger | Default |
|---|---|---|
| `scan_completed` | Scan finished successfully | Off |
| `scan_failed` | Scan ended in `failed` state | On (team admins) |
| `cve_detected` | New CVE re-detected on an existing project | On |
| `license_violation` | Forbidden / conditional license observed on a scan | On (team admins) |
| `approval_pending` | Component pending approval awaiting decision | On (team admins) |
| `policy_gate_failed` | Build gate (`POST /v1/scans/{id}/policy-gate`) returned `block` | On |

Channels: email (SMTP), Slack webhook, MS Teams webhook. Configure the webhook URLs in `.env` (`SMTP_*`, `SLACK_WEBHOOK_URL`, `TEAMS_WEBHOOK_URL`).

A `disk_pressure` notification kind is **not** in the enum at v2.0.0; disk pressure surfaces only on `/admin/disk`. See [disk-and-health](./disk-and-health.md).

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

The breaker only closes on a successful HALF_OPEN probe. If DT is flapping, the breaker will oscillate. The pragmatic recovery is:

1. Restart the DT container so the next probe sees a clean DT: `docker-compose restart dt`.
2. Force an immediate health probe via `POST /v1/admin/dt/health-check` (see [Manual probes](#manual-probes)). One green probe flips HALF_OPEN to CLOSED.
3. As a last resort, force the breaker back to CLOSED via the operator endpoint — see [Reset breaker](#reset-breaker-last-resort-recovery) below.

#### Reset breaker (last-resort recovery)

`POST /v1/admin/dt/breaker/reset` (super_admin only) forces the breaker to CLOSED and clears the consecutive-failure counter, regardless of the cooldown window. The next outbound DT call probes immediately instead of waiting.

The endpoint refuses with `409 Conflict` and `dt_breaker_already_closed: true` when the breaker is already CLOSED — operators should investigate why a reset looked necessary instead of letting a scripted retry no-op silently. The transition (`state_before` / `state_after` / `fail_count_before`) is recorded in the audit log under `target_table=dt_breaker`, `action=breaker_reset`, with the actor's user id.

The Admin → DT Connector page surfaces a **Reset breaker** button in the status card; the button enables only while the breaker is OPEN or HALF_OPEN, mirroring the backend's 409 contract so the affordance fades out instead of clicking through to an error toast.

```bash
curl -X POST -H "Authorization: Bearer $JWT" \
  https://portal.example.com/v1/admin/dt/breaker/reset
# 200 OK
# {"state_before": "open", "state_after": "closed", "fail_count_before": 5, "reset_at": "..."}
```

### Vulnerabilities not refreshing after DT comes back

The hourly resync Celery Beat task is the path that re-runs correlation against existing scans. There is no manual resync HTTP endpoint at v2.0.0 — wait for the next hourly tick or, if you cannot wait, restart the worker so the periodic schedule re-evaluates immediately:

```bash
docker-compose -f docker-compose.yml restart worker beat
```

Resync is idempotent — running twice produces the same result. A first-class manual-resync endpoint is on the roadmap.

### "Orphan list shows projects I don't recognize"

You are pointed at a shared external DT. Other teams may have created projects there. Set `DT_ORPHAN_AUTODELETE=false` (the default) and only delete orphans you own.

## Roadmap (v2.x)

The following operator affordances are referenced in early docs but are **not** shipped at v2.0.0:

- Automatic `docker restart dt` attempt when the health monitor flips to `down`.
- Operator-facing manual resync endpoint (`POST /v1/admin/dt/resync`); the Celery Beat hourly resync task itself ships and runs.

## See also

- [System health dashboard](./disk-and-health.md)
- [Backup & restore](./backup-and-restore.md)
- [Architecture — DT integration](../reference/architecture.md#dependency-track)
