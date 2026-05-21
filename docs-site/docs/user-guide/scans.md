---
id: scans
title: Scans
description: Trigger source and container scans, watch progress in real time, and read terminal status — the full scan lifecycle in TrustedOSS Portal.
sidebar_label: Scans
sidebar_position: 2
---

# Scans

A **scan** is one end-to-end run that detects components, licenses, and vulnerabilities for a project. Scans run on a Celery worker (never inline on the API) — typical durations range from 5 minutes (small npm projects) to 60 minutes (large multi-module Java repositories).

:::note Audience
Engineers with `developer` or higher on the project's team. Triggering scans against private repos requires repo credentials embedded in the project's `git_url` — see [Projects → Private repositories](./projects.md#private-repositories).
:::

## Scan kinds

| Kind | Pipeline | What it detects |
|---|---|---|
| **`source`** | `cdxgen` (CycloneDX generator) → scancode (first-party license detection) → Dependency-Track (DT) | Components and their **declared** licenses (from dependency metadata) plus **detected** licenses (scancode reading your own first-party source), and CVEs (Common Vulnerabilities and Exposures) from NVD / OSV / GitHub Advisory. |
| **`container`** | Trivy (Aqua Security container scanner) | OS-package vulnerabilities and (limited) language-package CVEs in a container image. |

`source` is the only kind exposed in the v2.0.0 UI trigger — the API also accepts `container` for clients that wire it up directly. See [Roadmap](#roadmap-v2x) for UI parity.

## Trigger a scan

### From the UI

1. Open **Projects** in the sidebar.
2. Find the project row and click the **Scan** button at the end of the row.
3. The scan starts immediately as a `source` scan against the project's default branch.

There is no kind-selection dialog or branch-override field in the v2.0.0 UI — those controls are deferred to v2.1 (see [Roadmap](#roadmap-v2x)). A right-slide drawer opens on the project list page with a live progress view backed by a WebSocket connection. You can close the tab — the scan continues on the worker. Reopen the project and reconnect at any time. While a scan is `queued` or `running`, the drawer carries a **Cancel scan** action — see [Cancel a scan](#cancel-a-scan).

![Scan progress drawer — bootstrap → fetch → cdxgen → scancode → DT → finalize stages, live over WebSocket](/img/screenshots/user-scans-progress-drawer.png)

:::warning Branch selection at v2.0.0
Scans run against the project's `default_branch` (typically `main`).
Neither the UI nor the API exposes a branch-override at v2.0.0 —
the `ScanCreate` payload accepts only `kind` and `metadata` (see
`apps/backend/schemas/scan.py`). To scan `develop` or a feature
branch, temporarily change `default_branch` in **Project Settings**
before triggering the scan, then revert. A first-class `branch`
field on the trigger is on the v2.1 roadmap.
:::

### From the API

```bash
curl -sS -X POST \
  "https://trustedoss.example.com/v1/projects/${PROJECT_ID}/scans" \
  -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"kind": "source"}' | jq .
```

The response carries the scan UUID. Poll:

```bash
curl -sS "https://trustedoss.example.com/v1/scans/${SCAN_ID}" \
  -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" | jq .status
```

### From CI

The recommended path is the [GitHub Action](../ci-integration/github-actions.md), the [GitLab CI template](../ci-integration/gitlab-ci.md), or the [Jenkinsfile example](../ci-integration/jenkins.md). Each one wraps the API and adds the build gate.

## Lifecycle

```
queued ─────► running ─────► succeeded
   │                  │
   │                  └────► failed
   │                  │
   └──────────────────┴────► cancelled
```

| Status | Meaning |
|---|---|
| `queued` | Enqueued; waiting for a free worker slot. |
| `running` | A worker has picked up the task and is executing the pipeline. |
| `succeeded` | Pipeline finished, components and findings are now queryable. |
| `failed` | The worker raised an error. Inspect `error_detail` in the API response or the worker log. |
| `cancelled` | A user or admin cancelled the run while it was `queued` or `running`. The worker task was stopped and its workspace reclaimed. See [Cancel a scan](#cancel-a-scan). |

`queued`, `running` are non-terminal; `succeeded`, `failed`, and `cancelled` are terminal. A scan can be cancelled only from a non-terminal state.

### Pipeline stages (source)

The progress view shows real-time stage transitions:

1. **Bootstrapping** — preparing the workspace.
2. **Fetching source** — `git clone` (or `git fetch` + checkout for an existing workspace).
3. **Detecting components** — `cdxgen` walks the repo and emits a CycloneDX SBOM, with **declared** licenses read from each dependency's package metadata.
4. **Detecting first-party licenses** — scancode scans the project's own source files and records the **detected** licenses it finds, each tagged with the `source_path` of the file it came from (see [Components & licenses → Detected vs. declared](./components-and-licenses.md#declared-vs-detected)). This stage is best-effort: if scancode is not installed, times out, or the tree is too large, the scan continues with declared licenses only — a degraded but non-fatal outcome. Legal-tier classification at v2.0.0 is then applied from the hard-coded `_LICENSE_CATEGORY_DEFAULTS` dictionary in `apps/backend/tasks/scan_source.py` (see [Components & licenses → Classification source](./components-and-licenses.md#license-classification)).
5. **Resolving vulnerabilities** — Dependency-Track correlates the SBOM against its feed mirror.
6. **Persisting** — components, licenses, and findings are written to PostgreSQL.

:::note ORT was replaced by scancode
Earlier builds ran the OSS Review Toolkit (ORT) at the license stage. v2.0.0 replaces it with scancode for **first-party** detection. Third-party dependency sources are deliberately not downloaded — that kept per-scan runtime within budget — so dependency licenses stay **declared** (from cdxgen) and scancode adds **detected** licenses for the code your team actually wrote.
:::

If Dependency-Track is unavailable when stage 5 runs, the [DT circuit breaker](../admin-guide/dt-connector.md) trips OPEN and the scan reads from the PostgreSQL vulnerability cache. The scan is marked `succeeded` with a warning surfaced in the UI.

## Average duration

| Project size | Source scan | Container scan |
|---|---|---|
| Small (≤ 50 components) | 3–8 min | 1–3 min |
| Medium (50–500) | 8–20 min | 2–5 min |
| Large (≥ 500, multi-module) | 20–60 min | 5–10 min |

The dominant cost in a source scan is Dependency-Track correlation, with scancode adding time proportional to the size of the first-party tree. Container scans are bound by image-pull time when the image is not in the worker's cache.

## The global scan queue

Visit **Scans** in the left sidebar for an organization-wide view of every running and queued scan. The queue is split into 5 status tabs: Running, Queued, Succeeded, Failed, All. Project- / team-level filters and per-worker views are on the roadmap.

![Global /scans queue — Running / Queued / Succeeded / Failed / All status tabs above a recent-runs table with project, kind, and started-at columns](/img/screenshots/user-scans-queue.png)

Each `queued` or `running` row carries a **Cancel scan** action in its Actions column — see [Cancel a scan](#cancel-a-scan).

## Cancel a scan

You can stop a scan that is still `queued` or `running` — for example, when you triggered it against the wrong branch, or a large repo is taking longer than expected and you want to free the worker slot.

:::note Audience
Any team member with `developer` or higher on the **owning** team. You can cancel only your own team's scans; a scan belonging to another team is not visible to you and cannot be cancelled. Super admins can cancel any scan from the [admin scan queue](../admin-guide/oncall-runbook.md#scenario-3--scan-stuck-in-running-for--4-hours).
:::

### From the UI

The **Cancel scan** action appears in two places:

- The **scan progress drawer** (opens after you trigger a scan, or when you reopen a running scan).
- The **Actions** column of each `queued` or `running` row in the global [scan queue](#the-global-scan-queue) (`/scans`).

To cancel:

1. Click **Cancel scan**.
2. An inline confirmation appears. Click **Cancel scan** again to confirm, or **Keep running** to dismiss.
3. The scan moves to `cancelled` and the progress bar stops.

What happens on the server when you confirm:

- The worker task is stopped (the Celery task is revoked with `SIGTERM`).
- The scan's workspace (the cloned source tree) is reclaimed.
- The status becomes `cancelled`, with a completion timestamp and `error_message = "cancelled by user"`.
- The action is recorded in the [audit log](../admin-guide/audit-log.md) as a `scans` `update`.

:::tip Closing the browser is safe
Cancellation is processed entirely on the server. After you confirm, you can close the panel or the browser tab — the worker stops and the workspace is cleaned up regardless.
:::

:::caution Already-finished scans cannot be cancelled
A scan that already reached a terminal state (`succeeded`, `failed`, or `cancelled`) cannot be cancelled. The UI shows the message *"This scan already finished and can no longer be cancelled."* This is expected — there is nothing left to stop.
:::

### From the API

```bash
curl -sS -X POST \
  "https://trustedoss.example.com/v1/scans/${SCAN_ID}/cancel" \
  -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" | jq .
```

| Response | Meaning |
|---|---|
| `200 OK` | The scan was cancelled. The body carries the updated scan record with `status: "cancelled"`. |
| `404 Not Found` | The scan does not exist, or it belongs to a team you are not a member of. Other teams' scans are existence-hidden — a `404` does not confirm that the scan exists. |
| `409 Conflict` | The scan is already in a terminal state. The RFC 7807 body carries the extension field `scan_already_cancelled: true`. |

### Verify the cancel worked

1. The scan status reads **Cancelled** in the drawer and in the `/scans` queue (Cancelled appears under the **All** tab).
2. The progress bar is no longer advancing.
3. The worker slot is free — a `queued` scan behind it begins `running`.
4. The audit log records a `scans` `update` event with the new status.

## WebSocket progress feed

The UI subscribes to `ws(s)://<host>/ws/scans/{scan_id}` for live stage and percentage updates. The connection auto-reconnects with exponential backoff if the network drops. Reconnect re-emits the latest stage so the UI converges quickly.

If you build a custom client, the message shape is:

```json
{
  "step": "dt_findings",
  "percent": 62,
  "ts": "2026-05-09T13:42:11Z"
}
```

`percent` is an integer 0–100. `step` is one of the pipeline slugs (`bootstrap`, `fetch`, `prep`, `cdxgen`, `scancode`, `dt_upload`, `dt_findings`, `finalize`) plus the two terminal states (`succeeded`, `failed`). The `scancode` slug replaced the former `ort` slug at the same progress percent. The frame does not echo `scan_id` — the subscriber already knows it from the URL.

## Verify it worked

After a scan completes:

1. The project status switches to **Succeeded**.
2. The Components count > 0.
3. The Vulnerabilities count is visible (may be 0 if the project is genuinely clean).
4. The Last scan timestamp on the Overview tab reflects "now".
5. The audit log records `target_table=scans&action=create` and `target_table=scans&action=update` events.

## Troubleshooting

### Scan stuck in `Queued`

No worker has picked it up. Either the worker is down or the queue is saturated.

```bash
docker-compose -f docker-compose.yml ps worker
docker-compose -f docker-compose.yml logs --tail=200 worker
```

If the worker is unhealthy, restart it:

```bash
docker-compose -f docker-compose.yml restart worker
```

If the queue is saturated, increase `CELERY_CONCURRENCY` in `.env` and `docker-compose up -d worker` to scale up. Each concurrent slot needs ~2 GB of RAM.

### Scan failed with `git clone` error

The worker could not reach the repository. Check:

- Is the repo URL correct? (Test from the worker: `docker-compose exec worker git ls-remote <url>`.)
- Is the repo private? Embed credentials in the `git_url` — see [Projects → Private repositories](./projects.md#private-repositories).
- Does the worker have outbound HTTPS to your Git host? Corporate proxies must be set in `.env` (`HTTP_PROXY`, `HTTPS_PROXY`).

### Scan finished but vulnerabilities are missing

Dependency-Track may be unavailable. Check **/admin/dt** — the circuit-breaker state should be `CLOSED`. If it is `OPEN`, the scan succeeded against the vulnerability cache; vulnerabilities will refresh on the next successful DT round-trip (typically the next hourly resync).

### "DT unreachable" warning on the scan

Same as above — the circuit breaker tripped. The scan completed using the cache and the warning is informational. Resolve the underlying DT outage and trigger a fresh scan to refresh.

### Scan stuck running for ≥ 4 hours

First try **Cancel scan** from the drawer or the `/scans` queue (see [Cancel a scan](#cancel-a-scan)). If the run does not move to `cancelled` — for example because the broker is unreachable — use the on-call playbook for force-cancel + worker inspect:
[On-call runbook → Scan stuck](../admin-guide/oncall-runbook.md#scenario-3--scan-stuck-in-running-for--4-hours).

### "Cancel scan" does nothing / the scan stays running

The cancel request reached the API but the worker did not stop in time:

- If the broker (Redis) was briefly unreachable, the scan is still marked `cancelled` and the workspace is reclaimed by the orphan-workspace cleaner and the worker hard-limit backstop — you do not need to retry.
- If the row still shows `running` after a minute, confirm the worker is up (`docker-compose -f docker-compose.yml ps worker`) and escalate via the [on-call runbook](../admin-guide/oncall-runbook.md#scenario-3--scan-stuck-in-running-for--4-hours).

### "This scan already finished and can no longer be cancelled"

The scan reached a terminal state (`succeeded` / `failed` / `cancelled`) between the moment the page loaded and the moment you clicked **Cancel scan**. Reload the queue to see the up-to-date status — no action is needed.

### Detected (first-party) licenses are missing

The **Detected** licenses come from scancode and are best-effort. They may be absent when:

- scancode is not installed in the worker image (the scan still succeeds with **declared** licenses only — non-fatal). Confirm with `docker-compose -f docker-compose.yml logs worker | grep scancode_stage_skipped`.
- The first-party tree exceeds the `SCANCODE_MAX_FILES` ceiling, scancode timed out, or the result was too large — all log a warning and fall back to declared-only.
- The relevant code lives inside an excluded directory (`node_modules`, `vendor`, `.git`, `dist`, `build`, `out`, `target`, `.venv`, …). Those are skipped by design — see [Components & licenses → Detected vs. declared](./components-and-licenses.md#declared-vs-detected).

## Roadmap (v2.x)

Items the manual previously promised that are not in v2.0.0; tracked for later releases.

- Kind-selection dialog (Source / Container) and branch-override field on the project-level **Scan** trigger — planned for v2.1.

## See also

- [Components & licenses](./components-and-licenses.md)
- [Vulnerabilities](./vulnerabilities.md)
- [GitHub Actions](../ci-integration/github-actions.md)
- [DT connector](../admin-guide/dt-connector.md)
