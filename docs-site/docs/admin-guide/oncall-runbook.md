---
id: oncall-runbook
title: On-call runbook
description: First-response playbook for PagerDuty / production alerts targeting TrustedOSS Portal.
sidebar_label: On-call runbook
sidebar_position: 99
---

# On-call runbook

Quick-reference playbook for the four most common PagerDuty alerts
against a production TrustedOSS Portal stack. Each scenario lists:

- **Symptom** — what triggered the page
- **Customer impact** — what users can/cannot do right now
- **Diagnose** — exact commands to run (host + container)
- **Recover** — ordered remediation steps
- **Escalate** — when to wake the portal dev team

All commands assume `docker-compose` V1 (hyphen) and a `bash` host shell.

:::tip Get a super-admin token (used by most curl examples)
```bash
# Replace EMAIL/PASSWORD with the super-admin you created at install.
EMAIL=admin@example.com
PASSWORD=...
ACCESS_TOKEN=$(curl -fsS -X POST "https://<your-host>/api/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}" | jq -r '.access_token')
```
:::

## Scenario 1 — DT down ≥ 15 min

### Symptom
PagerDuty: `TrustedOSS DT health = down for 15+ min` (from the `/admin/dt` probe or external monitor).

### Customer impact
- New scans CAN still be queued — the policy gate falls back to cached vulnerability data when the circuit breaker is OPEN.
- New CVE alerts will lag until DT comes back (no fresh vulnerability mirror).
- Portal UI, login, and existing project data are all unaffected.

### Diagnose
```bash
# 1. DT container alive?
docker-compose ps dt
# 2. Recent DT logs (last 200 lines)
docker-compose logs --tail=200 dt | grep -iE 'error|fatal'
# 3. Portal's view of DT health (structured)
docker-compose logs --tail=500 backend | grep dt_health_check | tail -10
# 4. Breaker state from the portal
curl -fsS "https://<your-host>/api/v1/admin/dt/status" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq
```

### Recover (in order)
1. **Container restart** (most cases — OOM, transient JVM hang):
   ```bash
   docker-compose restart dt
   sleep 30
   curl -fsS https://<your-host>/api/v1/admin/dt/health-check \
     -H "Authorization: Bearer $ACCESS_TOKEN"
   ```
2. **Manual breaker reset** (if breaker stayed OPEN after DT recovers):
   ```bash
   curl -fsS -X POST "https://<your-host>/api/v1/admin/dt/breaker/reset" \
     -H "Authorization: Bearer $ACCESS_TOKEN"
   ```
3. **Mirror re-sync** (if vuln data looks stale post-recovery): wait one full hourly beat cycle (the `dt_findings_resync` task in `celery_app.py`).

### Escalate
- If DT container will not stay up after 2 restarts, OR
- If breaker stays OPEN despite a green `health-check`, OR
- If `dt_health_check` logs show DB-side errors (Postgres unreachable from DT).

Page the portal dev team with: container logs (`docker-compose logs --tail=2000 dt`), breaker history from `/admin/dt/status`, and the last 5 minutes of `backend` logs.

## Scenario 2 — Auto-backup failed for 3 days

### Symptom
PagerDuty: `TrustedOSS auto-backup task failure count = 3`.

### Customer impact
- All in-portal data is at risk if the host crashes (no recent backup to restore from). Plan downstream tasks (compliance freezes, etc.) accordingly until a fresh backup lands.

### Diagnose
```bash
# 1. Celery Beat schedule heartbeat
docker-compose logs --tail=500 beat | grep daily-auto-backup
# 2. Worker logs for backup task runs
docker-compose logs --tail=2000 worker | grep -E 'backup\.(completed|failed)' | tail -20
# 3. Most recent backup row + status
curl -fsS "https://<your-host>/api/v1/admin/backup/list" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq '.items[0:5]'
# 4. Disk free on the backup volume
docker-compose exec backend df -h /backups
```

### Recover
1. **Manual trigger** (UI: `/admin/backup` → **Run manual backup now**, or):
   ```bash
   curl -fsS -X POST "https://<your-host>/api/v1/admin/backup/trigger" \
     -H "Authorization: Bearer $ACCESS_TOKEN"
   ```
2. **If manual also fails — inspect `pg_dump` directly**:
   ```bash
   docker-compose exec backend bash -c \
     'BACKUP_NAME=debug-$(date +%Y%m%dT%H%M%SZ); \
      bash /app/scripts/backup.sh --name "$BACKUP_NAME" 2>&1'
   ```
   - Permission denied → `BACKUPS_ROOT` volume mount problem (check compose `backups:/backups` mapping).
   - Server version mismatch → `postgresql-client-17` not installed in worker image (regression — escalate).
   - Disk full → see Scenario 4.

### Escalate
- If `bash scripts/backup.sh` fails for non-disk, non-permission reasons, OR
- If the most recent successful backup is older than 7 days (auto-purge window — restore options narrowing).

## Scenario 3 — Scan stuck in `running` for ≥ 4 hours

### Symptom
PagerDuty: `TrustedOSS scan running > 4h for project X`.

### Customer impact
- That project: blocked from new scans (one-running-at-a-time).
- Other projects: unaffected unless worker concurrency = 1 (default 2).

### Diagnose
```bash
# 1. Which stage is it stuck at?
curl -fsS "https://<your-host>/api/v1/scans/<scan_id>" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq '.progress_payload, .latest_log_frame'
# 2. Celery active tasks
docker-compose exec worker celery -A apps.backend.tasks.celery_app inspect active
# 3. Worker process tree (look for orphaned subprocesses)
docker-compose exec worker ps -ef | grep -E 'cdxgen|ort|trivy'
```

### Recover
1. **Force-cancel the scan** (preferred — no worker-wide impact):
   ```bash
   curl -fsS -X POST "https://<your-host>/api/v1/admin/scans/<scan_id>/cancel" \
     -H "Authorization: Bearer $ACCESS_TOKEN"
   ```
2. **If cancel doesn't release the task (worker truly hung)**:
   ```bash
   # Last resort — kills all in-flight tasks on this worker.
   docker-compose restart worker
   ```
   Other in-flight scans on the same worker will be marked failed and require manual re-run.

### Escalate
- If the same project hangs at the same stage twice in a row (suggests a content-side issue — large git history, malformed lockfile, or DT timeout). Page portal dev team with `<scan_id>` and the last 200 lines of `worker` logs filtered to that task.

## Scenario 4 — Host disk ≥ 95%

### Symptom
PagerDuty: `TrustedOSS portal disk = 95%+`.

### Customer impact
- In-flight scans continue. New scans are **blocked** at the `DISK_HARD_LIMIT_PCT` threshold (default 95%) — `/admin/scans` shows them as queued indefinitely.

### Diagnose
```bash
# 1. Host-wide
df -h /opt/trustedoss
docker system df
# 2. Per-card breakdown via the portal
curl -fsS "https://<your-host>/api/v1/admin/disk" \
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
3. **DT volume** (if `/admin/disk` shows `dt_volume` at fault): restart DT to flush its index temp files (`docker-compose restart dt`).
4. **Temporary threshold raise** (only as a stop-gap, NOT a fix):
   ```bash
   # Edit .env: DISK_HARD_LIMIT_PCT=98
   docker-compose up -d backend worker
   ```

### Escalate
- After workspace cleanup, disk still > 90%, OR
- Postgres growth is from `audit_logs` doubling every 24 hours (root cause needed — possibly a runaway integration emitting events).

## Standard escalation form

When paging the portal dev team, attach:

- Scenario number (1-4) and PagerDuty alert URL.
- Portal version: `docker-compose exec backend python -c "from main import APP_VERSION; print(APP_VERSION)"`
- Last 2000 lines of the relevant container: `docker-compose logs --tail=2000 <svc>`
- For DT issues: `/admin/dt/status` full JSON.
- For scan issues: `<scan_id>` and `/api/v1/scans/<scan_id>` full JSON.

## See also

- [DT connector](./dt-connector.md) — circuit breaker model + reset procedures.
- [Backup and restore](./backup-and-restore.md) — backup retention + restore flow.
- [Disk and health](./disk-and-health.md) — disk threshold model + Health dashboard.
