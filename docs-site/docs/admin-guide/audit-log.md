---
id: audit-log
title: Audit log
description: Read, filter, and export the append-only audit log of every write operation in TrustedOSS Portal.
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
| `action` | text | Either a bare verb (`create` / `update` / `delete`) for ORM-tracked table mutations, or a dot-namespaced event name (`backup.completed`, `oauth.identity.unlinked`, etc.) for explicit business-event audit calls. The verb-only form is what `target_table` automatically pairs with; explicit event names carry their own meaning and may use a `target_table` of `None`. Filter as `target_table=projects&action=create` for the verb form. |
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

## What gets logged

Every authenticated `POST`, `PATCH`, `PUT`, and `DELETE` produces exactly one entry. Read endpoints (`GET`) do not. SBOM exports emit a structlog `sbom_exported` event but **do not** create an `audit_logs` row at v2.0.0; integrating exports into the audit table is on the roadmap.

System jobs (Celery) also log. Examples cover both action forms — bare verbs from the ORM listener and dot-namespaced events from explicit audit calls:

- `target_table=scans&action=create` (system, when a webhook triggers a scan)
- `target_table=dt_orphans&action=delete`
- `target_table=backups&action=create`
- `target_table=notifications&action=create`
- `action=backup.completed` (explicit business event from `tasks/backup.py`; the row's `target_table` is `None`)
- `action=backup.failed`, `action=backup.pruned`, `action=backup.restored`, `action=backup.restore_failed` (sibling backup lifecycle events)
- `action=oauth.identity.unlinked` (explicit event from `services/oauth_identity_service.py`, paired with the underlying `oauth_identities&action=delete` row)

## The audit log page

**/admin/audit** is a paginated, filterable view.

### Filters

The inline filter bar at v2.0.0:

- **Actor user ID** — exact UUID match.
- **Target table** — single-select from the enum (`projects`, `teams`, `users`, `vulnerability_findings`, …).
- **Action** — free-text contains (case-sensitive).
- **Date range** — `from` and `to` (custom).
- **Search** — free-text query (`q`) performs an `ilike` match against the JSON-encoded `diff` column. Action and target_table are separate filter params (`action=`, `target_table=`).

Filters compose. The URL updates so you can share a filtered view with a teammate. Multi-select dropdowns, preset date ranges, request-ID filter, and a target-ID filter are on the roadmap (see below).

### Table

Default columns: `created_at`, `actor`, `action`, `target`, `ip`. Click a row to expand the full diff.

The table is virtualized; 10k entries scroll smoothly.

## Export to CSV

The **Export CSV** button on the toolbar exports the **currently filtered** result set, up to 100k rows per export. The CSV is UTF-8 with a leading byte-order mark (`EF BB BF`) so Excel on Korean / Japanese / Chinese locales auto-detects the encoding instead of falling back to CP949 / SJIS / GB18030 and rendering non-ASCII actor emails or audit row diffs as mojibake. Tools that already auto-detect UTF-8 (LibreOffice, awk, Python's `csv` / `utf-8-sig` codecs) silently strip the BOM.

For larger windows, paginate via the API:

```bash
curl -sS \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  "https://trustedoss.example.com/v1/admin/audit?from=2026-01-01&to=2026-01-31&page=1&page_size=1000"
```

The response is paginated by `page` + `page_size`.

## Common queries

### "Who deleted project X?"

Filter: `target_table=projects&action=delete&target_id=<project-uuid>`. There is exactly one row.

### "What did user Y do last week?"

Filter: `actor=y@acme.com`, date range last 7 days. The actions list summarizes the activity.

### "Who suppressed CVE-2024-12345 across all projects?"

Filter: `target_table=vulnerability_findings&action=update`, then expand each row's diff — the rows where `diff.new_state == "suppressed"` and the matching CVE ID are the answer. (A first-class CVE filter is on the roadmap.)

### "Trace one request end-to-end"

When a user reports an error, ask them for the `X-Request-ID` shown on the error page. Filter the audit log by that `request_id` and you get the canonical record of every write the request triggered. Cross-reference with structured logs:

```bash
docker-compose -f docker-compose.yml logs backend \
  | jq -c "select(.request_id == \"$REQ\")"
```

## Retention

The audit log is **never auto-pruned**. Storage is cheap relative to its compliance value (a typical install grows by ~50 MB / year per active user). If you need to reduce the table size, the recommended path is **archive then truncate** with operator confirmation:

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

1. **/admin/audit** shows a new row at the top within ~1 second.
2. The `request_id` matches the `X-Request-ID` response header from the originating request.
3. The `diff` matches your expectation. PII fields (email, password hash, API keys) appear masked.

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

```sql
SELECT * FROM audit_logs
 WHERE diff @> '{"new_state": "suppressed"}'::jsonb
 ORDER BY created_at DESC LIMIT 100;
```

This requires a `super_admin` SQL session (no UI).

## Roadmap (v2.x)

The following capabilities are referenced in early docs but are **not** shipped at v2.0.0:

- Multi-select filters (Action multi-select, Target table multi-select), preset date ranges (last hour / today / last 7 days), exact-match Target ID filter, and Request ID filter on `/admin/audit`.
- An `actor_kind` column / filter (today the audit row's actor is identified by `actor_user_id`; API-key actors are inferred from the action context).

## See also

- [Users & teams](./users-and-teams.md)
- [Backup & restore](./backup-and-restore.md)
- [API overview](../reference/api-overview.md)
