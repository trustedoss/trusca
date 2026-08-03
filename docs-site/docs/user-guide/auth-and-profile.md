---
id: auth-and-profile
title: Authentication & profile
description: Sign in with email + password or OAuth, recover lost passwords, and manage connected identities on the /profile page.
sidebar_label: Auth & profile
sidebar_position: 7
---

# Authentication & profile

<!-- docs-uat: id=auth-login kind=ui harness=login(dev@demo.trustedoss.dev,DemoTest2026!) tier=nightly -->
TRUSCA supports two interactive sign-in methods — **email + password** and **OAuth** (GitHub or Google) — plus a self-service password-recovery flow. This page walks through each path and covers identity management on the `/profile` page.

:::note Audience
Any signed-in user. No special role required to manage your own identities. The OAuth buttons appear only when the operator has configured the relevant `*_CLIENT_ID` / `*_CLIENT_SECRET` environment variables.
:::

## Sign in with email + password

1. Open `/login`.
2. Enter your email and password.
3. Submit.

![Login page with email + password fields and OAuth provider buttons](/img/screenshots/user-auth-login.png)

**What happens server-side**

- The password is hashed with **bcrypt cost 12** at registration; the login compares the candidate against the stored hash in constant time.
- A successful login returns a JWT **access token (30 min)** and a refresh token (**7 days**, rotated on every use, with reuse-detection that revokes the entire chain).
- Refresh tokens live in an `HttpOnly`, `Secure`, `SameSite=Lax` cookie. They are never visible to JavaScript.
- The login endpoint is rate-limited to **5 attempts per minute per IP**. Excess requests return HTTP 429 with a `Retry-After` header.

If you see *"Invalid email or password"*, check the email is correct and try once more — the message is intentionally generic so an attacker cannot enumerate accounts.

## Forgot your password

1. From `/login`, click **Forgot password?** to open `/forgot-password`.
2. Enter the email associated with your account.
3. Submit. The portal always returns a 204 No Content response — even if no account exists for that email — so an attacker cannot enumerate users.
4. Check your inbox. If an account exists, a message with the subject **"Reset your TRUSCA password"** arrives within ~30 seconds.

The reset link is **valid for 24 hours and can be used once**. After expiry or first use, the token is revoked.

![Forgot password page — email input with anti-enumeration submit](/img/screenshots/user-auth-forgot.png)

## Reset your password

The link in the email lands on `/reset-password?token=<opaque>`.

1. Enter the new password (≥ 12 characters, must not match the breach dictionary).
2. Confirm it in the second field.
3. Submit.

On success you are redirected to `/login`. The new password is bcrypt-hashed and the reset token is consumed. All existing refresh tokens for the account are revoked, forcing every other session to re-authenticate.

If the token has expired or has already been used, the page renders an error with a link back to `/forgot-password` to request a fresh one.

## Sign in with OAuth

If GitHub or Google is configured, the `/login` page shows the corresponding buttons below the email field.

1. Click **Continue with GitHub** or **Continue with Google**.
2. Approve the access request on the provider's consent screen.
3. You are redirected back to the portal and signed in.

**First-time OAuth sign-in** auto-creates an account from the provider's verified email. A personal team is provisioned automatically (named `<your-handle>`'s team).

**Subsequent sign-ins** look up the existing identity by `(provider, provider_user_id)`. The provider's `email` field is **never** used to match — this prevents account-takeover via a recycled email address at the provider.

Errors are surfaced as i18n-mapped messages. The seven distinct codes cover provider denial, missing scope, expired state, repeated state, identity collision, suspended account, and provider 5xx. Each code points the user to a specific recovery action.

## Manage connected accounts on `/profile`

The `/profile` page has a **Connected Accounts** section that lists the OAuth identities currently attached to your account:

- **GitHub** — present if you have ever signed in with GitHub.
- **Google** — present if you have ever signed in with Google.

![Profile page — header, identity card, and Connected Accounts panel](/img/screenshots/user-profile-mounted.png)

The Connected Accounts panel highlights every external identity currently linked to your portal account:

![Profile — Connected Accounts panel showing the linked GitHub identity](/img/screenshots/user-profile-connected-accounts.png)

Password sign-in is not displayed as a row in the Connected Accounts list in this release — it is implicit when your account was registered with email + password (or when you completed a password reset). Password recovery for OAuth-only accounts is on the roadmap; today the only path is the operator-side `/admin/users/{id}/password-reset` endpoint.

Each Connected Accounts row has an **Unlink** button. The portal protects you from locking yourself out:

- If unlinking would leave you with **no sign-in method** (e.g. you have only one OAuth identity and no password set), the request returns HTTP 409 and the UI shows an alert: *"Set a password before unlinking your last OAuth identity."*
- The fallback path is **Forgot password** — request a reset link, set a password, then return to `/profile` and unlink.

Linking a new provider is symmetric: sign out, sign in with the new provider, and the new identity attaches automatically because the verified email matches the existing account.

## Verify it worked

<!-- docs-uat: id=auth-header-profile-visible kind=ui harness=headerProfileVisible tier=nightly -->
- After password sign-in, the global bar shows your initials and the team you are acting as.

### Switching teams

If you belong to more than one team, the team name in the global bar is a menu. Picking a different team changes which team new projects are created under, and the choice survives a reload.

It does **not** filter what any screen shows. The Dashboard and the project list stay scoped to everything your memberships reach, whichever team is selected — a control that quietly narrowed a page would be easy to leave switched on by accident and hard to notice. Use a screen's own filters to narrow it.

If you belong to exactly one team the name is shown as a plain label, and if you belong to none — the seeded super admin, for instance — nothing is shown.
<!-- docs-uat: id=auth-oauth-provider-listed kind=manual tier=manual -->
- After OAuth sign-in, `/profile` lists the provider you used.
<!-- docs-uat: id=auth-unlink-disables-last kind=ui harness=profileUnlinkGithub tier=nightly -->
- After unlinking, the row disappears and the **Unlink** button on the remaining row is disabled if it would leave you stranded.

## Licenses and notices on `/about`

**About** in the sidebar shows what this deployment is and the license notices it
ships with. It needs no admin role — a license notice is something every user of
a deployment is entitled to read.

The page reports the product name, the running version, the SPDX license id, the
copyright holder, and a link to the published source. Below that, three tabs
carry the notice files verbatim:

| Tab | File | What it covers |
|---|---|---|
| License | `LICENSE` | The license TRUSCA itself is distributed under (Apache-2.0). |
| Notice | `NOTICE` | TRUSCA's own copyright notice, required by Apache-2.0 section 4(d). |
| Third-party notices | `THIRD_PARTY_NOTICES.md` | Attribution for third-party material vendored into the source tree, and for the tools bundled in the container images. |

The same three files ship at `/licenses/` inside every container image and inside
the Helm chart, so an operator with shell access can read them without the UI.
They are in the product as well because TRUSCA is self-hosted and supports
air-gapped installs, where following a link to GitHub is not an option.

If a tab reports the file as missing from the installation, the image was built
without it — check the release artifacts rather than the portal.

## See also

- [Notifications](./notifications.md) — how the portal reaches you about events on your projects.
- [Integrations](./integrations.md) — API keys for non-interactive clients.
- [Users & teams](../admin-guide/users-and-teams.md) — admin view of the same identities.
- [Components & licenses](./components-and-licenses.md) — the licenses of the code you scan, as opposed to TRUSCA's own.
