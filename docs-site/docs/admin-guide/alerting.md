---
id: alerting
title: Alerting
description: Example Prometheus alerting rules covering the /metrics series this deployment already publishes, and which on-call runbook scenarios each one maps to.
sidebar_label: Alerting
sidebar_position: 3.6
---

# Alerting

This repository ships an operational [metrics endpoint](./disk-and-health.md#metrics)
(`/metrics`, off by default) but no Prometheus server, Alertmanager, or paging
integration - those are yours to run and configure. This page ships one thing
to close that gap: an example rule file covering the series `/metrics`
already publishes, so the [on-call runbook](./oncall-runbook.md)'s named
alerts have something real behind them instead of assuming a page arrived
with context.

Download: [`trusca-alerts.rules.yml`](/prometheus/trusca-alerts.rules.yml)

## Load it

Drop the file where your Prometheus server's `rule_files` glob picks it up
and reload:

```yaml
# prometheus.yml
rule_files:
  - /etc/prometheus/rules/trusca-alerts.rules.yml
scrape_configs:
  - job_name: trusca
    metrics_path: /metrics
    # bearer_token: "<METRICS_TOKEN, if you set one>"
    static_configs:
      - targets: ["<your-host>:443"]
```

Wiring the firing alerts to a page (Alertmanager routes, PagerDuty/Slack
receivers, and so on) is a separate step this file does not attempt - every
deployment's paging setup is different, and the runbook only needs an alert
*name* to reference, not a specific receiver.

## What each rule covers

| Rule | Series | Runbook scenario |
|---|---|---|
| `TrustedOSSVulnDbStale` | `trusca_vuln_db_last_update_timestamp_seconds`, `trusca_vuln_db_refresh_interval_hours` | [Scenario 1](./oncall-runbook.md) |
| `TrustedOSSTaskRunRecorderStalled` | `trusca_task_runs_last_recorded_timestamp_seconds` | (no numbered scenario - see disk-and-health.md's own callout on this series) |
| `TrustedOSSAutoBackupNotSucceeding` | `trusca_task_runs_24h{task="trustedoss.backup.run"}` | [Scenario 2](./oncall-runbook.md) |
| `TrustedOSSWorkspaceDiskCritical` | `trusca_workspace_disk_used_ratio` | [Scenario 4](./oncall-runbook.md) |
| `TrustedOSSScanQueueBacklogHigh` / `TrustedOSSDefaultQueueBacklogHigh` | `trusca_broker_queue_backlog` (opt-in, `QUEUE_BACKLOG_METRICS_ENABLED`) | [Scenario 5](./oncall-runbook.md) |

Each threshold either matches a documented app default exactly (the disk and
queue-backlog rules) or is a starting point the rule file's own comment says
how to retune (vulnerability-database staleness, task-recorder stall). None
of them is tuned against a real production fleet - read the comment next to
each `expr:` before trusting the number.

## What is NOT covered here, and why {#what-is-not-covered-here-and-why}

Three runbook scenarios have no rule above because `/metrics` does not
publish anything they could alert on:

- **Scenario 3** (scan stuck running for hours): no series tracks how long
  the oldest running scan has been running. `trusca_scans_total{status=...}`
  is a count, not an age.
- **Scenario 6** (a worker crash-loops on boot): this is a container restart
  signal your orchestrator already has (Kubernetes' own restart count,
  `cAdvisor`, `kube-state-metrics`), not something this application's own
  `/metrics` can see from inside a process that never finishes starting.
- **Scenario 7** (Redis `degraded` on `/health/ready`): tracked internally
  (`core.redis_degradation`) and readable on `/health/ready`'s
  `redis_fail_open` field, but not (yet) mirrored onto `/metrics` as its own
  series.

Filing a gap here beats a rule that looks like it covers a scenario but
quietly does not.

## See also

- [Disk and health - Scraping metrics](./disk-and-health.md#metrics): the full series list, and why two of them (vulnerability-database staleness, the task-run recorder) are called out as worth an alert.
- [On-call runbook](./oncall-runbook.md): what to do once one of these fires.
- [Environment variables](../reference/env-variables.md): `METRICS_ENABLED`, `METRICS_TOKEN`, `QUEUE_BACKLOG_METRICS_ENABLED`, and the queue-backlog threshold/sustain/cooldown knobs the two backlog rules mirror.
