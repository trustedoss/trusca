---
id: webhooks
title: Webhooks
description: Configure GitHub and GitLab webhooks to trigger TRUSCA scans on push and PR/MR events with HMAC signature verification.
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
| GitHub | `POST https://trustedoss.example.com/v1/webhooks/github` | HMAC-SHA256 signature in `X-Hub-Signature-256`. |
| GitLab | `POST https://trustedoss.example.com/v1/webhooks/gitlab` | Token in `X-Gitlab-Token`. |

Both endpoints are public (no JWT) but require the project's webhook secret. The secret is per-project, generated when you enable the webhook.

### Issuing a webhook secret

Ask the portal for one. The secret is stored encrypted, so it cannot be written
by hand in SQL, and this is the only way to produce it.

<!-- docs-uat: id=webhooks-secret-issue kind=http tier=manual -->
```http
POST /v1/projects/{project_id}/webhook-secret
Authorization: Bearer <token>
Content-Type: application/json

{"provider": "github"}
```

`provider` is `github` or `gitlab` and is required. The gateway matches a
delivery on the project's git URL, its secret, AND the provider, so that one
provider's secret cannot be replayed against the other. There is no shape of
this request that sets one and not the other.

The response carries the secret once:

```json
{
  "project_id": "...",
  "provider": "github",
  "issued_at": "2026-09-05T09:00:00Z",
  "replaced_existing": false,
  "secret": "..."
}
```

Capture it here. It is stored as ciphertext and no endpoint returns it again;
`GET /v1/projects/{project_id}/webhook` answers whether one is set and for
which provider, never what it is. Losing it means issuing a new one.

Paste it into GitHub/GitLab → Settings → Webhooks → "Secret".

Calling this again on a project that already has a secret replaces it, and
`replaced_existing` comes back `true`. The old value stops being accepted
immediately, so deliveries fail until the new one reaches the SCM. That is the
rotation path; there is no separate one.

Requires `team_admin` on the team that owns the project.

A self-service activation UI lives on the roadmap.

## Setup — GitHub

### 1. Enable the webhook in the portal

Issue the secret with `POST /v1/projects/{project_id}/webhook-secret` as above. The Project Settings tab does not yet expose webhook controls, so the call is how activation happens; the resulting webhook URL is shown in the **Integrations** page → Webhooks section. A self-service activation UI is on the roadmap.

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

Issue the secret with `POST /v1/projects/{project_id}/webhook-secret` as above. The Project Settings tab does not yet expose webhook controls, so the call is how activation happens; the resulting webhook URL is shown in the **Integrations** page → Webhooks section. A self-service activation UI is on the roadmap.

### 2. Configure on GitLab

1. Project → **Settings → Webhooks → Add new webhook**.
2. **URL**: the delivery URL.
3. **Secret token**: the token you copied from the portal.
4. **Trigger**: check
   - Push events
   - Merge request events
5. **SSL verification**: enabled.
6. **Add webhook**.

Use the **Test → Push event** button to verify connectivity. The portal logs the delivery and acks 200 with a JSON body naming the outcome.

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

## Request limits

Both receivers are public: the signature covers the body, so the body has to be read and the repository resolved before the portal can tell whether the caller is anyone at all. Two limits bound what that costs.

| Setting | Default | Refused with |
|---|---|---|
| `WEBHOOK_MAX_BODY_BYTES` | 2 MiB | `413` |
| `WEBHOOK_RATE_LIMIT` | `120/minute` per source IP | `429` |

A real delivery is far below both. GitHub caps the commit list on a push payload and refuses to deliver anything over 25 MB, and 120 deliveries a minute from one address is more than a busy organisation sends.

Unlike the `skipped_*` statuses, these two are errors: the delivery is not recorded and nothing is scanned. Neither Git host retries a 4xx on its own, so a delivery refused here needs a manual redelivery from the host after you raise the limit. Raise `WEBHOOK_RATE_LIMIT` rather than lose events if a large install trips it. The rate limit is keyed on IP because that is the only identity available before the signature is checked, and a Git host delivers every repository's events from one address range.

## Idempotency

Both Git hosts retry deliveries on failure. The portal handles repeats with `delivery_id` deduplication:

- GitHub provides `X-GitHub-Delivery` (a UUID per delivery).
- GitLab provides `X-Gitlab-Webhook-UUID` (a UUID per delivery). Older GitLab versions omit it; the portal then falls back to an id derived from the payload, which is coarser — see [Troubleshooting](#a-second-push-to-the-same-merge-request-does-not-scan).

The portal stores `(source, delivery_id)` in `webhook_deliveries` with a unique index. A duplicate delivery returns 200 with `{"status": "duplicate"}` instead of triggering a second scan. This keeps the system idempotent across host-side retry storms.

## Events that trigger a scan

| Event | Action |
|---|---|
| GitHub `push` — any branch or tag | Triggers a `source` scan, keyed to that ref. |
| GitHub `pull_request` — `opened`, `synchronize`, `reopened` | Triggers a `source` scan keyed to `pr-<number>`. |
| GitLab `Push Hook` — any branch or tag | Same as GitHub `push`. |
| GitLab `Merge Request Hook` — `open`, `reopen`, `update` | Triggers a `source` scan keyed to `mr-<iid>`. |

Other events are accepted (200) but do not trigger scans, and so are pull request actions outside the list above — `closed`, `labeled`, `assigned` and the rest cannot change the dependency set. Every accepted delivery is recorded in `webhook_deliveries`, which the audit listener logs as `action=create`, `target_table=webhook_deliveries`.

Two things are worth knowing before enabling this on a busy repository.

There is no branch filter. A push to any branch or tag enqueues a scan, so a batch push of many branches enqueues one per ref. Select the events you want on the Git host side if that is more traffic than you want.

Scans check out the ref that triggered them: the branch that was pushed, or the pull request's merge ref. A pull request that adds a dependency is therefore visible to the scan its own event triggered.

If that ref no longer exists when the worker reaches it — a pull request merged or force-pushed while the scan sat in the queue — the scan falls back to the remote's default branch and records `metadata.ref_fallback` with the ref it wanted and why the fetch failed. A verdict from a fallback describes different code than the one requested, so check that field before reading such a scan as a statement about the pull request.

## What the response status means

| `status` | Meaning |
|---|---|
| `enqueued` | A scan was created. `scan_id` names it. |
| `duplicate` | This delivery id was already recorded — a replay from the Git host's retry logic. Nothing was done, which is correct. |
| `ignored` | The event is not one we scan on: an unlisted type, or a pull request action that cannot change dependencies. |
| `skipped_active_scan` | The delivery was new and scannable, but that ref already had a queued or running scan, so no second one was started. |
| `skipped_team_at_capacity` | The owning team is at its concurrent-scan cap (`SCAN_CONCURRENCY_CAP_PER_TEAM`). |
| `skipped_disk_full` | The workspace volume is over `DISK_HARD_LIMIT_PCT`. Operator action needed. |

Every `skipped_*` value means a commit went unscanned, so they are the ones to watch.

`skipped_active_scan` is the routine one: the portal allows one active scan per `(project, ref)`, and the scan already running was started from an earlier commit. The next delivery on that ref scans normally once the first finishes, so an active branch catches up on its own — but a push landing at the tail of a long-running scan is not scanned by itself. Re-deliver it from the Git host if you need that specific commit covered.

The other two are capacity signals and need an operator before anything else helps. `skipped_team_at_capacity` means the team is already running as many scans as `SCAN_CONCURRENCY_CAP_PER_TEAM` allows; raise the cap or let the running scans finish. `skipped_disk_full` means the workspace volume is past `DISK_HARD_LIMIT_PCT` and nothing will scan until space is freed.

You do not have to redeliver these two by hand. With `WEBHOOK_CAPACITY_RETRY_ENABLED` on (the default), a capacity skip schedules its own retry the moment it happens, then again on a growing backoff (roughly one minute, then two, four, and so on up to thirty minutes apart) for up to six more attempts - about one to three hours of coverage in total. The first retry that finds capacity free enqueues the scan and stamps the delivery `enqueued`, exactly as if it had succeeded the first time. If every attempt still finds the same condition, the delivery is stamped `capacity_retry_exhausted` and, if you have Slack or Teams configured, you get a notification naming the delivery and the project.

Redelivering from the Git host still works at any point in that timeline - before the first automatic retry, in the middle of it, or after it gives up - and takes effect immediately rather than waiting for the next scheduled attempt. The delivery is recorded with its reason, and the redelivery supersedes that row rather than reading as a duplicate: the id names the delivery's current state, not a counter that gets spent. Set `WEBHOOK_CAPACITY_RETRY_ENABLED=false` if you run your own redelivery tooling and do not want the two racing each other; manual redelivery is unaffected either way.

The duplicate check now runs first, so redelivering something that was already scanned answers `duplicate` whatever the capacity situation is, and its recorded outcome keeps saying `enqueued`. Only a delivery that never started a scan is re-run, which is the one where re-running is the point.

These are reported as `200` rather than an error on purpose. A 4xx or 5xx makes the Git host retry, and a retry storm aimed at a portal that is already at its limit helps nobody.

## Verify it worked

After configuring a webhook:

<!-- docs-uat: id=webhooks-ping-delivery kind=manual tier=manual -->
1. The Git host's webhook page shows a successful **ping / test** delivery.
<!-- docs-uat: id=webhooks-push-creates-scan kind=manual tier=manual -->
2. Pushing a commit creates a new scan in the portal within 30 seconds.
<!-- docs-uat: id=webhooks-audit-deliver kind=manual tier=manual -->
3. The audit log shows a `create` on `webhook_deliveries` carrying the delivery id and event type.

## Troubleshooting

### "Could not deliver: 401 Unauthorized"

Either the signature does not match, or the repository is not configured on this portal. Both answer 401 with the same body, deliberately — otherwise an unauthenticated caller could tell which repositories a portal watches by reading the status code. Causes:

- The repository has no project here, or its project has no `webhook_secret` set (see [Bootstrapping a webhook secret](#bootstrapping-a-webhook-secret-operator-only-in-this-release)).
- The secret was rotated in the portal but not updated on the Git host.
- The proxy in front of the portal modifies the body (compression, JSON re-serialization). The signature is over the raw bytes — a single byte change invalidates it.

The server log does distinguish these: `webhook.unknown_repository` carries the URL that failed to match, while `webhook.github.signature_invalid` names the project it did match. Check the backend log to tell a mistyped URL from a stale secret.

Re-sync: rotate the secret in the portal, paste the new value into the Git host, and trigger a redelivery.

### "Could not deliver: 404 Not Found"

Usually the delivery URL itself is wrong — missing `/v1/`, or hitting the frontend instead of the backend (`/webhooks/github` instead of `/v1/webhooks/github`). The portal also answers 404 when the payload carries no recognisable repository URL at all, which points at a malformed or hand-crafted body. An unconfigured *repository* answers 401, not 404.

### Webhook fires but no scan appears

The delivery was accepted but did not trigger. Possible reasons:

- A scan for the same ref was already queued or running, so the delivery is acknowledged without starting a second one. The response says `{"status": "skipped_active_scan"}`.
- The event type is outside the scan whitelist (`push` and `pull_request` for GitHub, `Push Hook` and `Merge Request Hook` for GitLab). A `ping` is accepted and recorded but never scans.
- The repository URL on the payload does not match any project's `git_url`, or that project has no webhook secret. Either answers 401, not 200 — see above.
- The team is at its concurrency cap or the workspace is full: `skipped_team_at_capacity` / `skipped_disk_full`. Redelivering works once the operator clears the condition, because a delivery that never started a scan can be delivered again.

### A second push to the same merge request does not scan

On GitLab versions that do not send `X-Gitlab-Webhook-UUID`, the portal derives the delivery id from the merge request's id combined with its head commit SHA, so it changes as the branch moves. If two deliveries arrive for the same merge request at the same commit — a re-notification that is not a new push — the second is a `duplicate`, which is the intended behaviour.

### Old deliveries replay after a portal outage

Both GitHub and GitLab queue undelivered events for ~24 hours. When the portal comes back, deliveries replay. Idempotency (above) prevents duplicate scans. To skip the replay, manually clear the queue from the Git host before bringing the portal back up — but most installs benefit from the replay because they catch the events that fired during the outage.

### Want HMAC on GitLab

Run the GitLab webhook through a small proxy (e.g. nginx with a Lua snippet, or a tiny Cloudflare Worker) that adds an HMAC header. Configure the portal to require it via a custom middleware. This is non-default and out of scope for the bundled deployment.

## See also

- [GitHub Actions](./github-actions.md)
- [GitLab CI](./gitlab-ci.md)
- [API keys](../admin-guide/api-keys.md)
- [Audit log](../admin-guide/audit-log.md)
