---
id: disk-and-health
title: Disk & system health
description: Read the system-health dashboard, configure the disk-pressure guard, and act on early warnings before scans start failing.
sidebar_label: Disk & health
sidebar_position: 3
---

# Disk & system health

The portal exposes two operator dashboards under `/admin`:

- **/admin/health** — current state of every container service plus the Trivy DB freshness card (coming soon).
- **/admin/disk** — workspace and database storage usage with a configurable hard limit.

![Admin System Health — overview of postgres, redis, celery, and the upcoming vulnerability data row](/img/screenshots/admin-health-cards.png)

![Admin Disk usage — workspace + database cards with usage gauges](/img/screenshots/admin-disk-list.png)

Together they let you catch problems before users notice.

:::note Audience
`super_admin` operating the host. Familiarity with `docker-compose ps` and basic shell.
:::

## System health dashboard {#health}

<!-- docs-uat: id=health-api kind=api auth=admin url=/v1/admin/health expect=status:200 tier=nightly -->
The **/admin/health** page lists every component the portal depends on. Each row shows:

- **Component** — one of `postgres`, `redis`, `celery`, `disk`, `active_scans`, `last_24h_errors`. Trivy DB freshness is shown in its own panel on the same page (see [Vulnerability data](vulnerability-data.md)).
- **State** — `ok` (green), `degraded` (yellow), `down` (red). The label rendered in the UI is locale-aware (the EN locale shows "OK / Degraded / Down"), but the API contract emits the lower-case enum above.
- **Detail** — error message or telemetry summary when the state is not `ok`.

All probes run synchronously when the page (or the API) requests them, so there is a single **Last updated** timestamp in the page header rather than a per-row "last check" — every card on the page reflects the same probe run.

The dashboard auto-refreshes via React Query polling (default 30 s; the user can pause polling from the page header). It is not a WebSocket stream — operators who want a wall display can leave the tab open and rely on the polling refresh.

### Health probes

Each row maps to a real probe in `services/admin_health_service.py`:

| Component | Probe |
|---|---|
| `postgres` | `SELECT 1` over the application's asyncpg pool. |
| `redis` | `PING` through the synchronous redis client. |
| `celery` | Celery `control.ping` answers within a fixed 2-second budget; the value is the number of workers that replied. |
| `disk` | Workspace volume usage compared to the warn / critical thresholds. |
| `active_scans` | Count of scans currently `queued` or `running`. Informational; surfaces as `degraded` when the queue length crosses an internal threshold. |
| `last_24h_errors` | Count of scans that **failed** in the last 24 h, counted by completion time. Informational. The name is historical and does not describe what is counted. |

The portal does not separately probe `backend`, `worker`, `beat`, `frontend`, or `traefik`. Their liveness is implicit: if the dashboard renders at all, the backend is up; if the `celery` row is `ok`, the worker (and the broker the worker depends on) are reachable.

### endoflife.date snapshot panel {#eol-panel}

<!-- docs-uat: id=eol-health-api kind=api auth=admin url=/v1/admin/eol/health expect=status:200 tier=nightly -->
Below the Trivy DB and KEV feed panels, the **endoflife.date snapshot** panel
tracks the dataset behind the Components tab's
[EOL badge](../user-guide/components-and-licenses.md#end-of-life-flagging):

- **Snapshot date** — the effective dataset's build date (the newer of the
  release-vendored snapshot and, when `EOL_REFRESH_ENABLED=true`, the last
  fetched one). The panel escalates to an amber **Stale** badge past 180
  days — upgrade the release or rebuild the snapshot with
  `python3 scripts/refresh_eol_snapshot.py`.
- **EOL-flagged components** — live count of catalog component versions past
  their end-of-life.
- **Recently stamped / cleared** — what the weekly beat's re-stamp pass did
  on its last tick. The re-stamp runs even when the live fetch is off — it
  is a pure-local pass that applies a newer vendored snapshot to existing
  rows and clears stamps the whitelist no longer covers.
- **Next tick** — derived from the live Celery beat schedule (Sunday 02:15
  UTC by default).

The footer shows the dataset origin (vendored vs fetched) and whether the
live refresh is on. The refresh is **off by default** — see
`EOL_REFRESH_ENABLED` in [Environment variables](../reference/env-variables.md).

## Disk dashboard {#disk}

<!-- docs-uat: id=disk-api kind=api auth=admin url=/v1/admin/disk expect=status:200 tier=nightly -->
**/admin/disk** renders one card per filesystem the portal cares about. The actual cards from v0.10.0 are: **workspace**, **trivy_db**, **postgres**, **redis** (the API returns them as `items: AdminDiskItem[]` and the page renders one card per item). The earlier **dt_volume** card was removed when Dependency-Track was retired.

Each card has a warn threshold and a critical threshold:

| Threshold | Default | Effect |
|---|---|---|
| **Warn** | 80% | Yellow card, dashboard banner, no other side effect. |
| **Critical** | 90% | Red card, dashboard banner, an admin notification fires. |

Override in `.env`:

<!-- docs-uat: id=disk-threshold-env kind=shell ctx=host tier=nightly waiver=env-config-snippet-not-a-command -->
```bash
DISK_THRESHOLD_WARNING_PCT=80
DISK_THRESHOLD_CRITICAL_PCT=90
```

Separately, the **scan disk-guard** uses a single `DISK_HARD_LIMIT_PCT` (default `95`) to **block new scans** when the workspace volume crosses that line. Cross-reference is intentional: the dashboard warns earlier (80% / 90%), the scan guard kicks in later (95%) to stop the bleed without surprising the operator.

<!-- docs-uat: id=disk-hard-limit-env kind=shell ctx=host tier=nightly waiver=env-config-snippet-not-a-command -->
```bash
DISK_HARD_LIMIT_PCT=95
```

The accepted range is `50` to `100`. Below 50 the guard would refuse every scan on a volume that is mostly empty, which reads as "nothing is scanning" rather than as a threshold; 100 leaves it effectively off. A value outside the range is clamped to the nearest bound, and a value that is not a number falls back to `95.0`. Both cases log a WARNING naming the variable, so `docker-compose logs backend | grep DISK_HARD_LIMIT_PCT` tells you whether the deployment is running the number you wrote.

### What "scans blocked" means

When `DISK_HARD_LIMIT_PCT` trips, `POST /v1/projects/{id}/scans` returns:

```json
{
  "type": "about:blank",
  "title": "Workspace Disk Full",
  "status": 503,
  "detail": "Workspace is at 96% (hard limit 95%). Free space and try again.",
  "instance": "/v1/projects/01H…/scans"
}
```

Existing in-flight scans are **not** killed; only new submissions are rejected. This avoids losing work but stops the bleed.

## What to do when disk fills up

### 1. Identify the offender

The scan workspace lives at `WORKSPACE_HOST_PATH`. The production compose
(`docker-compose.yml`) sets it to `/workspace` inside the container; if you
overrode it in `.env`, substitute your path. Each scan creates a
`${WORKSPACE_HOST_PATH}/<scan_id>/` directory.

<!-- docs-uat: id=disk-identify-offender kind=shell ctx=host tier=nightly waiver=production-compose-diagnostic -->
```bash
docker-compose -f docker-compose.yml exec backend \
  du -sh "${WORKSPACE_HOST_PATH:-/workspace}"/*  | sort -h | tail -20
```

Most often a single scan's source clone (`<scan_id>/source/`) + scancode license-detection output (`<scan_id>/scancode/scancode.json`) dominates the workspace. The `cdxgen` cache (`<scan_id>/cdxgen/`) also grows over time.

### 2. Free space

<!-- docs-uat: id=disk-free-space kind=shell ctx=host tier=nightly waiver=destructive-prune-on-production-compose -->
```bash
# Drop scancode result JSON older than 30 days (safe — rebuilt on next scan).
docker-compose -f docker-compose.yml exec backend \
  find "${WORKSPACE_HOST_PATH:-/workspace}" -name "scancode.json" -mtime +30 -delete

# Drop cdxgen SBOM caches older than 30 days (safe — rebuilt on next scan).
docker-compose -f docker-compose.yml exec backend \
  find "${WORKSPACE_HOST_PATH:-/workspace}" -type d -name "cdxgen" -mtime +30 -exec rm -rf {} +

# Drop the entire workspace directory for one finished scan.
docker-compose -f docker-compose.yml exec backend \
  rm -rf "${WORKSPACE_HOST_PATH:-/workspace}/<scan-id>/"
```

### 3. Verify

After cleanup, **/admin/disk** updates within ~10 seconds. Once below the hard threshold, scans are accepted again automatically — no service restart needed.

### 4. Long-term remediation

- Move `WORKSPACE_HOST_PATH` to a larger volume (edit `.env`, restart `backend`, `worker`).
- Lower `BACKUP_RETENTION_DAYS` if local backups are eating space.
- Move backups off-host (S3, NFS) and skip local pruning.

## Notification triggers

Disk pressure does not generate a notification today; operators are expected to monitor `/admin/disk` directly. A `disk_pressure` notification kind is on the roadmap.

## /admin/scans — Scan queue and worker monitoring

The `/admin/scans` page (super-admin only) lists every running, queued, succeeded, and failed scan across the org. Operators can:

- Inspect any task's full progress payload + last log frame.
- Force-cancel a stuck scan (`POST /v1/admin/scans/{scan_id}/cancel`).
- Filter by status, kind, or project name. (There is no per-worker filter — scans do not record which worker picked them up.)

Backend: `apps/backend/api/v1/admin/scans.py`. UI: `apps/frontend/src/features/admin/scans/AdminScansPage.tsx`.

## Scraping metrics {#metrics}

Off by default. Set `METRICS_ENABLED=true` and the portal serves the
Prometheus text format at `/metrics`.

Off means the path answers 404 rather than 403. A monitoring endpoint that
says "not allowed" tells whoever asked what this host is and who runs it, so a
deployment that has not asked for a scrape target looks like one without the
feature. A wrong token answers 404 for the same reason.

<!-- docs-uat: id=metrics-off-by-default kind=api url=/metrics expect=status:404 tier=nightly -->
What it publishes is a fixed list of aggregate counts:

| Series | Labels | What it counts |
|---|---|---|
| `trusca_projects_total` | | Projects, archived ones included |
| `trusca_scans_total` | `status` | Scans by status; queued and running are the ones to watch |
| `trusca_vulnerability_findings_open` | `severity` | Open findings, counted per project rather than per CVE |
| `trusca_component_approvals_pending` | | Approvals waiting on somebody |
| `trusca_users_active` | | Accounts that can sign in |
| `trusca_service_accounts_active` | | Automation identities that can still authenticate |
| `trusca_workspace_disk_used_ratio` | | Workspace volume in use, 0 to 1 |
| `trusca_task_runs_24h` | `task`, `outcome` | Background task runs in the last day. `outcome=running` counts runs that started and never reported an end |
| `trusca_task_run_duration_seconds_p50_24h` | `task` | Median duration of runs that finished in the last day |
| `trusca_task_run_duration_seconds_p95_24h` | `task` | Same at the 95th percentile |
| `trusca_vuln_db_last_update_timestamp_seconds` | | Unix time the vulnerability database was last updated upstream, `0` when none has been downloaded |
| `trusca_vuln_db_refresh_interval_hours` | | Configured hours between refresh attempts |
| `trusca_task_runs_last_recorded_timestamp_seconds` | | Unix time of the newest task-run row, `0` when the table is empty |

No project, package, repository or person's name appears anywhere in the
output, and the list is held to a fixture in the repository, so a series
cannot be added without somebody deciding it is safe to publish.

The four task-run series are gauges over a fixed one-day window, not counters.
Task history is swept on a retention schedule, so a cumulative count would go
down when the sweep runs and a collector reads a falling counter as a restart.
The window is in the name because changing it changes what the series means.

The two vulnerability-database series belong together: the timestamp says when
the data stopped changing, the interval says how long that is supposed to be
able to go on. Neither answers on its own, because an air-gapped install
mirroring on a slower cadence is not broken and only the interval separates it
from one that is. No staleness verdict ships in the metrics, and the fresh /
stale wording the panel uses is deliberately not published: that bucketing is
for a screen somebody is reading, and a series carrying it would put the
judgement beyond your collector's reach.

This one deserves an alert because a stale database is silent. Scans keep
running, keep finding what the database knew when it stopped, and report
success. One deployment was found 46 days behind with every scan green, and
what surfaced it was somebody looking at the disk for an unrelated reason.

`trusca_task_runs_last_recorded_timestamp_seconds` is worth an alert of its
own. It watches the recorder rather than the work: recording history is
designed never to fail a task, so a missing database grant or an unrun
migration produces no error anywhere, and the symptom is only that this value
stops moving while everything else looks healthy. No threshold ships with it,
because schedules here run anywhere from every five minutes to weekly and
which ones a deployment enables is a local fact. Compare it against your own
busiest schedule.

`METRICS_TOKEN` sets a bearer token the scraper must present. Leave it empty
when `/metrics` is off your public ingress and the monitoring system reaches
it on the internal network, which is the usual arrangement; a shared secret
that lives in a scraper config is one more thing to rotate. Set it when the
endpoint is reachable from somewhere you do not control. Both
`Authorization: Bearer <token>` and the bare token are accepted, because
scrapers differ.

The endpoint takes no portal session. Requiring one would mean the monitoring
system holds an account, which is a worse credential to leave in a config file
than the optional token.

## Verify it worked

After making changes:

<!-- docs-uat: id=disk-verify-health-green kind=manual tier=manual -->
1. **/admin/health** is all green.
<!-- docs-uat: id=disk-verify-disk-warn kind=manual tier=manual -->
2. **/admin/disk** is below the warn line.
<!-- docs-uat: id=disk-verify-test-scan kind=manual tier=manual -->
3. A test scan against any project succeeds end-to-end.

## Troubleshooting

:::info Logs to check first
- `docker-compose logs --tail=200 backend | grep disk_threshold` — the threshold check task's last verdict.
- `/admin/disk` API — per-card breakdown JSON (workspace, trivy_db, postgres, redis).
- Host: `df -h /opt/trustedoss && docker system df`.
:::

### Health page says everything is `healthy` but users complain

The dashboard is a snapshot of liveness, not full functionality. Liveness can pass while:

- The worker has accepted tasks but is hung on a sub-process (very rare). Restart the worker.
- The Trivy DB is on disk but has not been refreshed in a long time — see [Vulnerability data — Troubleshooting](./vulnerability-data.md#troubleshooting) for the weekly-refresh check.

### Disk gauge is wrong

The gauge reads the host-mounted volume from inside the backend container. If you changed `WORKSPACE_HOST_PATH` recently and forgot to restart, the gauge points at the old volume. Restart the backend.

### Hard limit is too aggressive

Raise it. 95% is a conservative default for `DISK_HARD_LIMIT_PCT` that gives operators room to react before the host runs out. If your monitoring catches issues earlier, you can lower it. Routinely operating above the warn threshold (80%) is a sign you should add disk.

## Roadmap

The following affordances are referenced in early docs but are **not** shipped in this release:

- Per-component liveness probes for `backend`, `worker`, `beat`, `frontend`, and `traefik` on the health dashboard (today these are inferred from the dashboard rendering and the `celery` row).
- WebSocket-streamed health updates (today the dashboard uses React Query polling).
- Multi-shot consecutive-miss state machine for components beyond the `vulnerability_data` row planned.

## See also

- [Vulnerability data (Trivy DB)](./vulnerability-data.md)
- [Backup & restore](./backup-and-restore.md)
- [Environment variables](../reference/env-variables.md)
