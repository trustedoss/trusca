# scan-bench - cdxgen/Trivy detection-power verification tools

Bulk-registers and scans the 32 baseline-scan fixtures (A: regression matrix) plus 3
real-world targets (B: benchmark) against the portal, and collects the results as
CSV/markdown. Reports land in `docs/scans/`.

## Prerequisites
- portal dev stack running (`docker-compose -f docker-compose.dev.yml up`)
- `frontend-admin@demo.trustedoss.dev` / `DemoTest2026!` account active

## Usage
```bash
cd scripts/scan-bench

# A - 32 fixtures
python3 run_bench.py --suite fixtures

# B - real-world (Juice Shop + WebGoat + our own v1 self-scan)
python3 run_bench.py --suite realworld

# C - container (real public images, pulled by the worker: alpine:3.19)
#     detection-power baseline for multi-CVE density (H-1-class) that synthetic
#     fixtures can't reproduce.
python3 run_bench.py --suite container

# a single target only
python3 run_bench.py --suite fixtures --only node
```

Output: `out/<suite>-<timestamp>.{csv,md,jsonl}` (a new file every run, nothing to compare against)

## Result warehouse (comparing runs over time)

The CSV in `out/` is a new file every run, so there's no way to compare against a
previous run or see a trend. Every run now also lands in a SQLite warehouse
(`warehouse.db`, default location is this directory) and prints a summary of what
changed since the previous run. Why not the portal's own Postgres is written up in
the plan document
(`~/projects/trusca-internal/docs/self-resource-validation-plan-2026-08-30.md` §6-1):
the portal's findings retention policy deletes them after 7 days, so it can't be used
as-is.

```bash
# run history
python3 warehouse_report.py history --suite fixtures

# latest run vs. the previous one (status changes, numeric deltas, added/dropped targets)
python3 warehouse_report.py compare --suite fixtures

# compare two specific runs
python3 warehouse_report.py compare --suite fixtures --run-a 3 --run-b 7
```

The warehouse location can be changed with the `SCAN_BENCH_WAREHOUSE_DB` env var or
`--warehouse-db`, for when it should live on persistent disk outside this repo
checkout, e.g. a cohort runner. Passing an empty string skips writing to the
warehouse.

## Bulk registration (S3 cohort prep, §6-2)

`run_bench.py` is built to serially run a small, fixed set of fixture/real-world
targets. S3 (about 120 teams x 15 repos each, roughly 1,800 scans) needs a different
tool for bulk registration: `bulk_register.py`. There's no zip/upload at all - every
project is registered with its real `git_url`, and sending just `{"kind": "source"}`
makes the worker clone it directly via its default (`source_type=git`), so no local
disk is needed regardless of scale. This relies on a super-admin account being able
to create a project under any team without needing team membership there.

The target list is a JSON file:
```json
{
  "teams": [
    {
      "name": "example-org",
      "slug": "example-org",
      "repos": [
        {"name": "example-repo", "slug": "example-repo", "git_url": "https://github.com/example/example-repo.git"}
      ]
    }
  ]
}
```

```bash
# create teams/projects and trigger scans (a re-run after an interruption skips
# anything already created)
python3 bulk_register.py register --cohort github-2026-09 --input targets.json \
    --admin-email admin@example.com --admin-password '...'

# retry only the ones that failed
python3 bulk_register.py register --cohort github-2026-09 --input targets.json --retry-failed \
    --admin-email admin@example.com --admin-password '...'

# internal/private targets (e.g. TDE GitLab) the worker can't clone unauthenticated:
# --git-credential (or COHORT_GIT_CREDENTIAL env var) PATCHes a read-only token onto
# every project via the encrypted git_credential field, right after it's created.
# Omit for public targets.
python3 bulk_register.py register --cohort tde-2026-09 --input targets.json \
    --admin-email admin@example.com --admin-password '...' --git-credential '...'

# refresh in-flight scan status (once, or with --watch until everything is done)
python3 bulk_register.py poll --cohort github-2026-09 --admin-email ... --admin-password ...

# summary (no portal access needed, reads registration state only)
python3 bulk_register.py status --cohort github-2026-09
```

Registration and scan state accumulate in this directory's `cohort.db` (SQLite,
changeable via `COHORT_DB`/`--cohort-db`). Both `register` and `poll` keep going when
a single target fails, recording why, so one exception at a 1,800-target scale
doesn't stop the rest.

## How it works (run_bench.py, small fixture/real-world targets)
1. Log in, holding the access token + refresh cookie (auto-renewed on 30-minute expiry)
2. Zip the input directory (excluding `node_modules/`, `.git/`, `target/`, `build/`, `.gradle/`, `venv/`)
3. `POST /v1/projects` to create the project (reused if the slug already exists)
4. `POST /v1/projects/{id}/source-archive` to get an archive_id
5. `POST /v1/projects/{id}/scans` to get a scan_id (kind=source, source_type=upload)
6. Poll `GET /v1/scans/{scan_id}` every 5 seconds until succeeded/failed/cancelled
7. Aggregate `GET /v1/projects/{id}/{overview,components,vulnerabilities,licenses}`

## Concurrency
Serial by default since there's one worker. concurrency cap=10/team, rate limit 20/min/user.
