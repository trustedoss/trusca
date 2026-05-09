---
id: auth-and-profile
title: Authentication & profile
description: Sign in with email + password or OAuth, recover lost passwords, and manage connected identities on the /profile page.
sidebar_label: Auth & profile
sidebar_position: 7
---

# Authentication & profile

TrustedOSS Portal supports two interactive sign-in methods — **email + password** and **OAuth** (GitHub or Google) — plus a self-service password-recovery flow. This page walks through each path and covers identity management on the `/profile` page.

:::note Audience
Any signed-in user. No special role required to manage your own identities. The OAuth buttons appear only when the operator has configured the relevant `*_CLIENT_ID` / `*_CLIENT_SECRET` environment variables.
:::

## Sign in with email + password

1. Open `/login`.
2. Enter your email and password.
3. Submit.

![Login page](./img/auth-login.png)

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
4. Check your inbox. If an account exists, a message with the subject **"Reset your TrustedOSS Portal password"** arrives within ~30 seconds.

The reset link is **valid for 24 hours and can be used once**. After expiry or first use, the token is revoked.

![Forgot password page](./img/auth-forgot.png)

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

`/profile` lists every identity that can sign you in:

- **Password** — present if you registered with email + password or set one later.
- **GitHub** — present if you have ever signed in with GitHub.
- **Google** — present if you have ever signed in with Google.

![Profile page — Connected Accounts](./img/auth-profile.png)

Each row has an **Unlink** button. The portal protects you from locking yourself out:

- If unlinking would leave you with **no sign-in method** (e.g. you have only one OAuth identity and no password set), the request returns HTTP 409 and the UI shows an alert: *"Set a password before unlinking your last OAuth identity."*
- The fallback path is **Forgot password** — request a reset link, set a password, then return to `/profile` and unlink.

Linking a new provider is symmetric: sign out, sign in with the new provider, and the new identity attaches automatically because the verified email matches the existing account.

## Verify it worked

- After password sign-in, the header avatar shows your initials and the navbar exposes your active team.
- After OAuth sign-in, `/profile` lists the provider you used.
- After unlinking, the row disappears and the **Unlink** button on the remaining row is disabled if it would leave you stranded.

## See also

- [Notifications](./notifications.md) — how the portal reaches you about events on your projects.
- [Integrations](./integrations.md) — API keys for non-interactive clients.
- [Users & teams](../admin-guide/users-and-teams.md) — admin view of the same identities.
