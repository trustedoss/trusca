---
id: audit-log
title: Audit log
description: Read, filter, and export the append-only audit log of every write operation in TRUSCA.
sidebar_label: Audit log
sidebar_position: 4
---

# Audit log

Every write operation in the portal is recorded to an **append-only** audit log. The log is the source of truth for "who did what, when, and to what" — it is the first place to look when investigating an incident or fulfilling a compliance request.

The `/admin/audit` page exposes the toolbar (actor / target table / action / time-range filters) and the row table over the live data:

![Admin Audit log — search toolbar with actor / target table / action filters and the row table](/img/screenshots/admin-audit-list.png)

:::note Audience
`super_admin` for org-wide reads; `team_admin` for team-scoped reads.
:::

## Schema

Each entry has:

| Field | Type | Description |
|---|---|---|
| `id` | UUID | Primary key. |
| `created_at` | timestamptz | When the action occurred (server clock, UTC). |
| `actor_user_id` | UUID | The user who performed the action (null for system jobs). |
| `team_id` | UUID | Team scope of the action when applicable (null for org-wide writes). |
| `action` | text | Verb only (`create` / `update` / `delete`). The table is captured separately in `target_table`. Filter as `target_table=projects&action=create`. |
| `target_table` | text | Table the affected object lives in (`projects`, `teams`, `users`, `vulnerability_findings`, …). |
| `target_id` | String(64) | The affected object's identifier. |
| `request_id` | text | Correlates with structured logs (`X-Request-ID`). |
| `diff` | jsonb | Sanitized before / after diff. PII is masked (`mask_pii`). |
| `ip` | inet | Source IP. |
| `user_agent` | text | Truncated UA string. |

The append-only contract is enforced **at two layers**:

1. **Application** — the audit listener only emits inserts and the API exposes no update / delete endpoints.
2. **Database** — two triggers (migration `0012`) raise `SQLSTATE 23000` (integrity_constraint_violation) on any mutation:
   - `audit_logs_immutable_trigger` — BEFORE UPDATE OR DELETE, FOR EACH ROW.
   - `audit_logs_immutable_truncate` — BEFORE TRUNCATE, FOR EACH STATEMENT (PostgreSQL fires row triggers only on UPDATE / DELETE; TRUNCATE bypasses BEFORE-row, so the table-wipe path needs its own statement-level guard).
   
   A super-admin running raw `psql` with `UPDATE audit_logs ...`, `DELETE FROM audit_logs ...`, or `TRUNCATE TABLE audit_logs` gets `ERROR: audit_logs is append-only (TG_OP=…)` and the transaction is aborted. INSERT is unaffected — the listener path stays functional.

These triggers close a defense-in-depth gap that PR #44 had documented as roadmap. **Known residual bypass**: in the default install the migration role and the runtime app role are the same PostgreSQL role (`trustedoss`). That role owns the function and the triggers, which means it can `DROP TRIGGER` / `ALTER FUNCTION ... OWNER` and bypass the gate via "DROP TRIGGER → mutate → re-CREATE TRIGGER". A Phase 7 / 8 hardening PR is expected to split the runtime role from the migration role (`trustedoss_app` DML-only on `audit_logs` + `trustedoss_owner` for migrations) at which point the triggers become unbypassable from the runtime app. Until then, the bypass is observable: `DROP TRIGGER` is itself a DDL statement, captured by `pg_event_trigger` (future audit-of-audit hardening) and by the operator's session log for two-operator retention purges.

### The one exception, and its shape

Since migration `0080` there is exactly one UPDATE the trigger allows: clearing `ip` and `user_agent` on the rows of a user being anonymised. It is narrow by construction. The caller must be a member of the table's owner role; the two columns may only move toward NULL; every other column, `diff` included, must be unchanged; and the function that performs it refuses unless the request table holds an approved request for that subject.

**On a role-separated install the application role is not a member of the owner role, so none of this is reachable from the running portal. On a single-role install it is**, because there the runtime role and the owner are the same role: the membership check is trivially true, and the owner's implicit EXECUTE on the scrub function survives the REVOKE. The paragraph above about the same-role default applies here too. Role separation is what makes this exception a boundary rather than a formality; see [User anonymisation](./user-anonymisation.md).

The function's approval check is a check that a row exists, not proof that two people agreed. Anything that can write to `user_anonymisation_requests` can produce such a row. What stands behind it is the two-person flow in the product, an operator who can see and contact both named parties, and the fact that a request written directly to the database leaves no matching entries in this log.

`diff` is deliberately outside the exception. An exception wide enough to rewrite what a change contained would end the property that makes this table evidence. The consequence, that an old address can survive inside a `diff`, is stated in [User anonymisation](./user-anonymisation.md) rather than quietly resolved by widening the hole.

One thing worth knowing before testing this yourself: the trigger compares values, so an UPDATE that writes a column the value it already holds changes nothing and passes. Aim a forged scrub at a row whose `ip` is already NULL and Postgres answers `UPDATE 1`, which reads like a bypass and is not.

## What gets logged

Every authenticated `POST`, `PATCH`, `PUT`, and `DELETE` produces exactly one entry. Read endpoints (`GET`) do not. SBOM exports emit a structlog `sbom_exported` event but **do not** create an `audit_logs` row in this release; integrating exports into the audit table is on the roadmap.

:::note What v0.10.0 does NOT audit
The following user-visible operations emit a `structlog` event but
do **not** create an `audit_logs` row in this release:

- SBOM export (`sbom_exported`)
- NOTICE file download (no structlog event today either; see roadmap)
- API-key revocation explicit event (`api_key.revoked`; the
  underlying `api_keys.update` ORM row IS in `audit_logs`)

For compliance audits of "who downloaded what when", check
`docker-compose logs backend | grep sbom_exported` and your Loki /
journald aggregator. Promoting these to `audit_logs` rows is on the
the roadmap.
:::

System jobs (Celery) also log. Each row carries the bare action verb plus its `target_table`. Examples:

- `target_table=scans&action=create` (system, when a webhook triggers a scan)
- `target_table=dt_orphans&action=delete`
- `target_table=backups&action=create`
- `target_table=notifications&action=create`

:::note Filter-visible vs raw-row tables
The Admin UI filter dropdown for `target_table` is bounded by the
`AuditTargetTable` whitelist in `apps/backend/schemas/admin_ops.py`.
Rows with table names outside this whitelist (e.g. `dt_orphans`,
`backups`, `api_keys`, `notifications`, `dt_breaker`) still land in
`audit_logs` but can only be queried by raw SQL.
:::

## The audit log page

**/admin/audit** is a paginated, filterable view.

### Filters

The inline filter bar in this release:

- **Actor user ID** — exact UUID match.
- **Target table** — single-select from the enum (`projects`, `teams`, `users`, `vulnerability_findings`, …).
- **Action** — free-text contains (case-insensitive — the server uses `ilike`, so `create` and `CREATE` match the same rows).
- **Date range** — `from` and `to` (custom).
- **Search** — free-text query (`q`). Performs an `ilike` match against the JSON-encoded `diff` column. `action` and `target_table` are separate filter parameters (`action=`, `target_table=`); `q` does not match those columns.

Filters compose. The URL updates so you can share a filtered view with a teammate. Multi-select dropdowns, preset date ranges, request-ID filter, and a target-ID filter are on the roadmap (see below).

### Table

Default columns: `created_at`, `actor`, `action`, `target`, `ip`. Click a row to expand the full diff.

The table is virtualized; 10k entries scroll smoothly.

![Audit log drawer — full diff JSON panel for a single row, with masked PII fields and the request_id correlator](/img/screenshots/admin-audit-row-diff.png)

## Export to CSV

The **Export CSV** button on the toolbar exports the **currently filtered** result set, up to 100k rows per export. The CSV is UTF-8 with a leading byte-order mark (`EF BB BF`) so Excel on Korean / Japanese / Chinese locales auto-detects the encoding instead of falling back to CP949 / SJIS / GB18030 and rendering non-ASCII actor emails or audit row diffs as mojibake. Tools that already auto-detect UTF-8 (LibreOffice, awk, Python's `csv` / `utf-8-sig` codecs) silently strip the BOM.

For larger windows, paginate via the API:

<!-- docs-uat: id=audit-api-paginate kind=api auth=admin url=/v1/admin/audit?page=1&page_size=10 expect=status:200 tier=nightly -->
```bash
curl -sS \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  "https://trustedoss.example.com/v1/admin/audit?from=2026-01-01&to=2026-01-31&page=1&page_size=200"
```

The response is paginated by `page` + `page_size`. `page_size` is capped at
**200** (values above that return `422`); for larger windows, increment `page`.

## Common queries

### "Who deleted project X?"

Filter: `target_table=projects&action=archive&target_id=<project-uuid>`. There is exactly one row. Project deletion is a *soft delete* (the row's `archived_at` is filled, nothing is physically removed), so the audit verb is `archive` — `action=delete` only matches physically deleted rows (memberships, teams' hard deletes, …). Restoring an archived project writes a matching `action=unarchive` row.

### "What did user Y do last week?"

Filter: `actor=y@acme.com`, date range last 7 days. The actions list summarizes the activity.

### "Who suppressed CVE-2024-12345 across all projects?"

Filter: `target_table=vulnerability_findings&action=update`, then expand each row's diff — the rows where `diff.new_state == "suppressed"` and the matching CVE ID are the answer. (A first-class CVE filter is on the roadmap.)

### "Trace one request end-to-end"

When a user reports an error, ask them for the `X-Request-ID` shown on the error page. Filter the audit log by that `request_id` and you get the canonical record of every write the request triggered. Cross-reference with structured logs:

<!-- docs-uat: id=audit-log-correlate kind=shell ctx=host tier=nightly waiver=illustrative-log-grep-no-deterministic-assertion -->
```bash
docker-compose -f docker-compose.yml logs backend \
  | jq -c "select(.request_id == \"$REQ\")"
```

## Continuous export to your log collector {#continuous-export}

The CSV export above is something a person clicks. If you collect logs
centrally, `AUDIT_EXPORT_URL` hands the trail over on its own: a background
task posts batches of rows every five minutes to a URL you own.

Off by default, and the audit page and its CSV export are unchanged either
way. This adds a way to push the trail somewhere; it takes nothing away.

Each batch carries the same columns the page shows, including `diff`, which
was already masked when it was written: passwords, tokens and API keys never
reach the column, so they cannot reach your collector.

```json
{
  "version": 1,
  "source": "trusca",
  "destination": "https://logs.example.com/ingest/trusca-audit",
  "count": 500,
  "rows": [
    {
      "id": "…", "created_at": "2026-08-20T02:11:44.912+00:00",
      "actor_user_id": "…", "team_id": "…",
      "action": "update", "target_table": "projects", "target_id": "…",
      "request_id": "…", "ip": "10.0.0.4", "user_agent": "…",
      "diff": {"name": {"old": "api", "new": "payments-api"}}
    }
  ]
}
```

Three behaviours are worth knowing before you switch it on.

**It starts at the beginning.** A newly configured destination is handed the
trail you already have, not just what happens next. An export that quietly
began at the moment it was configured would leave a hole nobody would think to
look for.

**It stalls rather than skips.** The position moves only after your collector
accepts a batch. A collector that is down, or that rejects the document, makes
the export retry and then stop making progress until somebody fixes it. That
is deliberate: the alternative to retrying is skipping rows, which is the one
outcome this feature exists to prevent. `rows_exported` and `last_run_at` on
the cursor row say how far it got.

**It stays slightly behind the present.** A row is stamped when its
transaction commits, so a transaction that began earlier can commit later. If
the export read up to the current instant it could pass a position that a
still-open transaction later writes behind, and that row would never be sent.
`AUDIT_EXPORT_LAG_SECONDS` is the margin, thirty seconds by default; your
collector's copy is that much behind, and no row is lost.

:::note The trail itself does not change
Nothing about the export marks rows in `audit_logs`. That table is append-only
and a trigger enforces it, so an `exported_at` column would mean carving an
exception into the property that makes the trail worth having. The position
lives in its own table.
:::

## Retention

The audit log is **never auto-pruned**. Storage is cheap relative to its compliance value (a typical install grows by ~50 MB / year per active user). If you need to reduce the table size, the recommended path is **archive then truncate** with operator confirmation:

:::tip Know when a purge is due
The daily `trustedoss.audit_log_retention_report` beat counts rows that are both already handed to your log collector (past the [continuous export](#continuous-export) cursor) and older than `AUDIT_LOG_RETENTION_DAYS`, a read-only signal, never a delete. See [Data retention → Audit log retention: why this one stays manual](./data-retention.md#audit-log-retention-why-this-one-stays-manual).
:::

<!-- docs-uat: id=audit-archive-truncate kind=shell ctx=host tier=nightly waiver=destructive-retention-archive-on-production-compose -->
```bash
docker-compose -f docker-compose.yml exec postgres \
  pg_dump -U trustedoss -t audit_logs trustedoss | gzip > audit-archive-2024.sql.gz

# Then delete rows older than the archive cutoff. There is no UI for this —
# it requires a manual SQL session by design.
docker-compose -f docker-compose.yml exec postgres \
  psql -U trustedoss -d trustedoss \
  -c "DELETE FROM audit_logs WHERE created_at < '2025-01-01';"
```

The `DELETE` is **blocked at the DB layer** by the immutability triggers ([Schema](#schema)). For a deliberate retention purge, drop both triggers inside the same maintenance transaction, run the `DELETE`, and re-create the triggers before commit:

<!-- docs-uat: id=audit-retention-purge kind=sql ctx=postgres tier=nightly waiver=destructive-drops-triggers-and-deletes-rows -->
```sql
BEGIN;
DROP TRIGGER audit_logs_immutable_truncate ON audit_logs;
DROP TRIGGER audit_logs_immutable_trigger ON audit_logs;
DELETE FROM audit_logs WHERE created_at < '2025-01-01';
CREATE TRIGGER audit_logs_immutable_trigger
  BEFORE UPDATE OR DELETE ON audit_logs
  FOR EACH ROW EXECUTE FUNCTION audit_logs_prevent_mutation();
CREATE TRIGGER audit_logs_immutable_truncate
  BEFORE TRUNCATE ON audit_logs
  FOR EACH STATEMENT EXECUTE FUNCTION audit_logs_prevent_mutation();
COMMIT;

-- Verify both triggers are restored before exiting the maintenance window.
SELECT tgname FROM pg_trigger
 WHERE tgrelid = 'audit_logs'::regclass AND NOT tgisinternal;
-- expect exactly two rows:
--   audit_logs_immutable_trigger
--   audit_logs_immutable_truncate
```

Capture the operator action separately (the trigger DDL itself does not emit an audit row). Run with two operators present; the second operator runs the `pg_trigger` verification query above and confirms both triggers are listed before the session is closed.

## Verify it worked

After any privileged action:

<!-- docs-uat: id=audit-verify-new-row kind=sql ctx=postgres expect=rows:>0 tier=nightly -->
1. **/admin/audit** shows a new row at the top within ~1 second.

   ```sql
   SELECT count(*) FROM audit_logs
    WHERE created_at > now() - interval '1 hour';
   ```

<!-- docs-uat: id=audit-verify-request-id kind=sql ctx=postgres expect=rows:>0 tier=nightly -->
2. The `request_id` matches the `X-Request-ID` response header from the originating request.

   ```sql
   SELECT count(*) FROM audit_logs
    WHERE request_id IS NOT NULL
      AND created_at > now() - interval '1 hour';
   ```

<!-- docs-uat: id=audit-verify-diff-masked kind=sql ctx=postgres expect=rows:0 tier=nightly -->
3. The `diff` matches your expectation. PII fields (email, password hash, API keys) appear masked.

   ```sql
   -- credential columns must be masked to '***' on every fresh audit row
   SELECT count(*) FROM audit_logs
    WHERE target_table = 'refresh_tokens'
      AND action = 'create'
      AND created_at > now() - interval '1 hour'
      AND (diff ->> 'token_hash' <> '***' OR diff ->> 'jti' <> '***');
   ```

## Troubleshooting

### Expected entry is missing

Three possibilities:

- The action is read-only (no audit row).
- The action failed before the audit hook fired (a 500 before commit). Check the structured logs by `request_id`.
- The actor does not have permission to read this row (team-admin scope hides cross-team rows). Use a super-admin session.

### CSV export truncated

The export is capped at 100k rows. Narrow the filter or use the API with pagination.

### Cannot grep payloads

The `diff` column is `jsonb`. SQL queries against it are fast with the GIN index the migrations create:

<!-- docs-uat: id=audit-diff-query kind=sql ctx=postgres expect=ok tier=nightly -->
```sql
SELECT * FROM audit_logs
 WHERE diff @> '{"new_state": "suppressed"}'::jsonb
 ORDER BY created_at DESC LIMIT 100;
```

This requires a `super_admin` SQL session (no UI).

## Roadmap

The following capabilities are referenced in early docs but are **not** shipped in this release:

- Multi-select filters (Action multi-select, Target table multi-select), preset date ranges (last hour / today / last 7 days), exact-match Target ID filter, and Request ID filter on `/admin/audit`.
- An `actor_kind` column / filter (today the audit row's actor is identified by `actor_user_id`; API-key actors are inferred from the action context).
- Promote SBOM export (`sbom_exported`), NOTICE file download, and API-key revocation events from `structlog`-only into `audit_logs` rows — planned.

## See also

- [Users & teams](./users-and-teams.md)
- [Backup & restore](./backup-and-restore.md)
- [API overview](../reference/api-overview.md)
