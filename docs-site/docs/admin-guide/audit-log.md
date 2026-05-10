---
id: audit-log
title: Audit log
description: Read, filter, and export the append-only audit log of every write operation in TrustedOSS Portal.
sidebar_label: Audit log
sidebar_position: 4
---

# Audit log

Every write operation in the portal is recorded to an **append-only** audit log. The log is the source of truth for "who did what, when, and to what" — it is the first place to look when investigating an incident or fulfilling a compliance request.

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
| `action` | text | Dot-namespaced verb, e.g. `project.create`, `vuln_finding.update`, `team_membership.delete`. |
| `target_table` | text | Table the affected object lives in (`projects`, `teams`, `users`, `vuln_findings`, …). |
| `target_id` | UUID | The affected object's UUID. |
| `request_id` | text | Correlates with structured logs (`X-Request-ID`). |
| `diff` | jsonb | Sanitized before / after diff. PII is masked (`mask_pii`). |
| `ip` | inet | Source IP. |
| `user_agent` | text | Truncated UA string. |

The append-only contract is enforced at the application layer — the audit listener only emits inserts and the API exposes no update / delete endpoints. A DB-level `CHECK` / trigger that would block direct SQL is on the roadmap (see below); until then, do not run UPDATE / DELETE against `audit_logs` outside a deliberate, audited maintenance window.

## What gets logged

Every authenticated `POST`, `PATCH`, `PUT`, and `DELETE` produces exactly one entry. Read endpoints (`GET`) do not, with one exception: SBOM and report downloads emit a `*.export` event so you can prove what was disclosed and to whom.

System jobs (Celery) also log. Examples:

- `scan.create` (system, when a webhook triggers a scan)
- `dt_orphan.delete`
- `backup.complete`
- `notification.send`

## The audit log page

**/admin/audit** is a paginated, filterable view.

### Filters

The inline filter bar at v2.0.0:

- **Actor user ID** — exact UUID match.
- **Target table** — single-select from the enum (`projects`, `teams`, `users`, `vuln_findings`, …).
- **Action** — free-text contains (case-sensitive).
- **Date range** — `from` and `to` (custom).
- **Search** — free-text query (`q`); matches across action and target fields.

Filters compose. The URL updates so you can share a filtered view with a teammate. Multi-select dropdowns, preset date ranges, request-ID filter, and a target-ID filter are on the roadmap (see below).

### Table

Default columns: `created_at`, `actor`, `action`, `target`, `ip`. Click a row to expand the full diff.

The table is virtualized; 10k entries scroll smoothly.

## Export to CSV

The **Export CSV** button on the toolbar exports the **currently filtered** result set, up to 100k rows per export. The CSV is UTF-8 (no BOM at v2.0.0 — Excel users on Korean / Japanese locales should pick UTF-8 explicitly when opening; UTF-8 BOM emission is on the roadmap).

For larger windows, paginate via the API:

```bash
curl -sS \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  "https://trustedoss.example.com/v1/admin/audit?from=2026-01-01&to=2026-01-31&page=1&page_size=1000"
```

The response is paginated by `page` + `page_size`.

## Common queries

### "Who deleted project X?"

Filter: `action=project.delete`, `target_id=<project-uuid>`. There is exactly one row.

### "What did user Y do last week?"

Filter: `actor=y@acme.com`, date range last 7 days. The actions list summarizes the activity.

### "Who suppressed CVE-2024-12345 across all projects?"

Filter: `action=vuln_finding.update`, then expand each row's payload — the rows where `payload.new_state == "suppressed"` and the matching CVE ID are the answer. (A first-class CVE filter is on the roadmap.)

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

The `DELETE` is not blocked at the DB layer at v2.0.0 (the append-only contract is enforced in the application; see [Schema](#schema)). Run it inside a deliberate maintenance window with two operators present, and capture the operator action separately (the deletion itself does not emit an audit row).

## Verify it worked

After any privileged action:

1. **/admin/audit** shows a new row at the top within ~1 second.
2. The `request_id` matches the `X-Request-ID` response header from the originating request.
3. The `payload` diff matches your expectation. PII fields (email, password hash, API keys) appear masked.

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

- DB-level immutability (PostgreSQL trigger or `CHECK` blocking UPDATE / DELETE on `audit_logs`).
- UTF-8 BOM prefix on the CSV export so Excel auto-detects non-ASCII without manual selection.
- Multi-select filters (Action multi-select, Target table multi-select), preset date ranges (last hour / today / last 7 days), exact-match Target ID filter, and Request ID filter on `/admin/audit`.
- An `actor_kind` column / filter (today the audit row's actor is identified by `actor_user_id`; API-key actors are inferred from the action context).

## See also

- [Users & teams](./users-and-teams.md)
- [Backup & restore](./backup-and-restore.md)
- [API overview](../reference/api-overview.md)
