---
id: docker-compose
title: Install with Docker Compose
description: Step-by-step install of TrustedOSS Portal on a Linux host using docker-compose V1 and the bundled install wizard.
sidebar_label: Docker Compose
sidebar_position: 1
---

# Install with Docker Compose

This is the supported install path for self-hosted deployments. The `scripts/install.sh` wizard pulls images, generates secrets, runs Alembic migrations, and creates the first `super_admin` user — typically in under 10 minutes on a warm Docker cache.

:::note Audience
Operators with `sudo` on a Linux host. Familiarity with `docker-compose` and basic shell. Not for end users — point them at the URL once the install completes.
:::

## Prerequisites

- **Linux host** (tested on Ubuntu 22.04 LTS, Debian 12, RHEL 9). macOS works for development but is not a supported production target.
- **`docker-compose` (V1, hyphenated).** Compose V2 (`docker compose`) is not supported — see [why](#why-docker-compose-v1-not-v2).
- **`openssl`** — used to generate the SECRET_KEY and database password.
- **`curl`** — used by the post-install health probe.
- **Outbound HTTPS** to Docker Hub (or a mirrored registry) and to the OSV / NVD feeds if you bundle Dependency-Track.
- **Disk:** ≥ 20 GB free for images, the workspace mount, and at least seven days of backups.
- **CPU/RAM:** 4 vCPU / 8 GB RAM minimum. Real ORT scans peak at ~6 GB on the worker — give it headroom.

Verify your environment:

```bash
docker-compose --version           # must print Compose 1.x — not V2
openssl version
curl --version
df -h /                            # at least 20 GB free
```

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
5. `docker-compose pull` — pulls the pinned images.
6. `docker-compose up -d` — starts the stack.
7. Waits for the backend `/health` endpoint to return 200 (60-second timeout).
8. Runs `alembic upgrade head` to bring the schema to the latest revision.
9. Prompts for the first super-admin email and password (12+ characters, confirmed).
10. Prints the final URL and next-steps reminder.

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
3. Visit **/admin/health** — every component should be **green**: backend, postgres, redis, worker, beat. The `dt` row will be **OPEN** (Dependency-Track not yet wired in) — that is normal at this stage.

The portal is fully functional without Dependency-Track for component and license analysis. To enable vulnerability data, see [DT connector](../admin-guide/dt-connector.md).

## Step 4 — Schedule backups

Off-host backups are not optional in production. Add a cron entry:

```bash
sudo crontab -e
# m h dom mon dow command
0 3 * * *  cd /opt/trustedoss-portal && bash scripts/backup.sh >> /var/log/trustedoss-backup.log 2>&1
```

`scripts/backup.sh` writes a timestamped directory under `backups/` containing `postgres.sql.gz`, `workspace.tar.gz`, and a `manifest.json`. Old backups are pruned after 7 days (override with `BACKUP_RETENTION_DAYS` in `.env`).

For full restore procedures see [backup & restore](../admin-guide/backup-and-restore.md).

## Adding bundled Dependency-Track (optional)

The default install does **not** include Dependency-Track. To bundle it:

```bash
docker-compose -f docker-compose.yml -f docker-compose.dt.yml up -d
```

Then follow [DT connector](../admin-guide/dt-connector.md) to wire the API key and enable the eight OSV ecosystems. The first vulnerability mirror sync takes ~1 hour for Maven and less for the others.

## Troubleshooting

### Port 80 or 443 already in use

```text
Bind for 0.0.0.0:443 failed: port is already allocated
```

Another process holds the port. List bound ports and free them:

```bash
sudo ss -tlnp | grep -E ':80|:443'
```

If you intend to keep an existing reverse proxy, edit `docker-compose.yml` to drop the Traefik service and route `/api`, `/health`, `/metrics` to the backend container, and `/` to the frontend.

### Backend never becomes healthy

```text
✗ backend did not become healthy. Run: docker-compose -f docker-compose.yml logs backend
```

The most common causes:

- `DATABASE_URL` references a host that is not on the compose network. Ensure the host part is `postgres` (the service name), not `localhost` or `127.0.0.1`.
- The Postgres container is not yet healthy. `docker-compose ps` should show `postgres` as `Up (healthy)`. If it is restarting, check `docker-compose logs postgres` for credential mismatches with `.env`.
- Schema migration failed. Run `docker-compose exec backend alembic upgrade head` manually and read the traceback.

### Out of disk space mid-install

The Docker layer cache for `cdxgen` + ORT + Trivy is around 4 GB. If `/var/lib/docker` runs out, the pull aborts. Free space and re-run `docker-compose pull` followed by `docker-compose up -d`.

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

## Why docker-compose V1, not V2?

The project standardizes on Compose V1 (`docker-compose`) because all current target environments ship V1. V2 syntax differences (notably around `version:` headers and dependency conditions) are not exercised in CI. PRs that introduce `docker compose` (V2) usage are blocked by review. See [`CLAUDE.md`](https://github.com/trustedoss/trustedoss-portal/blob/main/CLAUDE.md) rule #10.

## See also

- [Upgrade an existing install](./upgrade.md)
- [Environment variables reference](../reference/env-variables.md)
- [Architecture overview](../reference/architecture.md)
