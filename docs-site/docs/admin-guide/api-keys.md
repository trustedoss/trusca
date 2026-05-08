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

Each key carries:

- **Owning team** — the team the key acts on behalf of. Cross-team API calls fail with 403.
- **Effective role** — the role the key inherits within that team. `developer` is the default; `team_admin` is supported for keys that need to manage settings (rare).
- **Allowed actions** — the operations the key can perform: `scan:trigger`, `scan:read`, `report:download`, `webhook:receive`, `*` (all).
- **Expiry** — `null` (no expiry, rare) or an ISO timestamp.

A typical CI key is `developer` + `["scan:trigger", "scan:read", "report:download"]` + 1-year expiry.

## Issuing a key

### As a team admin

1. **Project Settings → CI/CD → API keys** (or **Team settings → API keys** for team-wide keys).
2. **New API key**.
3. Fill in:
   - **Label** (e.g. `github-action-checkout-service`)
   - **Allowed actions** (multi-select; defaults to the CI minimum)
   - **Expiry** (preset 30 / 90 / 180 / 365 days, or custom)
4. **Create**.

The full key is shown **once** in a modal. Copy it and store it in your CI's secret store (GitHub secrets, GitLab CI variables, Jenkins credentials). After you close the modal, only the prefix is visible from the UI; the full key is unrecoverable.

### As a super-admin

The same flow, but the team selector is unlocked so you can issue keys for any team.

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

1. **Project Settings → CI/CD → API keys** → key row → **Revoke**.
2. Confirm.

Revocation is immediate and irreversible. To bring a key back, issue a new one.

## Listing keys

The UI shows: label, prefix, owning team, role, allowed actions, expiry, last-used timestamp, last-used IP. There is no way to recover the secret of an existing key — by design.

## Audit log

Every key operation logs:

- `api_key.create` — actor, target prefix, scope.
- `api_key.revoke` — actor, target prefix.
- `api_key.use` — implicit on every authenticated request via the API key (recorded as the `actor` on the action's audit row, with `actor_kind=api_key`).

Filter the audit log by `actor_kind=api_key` to see every action a non-interactive client performed.

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

Someone tried to brute-force the secret. The portal logs every miss; super-admins receive a Slack notification when a single key has more than 5 misses in 60 seconds. Revoke and rotate.

### Key works locally but not from CI

Confirm:

- The CI secret is set on the right environment / branch.
- The runner's outbound IP is not blocked by your portal firewall (some installs whitelist office IPs only).
- The `Authorization` header is preserved through any reverse proxy your CI traffic transits.

## See also

- [GitHub Actions](../ci-integration/github-actions.md)
- [GitLab CI](../ci-integration/gitlab-ci.md)
- [Webhooks](../ci-integration/webhooks.md)
- [Audit log](./audit-log.md)
