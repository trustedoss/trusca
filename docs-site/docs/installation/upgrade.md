---
id: upgrade
title: Upgrade
description: Upgrade an existing TRUSCA install with the bundled wrapper script — backup, image pull, alembic upgrade, health probe.
sidebar_label: Upgrade
sidebar_position: 2
---

# Upgrade

The `scripts/upgrade.sh` wrapper performs an **in-place** upgrade of a running install. It always takes a pre-upgrade backup before touching anything, and it leans on `docker-compose pull` + `up -d` so only services whose image hash changed get recreated.

:::note Audience
Operators with `sudo` on the host that runs the portal. Familiarity with `docker-compose ps` / `logs`.
:::

## Compatibility & policy

- **Forward-only Alembic migrations.** TRUSCA does not support `alembic downgrade`. To revert, restore the pre-upgrade backup (see [Rollback](#rollback)).
- **Minor / patch upgrades** within the same major version are always supported in place. **Major upgrades** (e.g., 2.x → 3.x) are documented in dedicated release notes; do not run `scripts/upgrade.sh` blindly across major versions.
- **Downtime expectation:** the portal is briefly unavailable while `docker-compose up -d` recreates services whose image changed. Typical window is under 30 seconds.

## Prerequisites

- A previous successful install (i.e. `docker-compose -f docker-compose.yml ps` shows healthy services).
- `docker-compose` (V1) on PATH.
- At least 5 GB free disk for the new image layers and the pre-upgrade backup.
- The intended `IMAGE_TAG` is in `.env` (or the wizard accepts the default `0.11.0`). If you maintain a private registry, `IMAGE_TAG` should match the manifest published there.

## Step 1 — Inspect the upgrade window

Find a quiet moment (no scans in flight). The dashboard `/scans` view shows the global queue; wait until it drains.

```bash
docker-compose -f docker-compose.yml ps
```

Every row should be `Up (healthy)`. If any row is restarting or unhealthy, fix that first — do not stack an upgrade on a broken install.

## Step 2 — Run the upgrade wrapper

```bash
bash scripts/upgrade.sh
```

Flow:

1. **Pre-upgrade backup** — `bash scripts/backup.sh` (mandatory, no flag to skip).
2. **`docker-compose pull`** — fetches the new images.
3. **`docker-compose up -d`** — recreates only services whose image hash changed.
4. **`alembic upgrade head`** — applies any new migrations.
5. **Health probe** — polls `/health` for up to 60 seconds.

A successful run ends with:

```text
✓ backend is healthy
Upgrade complete
  If something looks off, restore the pre-upgrade backup:
  bash scripts/restore.sh $(ls -td backups/* | head -1)
```

## Step 3 — Verify the upgrade

1. Sign in to the portal.
2. Visit **/admin/health** — every component should be green.
3. Trigger a small scan against a known project (or re-scan the most recently scanned one). Watch the WebSocket progress feed go to **Completed**.
4. If the release notes call out new admin screens or settings, walk them once.

## Rollback

If the upgrade left the portal broken, restore the pre-upgrade backup:

```bash
bash scripts/restore.sh "$(ls -td backups/* | head -1)"
```

`scripts/restore.sh` will:

1. Confirm the destructive action interactively (type **y**).
2. Stop application containers (`backend`, `frontend`, `worker`, `beat`). PostgreSQL and Redis stay up so the dump can stream straight in.
3. Restore the PostgreSQL dump (`postgres.sql.gz`).
4. Restore the workspace tarball (`workspace.tar.gz`) if present.
5. Restart the application containers.
6. Verify the Alembic head matches the backup's `manifest.json`.

If `manifest.json` is missing or the head does not match, the script prints a warning and you should run `alembic upgrade head` manually.

:::warning Data loss
`restore.sh` **replaces** the live database content and the `WORKSPACE_HOST_PATH` directory. There is no undo. Make sure the backup you point at is the right one (`ls -td backups/*` prints newest first).
:::

## Skipping versions

A single `upgrade.sh` run can hop multiple versions provided each intermediate has a forward-only migration path, so 2.0.0 → 2.0.5 in one step is supported. CI exercises the chain both on an empty database and, from the latest published release to the development head, on one populated through the API, so a data migration that only misbehaves when there are rows to migrate is caught before release.

The wrapper refuses two moves before it changes anything:

- **A downgrade.** Migrations are forward-only, so pointing `IMAGE_TAG` at an older release does not roll anything back; it starts older code against a newer schema. To go back, [restore a backup](#rollback).
- **A skipped major**, for example 1.x straight to 3.x. Each major's release notes carry the manual steps for that hop, and the wrapper's own migration preludes are keyed to single-major moves.

Both checks compare the running backend container's image tag against the `IMAGE_TAG` you are moving to. If either version cannot be read as `X.Y.Z` (a custom tag, or a stack that is currently down), the check reports what it saw and continues rather than blocking the upgrade. Set `UPGRADE_ALLOW_VERSION_SKIP=true` to override a refusal deliberately.

For major-version hops, follow the release-notes "Migration steps" section before invoking the wrapper.

## After upgrading past a container-scan identity fix

Container scans used to record every package in every image as an Alpine package — `pkg:apk/{name}@{version}`, package type `apk` — regardless of what the image actually contained. A Rocky image's rpms, a Debian image's debs and a pip package inside a Python image were all inventoried under the wrong ecosystem. Findings were never lost, so the counts were right and only the identity was wrong.

Scans now record the ecosystem the scanner reports. Two consequences, both limited to deployments that scanned rpm or deb based images:

- **Existing rows are not rewritten.** A past scan keeps the identity it was stored with; that is what the scan saw at the time. Re-scan the image to get a corrected inventory.
- **A re-scan resets that project's vulnerability triage.** The first-detected clock and any analyst verdict (accepted, false positive, …) are keyed on the component, and the corrected package is a different component. Findings on a re-scanned rpm or deb image come back as new, and verdicts have to be entered again.

Deployments that only ever scanned Alpine images are unaffected — those values were already correct.

## Common issues

### `alembic upgrade head` fails with a constraint violation

Usually a real data issue: a NOT-NULL column was added but a row predates the default value. Restore the backup, inspect the offending rows, and report the migration in the issue tracker.

### Health probe times out after 60 seconds

The pull may have brought down a service that needs a longer warm-up (e.g. worker after a JRE upgrade). Tail the logs:

```bash
docker-compose -f docker-compose.yml logs --tail=200 backend worker
```

If the backend is up but the worker is not, scans will queue but never run. Restart the worker manually:

```bash
docker-compose -f docker-compose.yml restart worker beat
```

### Image pull rejected (403 / 401)

Your registry credentials expired. Re-authenticate:

```bash
docker login <your-registry>
```

Then re-run `bash scripts/upgrade.sh`.

## See also

- [Backup & restore](../admin-guide/backup-and-restore.md)
- [System health dashboard](../admin-guide/disk-and-health.md)
- [Release notes](https://github.com/trustedoss/trusca/releases)
