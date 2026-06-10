---
id: api-keys
title: API keys
description: Issue, scope, and rotate API keys for service accounts and CI integrations in TrustedOSS Portal.
sidebar_label: API keys
sidebar_position: 6
---

# API keys

API keys are credentials for **non-interactive** clients — CI runners, webhooks, scripts, and the GitHub Action. They authenticate machine-to-machine traffic without consuming a user's JWT session.

:::note Audience
`team_admin` (issues team-scoped keys) and `super_admin` (issues org-scoped keys).
:::

## Manage with the /integrations UI

Most users issue and revoke their own keys from the [Integrations page](../user-guide/integrations.md). The `/integrations` UI:

- Lists every key the signed-in user is permitted to manage.
- Opens a one-time reveal modal on **Create**, with a copy-to-clipboard button and a hard warning that the full key is shown only once.
- Offers per-row **Revoke** with a confirmation dialog; revocation propagates within ~5 seconds.

![/integrations — API keys section that admins use to mint and revoke keys](/img/screenshots/user-integrations-keys.png)

The Create dialog is the same surface for `team_admin` and `super_admin`; the scope dropdown adds `org` for super-admins:

![/integrations — Create API key dialog with label and scope inputs](/img/screenshots/user-integrations-key-create.png)

This page covers the **server-side mechanics** — key shape, hashing, scope semantics, audit log, and rotation strategy. Users who only need to wire a key into CI can stop at the [Integrations user guide](../user-guide/integrations.md).

## Key shape

```
tos_<8-char-prefix>_<32-char-secret>
```

Example: `tos_a1b2c3d4_eaff8b91d36c5e0a2f1c4d7e8a9b0c2d`.

- **`tos_`** — fixed prefix.
- **`<8-char-prefix>`** — random, **public**. Used for lookup and as a display label. Visible in the audit log.
- **`<32-char-secret>`** — random, **private**. Stored only as a bcrypt hash on the server. The full key is shown to the operator **once**, at creation, and never again.

Lookups are constant-time across the prefix; secret comparison uses `bcrypt.checkpw` to defeat timing attacks.

## Scope model

Each key carries a single **resource scope** that determines the authorization boundary:

- **`org`** — acts org-wide; can call any endpoint the issuing user could.
- **`team`** — acts on behalf of a specific team; cross-team calls fail with 403.
- **`project`** — bound to a specific project; calls outside that project fail with 403.

Who can issue each scope:

| Scope    | Who can issue       |
|----------|---------------------|
| `org`    | super-admin only    |
| `team`   | super-admin, team-admin |
| `project`| super-admin, team-admin, developer (within their team's projects) |

The key inherits the **role of the issuing user** at request time — there is no separate "effective role" or "allowed actions" list in this release. Permission checks fall through to the same RBAC code path as a JWT-authenticated request.

Keys support an **optional expiry (TTL)**. Pass `expires_in_days` (1–1825) when issuing and the key stops authenticating after that many days — a leaked CI key (pipeline log, forked-PR runner) then lapses on its own instead of living until manual revocation. Omit it for a non-expiring key (the legacy default). CI keys should set a TTL and rotate. A fine-grained `allowed_actions` taxonomy (`scan:trigger`, `scan:read`, `report:download`, …) is still on the roadmap.

<!-- docs-uat: id=apikeys-create-ttl kind=shell ctx=host tier=manual waiver=example-host-and-jwt-placeholder -->
```bash
curl -sS -X POST "https://trustedoss.example.com/v1/api-keys" \
  -H "Authorization: Bearer ${JWT}" -H "Content-Type: application/json" \
  -d '{"name": "ci-key", "scope": "project", "project_id": "<uuid>", "expires_in_days": 90}'
```

## Issuing a key

### As a team admin

1. Open **/integrations** (top-level sidebar entry, available to `team_admin` and above).
2. Switch to the **API keys** tab.
3. Click **New API key**.
4. Fill in:
   - **Label** (e.g. `github-action-checkout-service`)
   - **Scope** — `team` (default) or `project`
   - **Project** — required when scope is `project`
5. **Create**.

The full key is shown **once** in a modal. Copy it and store it in your CI's secret store (GitHub secrets, GitLab CI variables, Jenkins credentials). After you close the modal, only the prefix is visible from the UI; the full key is unrecoverable.

### As a super-admin

The same flow at **/integrations**, with the additional option to set the scope to `org` for keys that cross team boundaries (rare — most CI integrations should stay at `team` or `project` scope).

## Using an API key

Pass the key in the `Authorization` header as a Bearer token:

<!-- docs-uat: id=apikeys-use-curl kind=shell ctx=host tier=nightly waiver=example-host-and-placeholder-bearer-token -->
```bash
curl -sS -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
  https://trustedoss.example.com/v1/projects
```

The portal logs the prefix on every request to help with traceability.

## Rotation

### Why rotate

- **Compromise** — the key was committed to a public repo, or a CI runner was breached. **Revoke immediately.**
- **Personnel change** — the team admin who issued the key is leaving. Issue a fresh key, swap CI secrets, then revoke the old one.
- **Policy** — quarterly rotation as a defence-in-depth measure.

### How to rotate without downtime

1. **Issue a new key** with the same scope.
2. **Update CI secrets** to the new key.
3. **Wait** for one CI cycle to confirm the new key works.
4. **Revoke** the old key.

The old key is rejected within ~5 seconds of revocation (the auth cache TTL).

## Revocation

1. **/integrations → API keys** → key row → **Revoke**.
2. Confirm.

Revocation is immediate and irreversible. To bring a key back, issue a new one.

## Listing keys

The UI shows: label, prefix, scope (`org` / `team` / `project`), creator, created timestamp, last-used timestamp, expiry (`expires_at`, null when non-expiring), and revocation status. There is no way to recover the secret of an existing key — by design. Per-key role, allowed-actions, and last-used IP columns are on the roadmap (the corresponding model columns are not yet present).

## Audit log

Key lifecycle events log:

- `target_table=api_keys&action=create` — emitted by the ORM listener when a key row is inserted (actor, target prefix, scope).
- `api_key.revoked` — emitted by the API key service as a **structlog event only** on explicit revocation (actor, target prefix). It does **not** create an `audit_logs` row in this release. The ORM listener still records the underlying `api_keys.update` row when `revoked_at` flips, so the revocation is captured on the audit table — under `target_table=api_keys&action=update` rather than under the structlog event name.

Per-request audit rows are not emitted for API-key authentication in this release (an `api_key.use` event is on the roadmap). Audit rows that are produced by an API-key request still carry the resulting domain action (e.g. `target_table=scans&action=create`); the API key's prefix is captured by structured logs on the request, but the audit row's `actor_user_id` is the issuing user, not the key.

## Webhook secrets vs. API keys

These are not interchangeable. The portal distinguishes:

- **API keys** — outbound from a CI client to the portal API.
- **Webhook secrets** — used to verify inbound HMAC signatures on webhooks (GitHub `X-Hub-Signature-256`, GitLab `X-Gitlab-Token`).

See [Webhooks](../ci-integration/webhooks.md) for the webhook flow.

## Verify it worked

After issuing a key:

<!-- docs-uat: id=apikeys-verify-curl kind=manual tier=manual -->
1. `curl -sS -H "Authorization: Bearer <key>" .../v1/projects` returns 200 with the team's projects.
<!-- docs-uat: id=apikeys-verify-audit-row kind=manual tier=manual -->
2. The audit log records a `target_table=api_keys&action=create` row with the prefix. The Admin UI cannot filter on `target_table=api_keys` — `api_keys` is not in the `AuditTargetTable` whitelist (see [Audit log → Filter-visible vs raw-row tables](./audit-log.md#what-gets-logged)). Use raw SQL to verify:

   ```sql
   SELECT * FROM audit_logs
    WHERE target_table = 'api_keys'
      AND action = 'create'
      AND created_at > now() - interval '1 hour'
    ORDER BY created_at DESC;
   ```

<!-- docs-uat: id=apikeys-verify-ci-build kind=manual tier=manual -->
3. The CI build that consumes the key passes its first run.

## Troubleshooting

### 401 with a freshly created key

The two most common causes:

- The key value is malformed. Leading and trailing whitespace around the bearer value is stripped before authentication, so accidental surrounding whitespace is tolerated — but whitespace *inside* the key breaks it. Re-paste from the original modal: keys are exactly `tos_` + 8 + `_` + 32 chars.
- The portal distinguishes the two failure modes:
  - **401** = credential problem (no header, malformed Bearer, unknown
    prefix, signature mismatch, revoked, expired).
  - **403** = credential is valid but the key's scope does not cover the
    resource (e.g. `team`-scope key hitting an `org`-only endpoint).

### "Key prefix exists but secret does not match"

Someone tried to brute-force the secret, or a malformed key was sent. The portal logs every miss as an `api_key.auth_failed` event (with the key `key_prefix`, never the secret) in the structured backend log. Brute-force detection (a Slack alert when a single key crosses N misses per minute) is on the roadmap; until then, periodically grep the backend logs for repeated `api_key.auth_failed` lines:

<!-- docs-uat: id=apikeys-secret-mismatch-grep kind=shell ctx=host tier=nightly waiver=production-compose-log-grep-diagnostic -->
```bash
docker-compose -f docker-compose.yml logs --tail=2000 backend \
  | grep api_key.auth_failed | sort | uniq -c | sort -rn | head
```

If you see a single prefix repeating, revoke and rotate immediately.

### Key works locally but not from CI

Confirm:

- The CI secret is set on the right environment / branch.
- The runner's outbound IP is not blocked by your portal firewall (some installs whitelist office IPs only).
- The `Authorization` header is preserved through any reverse proxy your CI traffic transits.

## Roadmap

The following capabilities are referenced in early docs but are **not** shipped in this release:

- Per-key role override (`effective_role`) and a granular `allowed_actions` taxonomy (`scan:trigger`, `scan:read`, `report:download`, `webhook:receive`, `*`). Today the key inherits the issuing user's role and the full RBAC surface.
- The 30 / 90 / 180 / 365-day expiry presets in the New API key form (the API's `expires_in_days` / `expires_at` already ship — only the UI presets are on the roadmap).
- Per-request `api_key.use` audit event with `actor_kind = api_key`. Today key lifecycle (the ORM-listener insert and the explicit `api_key.revoked` action) is audited but per-request use is captured only in structured logs.
- `last_used_ip` column in the listing.
- Brute-force secret-mismatch alerting (Slack notification when a single key crosses 5 misses / 60 s).

## See also

- [GitHub Actions](../ci-integration/github-actions.md)
- [GitLab CI](../ci-integration/gitlab-ci.md)
- [Webhooks](../ci-integration/webhooks.md)
- [Audit log](./audit-log.md)
