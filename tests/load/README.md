# TrustedOSS Portal — Locust load tests

Manual load-test harness. **Not** part of CI — Locust is resource-intensive and the SLO scenario (50 users / 10 minutes) needs a beefy host or a staging environment that does not share runners with PR builds. See `docs/chore-backlog.md` Chore I for context.

## What it covers

Two user classes run concurrently against the live dev / staging API:

| Class | Weight | Behaviour |
|---|---|---|
| `AuthenticatedUser` | dominant | Logs in once, then loops over the four read-heavy endpoints (`GET /v1/projects`, `GET /v1/scans`, `GET /v1/projects/{id}`, `GET /v1/components`). Endpoint weights mirror the portal UI access pattern (5 / 3 / 2 / 2). |
| `ScanTriggerUser` | low | Logs in, triggers a scan via `POST /v1/scans/trigger` roughly once per minute. The point is connection-pool / Celery enqueue pressure, not actually running cdxgen / ORT / Trivy. |

Defaults from `tests/load/locust.conf`: **50 users · 5 users/s spawn · 10 min run-time · host=http://localhost:8000**.

## Target SLO

From CLAUDE.md §3 (quality / security / ops standards):

> **p95 < 1 s** for the four read-heavy endpoints under the 50-user / 3-concurrent-scan scenario.

The dashboard's "Statistics" tab shows the per-endpoint p95 in real time; the operator records the steady-state value at the end of the run.

## How to start

```bash
# 1. Bring up the dev stack so the API + worker + Postgres + Redis are running.
docker-compose -f docker-compose.dev.yml up -d

# 2. Seed the load-test user (matches LOAD_TEST_EMAIL / LOAD_TEST_PASSWORD in
#    locustfile.py — defaults to the same e2e@trustedoss.local fixture used
#    by Playwright). Run from inside the backend container so SQLAlchemy
#    points at the compose-internal Postgres host.
docker-compose -f docker-compose.dev.yml exec backend \
  python scripts/seed_e2e_user.py --project-names load-test-fixture

# 3. Switch the worker to mock mode so scan triggers do not actually run
#    the 5–60 minute cdxgen / ORT / Trivy chain. Restart the worker after
#    flipping the flag in your local .env (TRUSTEDOSS_SCAN_BACKEND=mock).

# 4. Bring up Locust.
docker-compose -f docker-compose.load.yml up

# 5. Open the dashboard.
open http://localhost:8089
```

The dashboard accepts overrides for `users`, `spawn-rate`, and `host` — the values from `locust.conf` are pre-filled. Hit "Start swarming" to begin; "Stop" terminates the run early.

## Reports

Generate an HTML report from a headless run:

```bash
docker-compose -f docker-compose.load.yml run --rm locust-master \
  -f /mnt/locust/locustfile.py \
  --config /mnt/locust/locust.conf \
  --headless \
  --html /mnt/locust/last-run.html \
  --csv /mnt/locust/last-run
```

The output lands in `tests/load/last-run.html` + `tests/load/last-run_*.csv` (gitignored).

## Why this is not in CI

- A 50-user / 10-minute run needs ~1 vCPU per 10 simulated users plus a backend that is not also running other CI jobs. GitHub-hosted runners (2 vCPU) cannot meet that; the result would be runner saturation, not API saturation, and the p95 numbers would be meaningless.
- Hard SLO regression catches belong on a separate scheduled workflow against a dedicated staging environment — that is **Chore K / GA hardening backlog**, not this PR.
- Chore I (`docs/chore-backlog.md`) explicitly marks "CI 게이트는 NO (수동 실행만 — staging 환경)".

## Operator runbook quick reference

| Symptom on dashboard | Likely cause | Action |
|---|---|---|
| All requests 401 after t=0 | seed user missing or wrong password | re-run `seed_e2e_user.py`, verify `LOAD_TEST_EMAIL` / `LOAD_TEST_PASSWORD` env |
| `POST /auth/jwt/login` failure rate >0% | rate limiter (5/min/IP) tripping | export `RATELIMIT_DISABLED=1` in the backend container, restart |
| `POST /v1/scans/trigger` 422 | trigger schema drifted | update the body shape in `locustfile.py` `trigger_scan` |
| p95 climbs unbounded | DB connection pool exhausted | bump `SQLALCHEMY_POOL_SIZE` in the backend `.env`, restart |
