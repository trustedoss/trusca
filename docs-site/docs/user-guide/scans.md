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
| **`source`** | `cdxgen` → ORT → Dependency-Track | Components and their declared / detected / concluded licenses, plus CVEs from NVD / OSV / GitHub Advisory. |
| **`container`** | Trivy | OS-package vulnerabilities and (limited) language-package CVEs in a container image. |

`source` is the only kind exposed in the v2.0.0 UI trigger — the API also accepts `container` for clients that wire it up directly. See [Roadmap](#roadmap-v2x) for UI parity.

## Trigger a scan

### From the UI

1. Open **Projects** in the sidebar.
2. Find the project row and click the **Scan** button at the end of the row.
3. The scan starts immediately as a `source` scan against the project's default branch.

There is no kind-selection dialog or branch-override field in the v2.0.0 UI — those controls are deferred to v2.1 (see [Roadmap](#roadmap-v2x)). The page switches to a live progress view backed by a WebSocket connection. You can close the tab — the scan continues on the worker. Reopen the project and reconnect at any time.

### From the API

```bash
curl -sS -X POST \
  "https://trustedoss.example.com/v1/projects/${PROJECT_ID}/scans" \
  -H "Authorization: ApiKey ${TRUSTEDOSS_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"kind": "source"}' | jq .
```

The response carries the scan UUID. Poll:

```bash
curl -sS "https://trustedoss.example.com/v1/scans/${SCAN_ID}" \
  -H "Authorization: ApiKey ${TRUSTEDOSS_API_KEY}" | jq .status
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
4. **Analyzing licenses** — ORT applies the rule set in `ort/rules.kts`.
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

Visit **Scans** in the left sidebar for an organization-wide view of every running and queued scan. Filters: status, kind, project, team. Super-admins also see the per-worker breakdown of the queue depth.

Cancel actions on this view are not exposed at v2.0.0 — see [Roadmap](#roadmap-v2x).

## WebSocket progress feed

The UI subscribes to `ws(s)://<host>/ws/scans/{scan_id}` for live stage and percentage updates. The connection auto-reconnects with exponential backoff if the network drops. Reconnect re-emits the latest stage so the UI converges quickly.

If you build a custom client, the message shape is:

```json
{
  "step": "resolving_vulnerabilities",
  "percent": 62,
  "ts": "2026-05-09T13:42:11Z"
}
```

`percent` is an integer 0–100. `step` matches the pipeline-stage slugs above. The frame does not echo `scan_id` — the subscriber already knows it from the URL.

## Verify it worked

After a scan completes:

1. The project status switches to **Succeeded**.
2. The Components count > 0.
3. The Vulnerabilities count is visible (may be 0 if the project is genuinely clean).
4. The Last scan timestamp on the Overview tab reflects "now".
5. The audit log records `scan.create` and `scan.update` events.

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

## Roadmap (v2.x)

Items the manual previously promised that are not in v2.0.0; tracked for later releases.

- Kind-selection dialog (Source / Container) and branch-override field on the project-level **Scan** trigger — planned for v2.1.
- `cancelled` lifecycle transition with `DELETE /v1/scans/{id}` and a UI cancel button on the global queue — planned for v2.1.

## See also

- [Components & licenses](./components-and-licenses.md)
- [Vulnerabilities](./vulnerabilities.md)
- [GitHub Actions](../ci-integration/github-actions.md)
- [DT connector](../admin-guide/dt-connector.md)
