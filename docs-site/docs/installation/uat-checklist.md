---
id: uat-checklist
title: Install UAT checklist
description: Operator-facing fresh-OS verification — install + backup + cross-host restore round-trip on Ubuntu 22.04 / Rocky Linux 9.
sidebar_label: Install UAT checklist
sidebar_position: 5
---

# Install UAT checklist

This is the **manual counterpart** of `.github/workflows/install-uat.yml`. The
CI workflow exercises the wrapper-script logic on every cron run; this
checklist is what an operator works through on a real customer-facing host
before declaring an environment ready. Run it any time you are bringing up
a fresh production host, and again any time you change `scripts/install.sh`,
`scripts/backup.sh`, `scripts/restore.sh`, or `scripts/upgrade.sh`.

:::note Audience
A platform / SRE engineer with `sudo` on a clean Ubuntu 22.04 LTS or
Rocky Linux 9 host. Comfortable with `docker-compose`, `psql`, and SSH.
:::

## 0. Goals

- Validate that the install bundle works **on a host you have not touched
  before** (no leftover state, no cached dependencies).
- Confirm the backup + restore round-trip survives a host swap (the host
  that took the backup is not necessarily the host that restores it).
- Surface drift between `docker-compose.yml` (production, Traefik-fronted)
  and `docker-compose.dev.yml` (the file CI uses).

## 1. Host preparation

| Item | Recommended |
|------|-------------|
| OS   | Ubuntu 22.04 LTS or Rocky Linux 9 |
| CPU  | 8 vCPU |
| RAM  | 16 GB |
| Disk | 100 GB free under `/opt` |
| Network | Outbound HTTPS to GitHub, Docker Hub, your registry |

Install Docker Engine + `docker-compose` **V1** (CLAUDE.md core rule #10
forbids the V2 plugin):

```bash
# Docker Engine — distro-specific. See https://docs.docker.com/engine/install/.
# Compose V1, version-pinned for reproducibility:
sudo curl -L "https://github.com/docker/compose/releases/download/1.29.2/docker-compose-$(uname -s)-$(uname -m)" \
  -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose
docker-compose --version    # → docker-compose version 1.29.2
```

Create the workspace dir:

```bash
sudo mkdir -p /opt/trustedoss/workspace /opt/trustedoss/backups
sudo chown -R "$USER":"$USER" /opt/trustedoss
```

## 2. Run `install.sh`

Clone the repo and run the wizard. Pick **one** of (a) interactive or (b)
non-interactive — the latter mirrors what CI runs.

```bash
git clone https://github.com/trustedoss/trustedoss-portal.git
cd trustedoss-portal
```

### (a) Interactive

```bash
bash scripts/install.sh
```

The wizard asks for: public URL, super-admin email, super-admin password
(twice, hidden). Defaults are sensible.

### (b) Non-interactive

```bash
INSTALL_HOST=http://portal.example.com \
INSTALL_ADMIN_EMAIL=admin@example.com \
INSTALL_ADMIN_PASSWORD='ReplaceWithStrongPassphrase!' \
bash scripts/install.sh --no-prompt
```

If `INSTALL_ADMIN_PASSWORD` is omitted, the script generates a random
password and prints it once on stdout — capture it before the terminal
scrolls. Rotate it on first login.

### Expected result

```text
✓ docker-compose found: docker-compose version 1.29.2
✓ openssl found
✓ curl found
✓ wrote .env from .env.example
✓ generated SECRET_KEY (64 hex chars) and Postgres password
✓ wrote CORS_ALLOWED_ORIGINS=… + DOMAIN to .env
✓ containers started
✓ backend is healthy
✓ schema is at HEAD
✓ super admin account ready

Installation complete
✓ TrustedOSS Portal is running at: http://portal.example.com
```

`docker-compose -f docker-compose.yml ps` should show every row as
`Up (healthy)`.

## 3. First sign-in + project create

1. Open the public URL in a browser, sign in with the bootstrap super-admin.
2. Create a Team (`/admin/teams` → **New team**).
3. Create a Project (`/projects/new`) with a real Git URL.
4. Trigger a scan. Watch `/scans` — the WebSocket progress feed should
   move to **Completed** within ~5 minutes for a small repo.

**Stop here** if any of the four steps fail; the host is not UAT-clean
and a backup taken now would carry the failure forward.

## 4. Take a backup

```bash
bash scripts/backup.sh
```

Expected:

```text
Backup → backups/2026-05-09-143022
✓ wrote backups/2026-05-09-143022/postgres.sql.gz (12K)
✓ wrote backups/2026-05-09-143022/workspace.tar.gz (3.2M)
✓ wrote backups/2026-05-09-143022/manifest.json (alembic head = abcd1234)
Backup complete
  backups/2026-05-09-143022
```

Confirm `manifest.json` lists the Alembic head you expected:

```bash
cat backups/2026-05-09-143022/manifest.json
```

## 5. Cross-host restore

This is the hardest UAT step and the one most often skipped. Spin up a
**second** identical VM (`vm-b`), repeat steps 1 + 2 (install Docker +
`docker-compose` V1), but **do not** run `install.sh` yet.

Transfer the backup:

```bash
# from vm-a
scp -r backups/2026-05-09-143022 vm-b:/tmp/backups/
```

On `vm-b`:

```bash
git clone https://github.com/trustedoss/trustedoss-portal.git
cd trustedoss-portal
bash scripts/install.sh --no-prompt
mkdir -p backups
mv /tmp/backups/2026-05-09-143022 backups/
bash scripts/restore.sh backups/2026-05-09-143022
```

When `restore.sh` prints the destructive-action prompt, type **y**. (For
automation use `BACKUP_RESTORE_CONFIRM=yes bash scripts/restore.sh …`.)

**Expected**: `restore.sh` ends with `✓ alembic head matches manifest`.
Sign in to `vm-b`'s portal — your `vm-a` projects, scans, and users are
present.

## 6. Multi-PG version migration (optional)

Run this only if you are planning a major Postgres bump:

1. On `vm-a` (Postgres 16): `bash scripts/backup.sh`.
2. On `vm-b` (Postgres 17 — the default for this release): edit
   `docker-compose.yml` to use the 17 image **before** running install.sh.
3. Follow §5. The restore step sends the dump through `psql`, which is
   forward-compatible across one major Postgres version.

## 7. Tear down

```bash
docker-compose -f docker-compose.yml down -v
sudo rm -rf /opt/trustedoss/workspace
```

This removes containers, volumes, and the workspace dir. The `backups/`
tree is left in place — back it up off-host if you want it preserved.

## 8. Reporting results

Open an issue on GitHub with:

- OS + version (`cat /etc/os-release`)
- Docker version (`docker version --format '{{.Server.Version}}'`)
- Compose version (`docker-compose --version`)
- TrustedOSS Portal commit SHA (`git rev-parse HEAD`)
- Which sections passed / failed
- For failures, attach the relevant `docker-compose -f docker-compose.yml logs --tail=300` output

## Troubleshooting

### `install.sh` fails at "backend is healthy"

The Postgres dump may be too large for the wait window, or Alembic is
stuck on a long migration. Tail the logs while the script runs:

```bash
docker-compose -f docker-compose.yml logs -f backend
```

### `restore.sh` exits at the confirm prompt

You typed something other than `y` / `Y`. Re-run; for scripted use prefix
with `BACKUP_RESTORE_CONFIRM=yes`.

### Cross-host restore fails on `alembic head mismatch`

The destination tree is at a different code revision than the source.
Either upgrade `vm-b` to the same commit or run `alembic upgrade head` on
`vm-b` after the restore. The script prints the exact recovery command.

## See also

- [Docker Compose installation](./docker-compose.md) — the underlying
  reference for the install bundle.
- [Backup & restore](../admin-guide/backup-and-restore.md) — admin-UI
  flow + scheduling.
- [Upgrade](./upgrade.md) — `scripts/upgrade.sh` usage.
- `.github/workflows/install-uat.yml` — the CI counterpart that runs
  steps 2 + 4 + 5 on every cron tick.
