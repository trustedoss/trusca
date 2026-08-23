---
id: data-retention
title: Data retention
description: How the portal bounds the growth of refresh tokens, password-reset tokens, notifications, webhook deliveries, report downloads, and the audit log, what gets deleted, what does not, and why the audit log stays a manual purge.
sidebar_label: Data retention
sidebar_position: 8.5
---

# Data retention

[Scan retention](./scan-retention.md) bounds the `scans` table and its findings. This page covers the six tables that had no retention policy before W9 (concurrency-scaling-plan-2026-08-22.md §3.5): `refresh_tokens`, `password_reset_tokens`, `notifications`, `webhook_deliveries`, `report_downloads`, and `audit_logs`. Five of them get a daily automated sweep. The sixth, `audit_logs`, does not, on purpose; see [Audit log retention: why this one stays manual](#audit-log-retention-why-this-one-stays-manual).

:::note Audience
`super_admin` operating a portal that has been running long enough for these tables to matter. Familiarity with `.env` editing and `docker-compose restart`.
:::

## What each table deletes and keeps

| Table | Deletes | Keeps | Cadence |
|---|---|---|---|
| `refresh_tokens` | Rows past `expires_at + REFRESH_TOKEN_RETENTION_GRACE_DAYS` (default 1 day past the 7-day TTL). | Every live and recently-expired token; a rotated/revoked row survives until its original expiry, so [reuse detection](../reference/env-variables.md#authentication) still has the row to compare against. | Daily, 03:15 UTC |
| `password_reset_tokens` | Rows past `expires_at + PASSWORD_RESET_TOKEN_RETENTION_GRACE_DAYS` (default 1 day past the 1-hour TTL). | Every unexpired token, used or not. | Daily, 03:15 UTC |
| `notifications` | Rows older than `NOTIFICATION_RETENTION_DAYS` (default 180), read or unread. | Six months of in-app notification history. | Daily, 03:30 UTC |
| `webhook_deliveries` | Rows older than `WEBHOOK_DELIVERY_RETENTION_DAYS` (default 90). | Three months of inbound GitHub/GitLab webhook history, for CI debugging. | Daily, 03:30 UTC |
| `report_downloads` | Rows older than `REPORT_DOWNLOAD_RETENTION_DAYS` (default 365). | A year of "who downloaded which SBOM / NOTICE / report" history. | Daily, 03:30 UTC |
| `audit_logs` | **Nothing, automatically.** A daily report counts rows that are both already exported and older than `AUDIT_LOG_RETENTION_DAYS` (default 90); an operator purges those by hand. | Every audit row, until an operator explicitly runs the documented purge. | Report only, daily 03:45 UTC |

None of the five automated sweeps needs a schema change. Every predicate above runs against an index the table already had before W9 (`ix_refresh_tokens_expires_at`, `ix_password_reset_tokens_expires_at`, `ix_webhook_deliveries_received_at`). `notifications` and `report_downloads` only have composite indexes led by `user_id` / `project_id` / `team_id`, so their daily sweep falls back to a sequential scan on the plain `created_at` predicate, acceptable once a day at today's scale; see the [`tasks/operational_retention.py`](https://github.com/trustedoss/trusca) module docstring if a deployment's table size ever makes that scan worth indexing.

## Configuration

All keys are read at runtime via `os.getenv`. Edit `.env` and restart the Celery beat service to change the cadence's inputs (the schedule itself, 03:15 / 03:30 / 03:45 UTC, is a code constant, the same convention every other beat entry in this codebase follows). See [Environment variables → Operational data retention](../reference/env-variables.md#operational-data-retention) for the canonical reference.

<!-- docs-uat: id=data-retention-env kind=shell ctx=host tier=manual waiver=env-config-snippet-not-a-command -->
```bash
# In the portal's .env
REFRESH_TOKEN_RETENTION_GRACE_DAYS=1
PASSWORD_RESET_TOKEN_RETENTION_GRACE_DAYS=1
NOTIFICATION_RETENTION_DAYS=180
WEBHOOK_DELIVERY_RETENTION_DAYS=90
REPORT_DOWNLOAD_RETENTION_DAYS=365
AUDIT_LOG_RETENTION_DAYS=90
```

:::caution Lowering a value reclaims sooner
The five automated sweeps are irreversible; reclaimed rows are gone. Raise a value first, watch disk for a few days, then lower it, the same guidance [Scan retention](./scan-retention.md#retention-policy-variables) gives for its own three keys.
:::

## Audit log retention: why this one stays manual

`audit_logs` is **append-only at the database layer**. Migration `0012` attaches triggers that raise on any `UPDATE`, `DELETE`, or `TRUNCATE` against the table; see [Audit log → Schema](./audit-log.md#schema). That is a deliberate compliance control: an unattended task that can silently mass-delete the compliance trail removes exactly the human-accountability property the trigger exists to guarantee. W9 does not weaken it.

Instead, a daily beat (`trustedoss.audit_log_retention_report`) answers the question the [documented manual purge](./audit-log.md#retention) needs answered before an operator opens that session: **how many rows are actually safe to delete right now.** A row counts only when it is both

1. already handed to your log collector, at or before the [continuous export](./audit-log.md#continuous-export) cursor, and
2. older than `AUDIT_LOG_RETENTION_DAYS`.

An unexported row is never counted, no matter its age. It is the one copy of that compliance record, and this report exists to make sure nobody purges it before it left the building. If `AUDIT_EXPORT_URL` is not configured, the report always returns zero: an organization that has exported nothing has, by construction, nothing that is safe to purge through this signal.

<!-- docs-uat: id=data-retention-audit-log-report kind=shell ctx=host tier=manual waiver=log-grep-illustrative-no-deterministic-assertion -->
```bash
docker-compose -f docker-compose.yml logs --tail=50 beat \
  | grep audit_log_retention_report_done
```

The line carries `ready_to_purge` and `retention_days`. When `ready_to_purge` is large enough to justify a maintenance window, follow [Audit log → Retention](./audit-log.md#retention) for the actual two-operator purge procedure.

## Verify it worked

<!-- docs-uat: id=data-retention-verify kind=manual tier=manual -->
1. Force an expired refresh token: log in, then update the row's `expires_at` to the past via `psql` (there is no API for this; it is a test-only step). After the next `auth-token-retention-daily` tick (or a manual `celery -A tasks.celery_app call trustedoss.auth_token_retention`), the row is gone; `SELECT count(*) FROM refresh_tokens WHERE id = '<id>'` returns `0`.
2. Trigger a notification (any scan completion works), then confirm it still appears in `/notifications`. The sweep only reaches rows older than `NOTIFICATION_RETENTION_DAYS`, so a fresh one is unaffected.
3. With `AUDIT_EXPORT_URL` unset, run `celery -A tasks.celery_app call trustedoss.audit_log_retention_report` by hand and confirm the log line shows `status=skipped` and `ready_to_purge=0`.

## Troubleshooting

:::info Logs to check first
`docker-compose -f docker-compose.yml logs --tail=200 beat | grep -E 'auth_token_retention_done|operational_retention_done|audit_log_retention_report_done'` shows each sweep's per-table deleted counts, or the audit report's `ready_to_purge`. On the dev compose the service is `celery-beat`, not `beat`.
:::

### A row I expected to be reclaimed is still here

For `refresh_tokens` / `password_reset_tokens`, check `expires_at` directly. The sweep never looks at whether the row was used or revoked, only at its own expiry plus the grace period. For the three occurrence-time tables, confirm the row's `created_at` (or `received_at` for `webhook_deliveries`) is actually past the configured window; a row created an hour ago is not reclaimed by a 90-day policy.

### `ready_to_purge` stays at `0` even though the table is large

Either `AUDIT_EXPORT_URL` is not configured (the report always returns zero in that case; see [above](#audit-log-retention-why-this-one-stays-manual)), or the export cursor has not caught up yet. Check `rows_exported` and `last_run_at` on the export cursor, described in [Audit log → Continuous export](./audit-log.md#continuous-export).

## See also

- [Scan retention](./scan-retention.md), the same model applied to the `scans` table
- [Audit log](./audit-log.md), schema, immutability triggers, continuous export, and the manual purge procedure
- [Disk & health](./disk-and-health.md), workspace artefact cleanup
- [Environment variables → Operational data retention](../reference/env-variables.md#operational-data-retention)
