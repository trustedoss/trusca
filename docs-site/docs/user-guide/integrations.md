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

Open `/integrations` and scroll to the **API keys** section. The list shows every key you can manage: label, prefix, scope, expiry, and last-used metadata.

![Integrations — API keys section with the Create button and the keys table](/img/screenshots/user-integrations-keys.png)

### Create a key

1. Click **New API key**. The dialog asks for a name and scope; submit to mint the key.

   ![Integrations — Create API key dialog with name + scope inputs](/img/screenshots/user-integrations-key-create.png)

2. Fill in the form:
   - **Name** — free-text reminder of what the key is for (e.g. `github-action-checkout-service`).
   - **Scope** — `org`, `team`, or `project`. Lower scopes are stricter; pick the smallest that covers the calls you need to make. The form has plain UUID inputs for `team_id` (required when scope=`team`) and `project_id` (required when scope=`project`); copy the IDs from the corresponding admin pages. A picker UI is on the roadmap.

   Who can issue each scope:

   | Scope    | Who can issue       |
   |----------|---------------------|
   | `org`    | super-admin only    |
   | `team`   | super-admin, team-admin |
   | `project`| super-admin, team-admin, developer (within their team's projects) |

3. Click **Create**.

:::caution Keys do not expire in this release
The key-creation form does not yet collect an expiry. Every key issued in this release is valid until you explicitly **Revoke** it. Treat the key like any other long-lived secret — store it in your CI's secret manager, never in source control. An expiry preset is on the roadmap (see below).
:::

The portal opens a **one-time reveal modal** with the full key:

```text
tos_a1b2c3d4_eaff8b91d36c5e0a2f1c4d7e8a9b0c2d
```

:::caution One-time reveal
The full key is shown **once**. After you close the modal, only the prefix is visible. Copy it now and paste it into your CI's secret store before you click **Done**.
:::

The modal has a **Copy** button and an explicit warning: *"This is the only time you will see the full key. If you lose it, you must create a new one."*

### Use a key

Pass the key in the `Authorization` header of every request using the `Bearer` scheme. API keys authenticate the CI surface — **triggering a scan** and **polling its status**. They are not accepted on the interactive read endpoints (e.g. `GET /v1/projects` is JWT-only and returns `401` for a key).

<!-- docs-uat: id=integrations-api-trigger-scan kind=shell ctx=host tier=manual waiver=example-curl-placeholder-host-and-api-key -->
```bash
curl -sS -X POST \
  -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"kind":"source"}' \
  https://trustedoss.example.com/v1/projects/<project-id>/scans
```

In **GitHub Actions**, store the key in the repository or organisation secrets, then expose it as an env var:

```yaml
- name: Trigger TRUSCA scan
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

Scroll to the **Webhooks** section. Unlike API keys, webhook URLs are **fixed** — the portal exposes them at well-known paths, and you wire your provider (GitHub / GitLab) to post into them.

![Integrations — Webhooks section with GitHub and GitLab URL cards](/img/screenshots/user-integrations-webhooks.png)

### GitHub

URL to register at GitHub: `https://<your-host>/v1/webhooks/github`.

- **Content-Type:** `application/json`.
- **Signature:** `X-Hub-Signature-256` HMAC-SHA256 over the raw body, with the per-project `webhook_secret` as the key.
- **Events:** `push` and `pull_request` are the supported triggers.

The portal stores a per-project `webhook_secret` field used to verify incoming deliveries. UI to generate or rotate that secret is not exposed in this release — see [Roadmap](#roadmap). Operators bootstrap the secret server-side today.

### GitLab

URL to register at GitLab: `https://<your-host>/v1/webhooks/gitlab`.

- **Content-Type:** `application/json`.
- **Token:** sent in the `X-Gitlab-Token` header. Set this to the project's `webhook_secret`.
- **Events:** **Push events** and **Merge request events**.

## Verify it worked

<!-- docs-uat: id=integrations-curl-200 kind=manual tier=manual -->
- After creating a key, trigger a scan with it — `curl -sS -X POST -H "Authorization: Bearer <key>" -H "Content-Type: application/json" -d '{"kind":"source"}' .../v1/projects/<project-id>/scans` — and confirm a `200` response with the new scan. Then poll `GET .../v1/scans/<scan-id>` with the same key. (`GET /v1/projects` is JWT-only and returns `401` for a key — that is expected, not a misconfiguration.)
<!-- docs-uat: id=integrations-github-webhook-202 kind=manual tier=manual -->
- After registering the webhook in GitHub, push a commit and check the **Webhook deliveries** view in GitHub — successful deliveries return HTTP 200.
<!-- docs-uat: id=integrations-audit-events kind=manual tier=manual -->
- A super-admin can confirm `target_table=api_keys&action=create` and `target_table=webhook_deliveries&action=create` events on `/admin/audit`. Team-scoped audit-log access is on the roadmap (see below).

## Troubleshooting

- **HTTP 401 from the API** — credential problem (no header, malformed Bearer, unknown prefix, signature mismatch, revoked, expired).
- **HTTP 403 from the API** — credential is valid but the key's scope does not cover the resource (e.g. `team`-scope key hitting an `org`-only endpoint). Issue a new key with a broader scope, or call a different endpoint.
- **HTTP 429 from the API** — you hit the per-key rate limit. The `Retry-After` header tells you how long to wait. Back off and retry.
- **GitHub webhook returns 401** — `X-Hub-Signature-256` did not validate. Confirm the secret matches and that GitHub is computing HMAC over the **raw** body, not a re-serialised JSON.
- **GitLab webhook returns 401** — the `X-Gitlab-Token` header value does not match the project's `webhook_secret`.

## Roadmap

Items the manual previously promised that are not in this release; tracked for later releases.

- API-key expiry presets (30 / 90 / 180 / 365 days, custom) — planned; today every issued key is non-expiring until revoked.
- **Project Settings → CI/CD** subtab with **Rotate webhook secret** action — planned; today the per-project `webhook_secret` is bootstrapped server-side.
- Team-scoped audit log at `/audit` for `team_admin` users — planned; today the audit log is super-admin only at `/admin/audit`.

## See also

- [Authentication & profile](./auth-and-profile.md) — interactive credentials for humans.
- [GitHub Actions](../ci-integration/github-actions.md) — end-to-end CI integration.
- [Webhooks (admin reference)](../ci-integration/webhooks.md) — payload schemas and admin-side configuration.
- [API keys (admin reference)](../admin-guide/api-keys.md) — backend behaviour, hashing, audit log.
