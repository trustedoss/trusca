---
id: hardening
title: Hardening a production deployment
description: A checklist for locking down TRUSCA before production traffic - TLS, authentication, secrets, rate limits, data protection, and supply chain.
sidebar_label: Hardening
sidebar_position: 1.5
---

# Hardening a production deployment

:::note Audience
`super_admin` operators preparing a deployment for production traffic, or auditing an existing one. Assumes familiarity with `.env` / Helm values and the [environment variable reference](../reference/env-variables.md), which this page does not repeat.
:::

Most of what a hardened TRUSCA deployment needs is already the default: password policy, refresh-token rotation, CORS validation, and rate limits are all on before you touch a config file. This page is a guided pass through the settings that are genuinely operator-owned, grouped by area, with a link out to the doc that covers each one in depth. Treat it as the order to work through when standing up a fresh deployment or auditing an existing one, not as a replacement for the reference page.

## Network and TLS

- **`CORS_ALLOWED_ORIGINS`** is a required key with no permissive default. The backend refuses to boot if it contains `*` (incompatible with credentialed requests) and, when `APP_ENV=prod`, refuses to boot if any origin uses plain `http://`. Set it to the exact scheme + host the SPA is served from, nothing broader.
- Terminate TLS at the edge. The production `docker-compose.yml` ships Traefik with a Let's Encrypt HTTP-01 challenge, wired from `DOMAIN` and `TLS_EMAIL` - see [Install with Docker Compose](../installation/docker-compose.md#prerequisites-for-https-deployments). The Helm chart expects an Ingress + cert-manager `ClusterIssuer` instead - see [Install on Kubernetes with Helm](../installation/helm.md).
- If your network sits behind an internal certificate authority or a TLS-intercepting proxy, do not disable certificate verification. Point the scan pipeline and the portal's own outbound calls at your CA bundle instead - see [Private certificate authorities](./private-ca.md).

## Authentication and sessions

- **Password policy is not a config knob.** Every password is checked against an 8-character NIST 800-63B floor and a common-password blocklist at the schema layer, for every registration and reset path. There is nothing to strengthen here; it cannot be weakened either.
- **`SECRET_KEY`** signs every JWT. It is a required key, refused at boot if shorter than 32 characters outside `dev`. Generate it with `openssl rand -hex 32` and never reuse it across deployments - rotating it invalidates every outstanding refresh token, so treat rotation as a deliberate, communicated event, not a routine one.
- **`ACCESS_TOKEN_EXPIRE_MINUTES`** (default 30) and **`REFRESH_TOKEN_EXPIRE_DAYS`** (default 7) bound how long a stolen token stays useful. Refresh tokens rotate on use with reuse detection already built in; shortening the defaults trades convenience for a smaller exposure window, it is not fixing a gap.
- **MFA** (TOTP + recovery codes) is available for any user to turn on for their own account. There is no organization-wide enforcement toggle in this release - encourage `super_admin` and other privileged accounts to enroll themselves.
- **`AUTH_SELF_REGISTRATION`** defaults to `true` (the public sign-up form is open). On an internal deployment, turn it off and provision accounts through your identity provider or bulk registration instead - open self-registration plus SSO both enabled means someone can sign up under a work address and later link it to SSO. See the [SSO section of the environment reference](../reference/env-variables.md#single-sign-on-generic-openid-connect) for the full `OIDC_*` picture, including why `OIDC_GROUP_ROLE_MAP` refuses to grant `super_admin` and why the issuer must be `https`.
- **`LOGIN_THROTTLE_ENABLED`** (default on) adds a per-address escalating lockout on top of the per-IP rate limit, so guessing spread across many source addresses against one account still gets caught. The only ways back in are a password reset or an administrator's unlock endpoint - see [`LOGIN_THROTTLE_WINDOWS` in the environment reference](../reference/env-variables.md#password-reset).
- **`PERMISSION_CACHE_TTL_SECONDS`** defaults to `0` (off): every request re-reads the caller's roles and memberships. Raising it trades that guarantee for less database load - whatever value you set is the longest a demotion or deactivation can go unfelt. Only raise it once the connection budget in [Postgres sizing and connection tuning](./postgres-tuning.md) actually needs the relief.

## Rate limits and abuse controls

Every public-facing endpoint that costs CPU or opens a session already carries a `slowapi` limit out of the box - registration, login, refresh, password reset, ticket-status refresh, group search, scan triggers, and the two webhook receivers. The full list with its defaults is in the [environment variable reference](../reference/env-variables.md); the ones worth revisiting are the ones where your traffic pattern differs from a single human at a browser:

- A CI runner or NAT'd office triggering scans or refreshing tokens for many users from one address can hit the per-IP limits meant for abuse. Raise the specific limit rather than disabling it.
- **`RATELIMIT_DISABLED`** is a test-only escape hatch (it exists so a shared-IP CI runner does not trip the login limit while running the end-to-end suite). It has no API or admin surface, so the only way it reaches a production process is a stray environment variable - if it is ever set there, every `slowapi` limit in the backend is off at once. Confirm it is unset on anything serving real traffic.
- **`WEBHOOK_MAX_BODY_BYTES`** and **`WEBHOOK_RATE_LIMIT`** bound what an unauthenticated caller can cost before the signature check runs, because the receivers read and buffer the body before verifying it. See [Webhooks](../ci-integration/webhooks.md#request-limits).
- **`WEBSOCKET_MAX_CONNECTIONS_PER_USER`** / **`WEBSOCKET_MAX_CONNECTIONS_GLOBAL`** cap the WebSocket gateway the same way, enforced against a shared registry so the cap holds regardless of how many backend processes you run.
- The per-team **scan concurrency cap** protects the shared Celery worker pool from one team's burst independently of the per-user trigger limit - see [Docker Compose - Scan capacity](../installation/docker-compose.md#scan-capacity-sizing-and-scaling) for the formula and `SCAN_TRIGGER_RATE_LIMIT` in the reference for the per-user half.

## Secrets, keys, and least privilege

- **`GITHUB_APP_ENCRYPTION_KEY`** encrypts every secret this deployment stores: GitHub App private keys and webhook secrets, per-project git credentials, private-registry passwords, and project webhook secrets, not only the GitHub App despite the name. Generate it before first boot and read [Rotating the encryption key](./encryption-key-rotation.md) before you ever need to rotate it under pressure.
- Private-registry credentials for cdxgen (Maven `settings.xml`, `.npmrc`, `pip.conf`, `.netrc`) are mounted read-only from a host directory (`REGISTRY_CONFIG_HOST_PATH`), never written into `.env` in plaintext. See [Private registries for dependency resolution](./private-registries.md).
- API keys are hashed (HMAC), scoped, and independently revocable per service account or CI integration - see [API keys](./api-keys.md) for issuing and rotating them, and prefer one key per integration over sharing a single key across CI pipelines.
- A GitHub App connection is registered per team, with its private key encrypted at rest and access scoped per installation rather than to every repository the App could technically reach - see [GitHub App connection](./github-app.md).
- For Postgres itself, the L1 runtime/owner role split (`DATABASE_URL_APP` for the backend and Celery runtime, `DATABASE_URL_OWNER` reserved for migrations) means a compromised backend process cannot run DDL against its own database. It is opt-in (`REQUIRE_DB_ROLE_SEPARATION=true` to enforce it, off by default because single-role matches the simpler out-of-the-box posture) - see the role-separation notes in [Install with Docker Compose](../installation/docker-compose.md) or [Install on Kubernetes with Helm](../installation/helm.md) depending on how you deploy.
- Backups are plaintext SQL by default. If your retention policy requires encryption at rest, see [Backup and restore - Encrypted backups](./backup-and-restore.md#encrypted-backups).
- Right-to-erasure requests go through a two-`super_admin`-approval workflow that anonymizes a user's personal data while keeping the audit trail intact - see [User anonymisation](./user-anonymisation.md).
- **`/metrics`** is off by default (`METRICS_ENABLED=false`, answering 404 rather than 403 so its absence is not itself a signal). If you turn it on and the endpoint is reachable from outside your own network, set **`METRICS_TOKEN`** - an empty token means anyone who can reach the path can scrape it.

## Data protection and egress

A handful of enrichment stages call out to a public registry or feed by design; every one of them defaults to sending nothing more than a package name, ecosystem, or advisory id, never source code or proprietary metadata, and every one has an off switch for an air-gapped or otherwise network-restricted deployment:

- `SCANOSS_ENABLED` (vendored-OSS fingerprint matching) defaults **off** - it is the one stage here that sends more than a name, so it is opt-in rather than opt-out.
- `LICENSE_FETCH_ENABLED` and `EXTERNAL_PACKAGE_LOOKUP_ENABLED` default **on** (registry license lookups and the deps.dev catalog search), each sending only a package identifier to a fixed public host.
- `EOL_REFRESH_ENABLED` defaults **off** (the local end-of-life snapshot vendored with the release still works without it); `KEV_REFRESH_ENABLED` and `MALICIOUS_REFRESH_ENABLED` govern the CISA KEV and known-malicious-package feeds the same way.

None of these need touching for a standard internet-connected install. For a deployment that must not reach the public internet at all, see [Vulnerability data - Air-gapped operation](./vulnerability-data.md#air-gapped), which covers mirroring the Trivy DB itself alongside these toggles.

## Supply chain and patching

- The CI build gate blocks a build on Critical CVEs and forbidden licenses unconditionally, and on known-malicious packages by default (`GATE_MALICIOUS_ENABLED=true`) - a malicious package has no honest version to upgrade to, so this one stays on even when the other `GATE_*` knobs are tuned looser. An optional EPSS threshold (`GATE_EPSS_THRESHOLD`) adds exploitation-likelihood as a fourth axis. See the [build gate reference](../reference/glossary.md#build-gates).
- The About screen (and `/metrics`, if enabled) report the running image's version, commit, and build time, which is the fastest way to confirm a host has actually picked up a released patch rather than an older cached image.
- Container images are not cosign-signed and release tags are not GPG-signed yet; verify a release by commit SHA and the image digest the release workflow prints until that ships. See [`SECURITY.md`](https://github.com/trustedoss/trusca/blob/main/SECURITY.md) for the full disclosure policy and current verification story, and [Upgrade cadence](../best-practices/upgrade-cadence.md) for how often to apply patch releases.

## Verify it worked

- `docker-compose -f docker-compose.yml logs --tail=50 backend | grep backend_starting` shows the backend accepted boot with your `.env` - a rejected `CORS_ALLOWED_ORIGINS` or an under-length `SECRET_KEY` crashes the process instead of starting degraded, so a running container already means those two checks passed.
- Load the portal's public URL in a browser with dev tools open and confirm the API responses carry `Access-Control-Allow-Origin` set to your exact origin, not `*`.
- `curl -I https://<your-domain>/metrics` returns `404` unless you deliberately enabled it, and `401`/`403` rather than a body if `METRICS_TOKEN` is set and you omit the bearer token.
- Attempt a login with an obviously weak password (`password123`) against `/auth/register` in a scratch environment and confirm it is refused with a 422.

## Troubleshooting

### Backend exits immediately after an `.env` edit

Check the boot log for the specific `RuntimeError` message - a wildcard CORS origin, a plain-`http://` origin in `prod`, or a `SECRET_KEY` under 32 characters all fail fast by design rather than starting in a weaker state. See [Validation](../reference/env-variables.md#validation).

### A CI runner or shared-IP office trips a rate limit that a real attacker would never reach

Raise the specific `*_RATE_LIMIT` env var for that endpoint rather than reaching for `RATELIMIT_DISABLED` - that variable has no scope smaller than the whole backend process.

### An operator forgot which secrets `GITHUB_APP_ENCRYPTION_KEY` actually covers

It is every encrypted column, not only GitHub App keys. See [Adding a new encrypted column](./encryption-key-rotation.md#adding-a-new-encrypted-column) for the authoritative list.

## See also

- [Environment variables](../reference/env-variables.md) - the exhaustive key-by-key reference this page curates from.
- [Postgres sizing and connection tuning](./postgres-tuning.md) - the connection-budget half of a production-ready deployment.
- [Rotating the encryption key](./encryption-key-rotation.md)
- [Private certificate authorities](./private-ca.md)
- [Private registries for dependency resolution](./private-registries.md)
- [API keys](./api-keys.md)
- [GitHub App connection](./github-app.md)
- [User anonymisation](./user-anonymisation.md)
- [Backup and restore](./backup-and-restore.md)
- [On-call runbook](./oncall-runbook.md)
