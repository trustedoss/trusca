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

- **`org`** — issued by a `super_admin`. Acts org-wide; can call any endpoint the issuing user could.
- **`team`** — issued by a `team_admin`. Acts on behalf of a specific team; cross-team calls fail with 403.
- **`project`** — issued by a `team_admin` or `developer` for a specific project; calls outside that project fail with 403.

The key inherits the **role of the issuing user** at request time — there is no separate "effective role" or "allowed actions" list at v2.0.0. Permission checks fall through to the same RBAC code path as a JWT-authenticated request.

Keys are **non-expiring** at v2.0.0. They are valid until manually revoked. Per-key expiry presets and a fine-grained `allowed_actions` taxonomy (`scan:trigger`, `scan:read`, `report:download`, …) are on the roadmap.

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

Pass the key in the `Authorization` header:

```bash
curl -sS -H "Authorization: ApiKey ${TRUSTEDOSS_API_KEY}" \
  https://trustedoss.example.com/api/v1/projects
```

Both `Authorization: ApiKey <key>` and `Authorization: Bearer <key>` are accepted; `ApiKey` is preferred for clarity. The portal logs the prefix on every request to help with traceability.

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

The UI shows: label, prefix, scope (`org` / `team` / `project`), creator, created timestamp, last-used timestamp, and revocation status. There is no way to recover the secret of an existing key — by design. Per-key role, allowed-actions, expiry, and last-used IP columns are on the roadmap (the corresponding model columns are not yet present).

## Audit log

Key lifecycle events log:

- `api_key.create` — actor, target prefix, scope.
- `api_key.revoke` — actor, target prefix.

Per-request audit rows are not emitted for API-key authentication at v2.0.0 (an `api_key.use` event is on the roadmap). Audit rows that are produced by an API-key request still carry the resulting domain action (e.g. `scan.create`); the API key's prefix is captured by structured logs on the request, but the audit row's `actor_user_id` is the issuing user, not the key.

## Webhook secrets vs. API keys

These are not interchangeable. The portal distinguishes:

- **API keys** — outbound from a CI client to the portal API.
- **Webhook secrets** — used to verify inbound HMAC signatures on webhooks (GitHub `X-Hub-Signature-256`, GitLab `X-Gitlab-Token`).

See [Webhooks](../ci-integration/webhooks.md) for the webhook flow.

## Verify it worked

After issuing a key:

1. `curl -sS -H "Authorization: ApiKey <key>" .../api/v1/projects` returns 200 with the team's projects.
2. The audit log records `api_key.create` with the prefix.
3. The CI build that consumes the key passes its first run.

## Troubleshooting

### 401 with a freshly created key

The two most common causes:

- The key was copied with a leading or trailing whitespace. Re-paste from the original modal — keys are exactly `tos_` + 8 + `_` + 32 chars.
- The key's allowed actions do not include the operation. The error response distinguishes 401 (bad key) from 403 (key valid but action not permitted).

### "Key prefix exists but secret does not match"

Someone tried to brute-force the secret, or a malformed key was sent. The portal logs every miss in the structured backend log. Brute-force detection (a Slack alert when a single key crosses N misses per minute) is on the roadmap; until then, periodically grep the backend logs for repeated `secret_mismatch` lines:

```bash
docker-compose -f docker-compose.yml logs --tail=2000 backend \
  | grep secret_mismatch | sort | uniq -c | sort -rn | head
```

If you see a single prefix repeating, revoke and rotate immediately.

### Key works locally but not from CI

Confirm:

- The CI secret is set on the right environment / branch.
- The runner's outbound IP is not blocked by your portal firewall (some installs whitelist office IPs only).
- The `Authorization` header is preserved through any reverse proxy your CI traffic transits.

## Roadmap (v2.x)

The following capabilities are referenced in early docs but are **not** shipped at v2.0.0:

- Per-key role override (`effective_role`) and a granular `allowed_actions` taxonomy (`scan:trigger`, `scan:read`, `report:download`, `webhook:receive`, `*`). Today the key inherits the issuing user's role and the full RBAC surface.
- Per-key `expires_at` field and the 30 / 90 / 180 / 365-day expiry presets in the New API key form. Today keys do not expire — they live until revoked.
- Per-request `api_key.use` audit event with `actor_kind = api_key`. Today key lifecycle (`api_key.create` / `api_key.revoke`) is audited but per-request use is captured only in structured logs.
- `last_used_ip` column in the listing.
- Brute-force secret-mismatch alerting (Slack notification when a single key crosses 5 misses / 60 s).

## See also

- [GitHub Actions](../ci-integration/github-actions.md)
- [GitLab CI](../ci-integration/gitlab-ci.md)
- [Webhooks](../ci-integration/webhooks.md)
- [Audit log](./audit-log.md)
