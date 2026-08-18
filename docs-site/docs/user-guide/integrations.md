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
`developer` to view and to issue project-scoped API keys for their own team's projects, `team_admin` to add team-scoped keys, `super_admin` to add org-scoped keys. The page hides actions you cannot perform.
:::

## API keys

Open `/integrations` and scroll to the **API keys** section. The list shows every key you can manage: label, prefix, scope, expiry, and last-used metadata.

![Integrations — API keys section with the Create button and the keys table](/img/screenshots/user-integrations-keys.png)

### Create a key

1. Click **Create API key**. The dialog asks for a name and scope; submit to mint the key.

   ![Integrations — Create API key dialog with name + scope inputs](/img/screenshots/user-integrations-key-create.png)

2. Fill in the form:
   - **Name**: free-text reminder of what the key is for (e.g. `github-action-checkout-service`).
   - **Scope**: the dropdown offers only the scopes you may issue, out of `project`, `team`, and `org`. Lower scopes are stricter; pick the smallest that covers the calls you need to make. The form has plain UUID inputs for `team_id` (required when scope=`team`) and `project_id` (required when scope=`project`); copy the IDs from the corresponding admin pages.
   - **What this key may do**: **Read only** (the default) or **Read and write**. See [Read-only keys](#read-only-keys) below.
   - **Expiration**: **Never expires**, or one of the 30 / 90 / 180 / 365-day presets. After the chosen period the key stops authenticating and CI calls using it fail. A key that leaks into a pipeline log then lapses on its own instead of living until someone revokes it, so CI keys should carry a TTL and be rotated.

   Who can issue each scope:

   | Scope    | Who can issue       |
   |----------|---------------------|
   | `org`    | super-admin only    |
   | `team`   | super-admin, team-admin |
   | `project`| super-admin, team-admin, developer (within their team's projects) |

3. Click **Create**.

:::caution A key with no expiry lives until you revoke it
**Never expires** is the default in the form, and a key issued that way stays valid until someone clicks **Revoke**. Treat it like any other long-lived secret: store it in your CI's secret manager, never in source control. Choosing a preset instead puts a deadline on the damage a leaked key can do.
:::

### Read-only keys {#read-only-keys}

Scope says which projects a key can reach. Breadth says what it can do to them, and the two are separate questions: a pipeline that reads scan results does not need to be able to start one.

A **read-only** key is refused every request that changes something, at the point the request is authenticated rather than per endpoint. In practice that means it can poll a scan, read its provenance and read a conformance report, and it cannot trigger a scan or push an SBOM. The refusal is a `403` that says the key is read-only, so whoever owns the pipeline can tell this apart from a permissions problem.

New keys default to read-only. Choose **Read and write** when the pipeline genuinely writes, which is the case for the scan-trigger action and for SBOM upload.

**Keys issued before this existed are read and write, and stay that way.** They were issued when that was the only kind there was, and narrowing them on upgrade would have stopped whatever is using them with nothing in the portal to explain it. The keys table shows the breadth of every key, so you can find the ones worth narrowing.

**Narrowing is one-way.** You can make a read-write key read-only from the keys table; you cannot widen one back. A key that has been sitting in a CI log for months should not be handed more privilege than it was issued with, so widening means issuing a new key with a new secret.

### Service accounts {#service-accounts}

A key stops working when the person who issued it is deactivated. That is the right rule for a personal key and the wrong one for a pipeline's: a nightly build that has run for a year stops the day its author leaves, and the first anyone hears of it is a red pipeline.

A **service account** is an identity for automation. A key issued to one lives as long as the account does, so people coming and going does not touch it.

Create one in the **Service accounts** panel on this page, above the keys. Whoever creates it becomes its **steward**: the person answerable for it. The steward is never part of authenticating; they exist so an unattended credential still has a name against it.

**The steward must be a member of the account's team.** Somebody outside it is a name that makes the check pass rather than a person who could be asked about the credential, so it is refused. The check runs again at issuance, so a steward who later moves off the team stops counting.

**When the steward leaves**, existing keys keep working, which is the whole point. What stops is issuing *new* keys for that account: the request is refused until somebody takes it over. The panel marks the account as having no steward and offers **Take it over**, so the fix is where the problem is shown rather than discovered on the next failed issuance.

**Stopping a service account** stops every key it holds, in one action. That is the counterpart to keys no longer dying with a person: there has to be a deliberate way to end them, and it should not be a hunt through the key list. The account record stays afterwards so the audit trail keeps its actor.

A few things a service account deliberately cannot do:

- **Log in.** It has no usable password and is refused at the login form regardless.
- **Receive a password reset.** There is nobody to send one to, and the endpoint answers as it does for any unknown address.
- **Link an external identity.** OAuth account matching skips them, so a person cannot acquire an interactive way into one.
- **Appear in the admin user list.** The actions a user list offers are wrong for it, and deactivating one from a leavers screen would be a pipeline outage that reads as tidying up.
- **Be a steward, or create another service account.** Only an active person on the team can be answerable for an account, so a chain of service accounts cannot vouch for each other with nobody at the end of it.
- **Be made a deployment administrator.** Refused by the database, not only by the code that creates them: the key such an account could then issue would outlive every session involved in making it.
- **Be added to or removed from a team on the team-members screen.** Its reach is set where it was created. Removing its membership there would leave it holding live keys with no way left to stop them.

The address a service account carries (`<name>@svc.trusca.internal`) is synthetic and undeliverable. It exists because the audit log prints an address, and a recognisable one is better than a blank.

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
- A super-admin can confirm `target_table=api_keys&action=create` and `target_table=webhook_deliveries&action=create` events on `/admin/audit`. Today the audit log is super-admin only — see [Roadmap](#roadmap).

## Troubleshooting

- **HTTP 401 from the API** — credential problem (no header, malformed Bearer, unknown prefix, signature mismatch, revoked, expired).
- **HTTP 403 from the API** — credential is valid but the key's scope does not cover the resource (e.g. `team`-scope key hitting an `org`-only endpoint). Issue a new key with a broader scope, or call a different endpoint.
- **HTTP 429 from the API** — you hit the per-key rate limit. The `Retry-After` header tells you how long to wait. Back off and retry.
- **GitHub webhook returns 401** — `X-Hub-Signature-256` did not validate. Confirm the secret matches and that GitHub is computing HMAC over the **raw** body, not a re-serialised JSON.
- **GitLab webhook returns 401** — the `X-Gitlab-Token` header value does not match the project's `webhook_secret`.

## Roadmap

Items the manual previously promised that are not in this release; tracked for later releases.

- A custom expiry value on the key-creation form. Planned; today the form offers Never plus the 30 / 90 / 180 / 365-day presets, and any other TTL between 1 and 1825 days has to be issued through the API's `expires_in_days`.
- A team / project picker on the key-creation form — planned; today the form takes plain UUID inputs.
- **Project Settings → CI/CD** subtab with **Rotate webhook secret** action — planned; today the per-project `webhook_secret` is bootstrapped server-side.
- Team-scoped audit log at `/audit` for `team_admin` users — planned; today the audit log is super-admin only at `/admin/audit`.

## See also

- [Authentication & profile](./auth-and-profile.md) — interactive credentials for humans.
- [GitHub Actions](../ci-integration/github-actions.md) — end-to-end CI integration.
- [Webhooks (admin reference)](../ci-integration/webhooks.md) — payload schemas and admin-side configuration.
- [API keys (admin reference)](../admin-guide/api-keys.md) — backend behaviour, hashing, audit log.
