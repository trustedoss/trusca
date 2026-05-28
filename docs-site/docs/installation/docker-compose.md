---
id: docker-compose
title: Install with Docker Compose
description: Step-by-step install of TrustedOSS Portal on a Linux host using docker-compose V1 and the bundled install wizard.
sidebar_label: Docker Compose
sidebar_position: 1
---

# Install with Docker Compose

This is the supported install path for self-hosted deployments. The `scripts/install.sh` wizard pulls images, generates secrets, and creates the first `super_admin` user — typically in under 10 minutes on a warm Docker cache. Alembic migrations are applied automatically by the backend container on start (`AUTO_MIGRATE`, default `true`), so neither path below needs a manual `alembic upgrade head`.

:::note Audience
Operators with `sudo` on a Linux host. Familiarity with `docker-compose` and basic shell. Not for end users — point them at the URL once the install completes.
:::

## Prerequisites

- **Linux host** (tested on Ubuntu 22.04 LTS, Debian 12, RHEL 9). macOS works for development but is not a supported production target.
- **Docker Compose.** `docker-compose` (V1, hyphenated) is the project standard; the `install.sh` wizard prefers it but **falls back to the `docker compose` (V2) plugin** when V1 is absent — so a stock modern host works. See [the V1/V2 note](#why-docker-compose-v1-not-v2).
- **`openssl`** — used to generate the SECRET_KEY and database password.
- **`curl`** — used by the post-install health probe (and by the no-clone quick install above).
- **Outbound HTTPS** to GitHub Container Registry (`ghcr.io`, where the portal images **and** the Trivy DB are published). For air-gapped operation, mirror the Trivy DB to an internal OCI registry — see [Vulnerability data — Air-gapped operation](../admin-guide/vulnerability-data.md#air-gapped).
- **Disk:** ≥ 20 GB free for images, the workspace mount, and at least seven days of backups.
- **CPU/RAM:** 4 vCPU / 8 GB RAM minimum. Real source scans (cdxgen + scancode) peak at ~6 GB on the worker — give it headroom.

Verify your environment:

```bash
docker-compose --version           # prints Compose 1.x (preferred)
# …or, if you only have the V2 plugin, the wizard falls back to:
docker compose version             # prints Compose v2.x
openssl version
curl --version
df -h /                            # at least 20 GB free
```

## Evaluation install (lightweight, one command)

Want to *try* TrustedOSS Portal before committing a production host? The
**evaluation profile** stands the portal up on a small machine and seeds a
realistic demo dataset, so you go from clone to a populated dashboard in one
command — no manual migration, no empty screen.

:::note When to use this
A laptop, a throwaway cloud VM, or any **2 vCPU / 4 GB RAM** host. For a real
deployment use the [install wizard](#step-2--run-the-install-wizard) instead —
the eval profile trades production hardening (TLS, role separation, the full
6 GB scan worker) for a low-friction first look.
:::

### Requirements

- **2 vCPU / 4 GB RAM** (vs. 4 vCPU / 8 GB recommended for the full stack).
- `docker-compose` (V1 preferred; the script falls back to the `docker compose`
  V2 plugin), `openssl`, and `curl`.
- A clone of the repository (the eval script lives at `scripts/eval-up.sh`).

### One command

```bash
git clone https://github.com/trustedoss/trustedoss-portal.git
cd trustedoss-portal
./scripts/eval-up.sh            # add --no-prompt for CI / automation
```

The script:

1. Picks the Compose binary (V1 preferred).
2. Bootstraps `.env` from `.env.example` — generates a strong `SECRET_KEY`, sets
   `APP_ENV=demo`, and pins `CORS_ALLOWED_ORIGINS` to `EVAL_URL`
   (default `http://localhost`). An existing `.env` is left untouched.
3. Brings up the eval stack:

   ```bash
   docker-compose -f docker-compose.yml -f docker-compose.eval.yml up -d
   ```

   with `--compatibility` so the eval resource limits actually bind.
4. Waits for the backend **readiness gate** — `GET /health/ready` returns `200`
   only once the Postgres schema is at the Alembic HEAD (`AUTO_MIGRATE=true`
   applies it on start). If readiness never flips, it runs a one-shot
   `alembic upgrade head` and re-polls.
5. Seeds the **demo dataset** (`scripts/seed_demo.py`, idempotent): 1 org,
   3 teams, 5 users, 5 projects, 10 CVEs, license findings, obligations, and
   in-app notifications.
6. Prints the URL and the demo accounts.

Open **`http://localhost/`** (or your `EVAL_URL`) and sign in:

| Account | Email |
| --- | --- |
| Super admin | `admin@demo.trustedoss.dev` |
| Team admins | `frontend-admin@demo.trustedoss.dev`, `backend-admin@…`, `security-admin@…` |
| Developer | `dev@demo.trustedoss.dev` |

The password is whatever you set in `DEMO_SUPER_ADMIN_PASSWORD` (in `.env`)
before the first run. If you leave it unset, `seed_demo.py` generates a random
one and `eval-up.sh` prints it once — store it, it is not persisted anywhere.

### Resource budget (2 vCPU / 4 GB)

The eval overlay (`docker-compose.eval.yml`) caps every container so no single
service starves the host:

| Service | CPU limit | Memory limit |
| --- | --- | --- |
| traefik (HTTP-only) | 0.25 | 128M |
| postgres | 0.50 | 768M |
| redis | 0.25 | 128M |
| backend | 0.75 | 768M |
| worker (concurrency 1) | 0.75 | 1280M |
| beat | 0.25 | 192M |
| frontend | 0.25 | 128M |

CPU caps deliberately oversubscribe the two cores (they are ceilings, not
reservations); the memory limits sum to ~3.4 GB, leaving headroom on a 4 GB
host. Compared to the prod stack, Traefik runs plain **HTTP only** (no Let's
Encrypt — so no public DNS / port 443 needed), and the worker runs a single
low-concurrency slot.

### How vulnerabilities show up

The eval profile ships findings via the seeded demo dataset; the worker also downloads the Trivy DB on first boot if it has internet egress. The eval host does not need any external vulnerability engine — Trivy and its DB live entirely inside the worker container.

For air-gapped evaluation (no `ghcr.io` egress), see [Vulnerability data — Air-gapped operation](../admin-guide/vulnerability-data.md#air-gapped).

:::warning Eval ≠ production
Real source scans (cdxgen + scancode) peak at ~6 GB on the worker; the eval
worker (1280M, concurrency 1) is sized for **browsing the seeded dataset**, not
for production scanning. It can run a small scan but will struggle on a large
repository. The eval profile also skips TLS and the L1 DB role separation — do
not expose it to the public internet. Use the [install
wizard](#step-2--run-the-install-wizard) for anything beyond a first look.
:::

Tear down when you are done:

```bash
docker-compose -f docker-compose.yml -f docker-compose.eval.yml down
# add -v to also delete the Postgres / workspace volumes (wipes the demo data)
```

## Prerequisites for HTTPS deployments

Before running the wizard, make sure your host meets these three
conditions. The wizard does not validate them and Traefik will fail
silently if any is missing.

- **DNS**: an `A` record (or `CNAME`) on the domain you plan to use
  (e.g. `oss.acme.com`) must point at your host's public IP. Verify
  with `dig +short oss.acme.com`.
- **Firewall**: ports `80` and `443` must be reachable from the
  public internet. Traefik uses HTTP-01 challenge on `:80` to issue
  the Let's Encrypt certificate; once that succeeds it redirects all
  traffic to `:443`. UFW / cloud-provider firewall / security group
  all need both open.
- **TLS_EMAIL**: the wizard collects this when the public URL is
  `https://...`. Let's Encrypt sends expiry warnings and rate-limit
  escalation here; use a real mailbox you check.

For HTTP-only / `localhost` installs (development, air-gapped UAT),
none of the above applies — the wizard skips TLS_EMAIL and Traefik
does not enter the ACME flow.

## Quick install (no clone)

If you just want the stack running and don't need the helper scripts, you can install directly from the published images without cloning the repository — a single-file install experience. The production images are published to GitHub Container Registry (`ghcr.io/trustedoss/backend`, `…/backend-worker`, `…/frontend`) and pull anonymously.

Fetch the three files the compose stack needs (the compose file, the env template, and the one-time Postgres role init script), edit `.env`, then start:

```bash
mkdir -p trustedoss && cd trustedoss
BASE=https://raw.githubusercontent.com/trustedoss/trustedoss-portal/v2.0.0

# 1. The self-contained production compose file (no `build:` section — pulls
#    images from ghcr.io) and the env template.
curl -fsSLO "$BASE/docker-compose.yml"
curl -fsSL  "$BASE/.env.example" -o .env

# 2. The compose file mounts one repo file into Postgres for first-boot role
#    provisioning. Fetch it to the path the compose file expects.
mkdir -p scripts
curl -fsSL "$BASE/scripts/postgres-init.sh" -o scripts/postgres-init.sh
chmod +x scripts/postgres-init.sh

# 3. Edit .env — at minimum set SECRET_KEY (openssl rand -hex 32), strong
#    POSTGRES_PASSWORD / POSTGRES_APP_PASSWORD, DOMAIN, TLS_EMAIL, and
#    CORS_ALLOWED_ORIGINS=https://<your-domain>. Pin IMAGE_TAG to the release
#    you want (defaults to 2.0.0).
$EDITOR .env

# 4. Pull and start.
docker-compose -f docker-compose.yml pull
docker-compose -f docker-compose.yml up -d
```

The published backend image's entrypoint **applies Alembic migrations automatically on start** (`AUTO_MIGRATE`, default `true`) and only then starts uvicorn — so the schema is at HEAD by the time the backend reports healthy. You do **not** need to run `alembic upgrade head` by hand. Automatic migration does **not** create users, so you still bootstrap the first admin once:

```bash
# Read the password into the shell WITHOUT echoing it, then pass only the
# variable NAME to `-e` so the value is inherited from the calling shell and
# never lands in argv (visible in `ps -ef`) or in your shell history.
read -rs ADMIN_PASSWORD; export ADMIN_PASSWORD   # type the 12+ char password, press Enter

# Create the first super_admin (the schema is already at HEAD).
docker-compose -f docker-compose.yml exec -T \
  -e ADMIN_EMAIL=you@example.com \
  -e ADMIN_PASSWORD \
  backend python -m scripts.create_super_admin

unset ADMIN_PASSWORD   # clear it from the shell once the user exists
```

:::warning Do not inline the password
Avoid `-e ADMIN_PASSWORD='literal'`: the literal is visible to any user who
runs `ps -ef` while the command executes and is written to your shell history.
Passing the bare name (`-e ADMIN_PASSWORD`) makes Docker inherit the value
from the environment instead.
:::

:::note Managing the schema out-of-band
The single-role `.env` template ships `AUTO_MIGRATE=true` and it just works. If you run an **L1 role-separated** stack (separate `DATABASE_URL_OWNER` for DDL and `DATABASE_URL_APP` for runtime), the runtime container only holds the DML-only app DSN and cannot run DDL, so automatic migration must be off.

- **With the wizard (Step 2):** `install.sh` **detects L1** (`DATABASE_URL_OWNER` is set and differs from the runtime DSN) and **writes `AUTO_MIGRATE=false` to `.env` automatically**, then applies migrations as the owner role itself. You do not need to set anything.
- **On this no-clone path:** there is no wizard, so **you must set `AUTO_MIGRATE=false` in `.env` yourself** for an L1 stack and run `alembic upgrade head` as the owner role (override `DATABASE_URL` with `DATABASE_URL_OWNER` for that one command). If you leave it `true` on an L1 stack the backend entrypoint fails fast (exit 1, no crash-loop) with a clear DDL-permission error in the logs.
:::

### Liveness vs. readiness: how the stack waits for the schema

The backend exposes **two** unauthenticated health endpoints. They answer different questions, and the Compose / Kubernetes startup gates depend on the distinction.

| Endpoint | Question it answers | Touches the DB? | Used by |
| --- | --- | --- | --- |
| `GET /health` | Is the uvicorn **process** up and accepting requests? (pure liveness) | No | Kubernetes `livenessProbe`; liveness-only consumers |
| `GET /health/ready` | Is the Postgres **schema at the Alembic HEAD** revision, i.e. is it safe to serve traffic and start workers? (readiness) | Yes (a read-only `SELECT` on `alembic_version`) | Compose backend `healthcheck`; Kubernetes `readinessProbe` |

`/health/ready` returns `200 {"status":"ready"}` only when the schema matches HEAD. Otherwise it returns `503` with an RFC 7807 `application/problem+json` body summarising the revision mismatch (it never leaks the DSN or credentials).

Since v2.1 (Track B), the `backend` service's Compose `healthcheck` probes **`/health/ready`**, so the `worker` and `beat` services — which declare `depends_on: backend (condition: service_healthy)` — start only **after the schema is migrated**, under both toggles:

- **`AUTO_MIGRATE=true`** (single-role default): the backend container runs `alembic upgrade head` on start and `/health/ready` flips to `200` once it finishes. Workers then start against a migrated schema. This is the normal path and needs no operator action.
- **`AUTO_MIGRATE=false`** (L1 role-separated stack): uvicorn answers `/health` immediately, but `/health/ready` stays `503` (the container stays `health: starting`) until your **external** `alembic upgrade head` (run as the owner role — `install.sh` / `upgrade.sh` do this) brings the schema to HEAD. **This is intended:** the worker and beat wait for the schema instead of starting against a not-yet-migrated database. If you forget to run the migration on an L1 stack, the backend will simply never become healthy — check `docker-compose logs backend` and run the owner-role migration.

:::note Why a long migration won't flip the container to `unhealthy`
The backend healthcheck uses a generous `start_period` (60s). A large first migration on a big database can run for a while before `/health/ready` turns `200`; the `start_period` keeps Docker from marking the container `unhealthy` (and restarting it) before that first migrate completes.
:::

:::tip Prefer the wizard for a guided install
The `install.sh` wizard (Steps 1–3 below) does all of this for you — secret generation, the health-wait loop, the migration, and the admin bootstrap — and it also works with the Compose **V2** plugin (`docker compose`) if your host doesn't have V1. Use the no-clone path when you want full control over each step or are baking your own automation.
:::

## Step 1 — Clone the repository

```bash
git clone https://github.com/trustedoss/trustedoss-portal.git
cd trustedoss-portal
```

If you maintain a fork, clone the fork instead. Pin to a release tag for reproducible installs:

```bash
git checkout v2.0.0
```

## Step 2 — Run the install wizard

```bash
bash scripts/install.sh
```

The wizard does the following in order:

1. Verifies `docker-compose`, `openssl`, and `curl` are on PATH.
2. Copies `.env.example` to `.env` if `.env` is absent (or backs up the existing one on request).
3. Generates a 64-hex-char `SECRET_KEY` and a strong PostgreSQL password.
4. Prompts for the **public URL** the portal should be reachable at, then writes `CORS_ALLOWED_ORIGINS` and `DOMAIN` to `.env`.
5. Decides the **migration policy**: if it detects an L1 role-separated stack (`DATABASE_URL_OWNER` is set and differs from the runtime DSN), it writes `AUTO_MIGRATE=false` to `.env` so the runtime container does not attempt an app-role DDL run; single-role stacks keep the default `true`.
6. `docker-compose pull` — pulls the pinned images.
7. `docker-compose up -d` — starts the stack. On a single-role stack the backend container applies Alembic migrations on start (`AUTO_MIGRATE=true`); on L1 it does not (policy set in the previous step).
8. Waits for the backend `/health` endpoint to return 200 (60-second timeout).
9. Runs `alembic upgrade head` once as the **owner** role (`DATABASE_URL_OWNER`). On L1 this is the authoritative DDL pass (the runtime container only holds the DML-only app DSN); on a single-role stack where the entrypoint already migrated it is an idempotent re-check — already-applied revisions are skipped.
10. Prompts for the first super-admin email and password (12+ characters, confirmed). Automatic migration does not create users, so this step always runs.
11. Prints the final URL and next-steps reminder.

### What you should see at the end

```
Installation complete
✓ TrustedOSS Portal is running at: https://trustedoss.example.com
  Login:           you@example.com
  Admin panel:     https://trustedoss.example.com/admin
  API docs:        https://trustedoss.example.com/api/docs
```

## Step 3 — Sign in and verify

1. Open the URL printed by the wizard.
2. Sign in with the super-admin credentials.
3. Visit **/admin/health** — every component should be **green**: backend, postgres, redis, worker, beat. The worker downloads the Trivy DB on first boot (1–3 minutes); the Vulnerability data card flips to green once the download completes.

To operate the Trivy DB (refresh cadence, air-gapped mirror, troubleshooting), see [Vulnerability data (Trivy DB)](../admin-guide/vulnerability-data.md).

## Step 4 — Schedule backups

Off-host backups are not optional in production. Add a cron entry:

```bash
sudo crontab -e
# m h dom mon dow command
0 3 * * *  cd /opt/trustedoss-portal && bash scripts/backup.sh >> /var/log/trustedoss-backup.log 2>&1
```

`scripts/backup.sh` writes a timestamped directory under `backups/` containing `postgres.sql.gz`, `workspace.tar.gz`, and a `manifest.json`. Old backups are pruned after 7 days (override with `BACKUP_RETENTION_DAYS` in `.env`).

For full restore procedures see [backup & restore](../admin-guide/backup-and-restore.md).

## End-to-end first-success checklist (30 minutes)

After `bash scripts/install.sh` completes:

- [ ] Open `https://<your-host>` — login screen renders, browser
  shows a valid TLS lock (if HTTPS).
- [ ] Log in with the super-admin email/password the wizard
  printed.
- [ ] Wait for the worker to finish the **first Trivy DB download** —
  `docker-compose -f docker-compose.yml logs --tail=100 worker | grep trivy_db`
  shows `trivy_db_download_complete` within 1–3 minutes of first boot.
  Until it lands, the Vulnerabilities tab on a new scan is empty.
- [ ] Go to `/admin/teams` → **New team** → name it `engineering`.
- [ ] Ask a teammate to register at `/register`, then add them at
  `/admin/users → <user> → Memberships → Add to team`.
- [ ] Switch to the teammate's session → create a project at
  `/projects → New project` with a small public repo (test).
- [ ] Trigger a scan; the right-slide progress drawer should walk
  through `bootstrap → fetch → prep → cdxgen → scancode →
  sbom_upload → vuln_match → finalize` in about 2-5 minutes. WebSocket
  frames at v2.4.0 still carry the historical slugs `dt_upload`/`dt_findings`
  for compatibility — the on-screen labels read the new names.
- [ ] Open the project's **Vulnerabilities** tab — any CVEs from
  the test repo should be listed.

If any step fails, see `/docs/installation/troubleshooting` and the
Admin → Health dashboard.

## Troubleshooting

### Port 80 or 443 already in use

```text
Bind for 0.0.0.0:443 failed: port is already allocated
```

Another process holds the port. List bound ports and free them:

```bash
sudo ss -tlnp | grep -E ':80|:443'
```

If you intend to keep an existing reverse proxy, edit `docker-compose.yml` to drop the Traefik service and route `/api`, `/health`, `/health/ready`, `/metrics` to the backend container, and `/` to the frontend.

### Backend never becomes healthy

```text
✗ backend did not become healthy. Run: docker-compose -f docker-compose.yml logs backend
```

The most common causes:

- `DATABASE_URL` references a host that is not on the compose network. Ensure the host part is `postgres` (the service name), not `localhost` or `127.0.0.1`.
- The Postgres container is not yet healthy. `docker-compose ps` should show `postgres` as `Up (healthy)`. If it is restarting, check `docker-compose logs postgres` for credential mismatches with `.env`.
- Automatic migration failed. With `AUTO_MIGRATE=true` (default) the backend runs `alembic upgrade head` on start and exits non-zero if it fails after its retry loop, so the container never becomes healthy. Read the alembic traceback in `docker-compose logs backend`. On an L1 role-separated stack the runtime DSN cannot run DDL — set `AUTO_MIGRATE=false` and run the migration as the owner role (the wizard does this in Step 2).

### Out of disk space mid-install

The Docker layer cache for `cdxgen` + scancode + Trivy is around 4 GB. If `/var/lib/docker` runs out, the pull aborts. Free space and re-run `docker-compose pull` followed by `docker-compose up -d`.

### Need to start over with a fresh `.env`

Delete `.env` (or move it aside) and re-run the wizard:

```bash
mv .env .env.backup
bash scripts/install.sh
```

The wizard will re-generate secrets. **Existing data in PostgreSQL is preserved** — secrets in `.env` only affect new sessions, but rotating `SECRET_KEY` invalidates all current refresh tokens and forces every user to sign in again. Prefer this over editing secrets by hand.

## Uninstall

To stop the stack but keep data:

```bash
docker-compose -f docker-compose.yml down
```

To remove **everything including the database and workspace**:

```bash
docker-compose -f docker-compose.yml down -v
sudo rm -rf /opt/trustedoss/workspace
```

:::warning Data loss
`docker-compose down -v` deletes the named volumes (`postgres-data`, `redis-data`, `traefik-acme`, `workspace`). There is no recovery without a recent backup.
:::

## Maintainer note — publishing the images (one-time org setup)

The portal images are published to GitHub Container Registry by the [`release.yml`](https://github.com/trustedoss/trustedoss-portal/blob/main/.github/workflows/release.yml) workflow, triggered by pushing a `vX.Y.Z` git tag (or via **Run workflow** with a tag input). For that workflow to push, the **organisation must let GitHub Actions write packages** — Org → Settings → Actions → Workflow permissions → *Read and write permissions* (or grant the repo the *Write* role under the package's *Manage Actions access*). The workflow uses the built-in `GITHUB_TOKEN`; no personal access token is required.

After the first push, set each package's visibility to **Public** (ghcr package → Package settings → Change visibility → Public) so operators can `docker pull` anonymously — the no-clone quick install relies on this. Each release publishes an immutable `X.Y.Z` tag and a movable `X.Y` tag; there is no `latest` tag (CLAUDE.md rule #9).

## Why docker-compose V1, not V2?

The project's **development and CI** environment standardizes on Compose V1 (`docker-compose`) — V2 syntax differences are not exercised in our internal pipelines, and PRs that introduce `docker compose` (V2) into the dev/CI surface are blocked by review (see [`CLAUDE.md`](https://github.com/trustedoss/trustedoss-portal/blob/main/CLAUDE.md) rule #10).

That constraint is internal. For **end-user installs**, the `install.sh` wizard prefers V1 but falls back to the V2 plugin (`docker compose`) so a stock modern host — where V1 reached end-of-life in 2023 — works out of the box. The compose files themselves use the V1 file format, which V2 also reads.

## See also

- [Upgrade an existing install](./upgrade.md)
- [Environment variables reference](../reference/env-variables.md)
- [Architecture overview](../reference/architecture.md)
