---
id: disk-and-health
title: Disk & system health
description: Read the system-health dashboard, configure the disk-pressure guard, and act on early warnings before scans start failing.
sidebar_label: Disk & health
sidebar_position: 3
---

# Disk & system health

The portal exposes two operator dashboards under `/admin`:

- **/admin/health** — current state of every container service plus the DT connector.
- **/admin/disk** — workspace and database storage usage with a configurable hard limit.

Together they let you catch problems before users notice.

:::note Audience
`super_admin` operating the host. Familiarity with `docker-compose ps` and basic shell.
:::

## System health dashboard {#health}

The **/admin/health** page lists every component the portal depends on. Each row shows:

- **Component** — one of `backend`, `postgres`, `redis`, `worker`, `beat`, `frontend`, `traefik`, `dt`.
- **State** — `healthy` (green), `degraded` (yellow), `down` (red).
- **Last check** — timestamp of the most recent probe.
- **Detail** — error message when the state is not `healthy`.

The dashboard auto-refreshes every 5 seconds via WebSocket. Operators can pin the page to a wall display.

### Health probes

Each row maps to a real probe:

| Component | Probe |
|---|---|
| `backend` | `curl /health` returns 200 within 5 s. |
| `postgres` | `pg_isready -U $POSTGRES_USER`. |
| `redis` | `redis-cli ping` returns `PONG`. |
| `worker` | Celery `inspect ping` returns within 5 s. |
| `beat` | The Beat scheduler emitted a heartbeat in the last 90 s. |
| `frontend` | `curl /healthz` on the nginx sidecar returns 200. |
| `traefik` | The edge entrypoint is reachable on `:80`. |
| `dt` | See [DT connector → health monitor](./dt-connector.md#operational-layers). |

A row turns `degraded` after a single probe miss and `down` after three consecutive misses.

## Disk dashboard {#disk}

**/admin/disk** shows two gauges:

- **Workspace** — bytes used / capacity on the volume backing `WORKSPACE_HOST_PATH`.
- **PostgreSQL** — `pg_database_size('trustedoss')` over the volume capacity.

Both gauges have a hard threshold and a warn threshold:

| Threshold | Default | Effect |
|---|---|---|
| **Warn** | 70% | Yellow gauge, dashboard banner, no other side effect. |
| **Hard** | 90% | Red gauge, **scans are blocked**, an admin notification fires. |

Override in `.env`:

```bash
DISK_WARN_LIMIT_PCT=70
DISK_HARD_LIMIT_PCT=90
```

### What "scans blocked" means

When the hard limit trips, `POST /api/v1/projects/{id}/scans` returns:

```json
{
  "type": "https://trustedoss.io/problems/disk-pressure",
  "title": "Scans temporarily disabled — disk usage above hard limit",
  "status": 503,
  "detail": "Workspace is at 92% (hard limit 90%). Free space and try again.",
  "instance": "/api/v1/projects/01H…/scans"
}
```

Existing in-flight scans are **not** killed; only new submissions are rejected. This avoids losing work but stops the bleed.

## What to do when disk fills up

### 1. Identify the offender

```bash
docker-compose -f docker-compose.yml exec backend \
  du -sh /workspace/*  | sort -h | tail -20
```

Most often a single project's repo + ORT analyzer output dominates the workspace. The `cdxgen` cache also grows over time.

### 2. Free space

```bash
# Drop ORT analyzer outputs older than 30 days (safe — rebuilt on next scan).
docker-compose -f docker-compose.yml exec backend \
  find /workspace -name "analyzer-result.yml" -mtime +30 -delete

# Drop entire workspace directories for projects that have been archived.
docker-compose -f docker-compose.yml exec backend \
  rm -rf /workspace/<archived-project-id>/
```

### 3. Verify

After cleanup, **/admin/disk** updates within ~10 seconds. Once below the hard threshold, scans are accepted again automatically — no service restart needed.

### 4. Long-term remediation

- Move `WORKSPACE_HOST_PATH` to a larger volume (edit `.env`, restart `backend`, `worker`).
- Lower `BACKUP_RETENTION_DAYS` if local backups are eating space.
- Move backups off-host (S3, NFS) and skip local pruning.

## Notification triggers

When disk usage crosses the hard limit, the portal fires the **disk pressure** notification:

- Email to all `super_admin` users (when SMTP is configured).
- Slack webhook (when `SLACK_WEBHOOK_URL` is set).
- MS Teams webhook (when `TEAMS_WEBHOOK_URL` is set).

The same notification fires once per crossing, not on every probe. Crossing back below the warn line emits a "recovered" notification.

## Verify it worked

After making changes:

1. **/admin/health** is all green.
2. **/admin/disk** is below the warn line.
3. A test scan against any project succeeds end-to-end.

## Troubleshooting

### Health page says everything is `healthy` but users complain

The dashboard is a snapshot of liveness, not full functionality. Liveness can pass while:

- The worker has accepted tasks but is hung on a sub-process (very rare). Restart the worker.
- DT is `healthy` but its NVD mirror is stale. Trigger a manual resync — see [DT connector](./dt-connector.md#troubleshooting).

### Disk gauge is wrong

The gauge reads the host-mounted volume from inside the backend container. If you changed `WORKSPACE_HOST_PATH` recently and forgot to restart, the gauge points at the old volume. Restart the backend.

### Hard limit is too aggressive

Raise it. 90% is a conservative default that gives operators room to react before the host runs out. If your monitoring catches issues earlier, you can push it to 95%. Going above 95% routinely is a sign you should add disk.

## See also

- [DT connector](./dt-connector.md)
- [Backup & restore](./backup-and-restore.md)
- [Environment variables](../reference/env-variables.md)
