# soak-metrics - scrape and persist /metrics for the S4 soak instance

The portal's own `/metrics` endpoint (`apps/backend/api/v1/metrics.py`) is a snapshot:
it answers whatever the current gauges are at the moment of the request, off by
default, and nothing scrapes it over time. S4 (self-resource-validation-plan-2026-08-30.md
§6-6) is meant to run for 90 days, and observing that run means having a trend
afterward, not just an endpoint that could in principle answer a probe during the
window. This directory polls it on an interval and logs every scrape (success or
failure) to a local SQLite file, then reports on what accumulated.

## Enabling the endpoint on the soak instance

Off by default; both flags are independent (see `.env.example` around
`METRICS_ENABLED`). For a soak run, turn both on so the collector gets the full
9-gauge set including the two broker-backlog series:

```bash
# .env on the soak instance
METRICS_ENABLED=true
QUEUE_BACKLOG_METRICS_ENABLED=true
# METRICS_TOKEN=... (optional - only needed if /metrics is reachable from
# somewhere you don't control; leave unset when the soak instance and the
# collector are on the same private network)
```

Then recreate the backend container so it picks up the new environment
(`docker-compose -f docker-compose.yml up -d backend` or the dev equivalent -
env vars are read at process start, an already-running container won't see an
`.env` edit until it restarts).

## Running the collector

No extra dependency (stdlib `urllib` + `sqlite3` only):

```bash
cd scripts/soak-metrics

# long-running, one scrape every 5 minutes (default), Ctrl-C / SIGTERM to stop
python3 collector.py --portal-url http://soak-host:8000 --token "$METRICS_TOKEN"

# smoke test - one scrape, then exit
python3 collector.py --portal-url http://soak-host:8000 --once
```

For the actual 90-day run, put it under something that survives a terminal
closing and restarts it if it dies - `nohup ... &` with the parent shell
disowned, a systemd unit, or a `cron @reboot` line calling a wrapper script.
The collector itself doesn't daemonize or write a pidfile; it's meant to be
supervised by whatever the soak host already uses for that, not to reinvent it.

Every attempt lands in `soak-metrics.db` (default: this directory, override with
`SOAK_METRICS_DB` or `--db`) whether it succeeded or not, so a report afterward
can show gaps (endpoint down, wrong token, network partition) rather than just
silence.

## Reading it back

```bash
# scrape counts, last sample of every series, first-vs-last delta on the
# bare-label (no per-status/severity breakdown) ones
python3 report.py summary

# every recorded value for one series, in order
python3 report.py history --metric trusca_scan_queue_wait_seconds

# scrapes that came back without a body (off, wrong token, network error)
python3 report.py failures

# one series' full history as CSV, for a spreadsheet or a chart
python3 report.py csv --metric trusca_workspace_disk_used_ratio --out disk.csv
```

## Design notes

Raw scrape text is stored as-is and parsed at read time (`store.parse_samples`),
not turned into per-metric columns at write time. `metrics_service.py`'s own
module docstring says series get added one at a time; parsing at read time means
this store's schema never needs to change in step with that module's contract,
it just reports on whatever series are actually present in a given scrape.

A SQLite file here rather than the portal's own Postgres for the same reason
`scan-bench/warehouse.py` isn't: this data needs to outlive the portal's own
retention policies, and living outside the product database means a soak-test
tool never competes with product migrations for that schema.
