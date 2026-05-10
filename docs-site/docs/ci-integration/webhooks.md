---
id: webhooks
title: Webhooks
description: Configure GitHub and GitLab webhooks to trigger TrustedOSS scans on push and PR/MR events with HMAC signature verification.
sidebar_label: Webhooks
sidebar_position: 4
---

# Webhooks

Webhooks let your Git host push events to the portal — typically `push` and `pull_request` (GitHub) / `merge_request` (GitLab) — and the portal kicks off a scan automatically. They are an alternative to running the scan from CI; many teams use both.

:::note Audience
`team_admin` configuring per-project webhooks; engineers wiring up the Git-host side. The portal endpoint is reachable from the public internet.
:::

## Endpoints

| Source | URL | Auth |
|---|---|---|
| GitHub | `POST https://trustedoss.example.com/api/v1/webhooks/github` | HMAC-SHA256 signature in `X-Hub-Signature-256`. |
| GitLab | `POST https://trustedoss.example.com/api/v1/webhooks/gitlab` | Token in `X-Gitlab-Token`. |

Both endpoints are public (no JWT) but require the project's webhook secret. The secret is per-project, generated when you enable the webhook.

## Setup — GitHub

### 1. Enable the webhook in the portal

At v2.0.0 webhook activation is operator-only. The Project Settings tab does not yet expose webhook controls. Operators bootstrap a per-project `webhook_secret` server-side (see `apps/backend/services/webhook_service.py`); the resulting webhook URL is shown in the **Integrations** page → Webhooks section. A self-service activation UI is on the roadmap.

### 2. Configure on GitHub

1. Repo → **Settings → Webhooks → Add webhook**.
2. **Payload URL**: the delivery URL.
3. **Content type**: `application/json`.
4. **Secret**: the secret you copied from the portal.
5. **Which events**: choose
   - **Push** events.
   - **Pull requests** events.
6. **Active**: yes.
7. **Add webhook**.

GitHub immediately delivers a `ping` event. Confirm it shows green ("Last delivery was successful") — see [troubleshooting](#troubleshooting) if it does not.

### 3. Verify

Push a commit. In the portal: **Project → Scans** should show a new scan within ~30 seconds.

## Setup — GitLab

### 1. Enable the webhook in the portal

At v2.0.0 webhook activation is operator-only. The Project Settings tab does not yet expose webhook controls. Operators bootstrap a per-project `webhook_secret` server-side (see `apps/backend/services/webhook_service.py`); the resulting webhook URL is shown in the **Integrations** page → Webhooks section. A self-service activation UI is on the roadmap.

### 2. Configure on GitLab

1. Project → **Settings → Webhooks → Add new webhook**.
2. **URL**: the delivery URL.
3. **Secret token**: the token you copied from the portal.
4. **Trigger**: check
   - Push events
   - Merge request events
5. **SSL verification**: enabled.
6. **Add webhook**.

Use the **Test → Push event** button to verify connectivity. The portal logs the delivery and acks 204.

### 3. Verify

Push a commit. The portal's scan queue picks it up within ~30 seconds.

## Signature verification

### GitHub — HMAC-SHA256

GitHub computes:

```
X-Hub-Signature-256: sha256=<hex(hmac_sha256(secret, body))>
```

The portal recomputes the same HMAC over the raw body and compares using a constant-time check. A mismatch returns 401 and the delivery is logged.

### GitLab — token equality

GitLab sends the token verbatim:

```
X-Gitlab-Token: <token>
```

The portal compares against the project's stored token using a constant-time check. Mismatch returns 401.

GitLab does not support HMAC by default. If your security policy requires HMAC, put a reverse proxy in front that adds it, and verify the proxy in the portal layer.

## Idempotency

Both Git hosts retry deliveries on failure. The portal handles repeats with `delivery_id` deduplication:

- GitHub provides `X-GitHub-Delivery` (a UUID per delivery).
- GitLab provides `X-Gitlab-Event-UUID` (a UUID per delivery, since 14.x).

The portal stores `(source, delivery_id)` in `webhook_deliveries` with a unique index. A duplicate delivery returns 200 with `{"status": "duplicate"}` instead of triggering a second scan. This keeps the system idempotent across host-side retry storms.

## Events that trigger a scan

| Event | Action |
|---|---|
| GitHub `push` to default branch | Triggers a `source` scan against the new commit. |
| GitHub `pull_request` (opened, synchronize, reopened) | Triggers a `source` scan against the PR head SHA, posts SCA comment. |
| GitLab `Push Hook` to default branch | Same as GitHub `push`. |
| GitLab `Merge Request Hook` (open, update, reopen) | Same as GitHub `pull_request`. |

Other events are accepted (200) but do not trigger scans. The portal records every accepted delivery in the audit log.

## Verify it worked

After configuring a webhook:

1. The Git host's webhook page shows a successful **ping / test** delivery.
2. Pushing a commit creates a new scan in the portal within 30 seconds.
3. The audit log records `webhook.deliver` with `delivery_id` and `event` fields.

## Troubleshooting

### "Could not deliver: 401 Unauthorized"

The signature does not match. Causes:

- Webhook secret was rotated in the portal but not updated on the Git host.
- The proxy in front of the portal modifies the body (compression, JSON re-serialization). The signature is over the raw bytes — a single byte change invalidates it.

Re-sync: rotate the secret in the portal, paste the new value into the Git host, and trigger a redelivery.

### "Could not deliver: 404 Not Found"

The URL is wrong. Common typos: missing `/api/`, missing `/v1/`, hitting the frontend instead of the backend (`/webhooks/github` instead of `/api/v1/webhooks/github`).

### Webhook fires but no scan appears

The delivery was accepted but did not trigger. Possible reasons:

- The push was to a branch other than the project's default branch. The portal scans only the default branch (configurable per project — see [Projects](../user-guide/projects.md)).
- The PR's head SHA is identical to a previous scan's commit (e.g. force-push that re-uses the SHA). The portal deduplicates by SHA.

### Old deliveries replay after a portal outage

Both GitHub and GitLab queue undelivered events for ~24 hours. When the portal comes back, deliveries replay. Idempotency (above) prevents duplicate scans. To skip the replay, manually clear the queue from the Git host before bringing the portal back up — but most installs benefit from the replay because they catch the events that fired during the outage.

### Want HMAC on GitLab

Run the GitLab webhook through a small proxy (e.g. nginx with a Lua snippet, or a tiny Cloudflare Worker) that adds an HMAC header. Configure the portal to require it via a custom middleware. This is non-default and out of scope for the bundled deployment.

## See also

- [GitHub Actions](./github-actions.md)
- [GitLab CI](./gitlab-ci.md)
- [API keys](../admin-guide/api-keys.md)
- [Audit log](../admin-guide/audit-log.md)
