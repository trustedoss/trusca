---
id: oncall-runbook
title: On-call runbook
description: First-response playbook for PagerDuty / production alerts targeting TRUSCA.
sidebar_label: On-call runbook
sidebar_position: 99
---

# On-call runbook

Quick-reference playbook for the most common PagerDuty / notification
alerts against a production TRUSCA stack. Each scenario lists:

- **Symptom** — what triggered the page
- **Customer impact** — what users can/cannot do right now
- **Diagnose** — exact commands to run (host + container)
- **Recover** — ordered remediation steps
- **Escalate** — when to wake the portal dev team

All commands assume `docker-compose` V1 (hyphen) and a `bash` host shell.

:::tip Get a super-admin token (used by most curl examples)
<!-- docs-uat: id=oncall-auth-check kind=shell ctx=host tier=nightly waiver=runbook-diagnostic-prod-compose-placeholder-creds -->
```bash
# Replace EMAIL/PASSWORD with the super-admin you created at install.
EMAIL=admin@example.com
PASSWORD=...
ACCESS_TOKEN=$(curl -fsS -X POST "https://<your-host>/api/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}" | jq -r '.access_token')
```
:::

## Scenario 1 — Trivy DB stale or missing

### Symptom
PagerDuty: `TRUSCA Trivy DB last refresh > 14 days` or `TRUSCA Trivy DB missing on worker`. The upcoming `/admin/health → Vulnerability data` card drives this.

### Customer impact
- New scans CAN still be queued — `cdxgen` + scancode still produce SBOMs and licence findings.
- New CVE detections stop landing until the DB refresh succeeds.
- Existing `vulnerability_findings` rows are unchanged — the gap is forward-only.

### Diagnose
<!-- docs-uat: id=oncall-trivy-db-check kind=shell ctx=host tier=nightly waiver=runbook-diagnostic-prod-compose-worker -->
```bash
# 1. Is the DB on disk?
docker-compose -f docker-compose.yml exec worker \
  ls -lh /var/lib/trivy/db/
# 2. DB metadata (Created timestamp)
docker-compose -f docker-compose.yml exec worker \
  cat /var/lib/trivy/db/metadata.json
# 3. Recent download / refresh logs
docker-compose -f docker-compose.yml logs --tail=500 worker | grep trivy_db
docker-compose -f docker-compose.yml logs --tail=500 beat | grep trivy_db_refresh
# 4. Outbound HTTPS to ghcr.io reachable?
docker-compose -f docker-compose.yml exec worker \
  curl -fsS https://ghcr.io/v2/ -o /dev/null -w "%{http_code}\n"
```

### Recover (in order)
1. **Force a one-shot refresh** (preferred — single command, no restart):
   ```bash
   docker-compose -f docker-compose.yml exec worker \
     celery -A apps.backend.tasks.celery_app call tasks.trivy_db.refresh
   sleep 30
   docker-compose -f docker-compose.yml exec worker \
     cat /var/lib/trivy/db/metadata.json | jq '.Created'
   ```
2. **Wipe + re-download** (if metadata is corrupted):
   ```bash
   docker-compose -f docker-compose.yml exec worker \
     rm -rf /var/lib/trivy/db
   docker-compose -f docker-compose.yml restart worker
   ```
   The boot-time `trivy --download-db-only` runs and re-populates the directory within 1–3 minutes.
3. **Mirror fallback** (if `ghcr.io` is unreachable from the worker): point `TRIVY_DB_REPOSITORY` at your internal mirror — see [Vulnerability data — Air-gapped operation](./vulnerability-data.md#air-gapped).

After recovery, the automatic re-match beat picks up missed CVEs against existing scans on its next cycle — no operator action.

### Escalate
- If two refresh attempts fail with the same error, OR
- If the internal mirror itself reports `unauthorized` despite recent `trivy registry login`, OR
- If `metadata.json` exists but `Results` on a spot scan is empty across multiple ecosystems (suggests a schema mismatch).

Page the portal dev team with: worker logs (`docker-compose logs --tail=2000 worker`), the `metadata.json` content, and the output of `trivy --version` from inside the worker.

## Scenario 2 — Auto-backup failed for 3 days

### Symptom
PagerDuty: `TRUSCA auto-backup task failure count = 3`.

### Customer impact
- All in-portal data is at risk if the host crashes (no recent backup to restore from). Plan downstream tasks (compliance freezes, etc.) accordingly until a fresh backup lands.

### Diagnose
<!-- docs-uat: id=oncall-backup-beat-check kind=shell ctx=host tier=nightly waiver=runbook-diagnostic-prod-compose-logs -->
```bash
# 1. Celery Beat schedule heartbeat
docker-compose logs --tail=500 beat | grep daily-auto-backup
# 2. Worker logs for backup task runs
docker-compose logs --tail=2000 worker | grep -E 'backup\.(completed|failed)' | tail -20
# 3. Most recent backup row + status
curl -fsS "https://<your-host>/v1/admin/backup/list" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq '.items[0:5]'
# 4. Disk free on the backup volume (BACKUPS_ROOT is mounted at
#    /opt/trustedoss/backups in the backend container)
docker-compose -f docker-compose.yml exec backend df -h /opt/trustedoss/backups
```

### Recover
1. **Manual trigger** (UI: `/admin/backup` → **Run manual backup now**, or):
   ```bash
   curl -fsS -X POST "https://<your-host>/v1/admin/backup/trigger" \
     -H "Authorization: Bearer $ACCESS_TOKEN"
   ```
2. **If manual also fails — run the host backup script directly**:

   `scripts/backup.sh` is a **host** script: it shells out to
   `docker-compose ... exec` for `pg_dump` and tars the workspace mount, so run
   it on the host (not inside a container). It writes to `BACKUP_DIR` when set,
   otherwise `backups/<stamp>` under the repo root (mounted at
   `/opt/trustedoss/backups`).
   ```bash
   # From the deploy directory on the host (where docker-compose.yml + .env live).
   BACKUP_DIR=backups/debug-$(date +%Y%m%d-%H%M%S) bash scripts/backup.sh --no-prune 2>&1
   ```
   - `.env not found` → run from the deploy directory, or the install is incomplete.
   - Server version mismatch → `postgresql-client-17` missing in the postgres image (regression — escalate).
   - Disk full → see Scenario 4.

### Escalate
- If `bash scripts/backup.sh` fails for non-disk, non-permission reasons, OR
- If the most recent successful backup is older than 7 days (auto-purge window — restore options narrowing).

## Scenario 3 — Scan stuck in `running` for ≥ 4 hours

### Symptom
PagerDuty: `TRUSCA scan running > 4h for project X`.

### Customer impact
- That project: blocked from new scans (one-running-at-a-time).
- Other projects: unaffected unless worker concurrency = 1 (default 2).

### Diagnose
<!-- docs-uat: id=oncall-scan-stuck-check kind=shell ctx=host tier=nightly waiver=runbook-diagnostic-prod-compose -->
```bash
# 1. Which stage is it stuck at?
curl -fsS "https://<your-host>/v1/scans/<scan_id>" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq '.progress_payload, .latest_log_frame'
# 2. Celery active tasks
docker-compose exec worker celery -A apps.backend.tasks.celery_app inspect active
# 3. Worker process tree (look for orphaned subprocesses)
docker-compose exec worker ps -ef | grep -E 'cdxgen|ort|trivy'
```

### Recover
1. **Force-cancel the scan** (preferred — no worker-wide impact):
   ```bash
   curl -fsS -X POST "https://<your-host>/v1/admin/scans/<scan_id>/cancel" \
     -H "Authorization: Bearer $ACCESS_TOKEN"
   ```
2. **If cancel doesn't release the task (worker truly hung)**:
   ```bash
   # Last resort — kills all in-flight tasks on this worker.
   docker-compose restart worker
   ```
   Other in-flight scans on the same worker will be marked failed and require manual re-run.

### Escalate
- If the same project hangs at the same stage twice in a row (suggests a content-side issue — large git history, malformed lockfile, or `trivy sbom` timeout). Page portal dev team with `<scan_id>` and the last 200 lines of `worker` logs filtered to that task.

## Scenario 4 — Host disk ≥ 95%

### Symptom
PagerDuty: `TRUSCA disk = 95%+`.

### Customer impact
- In-flight scans continue. New scans are **blocked** at the `DISK_HARD_LIMIT_PCT` threshold (default 95%) — `/admin/scans` shows them as queued indefinitely.

### Diagnose
<!-- docs-uat: id=oncall-disk-check kind=shell ctx=host tier=nightly waiver=runbook-diagnostic-host-df -->
```bash
# 1. Host-wide
df -h /opt/trustedoss
docker system df
# 2. Per-card breakdown via the portal
curl -fsS "https://<your-host>/v1/admin/disk" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq
# 3. Workspace breakdown (most common offender)
docker-compose exec worker du -sh /workspace/* | sort -h | tail -10
# 4. Postgres database size
docker-compose exec postgres psql -U trustedoss -d trustedoss \
  -c "SELECT pg_size_pretty(pg_database_size('trustedoss'));"
```

### Recover
1. **Workspace cleanup** (almost always the answer):
   ```bash
   docker-compose exec worker find /workspace -mindepth 1 -mtime +30 -delete
   ```
2. **Postgres bloat** (if `pg_database_size` > 2 GB and growth is recent): VACUUM the heavy tables.
   ```bash
   docker-compose exec postgres psql -U trustedoss -d trustedoss \
     -c "VACUUM FULL audit_logs, vulnerability_findings;"
   ```
3. **Trivy DB volume** (if `/admin/disk` shows `trivy_db` at fault): the Trivy DB is ~500 MB and should not grow further; if it has, prune the cache and re-download (`docker-compose -f docker-compose.yml exec worker rm -rf /var/lib/trivy/db && docker-compose restart worker`).
4. **Temporary threshold raise** (only as a stop-gap, NOT a fix):
   ```bash
   # Edit .env: DISK_HARD_LIMIT_PCT=98
   docker-compose up -d backend worker
   ```

### Escalate
- After workspace cleanup, disk still > 90%, OR
- Postgres growth is from `audit_logs` doubling every 24 hours (root cause needed — possibly a runaway integration emitting events).

## Scenario 5 - Queue backlog alert fired

### Symptom
A Slack/Teams message titled "Queue backlog alert" for `trustedoss.scan` or `trustedoss.default` (the existing notification channels, this is not a new integration). Requires `QUEUE_BACKLOG_ALERT_ENABLED` + `QUEUE_BACKLOG_METRICS_ENABLED` to both be on; see [Environment variables - Queue backlog alert](../reference/env-variables.md) and [Docker Compose - Scan capacity](../installation/docker-compose.md#scan-capacity-sizing-and-scaling).

### Customer impact
- `trustedoss.scan`: new scans queue behind existing ones and take longer to start. Nothing fails outright, this is a capacity signal, not an error.
- `trustedoss.default`: notifications, backups, audit export, and ticket webhooks are delayed. If sustained, treat it as a possible stuck worker rather than pure overload (see Diagnose).

### Diagnose
<!-- docs-uat: id=oncall-queue-backlog-check kind=shell ctx=host tier=nightly waiver=runbook-diagnostic-prod-compose-worker -->
```bash
# 1. Current backlog and oldest-queued-scan wait (needs METRICS_ENABLED too)
curl -fsS "https://<your-host>/metrics" | grep -E 'trusca_broker_queue_backlog|trusca_scan_queue_wait_seconds'
# 2. Are the worker-scan replicas actually up and consuming?
docker-compose -f docker-compose.yml ps worker-scan
docker-compose -f docker-compose.yml exec worker-scan celery -A apps.backend.tasks.celery_app inspect active
# 3. Recent scan throughput - are scans finishing, or piling up?
curl -fsS "https://<your-host>/v1/admin/scans?status=queued" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq '.total'
```

### Recover
1. **This is arrival rate over capacity (the common case)**: scale the worker service the alert named. `worker-scan` for scan throughput, `worker-default` for everything else; scaling the wrong one does nothing (see the capacity guide linked above):
   ```bash
   docker-compose -f docker-compose.yml up -d --scale worker-scan=4
   ```
2. **This is a stuck worker, not real load**: if `celery inspect active` shows a task that has been running far longer than a normal scan (compare against `SCAN_HARD_TIME_LIMIT_SECONDS`), follow Scenario 3's recovery steps for that scan first. Clearing the stuck task frees the slot without permanently adding capacity you don't need.
3. **Confirm it clears**: the alert re-fires on a cooldown (`QUEUE_BACKLOG_ALERT_COOLDOWN_SECONDS`, default 1h) while still breached, and stops once the backlog drops back at or under its threshold on a later beat tick (checked every 5 minutes).

### Escalate
- If scaling `worker-scan` does not bring the backlog down within one scan-duration cycle (suggests the bottleneck is elsewhere: disk, Postgres, or the broker itself), OR
- If the alert keeps re-firing (past its cooldown) for the same queue across multiple days.

## Scenario 6 - A worker restarts on boot with `task_registry.empty`

### Symptom
A worker container will not stay up. Its log ends with a line naming
`task_registry.empty`, or on the process's own stderr:

```
FATAL task_registry.empty: this worker registered none of the portal's tasks
```

Under Compose the service restarts in a loop; under Kubernetes the pod reports
CrashLoopBackOff.

### Customer impact
Scans queued for that worker do not run. They stay queued rather than
disappearing, which is the point of the worker stopping: a worker with no tasks
that keeps running takes those messages, cannot execute them, and discards
them, and a healthy worker started afterwards finds nothing left to do.

If another worker of the same kind is healthy it will pick the work up and
there is no customer impact at all, only a restarting container.

### Diagnose
The worker's task list is built from an include list in the application, so
this is a deployment or packaging fault rather than a configuration one. It
means either that the list is empty or that no module on it imported.

```bash
# 1. What the worker says on the way down
docker-compose -f docker-compose.yml logs --tail=50 worker-scan | grep -i task_registry
# 2. Whether the modules import at all in that image
docker-compose -f docker-compose.yml run --rm --entrypoint python worker-scan \
  -c "from tasks.celery_app import celery_app; celery_app.loader.import_default_modules(); \
      print(len([t for t in celery_app.tasks if t.startswith('trustedoss.')]))"
```

The second command prints the count the guard would see. A healthy image prints
a number in the twenties or thirties. Zero, or a traceback, is the fault.

### Recover
1. **A mismatched or partial image**: pull the tagged image again and recreate
   the service. An image built from an incomplete tree is the usual cause.
2. **A module that fails to import**: the second command above prints the
   traceback. That is a defect in the release rather than something to
   configure around; roll back to the previous tag.

### Escalate
Immediately if the second command shows a traceback on an official release
image: every worker of that kind in every deployment on that tag is affected.

## Standard escalation form

When paging the portal dev team, attach:

- Scenario number (1-5) and PagerDuty alert URL.
- Portal version: `docker-compose -f docker-compose.yml exec backend python -c "from main import app; print(app.version)"`
- Last 2000 lines of the relevant container: `docker-compose logs --tail=2000 <svc>`
- For Trivy DB issues: the worker's `/var/lib/trivy/db/metadata.json` content and `docker-compose logs --tail=500 worker | grep trivy_db`.
- For scan issues: `<scan_id>` and `/v1/scans/<scan_id>` full JSON.

## See also

- [Vulnerability data (Trivy DB)](./vulnerability-data.md) — DB lifecycle and troubleshooting.
- [Backup and restore](./backup-and-restore.md) — backup retention + restore flow.
- [Disk and health](./disk-and-health.md) — disk threshold model + Health dashboard.
- [Docker Compose - Scan capacity](../installation/docker-compose.md#scan-capacity-sizing-and-scaling) - slot capacity formula and scaling `worker-scan` / `worker-default`.
