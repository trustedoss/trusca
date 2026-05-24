---
id: live-demo
title: Live read-only demo
description: Run TrustedOSS Portal as a public, read-only live demo with a nightly dataset reset.
sidebar_label: Live demo
sidebar_position: 4
---

# Live read-only demo

The portal can run as a **public live demo**: anyone can sign in with the seeded
demo accounts and browse real projects, scans, vulnerabilities, licenses, SBOMs,
and reports — but **all writes are disabled**, and the dataset is reset to a
clean state every night.

This is built from two independent pieces:

1. **`DEMO_READ_ONLY` read-only mode** (any deploy — Docker Compose or GCP).
2. **A daily reset** (GCP only — a Cloud Scheduler → Cloud Run Job in the
   bundled Terraform module).

## 1. Read-only mode (`DEMO_READ_ONLY`)

Set the backend environment variable:

```bash
DEMO_READ_ONLY=true
```

(Accepted truthy values: `1`, `true`, `yes`, `on` — case-insensitive. Read at
request time, so flipping it only needs a process restart, not a rebuild.)

When enabled, a single middleware enforces the policy for the **whole API**, so
no individual endpoint can escape it:

- **Reads always pass** — `GET`, `HEAD`, and `OPTIONS` (the last so CORS
  preflight keeps working).
- **Writes are blocked by default** — every `POST` / `PUT` / `PATCH` / `DELETE`
  (and any other verb) is rejected unless it is on a tiny **allow-list**.
- The allow-list is exactly the auth flows a demo still needs:
  `POST /auth/login`, `POST /auth/refresh`, `POST /auth/logout`. Everything else
  — including self-registration, password reset/change, project creation, scan
  triggers, approvals, settings, webhooks, and file uploads — is blocked.

A blocked request gets an **RFC 7807** `403` with
`Content-Type: application/problem+json`:

```json
{
  "type": "urn:trustedoss:problem:demo-read-only",
  "title": "Read-only demo",
  "status": 403,
  "detail": "This is a read-only live demo. Creating, updating, or deleting data is disabled. …",
  "instance": "/v1/projects",
  "demo_read_only": true
}
```

### Bypass hardening

The guard is an **allow-list, not a block-list** — a new mutating endpoint added
later is blocked automatically, with no change required. The path is normalized
(back-slashes folded, `.`/`..` segments resolved, trailing slash stripped) before
the allow-list check, so traversal tricks like `/v1/projects/../auth/login`
cannot smuggle a write path onto the list. The HTTP method is matched
case-insensitively and the allow-list is keyed on `(method, path)` pairs, so an
exotic verb cannot ride an allow-listed path.

### Frontend behaviour

The SPA reads the flag from the public `GET /health` response
(`{"status":"ok","demo_read_only":true}`) and:

- shows a slim **"Read-only demo"** banner at the top of the app, and
- disables write actions (e.g. the "Scan" and "Register project" buttons) with a
  tooltip explaining why.

The middleware is the real boundary; the UI gating only avoids dead-end clicks.

## 2. Daily reset (GCP)

The bundled Terraform module ships a **Cloud Scheduler → Cloud Run Job** that
runs `scripts/reset_demo.py` once a day. The job:

- **drops only the demo dataset** — the `demo-org` organization (FK cascade
  removes its teams → projects → scans → findings) and the demo users (matched
  by the stable `@demo.trustedoss.dev` email suffix; cascade removes their
  memberships / notifications). No global truncate; a co-tenant's data is never
  touched.
- **reseeds** via the idempotent `seed_demo._seed`, so the dataset shape is
  single-sourced with the normal seed.
- **refuses to run** unless `APP_ENV` is `dev` or `demo` (it can never run
  against a production database).

Enable it in `terraform.tfvars` (defaults shown):

```hcl
demo_read_only       = true          # block all non-auth mutations
demo_reset_enabled   = true          # provision the daily Scheduler + Job
demo_reset_schedule  = "17 3 * * *"  # cron (Cloud Scheduler syntax)
demo_reset_time_zone = "Etc/UTC"

# Optional — pin a STABLE demo super-admin password so the published demo
# credentials survive the nightly reset. Leave unset to rotate it randomly
# each night (the new value is logged once in the Job output).
# demo_super_admin_password = "REPLACE_ME_MIN_12_CHARS"
```

The reset Job reuses the backend image, service account, Cloud SQL connection,
and secrets, so there is nothing extra to build or grant. See
[GCP Demo SaaS deploy](./gcp-deploy.md) for the full deploy runbook.

:::note Manual reset
You can trigger a reset on demand without waiting for the schedule:

```bash
gcloud run jobs execute <name_prefix>-<env>-demo-reset --region <region>
```

The job name is in the `demo_reset_job_name` Terraform output.
:::

## Local read-only demo (Docker Compose)

The read-only mode works on any deploy — for a local read-only instance, add
`DEMO_READ_ONLY=true` to your `.env` and restart the backend. The nightly reset
is GCP-only; locally, re-run `apps/backend/scripts/reset_demo.py` (with
`APP_ENV=demo`) whenever you want a clean dataset.
