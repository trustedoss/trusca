# Operator Runbook — Demo SaaS on Hetzner (CAX31)

> Korean version: [`operator-runbook-hetzner.ko.md`](./operator-runbook-hetzner.ko.md)

This runbook takes you from **zero to a live, public, read-only demo** of
TrustedOSS Portal on a single Hetzner ARM server, using the project's existing
`docker-compose.yml` (Traefik + automatic HTTPS). It is written for someone who
has **never deployed a service before** — every command is copy-pasteable and
every decision is explained.

You run the commands; nothing here is automated for you. Take it one section at
a time.

---

## 0. What you end up with

```
Visitor ──HTTPS──> Cloudflare DNS ──> Hetzner CAX31 (Ubuntu 24.04)
                                         └─ Traefik (TLS, Let's Encrypt)
                                            ├─ frontend  (React SPA)
                                            └─ backend   (FastAPI) + worker + beat
                                               └─ Postgres 17 + Redis 7
```

- **Public, read-only demo**: anyone can log in with the demo accounts and
  browse real scan data, but every create/update/delete is blocked (HTTP 403).
- **Self-healing data**: a daily timer wipes and reseeds the demo dataset at
  03:17 UTC, so visitor noise never accumulates.
- **Daily local backup**: a timer runs `pg_dump` + workspace archive at 02:30 UTC.
- **Cost**: ~**$18/month** (CAX31 $15 + Hetzner backups $3). Cloudflare DNS is free.

### Decisions baked into this runbook

| Topic | Choice | Why |
|-------|--------|-----|
| Reverse proxy / TLS | **Traefik** (reuse existing compose) | Already configured + verified; zero extra work |
| Backup | **Local only** (for now) | Simplest start; offsite is a later add-on (§11) |
| Deploy/upgrade | **Manual** (`upgrade.sh` over SSH) | You see exactly what happens — best while learning |
| Compose binary | **V2 binary named `docker-compose`** | arm64-native; keeps the hyphenated command our scripts call |

---

## 1. Before you start — what you need

1. **A domain you control**, e.g. `demo.trustedoss.dev`. You will point a DNS
   `A` record at the server. (Any registrar works; this runbook uses Cloudflare
   for DNS because it is free and simple.)
2. **A Hetzner Cloud account** — <https://console.hetzner.com>. Add a payment
   method.
3. **An SSH key pair on your own laptop.** If you do not have one:
   ```bash
   ssh-keygen -t ed25519 -C "trustedoss-demo"
   # press Enter for defaults; set a passphrase if you like
   cat ~/.ssh/id_ed25519.pub      # <-- this is your PUBLIC key, you'll paste it
   ```
   The line printed by `cat` (starting `ssh-ed25519 ...`) is what goes into the
   cloud-init file. **Never share the private key** (`id_ed25519`, no `.pub`).
4. **A strong super-admin password** you'll type during install (12+ chars).

---

## 2. Create the server (cloud-init does the OS prep)

The repo ships [`scripts/hetzner-cloud-init.yaml`](../scripts/hetzner-cloud-init.yaml).
It installs Docker, a `docker-compose` binary, a firewall, creates a `trustedoss`
login user with your SSH key, and clones the repo to `/opt/trustedoss/portal`.
It does **not** touch secrets — you do the app install by hand in §5.

1. Open `scripts/hetzner-cloud-init.yaml` and replace **`__SSH_PUBLIC_KEY__`**
   with the full line from `cat ~/.ssh/id_ed25519.pub`.
   - (Optional) bump the Compose version in the file to the latest from
     <https://github.com/docker/compose/releases>.
2. In the Hetzner Console → **Add Server**:
   - **Location**: Helsinki (`hel1`) or Falkenstein (`fsn1`).
   - **Image**: Ubuntu 24.04.
   - **Type**: **CAX31** (Arm64, 8 vCPU / 16 GB).
   - **Backups**: enable (the $3/mo option — Hetzner-side VM snapshots, separate
     from our pg_dump backups; both are cheap insurance).
   - **Cloud config**: paste the *entire edited* `hetzner-cloud-init.yaml`.
   - Create the server and note its **public IPv4**.
3. Wait ~2–3 minutes for first boot. cloud-init runs once; you can watch it via
   the Hetzner web console (the VM's serial console) if you're curious.

> The clone tracks `main` so it includes these deploy files. The **app
> container versions** are pinned independently by `IMAGE_TAG` in `.env`
> (default `0.10.0`) — the git checkout only supplies the compose file, scripts,
> and systemd units.

---

## 3. DNS — point your domain at the server

In your DNS provider (e.g. Cloudflare dashboard → your zone → DNS):

- Add an **`A` record**: name `demo` (→ `demo.trustedoss.dev`), value = the
  server's **public IPv4**.
- **Set it to "DNS only" (grey cloud), NOT proxied (orange cloud).**
  This matters: Traefik obtains the TLS certificate from Let's Encrypt using the
  **HTTP-01 challenge on port 80**, which needs the real server reachable
  directly. A proxied (orange) record fronts your site with Cloudflare's own
  cert and can break first-time issuance. You can switch to proxied later once
  certs are stable, but start grey.

Verify propagation (wait until it returns the server IP):
```bash
dig +short demo.trustedoss.dev
```

---

## 4. First SSH login

From your laptop:
```bash
ssh trustedoss@demo.trustedoss.dev
# or: ssh trustedoss@<server-ipv4>
```
You should land in a shell with a message of the day telling you the next step.
If you get "Permission denied (publickey)", your `__SSH_PUBLIC_KEY__` was wrong
or missing — re-check §1.3 and §2.1.

Confirm the basics:
```bash
docker --version
docker-compose version          # should print Compose v2.x
ls /opt/trustedoss/portal       # the cloned repo
```

---

## 5. Install the app (interactive — you set the secrets here)

```bash
cd /opt/trustedoss/portal
bash scripts/install.sh
```

Answer the prompts:

| Prompt | Enter |
|--------|-------|
| `Public URL` | `https://demo.trustedoss.dev` (your real HTTPS domain) |
| `Let's Encrypt contact email` | a real email (cert expiry notices go here) |
| `Super admin email` | your admin login, e.g. `admin@trustedoss.dev` |
| `Password (12+ chars)` | a strong password you'll remember |

The script generates all other secrets (JWT key, DB passwords), pulls the
`:0.10.0` images (arm64), starts the stack, runs database migrations, and
creates your super-admin. Traefik requests the TLS certificate automatically —
the first request can take 10–30 s while the cert is issued.

> **Trivy vulnerability DB**: the worker downloads ~600 MB on first boot.
> Vulnerability findings populate within 1–3 minutes after the stack is up.

---

## 6. Turn on demo mode

`install.sh` set up a normal (writable) deployment. For a **public read-only
demo** you flip two switches in `.env`, then recreate the containers so they
pick up the change.

```bash
cd /opt/trustedoss/portal
nano .env
```
Set / add these lines:
```ini
APP_ENV=demo
DEMO_READ_ONLY=true
```
- `APP_ENV=demo` unlocks the seed/reset scripts (they refuse to run outside
  `dev`/`demo`) and relaxes a couple of prod-only hard requirements.
- `DEMO_READ_ONLY=true` makes the backend reject every write over HTTP (except
  the auth login/refresh/logout flow), returning a friendly 403. Reads are
  unaffected. This is the public-safety boundary.

Confirm `DOMAIN`, `TLS_EMAIL`, and `CORS_ALLOWED_ORIGINS` were set correctly by
the installer (they should match your HTTPS domain). Then recreate:
```bash
docker-compose -f docker-compose.yml up -d
```

---

## 7. Seed the demo dataset

The `DEMO_SUPER_ADMIN_PASSWORD` in `.env` defaults to `DemoTest2026!` — this is
the password for **all seeded demo accounts**. Change it in `.env` first if you
want a different one (then it applies to the seeded users).

```bash
docker-compose -f docker-compose.yml exec -T backend python -m scripts.seed_demo
```
This creates `demo-org`, 3 teams, 5 users (`*@demo.trustedoss.dev`), 5 projects,
and realistic CVE / license / notification data. It is **idempotent** — running
it again when `demo-org` already exists is a safe no-op.

Demo logins (share these on the demo's landing page):
- `frontend-admin@demo.trustedoss.dev` / `DemoTest2026!` (richest CVE + license data)
- other seeded users follow the same `@demo.trustedoss.dev` / `DemoTest2026!` pattern

---

## 8. Verify it's live

- Open `https://demo.trustedoss.dev` in a browser → the login page loads over
  **valid HTTPS** (padlock, no warning).
- Log in with a demo account → you can browse projects, components, CVEs.
- Try to create/edit anything → you get a **"Read-only live demo"** 403 message.
  That confirms `DEMO_READ_ONLY` is working.
- Health check from the server:
  ```bash
  curl -fsS https://demo.trustedoss.dev/health && echo OK
  ```

---

## 9. Enable the daily timers (reset + backup)

Two systemd units ship in [`deploy/hetzner/`](../deploy/hetzner/):

```bash
cd /opt/trustedoss/portal
sudo cp deploy/hetzner/trustedoss-demo-reset.service \
        deploy/hetzner/trustedoss-demo-reset.timer \
        deploy/hetzner/trustedoss-backup.service \
        deploy/hetzner/trustedoss-backup.timer \
        /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now trustedoss-demo-reset.timer trustedoss-backup.timer
```

Check they're scheduled:
```bash
systemctl list-timers 'trustedoss-*'
```
You should see the next run times (02:30 backup, 03:17 reset, both UTC).

Test the reset once by hand (optional — it wipes+reseeds the demo data now):
```bash
sudo systemctl start trustedoss-demo-reset.service
journalctl -u trustedoss-demo-reset.service -n 30 --no-pager
```

> The unit files assume the repo is at `/opt/trustedoss/portal`, the user is
> `trustedoss`, and the compose binary is `/usr/local/bin/docker-compose` — all
> true if you used the cloud-init. If you changed any of those, edit the unit
> files before copying.

---

## 10. Day-2 operations

**View logs**
```bash
cd /opt/trustedoss/portal
docker-compose -f docker-compose.yml logs -f backend     # or: traefik, worker, frontend
```

**Restart a service**
```bash
docker-compose -f docker-compose.yml restart backend
```

**Manual backup now**
```bash
bash scripts/backup.sh          # writes backups/<UTC-stamp>/
ls -lh backups/
```

**Restore from a backup** (destructive — overwrites current data)
```bash
bash scripts/restore.sh backups/<the-stamp-dir>
```

**Upgrade to a new release** (e.g. when `v0.11.0` ships)
```bash
cd /opt/trustedoss/portal
git fetch --tags
git checkout v0.11.0            # the new tag
# bump IMAGE_TAG in .env to match if it isn't templated:
nano .env                       # IMAGE_TAG=0.11.0
bash scripts/upgrade.sh         # backs up, pulls, restarts, migrates
```

**Check disk** (Trivy DB + scan workspaces grow over time)
```bash
df -h /
du -sh /opt/trustedoss/workspace backups/
```

---

## 11. Going offsite (later)

Backups are **local only** today (operator's choice). When you want an offsite
copy so a dead server doesn't lose the data:

1. Create a free **Cloudflare R2** (10 GB) or **Backblaze B2** bucket + API key.
2. Install + configure `rclone` on the server (`rclone config`, S3-compatible
   remote pointing at the bucket).
3. Add an `ExecStartPost=` to `trustedoss-backup.service` that pushes the newest
   `backups/<stamp>/` to the remote, e.g.:
   ```ini
   ExecStartPost=/usr/bin/rclone copy /opt/trustedoss/portal/backups r2:trustedoss-backups --max-age 25h
   ```
4. `sudo systemctl daemon-reload`.

No app changes are needed — this is purely a backup-pipeline add-on.

---

## 12. Teardown

To shut the demo down completely:
```bash
cd /opt/trustedoss/portal
docker-compose -f docker-compose.yml down          # add -v to also delete data volumes
```
Then delete the server in the Hetzner Console and remove the DNS `A` record.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Browser shows a TLS warning / "not secure" | Cert not issued yet, or DNS still proxied | Confirm DNS is **grey cloud**; wait 30 s; `docker-compose logs traefik` for ACME errors |
| `Permission denied (publickey)` on SSH | Wrong/missing `__SSH_PUBLIC_KEY__` | Re-check §1.3; you can add a key via the Hetzner web console + `~/.ssh/authorized_keys` |
| Backend never becomes healthy in install | Image pull failed or migration error | `docker-compose logs backend`; ensure the `:0.10.0` arm64 images pulled |
| Writes succeed when they shouldn't | `DEMO_READ_ONLY` not picked up | It's runtime env — did you `docker-compose up -d` after editing `.env`? (§6) |
| `seed_demo` exits 1 "APP_ENV not allowed" | `APP_ENV` still `dev`/unset in the container | Set `APP_ENV=demo` in `.env`, `up -d`, retry (§6) |
| Reset timer didn't run | Timer not enabled, or container down | `systemctl list-timers`; `journalctl -u trustedoss-demo-reset` |

---

## Reference — files in this deployment

| File | Role |
|------|------|
| `scripts/hetzner-cloud-init.yaml` | First-boot OS provisioning (Docker, user, firewall, repo clone) |
| `docker-compose.yml` | The 7-service stack (Traefik + Postgres + Redis + backend/worker/beat + frontend) |
| `scripts/install.sh` | Interactive first install (secrets, up, migrate, super-admin) |
| `scripts/upgrade.sh` / `backup.sh` / `restore.sh` | Day-2 lifecycle |
| `deploy/hetzner/trustedoss-demo-reset.{service,timer}` | Daily 03:17 UTC demo wipe + reseed |
| `deploy/hetzner/trustedoss-backup.{service,timer}` | Daily 02:30 UTC local backup |
| `.env` | All runtime config + secrets (never commit this) |
