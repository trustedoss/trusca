---
id: github-app
title: GitHub App connection
description: Register a GitHub App per team, store its private key encrypted at rest, and opt projects in for fine-grained, per-installation access.
sidebar_label: GitHub App connection
sidebar_position: 7
---

# GitHub App connection

A **GitHub App** is the credential TrustedOSS uses for fine-grained, per-repository access — for example, the upcoming auto-remediation flow that opens dependency-bump pull requests. Unlike a personal access token (PAT), a GitHub App is:

- **Installable** per organization / repository, with fine-grained permissions (`contents` + `pull_requests: write`).
- **Short-lived** at the token level — TrustedOSS mints a fresh installation access token for each operation; the long-lived secret never leaves the server.
- **Multi-tenant** — each team registers and manages its own App independently.

:::note Audience
`team_admin` registers / revokes credentials and links installations for their team. `super_admin` can manage any team's. `developer` can read (list / view) their team's credentials but cannot mutate them.
:::

:::info API-only management
There is no in-portal form for registering a GitHub App credential in this release — the registration / linking endpoints listed below are the only management surface. A `team_admin`-facing UI is on the roadmap. Until then, the audit trail of any registration is visible under [Audit log](./audit-log.md) (with the encrypted columns masked to `***`).
:::

## What it stores

A registered credential is a row scoped to one team. It holds:

| Field | Stored as | Notes |
|-------|-----------|-------|
| `app_id` | plaintext | GitHub App numeric id (used as the App-JWT `iss`). |
| `app_slug` | plaintext (optional) | Human-readable App slug. |
| **private key (PEM)** | **Fernet ciphertext** | The App's private key. Accepted once at registration; **never** returned by any endpoint. |
| **webhook secret** | **Fernet ciphertext** (optional) | The App's webhook HMAC secret. |

The plaintext private key is accepted **only** on the registration request body and is encrypted **before** it is written to PostgreSQL. No read endpoint ever returns the key or its ciphertext — responses carry only metadata plus a `has_private_key` / `has_webhook_secret` boolean.

## Encryption at rest

The private key and webhook secret are encrypted with **Fernet** (AES-128-CBC + HMAC-SHA256) before persistence and decrypted only in memory, for the lifetime of a single token-minting operation.

The encryption key is resolved at runtime:

1. **`GITHUB_APP_ENCRYPTION_KEY`** — a URL-safe-base64-encoded 32-byte Fernet key. **Set this in production.** Generate one with:

   ```python
   from cryptography.fernet import Fernet
   print(Fernet.generate_key().decode())
   ```

2. If unset, TrustedOSS **derives** a key deterministically from `SECRET_KEY` so local / development bring-up works without extra configuration. A structured `WARNING` is logged whenever the derived key is used.

:::warning Rotate deliberately
The derived key shares its fate with `SECRET_KEY`. If you run without a dedicated `GITHUB_APP_ENCRYPTION_KEY`, **rotating `SECRET_KEY` will make every stored GitHub App credential undecryptable** — you would have to re-register each App. Set a dedicated, independently rotatable `GITHUB_APP_ENCRYPTION_KEY` in production.
:::

A credential that cannot be decrypted (because the key was rotated away) surfaces a clean error when used — never a key leak.

## Audit log

Registering, revoking, or re-linking a credential emits an `audit_logs` row. The `private_key_encrypted` and `webhook_secret_encrypted` columns are **masked to `***`** in the audit diff, so credential material never lands in the audit trail.

Revocation is a **soft delete** (sets `revoked_at`), mirroring API keys: the credential is immediately unusable but the row is retained for forensic queries.

## Installation opt-in

A credential alone does not grant TrustedOSS the right to touch a project's repository. A team must explicitly **link an installation** (account / repo) to a TrustedOSS project. The opt-in project must belong to the **same team** as the credential — cross-team links are rejected.

## Endpoints

All endpoints require JWT authentication and return RFC 7807 `application/problem+json` on error. Prefix: `/v1/github-app-credentials`.

| Method | Path | Role | Purpose |
|--------|------|------|---------|
| `POST` | `/v1/github-app-credentials?team_id=…` | `team_admin` | Register a credential (201). Private key never returned. |
| `GET` | `/v1/github-app-credentials` | member | List credentials visible to the caller. |
| `GET` | `/v1/github-app-credentials/{id}` | member | Fetch one credential's metadata. |
| `DELETE` | `/v1/github-app-credentials/{id}` | `team_admin` | Revoke (soft-delete). Idempotent. |
| `POST` | `/v1/github-app-credentials/{id}/installations` | `team_admin` | Link / opt-in an installation. Idempotent on re-link. |
| `GET` | `/v1/github-app-credentials/{id}/installations` | member | List installations under a credential. |
| `DELETE` | `/v1/github-app-credentials/{id}/installations/{installation_id}` | `team_admin` | Unlink an installation. Idempotent. |

A non-member who probes a credential id gets `404` (existence-hide), not `403`, so credential ids cannot be enumerated.

## Related configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `GITHUB_APP_ENCRYPTION_KEY` | _(derived from `SECRET_KEY`)_ | Fernet key for credential encryption at rest. |
| `GITHUB_API_URL` | `https://api.github.com` | GitHub REST base; override for GitHub Enterprise Server. |
