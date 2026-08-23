# TrustedOSS Portal — Locust load tests

Manual load-test harness. **Not** part of CI: Locust is resource-intensive and even the smallest (capacity) stage below needs a beefy host or a staging environment that does not share runners with PR builds.

## What it covers

`locustfile.py` runs five user classes concurrently against the live dev / staging API:

| Class | Weight | Behaviour |
|---|---|---|
| `PortalReadUser` | 8 | Steady-state reads across every project surface (`GET /v1/projects`, `/v1/scans`, `/{id}/components`, `/{id}/vulnerabilities`, `/{id}/licenses`, `/{id}/source-tree`). |
| `ReportHeavyUser` | 2 | Expensive synchronous document generation (vuln PDF, 4 SBOM formats, NOTICE html/text). |
| `AuthChurnUser` | 1 | Login churn against the rate-limited auth path (5/min/IP): verifies the limiter degrades gracefully (429), never 5xx. |
| `ScanTriggerUser` | 1 | Low-frequency `POST /v1/projects/{id}/scans` triggers: connection-pool / Celery enqueue pressure, once a minute per simulated user. Does **not** measure queue wait by itself; see `scan_queue_wait.py` below. |
| `ApiKeyCIUser` | 1 | The CI traffic shape (trigger → poll status → fetch results → gate verdict), authenticated with a `tos_` API key instead of the shared JWT session the other classes use. |

`PortalReadUser`, `ReportHeavyUser`, and `ScanTriggerUser` share one bootstrap JWT (obtained once at test start; see the module docstring for why per-simulated-user login would just measure the login rate limiter instead of read/write load). `ApiKeyCIUser` shares one project-scoped API key issued at the same bootstrap.

Defaults from `tests/load/locust.conf`: **50 users, 5 users/s spawn rate, 10 min run-time, host=http://localhost:8000.** Override these per stage below.

## Capacity, buffer, and stress: not "10,000 users"

An earlier version of this file called 10,000 concurrent users the load test's "design target". It was not derived from anything. `~/projects/trusca-internal/docs/concurrency-scaling-plan-2026-08-22.md` §0.3 works the number from first principles (developer counts, session rates, burst margin) per deployment tier, and the largest realistic self-hosted tier comes out to **N=250** concurrent portal users, alongside a CI/API-key rate of ~17 requests/second. This file now runs three distinct stages:

| Stage | Load | Gate |
|---|---|---|
| Capacity | N=250 | Must clear the SLO below. **This is the number a real deployment should be sized against.** |
| Buffer | N=1,250 (5×) | Error rate must stay under the cap; latency may grow but not turn into errors. |
| Stress | N=10,000 | Not a gate. The point is to see *how* the stack fails, not whether it passes. |

```bash
# capacity (the real target)
locust -f tests/load/locustfile.py --headless -u 250 -r 25 -t 5m --host http://localhost:8000

# buffer (5x)
locust -f tests/load/locustfile.py --headless -u 1250 -r 100 -t 5m --host http://localhost:8000

# stress (breaking-point probe only; LOAD_SLO_ENFORCE=0 stops a breach from
# flipping the exit code, since a deliberately overloaded run is expected to
# breach the SLO)
LOAD_SLO_ENFORCE=0 locust -f tests/load/locustfile.py --headless -u 10000 -r 200 -t 5m --host http://localhost:8000
```

## Target SLO

* error rate ≤ `LOAD_MAX_FAIL_RATIO` (default 1%)
* p95 read latency ≤ `LOAD_MAX_P95_MS` (default 1500 ms)
* p99 read latency ≤ `LOAD_MAX_P99_MS` (default 4000 ms)

A `quitting` hook fails the process (exit 1) when the aggregate breaches the SLO at the capacity/buffer stages, so `locust --headless` can be scripted and a regression flips the exit code without a human reading the dashboard. Set `LOAD_SLO_ENFORCE=0` (or `false`/`no`/`off`) to downgrade a breach to a printed warning at exit 0, only for the stress stage.

## How to start

```bash
# 1. Bring up the dev stack so the API + worker + Postgres + Redis are running.
docker-compose -f docker-compose.dev.yml up -d

# 2. Seed the load-test user + projects (matches LOAD_TEST_EMAIL / LOAD_TEST_PASSWORD
#    in locustfile.py, defaults to the same e2e-admin fixture Playwright uses).
#    ScanTriggerUser/ApiKeyCIUser need at least a few source-kind projects with a
#    git_url; scan_queue_wait.py (below) needs slots x 5 distinct ones for its
#    largest sweep. Run from inside the backend container so SQLAlchemy points at
#    the compose-internal Postgres host.
docker-compose -f docker-compose.dev.yml exec backend \
  python scripts/seed_e2e_user.py --project-names load-1,load-2,load-3,load-4,load-5

# 3. Bring up Locust.
docker-compose -f docker-compose.load.yml up

# 4. Open the dashboard.
open http://localhost:8089
```

The dashboard accepts overrides for `users`, `spawn-rate`, and `host`; the values from `locust.conf` are pre-filled. Hit "Start swarming" to begin; "Stop" terminates the run early. For a scripted headless run, use `run_hard.sh` (below) or the `locust --headless` invocations under "Capacity, buffer, and stress" above.

### Load-test delay injection (queue-depth measurement)

The scan pipeline's `real` backend takes 5-60 real minutes per scan and the `mock` backend (`TRUSTEDOSS_SCAN_BACKEND=mock`, the dev/CI default for functional tests) finishes near-instantly; neither builds a queue you can see on a laptop-scale run. `core.config.scan_load_test_delay_seconds()` is a dev-only knob that, when the scan pipeline checks it, holds a worker slot busy for a fixed number of seconds instead of running cdxgen/Trivy, so trigger-to-`started_at` and `started_at`-to-`completed_at` gaps become measurable under N concurrent triggers.

It is **off by default and refused outside `APP_ENV=dev`** even if the enable flag is set (a load-test toggle left on in a real deployment would fake every scan result). To turn it on for a dev-stack load run, set on the backend/worker containers:

```
APP_ENV=dev
SCAN_LOAD_TEST_DELAY_ENABLED=true
SCAN_LOAD_TEST_DELAY_SECONDS=20
```

See `docs-site/docs/reference/env-variables.md` for the full contract.

## Measuring queue wait directly

`locustfile.py`'s `ScanTriggerUser` fires roughly once a minute per simulated user, which is realistic traffic but the wrong shape for reading queue depth off the aggregate stats: N users spread over a minute rarely land on the same instant. `scan_queue_wait.py` in this directory instead fires N triggers *simultaneously* across N distinct projects (the `(project, branch)` active-scan unique index means one project can only have one in-flight scan, so N concurrent triggers need N projects) and polls each to a terminal state, printing per-scan queue wait, execution duration, completions/hour, and a comparison against the plan's `floor((j-1)/S) * M` prediction (`S` = worker slots, `M` = the run's own measured mean duration).

```bash
# with delay injection on (see above) and enough seeded projects:
python3 tests/load/scan_queue_wait.py --slots 2 --multiplier 1   # N = slots
python3 tests/load/scan_queue_wait.py --slots 2 --multiplier 2   # N = 2x slots
python3 tests/load/scan_queue_wait.py --slots 2 --multiplier 5   # N = 5x slots
```

`--slots` is the worker slot count of the stack under test (`CELERY_CONCURRENCY` × worker replica count, 2 on dev compose by default). Record what you observe, including divergence from the prediction, in `concurrency-scaling-tracker.md` §3, not just whether it "passed": this script has no pass/fail gate.

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

- Even the capacity stage (250 users) needs ~1 vCPU per 10 simulated users plus a backend that is not also running other CI jobs. GitHub-hosted runners (2 vCPU) cannot meet that; the result would be runner saturation, not API saturation, and the p95 numbers would be meaningless.
- Hard SLO regression catches belong on a separate scheduled workflow against a dedicated staging environment.
- Both `locustfile.py` and `scan_queue_wait.py` are explicitly CI-out-of-scope, manual-only harnesses; see `concurrency-scaling-plan-2026-08-22.md` §3.1: "M1과 M3은 CI에서 돌리지 않는다."

## Operator runbook quick reference

| Symptom on dashboard | Likely cause | Action |
|---|---|---|
| All requests 401 after t=0 | seed user missing or wrong password | re-run `seed_e2e_user.py`, verify `LOAD_TEST_EMAIL` / `LOAD_TEST_PASSWORD` env |
| `POST /auth/login` failure rate >0% | rate limiter (5/min/IP) tripping | export `RATELIMIT_DISABLED=1` in the backend container, restart |
| `POST /projects/{id}/scans (trigger)` 422 | trigger schema drifted | update the body shape in `locustfile.py`'s `ScanTriggerUser.trigger_scan` / `ApiKeyCIUser.ci_run` |
| `POST /projects/{id}/scans (api-key trigger)` 401 | bootstrap API-key issuance failed | check the `LOAD bootstrap` print lines for `api-key issuance FAILED`; the login user needs a project to scope the key to |
| p95 climbs unbounded | DB connection pool exhausted | check `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` against the connection budget (`core/connection_budget.py`), bump if the deployment's own math says it is short |
| `scan_queue_wait.py` exits 2 ("need >= N distinct projects") | not enough seeded projects for the requested `--multiplier` | seed more with `seed_e2e_user.py --project-names ...` |
