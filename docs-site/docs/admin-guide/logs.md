---
id: logs
title: Logs
description: What TRUSCA writes to its logs, how to read one incident across containers, and how to ship them somewhere central.
sidebar_label: Logs
sidebar_position: 3.5
---

# Logs

Every container writes JSON to stdout, one object per line. Nothing is written
to a file inside the container, so collecting logs is a matter of pointing
Docker's logging at somewhere useful rather than mounting a volume and
tailing it.

## What a line looks like

```json
{"event": "scancode_stage_done", "level": "info", "timestamp": "2026-09-03T04:12:07.918431Z",
 "request_id": "0199...", "task_name": "trustedoss.scan_source",
 "task_id": "b2c8...", "scan_id": "7f31..."}
```

`event` is an identifier, not a sentence. It is meant to be filtered on
(`event="scancode_stage_done"`), which is why the interesting values sit in
their own fields rather than being interpolated into a message.

Four fields matter for finding things:

| Field | Where it comes from |
|---|---|
| `request_id` | the `X-Request-ID` header, or a UUID the middleware mints. Present on every line the request produces, **and** on every line the tasks it dispatched produce |
| `user_id`, `team_id` | the auth middleware; absent on unauthenticated calls |
| `task_name`, `task_id` | bound in the worker for every background task |

Tasks add their own: `scan_id` in the scan pipeline, `dry_run` in the sweeps,
and so on.

## Reading one incident across containers

A scan starts as an HTTP request the backend handles and finishes minutes
later inside a worker. `request_id` is what joins those halves: it is copied
onto the Celery message when the task is dispatched and bound again on the
other side.

```bash
# Everything one request caused, backend and workers together, oldest first.
REQ=0199abcd-...
docker-compose logs --no-color --timestamps backend worker-scan worker-default \
  | grep "\"request_id\": \"$REQ\"" \
  | sort -k1,1
```

Beat-scheduled work has no request behind it and carries no `request_id`.
That absence is meaningful and is not filled in with a substitute, so filter
those by `task_name` instead:

```bash
docker-compose logs --no-color beat worker-default \
  | grep '"task_name": "trustedoss.kev_catalog_refresh"'
```

For "what has this task been doing lately", the admin task-run history answers
better than logs: it holds one row per execution with outcome and duration,
and it survives log rotation. See [Disk and health](disk-and-health.md).

## Levels

`LOG_LEVEL` (default `INFO`) applies to the backend, the workers and beat.
`TRAEFIK_LOG_LEVEL` is separate and covers the proxy only.

`INFO` gives one line per request and per task transition, which is what the
join above relies on. `DEBUG` adds whatever the libraries below the
application emit at that level, and is loud enough to fill a bounded log ring
quickly, so raise it for a diagnosis and lower it again afterwards.

Both are read at container start, so a change needs a restart of the affected
services.

## What is not in the logs

Passwords, tokens, API keys and email addresses do not appear in plaintext.
Values pass through a masking helper that replaces sensitive subtrees with
`***`, and credentials embedded in connection strings are stripped before an
error is logged.

This is a floor, not a guarantee about your own additions: a field a future
integration logs under an unrecognised name is not masked by magic. If you
ship logs off-host, treat them as containing operational data about who did
what and when, and give them the retention and access controls that implies.

## Shipping them somewhere

Docker's default `json-file` driver keeps logs on the host with **no size
limit configured in this repository's compose files**. On a busy deployment
that grows until the disk does not have room for it, and a full disk stops
scans. Two things to do, in this order.

First, bound what stays on the host. Add a `logging:` block to the services in
your own compose override rather than editing the shipped file, so an upgrade
does not revert it:

```yaml
services:
  backend:
    logging:
      driver: json-file
      options:
        max-size: "50m"
        max-file: "5"
```

Repeat for `worker-scan`, `worker-default`, `beat` and `traefik`. The workers
are the loud ones.

Second, if you have somewhere central to send them, point the driver there
instead. Any Docker logging driver works because the application only writes
to stdout: `syslog`, `gelf`, `awslogs`, `fluentd`, or a collector like Vector
or Promtail reading the container socket. Because the payload is already JSON,
configure the collector to parse it as such rather than treating the line as a
message string, otherwise the fields above stay invisible to your queries.

:::caution A remote driver can block the container
`fluentd`, `gelf` and `syslog` drivers can make a container hang when the
destination is unreachable, depending on the driver's mode. Set
`mode: non-blocking` (with a `max-buffer-size`) if the log destination is not
in the same failure domain as the deployment, so a collector outage does not
take the scanners down with it.
:::

## Related

- [Disk and health](disk-and-health.md) covers the metrics endpoint and the
  task-run history, which answer "is it working" better than logs do.
- The field list and the binder pattern are documented for developers in the
  contributor guide's coding standards.
