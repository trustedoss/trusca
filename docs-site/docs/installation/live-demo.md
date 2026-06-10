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

1. **`DEMO_READ_ONLY` read-only mode** (any deploy).
2. **A daily reset** (a systemd timer on the demo host that runs the reset
   script inside the backend container).

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

## 2. Daily reset (systemd timer)

The Hetzner demo host runs the reset on a **systemd timer**. The unit files ship
in `deploy/hetzner/`:

- `trustedoss-demo-reset.service` — a `oneshot` unit that runs the reset script
  inside the running backend container:

  ```
  ExecStart=/usr/local/bin/docker-compose -f docker-compose.yml \
    exec -T -e APP_ENV=demo backend python -m scripts.reset_demo
  ```

  Running it inside the container bypasses the HTTP `DEMO_READ_ONLY` guard
  (the script talks to Postgres directly); the script's own `APP_ENV` allow-list
  is the safety boundary.
- `trustedoss-demo-reset.timer` — fires the service daily at **03:17 UTC**
  (`OnCalendar=*-*-* 03:17:00 UTC`, `Persistent=true` so a reset missed while the
  host was down runs once on next boot).

The reset (`apps/backend/scripts/reset_demo.py`):

- **drops only the demo dataset** — the `demo-org` organization (FK cascade
  removes its teams → projects → scans → findings) and the demo users (scoped
  by **demo-org membership** — only users who belong exclusively to the demo
  org are removed; cascade removes their memberships / notifications). No global
  truncate; a co-tenant who also belongs to another org is never touched.
- **reseeds** via the idempotent `seed_demo._seed`, so the dataset shape is
  single-sourced with the normal seed.
- **refuses to run** unless `APP_ENV` is `dev` or `demo` (it can never run
  against a production database).

Install and enable the timer on the host:

```bash
sudo cp deploy/hetzner/trustedoss-demo-reset.service /etc/systemd/system/
sudo cp deploy/hetzner/trustedoss-demo-reset.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now trustedoss-demo-reset.timer
```

:::note Pin a stable demo password
Set `DEMO_SUPER_ADMIN_PASSWORD` in the host `.env` to a known value so the
published demo credentials survive the nightly reset. If you leave it unset, the
reseed generates a random password each night but does **not** log the
plaintext, so you would not learn the new credential.
:::

See [GCP Demo SaaS deploy](./gcp-deploy.md) for the full deploy runbook.

:::note Manual reset
Trigger a reset on demand without waiting for the timer:

```bash
sudo systemctl start trustedoss-demo-reset.service
# or run the underlying command directly:
docker-compose -f docker-compose.yml exec -T -e APP_ENV=demo backend python -m scripts.reset_demo
```
:::

## Local read-only demo (Docker Compose)

The read-only mode works on any deploy — for a local read-only instance, add
`DEMO_READ_ONLY=true` to your `.env` and restart the backend. The systemd timer
is for the demo host; locally, re-run the reset inside the backend container
whenever you want a clean dataset:

```bash
docker-compose -f docker-compose.dev.yml exec -e APP_ENV=demo backend \
  python -m scripts.reset_demo
```
