---
id: integrations
title: Integrations
description: Issue API keys for CI runners and configure GitHub or GitLab webhooks from the /integrations page.
sidebar_label: Integrations
sidebar_position: 9
---

# Integrations

`/integrations` is the user-facing home for **non-interactive credentials**. It groups two distinct things:

- **API keys** — credentials a CI runner, script, or external service uses to authenticate to the portal API.
- **Webhooks** — inbound URLs the portal exposes for GitHub and GitLab to push repository events (push, pull request).

:::note Audience
`developer` to view, `team_admin` to issue or revoke team-scoped API keys, `super_admin` to issue org-scoped keys. The page hides actions you cannot perform.
:::

## API keys

Open `/integrations` and switch to the **API keys** tab. The list shows every key you can manage: label, prefix, scope, expiry, and last-used metadata.

![Integrations — API keys list](./img/integrations-keys.png)

### Create a key

1. Click **New API key**.
2. Fill in the form:
   - **Label** — free-text reminder of what the key is for (e.g. `github-action-checkout-service`).
   - **Scope** — `org`, `team`, or `project`. Lower scopes are stricter; pick the smallest that covers the calls you need to make.
   - **Expiry** — preset 30 / 90 / 180 / 365 days, or custom. Keys with no expiry are discouraged.
3. Click **Create**.

The portal opens a **one-time reveal modal** with the full key:

```text
tos_a1b2c3d4_eaff8b91d36c5e0a2f1c4d7e8a9b0c2d
```

:::caution One-time reveal
The full key is shown **once**. After you close the modal, only the prefix is visible. Copy it now and paste it into your CI's secret store before you click **Done**.
:::

The modal has a **Copy** button and an explicit warning: *"This is the only time you will see the full key. If you lose it, you must create a new one."*

### Use a key

Pass the key in the `Authorization` header of every request. Both `Bearer` and `ApiKey` schemes are accepted; `Bearer` is preferred for OpenAPI tooling compatibility:

```bash
curl -sS \
  -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
  https://trustedoss.example.com/api/v1/projects
```

In **GitHub Actions**, store the key in the repository or organisation secrets, then expose it as an env var:

```yaml
- name: Trigger TrustedOSS scan
  env:
    TRUSTEDOSS_API_KEY: ${{ secrets.TRUSTEDOSS_API_KEY }}
  run: curl -sS -H "Authorization: Bearer $TRUSTEDOSS_API_KEY" ...
```

In **Jenkins**, use the **Credentials** plugin (Secret text) and bind it inside a stage:

```groovy
stage('Scan') {
  withCredentials([string(credentialsId: 'trustedoss-api-key', variable: 'TRUSTEDOSS_API_KEY')]) {
    sh 'curl -sS -H "Authorization: Bearer $TRUSTEDOSS_API_KEY" ...'
  }
}
```

### Revoke a key

In the API keys list, hover the row and click **Revoke**. Confirm in the dialog. Revocation is immediate (auth cache TTL ~5 seconds) and irreversible.

## Webhooks

Switch to the **Webhooks** tab. Unlike API keys, webhook URLs are **fixed** — the portal exposes them at well-known paths, and you wire your provider (GitHub / GitLab) to post into them.

![Integrations — Webhooks tab](./img/integrations-webhooks.png)

### GitHub

URL to register at GitHub: `https://<your-host>/v1/webhooks/github`.

- **Content-Type:** `application/json`.
- **Signature:** `X-Hub-Signature-256` HMAC-SHA256 over the raw body, with the per-project `webhook_secret` as the key.
- **Events:** `push` and `pull_request` are the supported triggers.

Generate the project's `webhook_secret` under **Project → Settings → CI/CD**. Rotation regenerates a new secret; copy and paste it into the GitHub webhook config.

### GitLab

URL to register at GitLab: `https://<your-host>/v1/webhooks/gitlab`.

- **Content-Type:** `application/json`.
- **Token:** sent in the `X-Gitlab-Token` header. Set this to the project's `webhook_secret`.
- **Events:** **Push events** and **Merge request events**.

### Rotate a webhook secret

Webhook secrets are per-project. Open the project's **Settings → CI/CD** tab, click **Rotate webhook secret**, and confirm. The new secret is shown once. Update GitHub / GitLab with the new value.

The old secret stops verifying within ~5 seconds of rotation. Until both ends are updated, deliveries fail with HTTP 401 — keep the rotation window short.

## Verify it worked

- After creating a key, run `curl -sS -H "Authorization: Bearer <key>" .../api/v1/projects` and confirm a 200 response with the team's projects.
- After registering the webhook in GitHub, push a commit and check the **Webhook deliveries** view in GitHub — successful deliveries return HTTP 202.
- Open the audit log (`/admin/audit` for super-admins, `/audit` for team admins) and confirm `api_key.create` and `webhook.delivery` events with your prefix.

## Troubleshooting

- **HTTP 401 from the API** — the key is unknown, expired, or revoked. The error response distinguishes 401 (credential problem) from 403 (credential valid but action not allowed).
- **HTTP 403 from the API** — the key's scope does not cover the call. Issue a new key with a broader scope, or call a different endpoint.
- **HTTP 429 from the API** — you hit the per-key rate limit. The `Retry-After` header tells you how long to wait. Back off and retry.
- **GitHub webhook returns 401** — `X-Hub-Signature-256` did not validate. Confirm the secret matches and that GitHub is computing HMAC over the **raw** body, not a re-serialised JSON.
- **GitLab webhook returns 401** — the `X-Gitlab-Token` header value does not match the project's `webhook_secret`.

## See also

- [Authentication & profile](./auth-and-profile.md) — interactive credentials for humans.
- [GitHub Actions](../ci-integration/github-actions.md) — end-to-end CI integration.
- [Webhooks (admin reference)](../ci-integration/webhooks.md) — payload schemas and admin-side configuration.
- [API keys (admin reference)](../admin-guide/api-keys.md) — backend behaviour, hashing, audit log.
