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
| **`source`** | `cdxgen` (CycloneDX generator) → OSS Review Toolkit (ORT) → Dependency-Track (DT) | Components and their declared / detected / concluded licenses, plus CVEs (Common Vulnerabilities and Exposures) from NVD / OSV / GitHub Advisory. |
| **`container`** | Trivy (Aqua Security container scanner) | OS-package vulnerabilities and (limited) language-package CVEs in a container image. |

`source` is the only kind exposed in the v2.0.0 UI trigger — the API also accepts `container` for clients that wire it up directly. See [Roadmap](#roadmap-v2x) for UI parity.

## Trigger a scan

### From the UI

1. Open **Projects** in the sidebar.
2. Find the project row and click the **Scan** button at the end of the row.
3. The scan starts immediately as a `source` scan against the project's default branch.

There is no kind-selection dialog or branch-override field in the v2.0.0 UI — those controls are deferred to v2.1 (see [Roadmap](#roadmap-v2x)). A right-slide drawer opens on the project list page with a live progress view backed by a WebSocket connection. You can close the tab — the scan continues on the worker. Reopen the project and reconnect at any time.

![Scan progress drawer — bootstrap → fetch → cdxgen → ORT → DT → finalize stages, live over WebSocket](/img/screenshots/user-scans-progress-drawer.png)

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
                      │
                      └────► failed
```

| Status | Meaning |
|---|---|
| `queued` | Enqueued; waiting for a free worker slot. |
| `running` | A worker has picked up the task and is executing the pipeline. |
| `succeeded` | Pipeline finished, components and findings are now queryable. |
| `failed` | The worker raised an error. Inspect `error_detail` in the API response or the worker log. |

A `cancelled` terminal state is reserved in the data model but not exposed by an API or UI control at v2.0.0 — see [Roadmap](#roadmap-v2x).

### Pipeline stages (source)

The progress view shows real-time stage transitions:

1. **Bootstrapping** — preparing the workspace.
2. **Fetching source** — `git clone` (or `git fetch` + checkout for an existing workspace).
3. **Detecting components** — `cdxgen` walks the repo and emits a CycloneDX SBOM.
4. **Analyzing licenses** — ORT resolves declared / detected / concluded licenses. Legal-tier classification at v2.0.0 is then applied from the hard-coded `_LICENSE_CATEGORY_DEFAULTS` dictionary in `apps/backend/tasks/scan_source.py` (see [Components & licenses → Classification source](./components-and-licenses.md#license-classification)); the repo's `ort/rules.kts` is a placeholder until ORT-driven customization lands in v2.2.
5. **Resolving vulnerabilities** — Dependency-Track correlates the SBOM against its feed mirror.
6. **Persisting** — components, licenses, and findings are written to PostgreSQL.

If Dependency-Track is unavailable when stage 5 runs, the [DT circuit breaker](../admin-guide/dt-connector.md) trips OPEN and the scan reads from the PostgreSQL vulnerability cache. The scan is marked `succeeded` with a warning surfaced in the UI.

## Average duration

| Project size | Source scan | Container scan |
|---|---|---|
| Small (≤ 50 components) | 3–8 min | 1–3 min |
| Medium (50–500) | 8–20 min | 2–5 min |
| Large (≥ 500, multi-module) | 20–60 min | 5–10 min |

The dominant cost in a source scan is ORT + Dependency-Track correlation, not `cdxgen`. Container scans are bound by image-pull time when the image is not in the worker's cache.

## The global scan queue

Visit **Scans** in the left sidebar for an organization-wide view of every running and queued scan. The queue is split into 5 status tabs: Running, Queued, Succeeded, Failed, All. Project- / team-level filters and per-worker views are on the roadmap.

![Global scan queue with status tabs and recent runs.](/img/screenshots/user-scans-queue.png)

Cancel actions on this view are not exposed at v2.0.0 — see [Roadmap](#roadmap-v2x).

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

`percent` is an integer 0–100. `step` is one of the seven pipeline slugs (`bootstrap`, `fetch`, `prep`, `cdxgen`, `ort`, `dt_upload`, `dt_findings`, `finalize`) plus the two terminal states (`succeeded`, `failed`). The frame does not echo `scan_id` — the subscriber already knows it from the URL.

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

Use the on-call playbook for force-cancel + worker inspect:
[On-call runbook → Scan stuck](../admin-guide/oncall-runbook.md#scenario-3--scan-stuck-in-running-for--4-hours).

## Roadmap (v2.x)

Items the manual previously promised that are not in v2.0.0; tracked for later releases.

- Kind-selection dialog (Source / Container) and branch-override field on the project-level **Scan** trigger — planned for v2.1.
- `cancelled` lifecycle transition with `DELETE /v1/scans/{id}` and a UI cancel button on the global queue — planned for v2.1.

## See also

- [Components & licenses](./components-and-licenses.md)
- [Vulnerabilities](./vulnerabilities.md)
- [GitHub Actions](../ci-integration/github-actions.md)
- [DT connector](../admin-guide/dt-connector.md)
