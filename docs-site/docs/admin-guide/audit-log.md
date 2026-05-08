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
| `id` | UUIDv7 | Primary key. Lexicographically sortable by time. |
| `ts` | timestamptz | When the action occurred (server clock, UTC). |
| `actor_user_id` | UUID | The user who performed the action (null for system jobs). |
| `actor_kind` | enum | `user`, `api_key`, `system`. |
| `action` | text | Dot-namespaced verb, e.g. `project.create`, `vuln_finding.update`, `team_membership.delete`. |
| `target_kind` | text | Object class affected (`project`, `team`, `user`, `vuln_finding`, …). |
| `target_id` | UUID | The affected object's UUID. |
| `request_id` | text | Correlates with structured logs (`X-Request-ID`). |
| `payload` | jsonb | Sanitized before / after diff. PII is masked (`mask_pii`). |
| `ip` | inet | Source IP. |
| `user_agent` | text | Truncated UA string. |

The table has a `CHECK` constraint that prevents updates and deletes — only inserts are allowed. Forward-only Alembic migrations preserve this property across releases.

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

The inline filter bar:

- **Actor** — search by email, user ID, or `system`.
- **Action** — multi-select.
- **Target kind** — multi-select.
- **Target ID** — exact match.
- **Date range** — preset (last hour, today, last 7 days) or custom.
- **Request ID** — exact match (handy when you have a structured-log line).

Filters compose. The URL updates so you can share a filtered view with a teammate.

### Table

Default columns: `ts`, `actor`, `action`, `target`, `ip`. Click a row to expand the full payload diff.

The table is virtualized; 10k entries scroll smoothly.

## Export to CSV

The **Export CSV** button on the toolbar exports the **currently filtered** result set, up to 100k rows per export. The CSV is UTF-8 with BOM so Excel handles non-ASCII correctly.

For larger windows, paginate via the API:

```bash
curl -sS \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  "https://trustedoss.example.com/api/v1/admin/audit?from=2026-01-01&to=2026-01-31&page=1&size=1000"
```

The response is paginated; `next` is null when you are at the last page.

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
  pg_dump -U trustedoss -t audit_log trustedoss | gzip > audit-archive-2024.sql.gz

# Then delete rows older than the archive cutoff. There is no UI for this —
# it requires a manual SQL session by design.
docker-compose -f docker-compose.yml exec postgres \
  psql -U trustedoss -d trustedoss \
  -c "DELETE FROM audit_log WHERE ts < '2025-01-01';"
```

The `DELETE` requires temporarily disabling the immutability constraint, which itself emits an audit-log entry. Use only for genuinely old data.

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

The `payload` column is `jsonb`. SQL queries against it are fast with the GIN index the migrations create:

```sql
SELECT * FROM audit_log
 WHERE payload @> '{"new_state": "suppressed"}'::jsonb
 ORDER BY ts DESC LIMIT 100;
```

This requires a `super_admin` SQL session (no UI).

## See also

- [Users & teams](./users-and-teams.md)
- [Backup & restore](./backup-and-restore.md)
- [API overview](../reference/api-overview.md)
