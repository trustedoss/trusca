---
id: scans
title: Scans
description: Trigger source and container scans, watch progress in real time, and read terminal status — the full scan lifecycle in TRUSCA.
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
| **`source`** | `cdxgen` (CycloneDX generator) → scancode (first-party license detection) → Trivy (`trivy sbom`) | Components and their **declared** licenses (from dependency metadata) plus **detected** licenses (scancode reading your own first-party source), and CVEs (Common Vulnerabilities and Exposures) matched by the local Trivy DB against NVD + OSV + GHSA + EPSS + KEV. |
| **`container`** | Trivy (Aqua Security container scanner) | OS-package vulnerabilities and (limited) language-package CVEs in a container image. |
| **`sbom`** | conformance scoring → component persistence → Trivy (`trivy sbom`) | An SBOM your own tooling already produced (CycloneDX-JSON or SPDX). TRUSCA does not clone or build your source — it scores the SBOM's quality, persists its components, and matches CVEs. See [Received SBOMs](#received-sboms-uploaded) below. |

**Source** and **Container** are selectable from the UI scan dialog — pick one when you trigger a scan (see [Trigger a scan → From the UI](#from-the-ui)). An **`sbom`** scan is created differently: you upload an existing SBOM to the ingest endpoint rather than picking it in the dialog (see [Received SBOMs](#received-sboms-uploaded)). The API accepts all three kinds.

## Trigger a scan

### From the UI

1. Open **Projects** in the sidebar.
2. Find the project row and click the **Scan** button at the end of the row.
3. The **scan dialog** opens. At the top, choose the scan type:
   - **Source** — runs cdxgen + scancode + Trivy on the project's source. This is the default.
   - **Container** — runs Trivy on a container image you name. See [Scan a container image](#scan-a-container-image).
4. For a **Source** scan, pick how to provide the source (Git URL, an uploaded `.zip`, or a folder zipped in the browser), then click **Start scan**.

:::tip Verbose logs (debug)
The scan dialog has a **Verbose logs (debug)** toggle (off by default). Leave it off for the standard progress trace. Turn it on for a single scan to stream the **full** cdxgen / scancode / Trivy diagnostic output into the [per-stage log panel](#watching-scan-progress) — cdxgen runs in debug mode, scancode emits a per-file line, and Trivy switches to `--debug`. Use it when you are debugging why a scan found too few components, missed a license, or matched an unexpected CVE. Verbose output can be large; the per-scan line budget (`SCAN_LOG_MAX_LINES_PER_SCAN`, default 20000) still caps it. Credentials are redacted from the log on the way out, and a verbose **source** scan additionally lists each scanned file's path — visible to anyone on the team who can open the scan, so prefer it on internal/trusted projects when sharing logs widely. Over the API, set `metadata.verbosity` to `"verbose"` (absent or `"normal"` keeps the quiet trace).
:::

A right-slide drawer opens on the project list page with a live progress view backed by a WebSocket connection. You can close the tab — the scan continues on the worker. Reopen the project and reconnect at any time. While a scan is `queued` or `running`, the drawer carries a **Cancel scan** action — see [Cancel a scan](#cancel-a-scan).

:::note Only one scan at a time per project
If a project already has a `queued` or `running` scan, the **Scan** button is disabled on the project detail header and its tooltip points you at the in-progress chip in the header (clicking the chip re-opens the existing scan's progress drawer). Triggering a second scan via the API returns `409 Conflict` with the RFC 7807 extension `scan_already_in_progress: true` — wait for the active scan to reach a terminal state, or **Cancel** it, before starting another. The same guard applies to UI, API, and CI clients.
:::

![Scan progress drawer — bootstrap → fetch → cdxgen → scancode → vuln_match → finalize stages, live over WebSocket](/img/screenshots/user-scans-progress-drawer.png)

:::warning Branch selection for source scans
Source scans run against the project's `default_branch` (typically
`main`). Neither the UI nor the API exposes a branch override in this
release. To scan `develop` or a feature branch, temporarily change
`default_branch` in **Project Settings** before triggering the scan,
then revert. A first-class `branch` field on the trigger is on the
roadmap.
:::

### Scan a container image

Pick **Container** in the scan dialog to scan a built image instead of source. Trivy (the Aqua Security container scanner) inspects the image's **OS packages** for known vulnerabilities — complementary to a source scan, which covers your application's dependency tree.

1. Open the scan dialog from the project row's **Scan** button.
2. At the top of the dialog, select **Container**.
3. Enter the **container image** reference in `name:tag` form, for example `alpine:3.19` or `ghcr.io/org/app:1.2.3`. The image must be pullable from the worker (public registries, or a registry the worker is authenticated against).
4. Click **Start scan**.

The same progress drawer opens. When the scan reaches `succeeded`, the OS-package vulnerabilities appear under the project's **Vulnerabilities** tab.

:::note Container scans do not need a Git URL
A container scan reads an image reference, not the repository. A project with no `git_url` can still run container scans. The Source / Container choice is independent of the project's source configuration.
:::

### From the API

<!-- docs-uat: id=scans-api-start-source kind=shell ctx=host tier=manual waiver=example-curl-placeholder-host-and-api-key -->
```bash
curl -sS -X POST \
  "https://trustedoss.example.com/v1/projects/${PROJECT_ID}/scans" \
  -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"kind": "source"}' | jq .
```

The response carries the scan UUID. Poll:

<!-- docs-uat: id=scans-api-poll-status kind=shell ctx=host tier=manual waiver=example-curl-placeholder-host-and-api-key -->
```bash
curl -sS "https://trustedoss.example.com/v1/scans/${SCAN_ID}" \
  -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" | jq .status
```

For a container scan, set `kind` to `container` and pass the image reference under `metadata.image_ref`:

<!-- docs-uat: id=scans-api-start-container kind=shell ctx=host tier=manual waiver=example-curl-placeholder-host-and-api-key -->
```bash
curl -sS -X POST \
  "https://trustedoss.example.com/v1/projects/${PROJECT_ID}/scans" \
  -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"kind": "container", "metadata": {"image_ref": "alpine:3.19"}}' | jq .
```

### From CI

The recommended path is the [GitHub Action](../ci-integration/github-actions.md), the [GitLab CI template](../ci-integration/gitlab-ci.md), or the [Jenkinsfile example](../ci-integration/jenkins.md). Each one wraps the API and adds the build gate.

## Received SBOMs (uploaded) {#received-sboms-uploaded}

If your own build or CI already produces an SBOM, you can upload it instead of having TRUSCA clone and scan your source. This creates an **`sbom`** scan: TRUSCA persists the SBOM's components, matches CVEs with Trivy, and classifies declared licenses — the same component / vulnerability / license views you get from a source scan, and the build gate runs on it too.

- **Formats**: CycloneDX-JSON, or SPDX (JSON or Tag-Value). Trivy auto-detects the format for matching; SPDX is mapped to CycloneDX internally for the component graph.
- **How to upload**: `POST /v1/projects/{project_id}/sbom-ingest` with an API key. The full how-to (fields, size limits, errors) is in [Upload an SBOM](../ci-integration/sbom-upload.md).

### Conformance verdict

Because a supplier-provided SBOM can be a "shell" with missing versions, PURLs, or no dependency graph, TRUSCA scores its **quality** on ingest and shows a **pass / warn / fail** badge plus a per-requirement table on the scan detail page. The verdict is **advisory** — a `fail` does not block ingest (CVE matching still runs); it tells you whether to accept the SBOM or send it back to the supplier. Mandatory checks include a timestamp, tool info, a top-level component, 100% component name+version, PURL coverage ≥ 90%, no `pkg:generic` placeholders, and a transitive dependency graph; license and hash coverage are recommended (warn-only). Read it via the UI panel or `GET /v1/projects/{project_id}/scans/{scan_id}/conformance` — see [Upload an SBOM → Read the conformance verdict](../ci-integration/sbom-upload.md#read-the-conformance-verdict).

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
4. **Detecting first-party licenses** — scancode scans the project's own source files and records the **detected** licenses it finds, each tagged with the `source_path` of the file it came from (see [Components & licenses → Detected vs. declared](./components-and-licenses.md#declared-vs-detected)). This stage is best-effort: if scancode is not installed, times out, or the tree is too large, the scan continues with declared licenses only — a degraded but non-fatal outcome. Legal-tier classification is then applied from the built-in classifier catalog (see [Components & licenses → Classification source](./components-and-licenses.md#license-classification)).
5. **Resolving vulnerabilities** — `trivy sbom` matches the CycloneDX SBOM against the local Trivy DB (NVD + OSV + GHSA + EPSS + KEV). No network call per scan.
6. **Persisting** — components, licenses, and findings are written to PostgreSQL.

:::note ORT was replaced by scancode
Earlier builds ran the OSS Review Toolkit (ORT) at the license stage. v0.10.0 replaces it with scancode for **first-party** detection. Third-party dependency sources are deliberately not downloaded — that kept per-scan runtime within budget — so dependency licenses stay **declared** (from cdxgen) and scancode adds **detected** licenses for the code your team actually wrote.
:::

If the local Trivy DB has not finished downloading when stage 5 runs (most common on a fresh install), the scan completes with **0 vulnerability findings** and a banner on the Vulnerabilities tab pointing operators at [Vulnerability data (Trivy DB)](../admin-guide/vulnerability-data.md). The automatic re-match beat picks up findings once the DB lands — no re-scan needed.

## Average duration

| Project size | Source scan | Container scan |
|---|---|---|
| Small (≤ 50 components) | 3–8 min | 1–3 min |
| Medium (50–500) | 8–20 min | 2–5 min |
| Large (≥ 500, multi-module) | 20–60 min | 5–10 min |

The dominant cost in a source scan is the `cdxgen` walk, with scancode adding time proportional to the size of the first-party tree. The `trivy sbom` matching stage is fast — the Trivy DB is local and per-scan I/O is well under a second per thousand components. Container scans are bound by image-pull time when the image is not in the worker's cache.

## The global scan queue

Visit **Scans** in the left sidebar for an organization-wide view of every running and queued scan. The queue is split into 5 status tabs: Running, Queued, Succeeded, Failed, All. Project- / team-level filters and per-worker views are on the roadmap.

![Global /scans queue — Running / Queued / Succeeded / Failed / All status tabs above a recent-runs table with project, kind, and started-at columns](/img/screenshots/user-scans-queue.png)

<!-- screenshot above predates the project-name column added in P1 #5; refresh post-merge -->

The **Project** column shows the project's display name and links to its detail page; rows where the underlying project name could not be resolved (a foreign-key fallback path) fall back to the first 8 characters of the project UUID. The list endpoint batch-loads the project relationship in a single round-trip, so the column populates without per-row lookups even on a queue of hundreds of scans.

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

<!-- docs-uat: id=scans-api-cancel kind=shell ctx=host tier=manual waiver=example-curl-placeholder-host-and-api-key -->
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

## Watching scan progress

Once a scan is queued the **scan progress drawer** opens with three panels stacked top-to-bottom:

1. **Stage list** — every pipeline stage with its current state (`pending`, `running`, `succeeded`, `failed`, `skipped`). The active stage carries a live spinner.
2. **Per-stage log panel** — a scrollable text panel mirroring the worker's log frames for the *currently selected* stage. Click any stage row to switch the panel to that stage's frames; the panel auto-scrolls to the latest frame while you stay near the bottom and pauses auto-scroll when you scroll up to read earlier output. Frames are buffered up to the most recent ~500 lines per stage; older lines roll off.
3. **Action footer** — **Cancel scan** while the run is non-terminal; close affordance once it reaches `succeeded` / `failed` / `cancelled`.

Re-opening the drawer for an already-completed scan replays the persisted stage transitions and final log frame from the database (the spinner does **not** keep spinning on a `succeeded` row — the stage's terminal state is shown). Live frames stream over the WebSocket below.

### WebSocket progress feed

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

<!-- docs-uat: id=scans-queue-populated kind=ui harness=scansListPopulated tier=nightly -->
1. The project status switches to **Succeeded**.
<!-- docs-uat: id=scans-components-nonzero kind=ui harness=componentsHaveData(portal-web) tier=nightly -->
2. The Components count > 0.
<!-- docs-uat: id=scans-vulns-tab-ready kind=ui harness=vulnerabilitiesTabReady(portal-web) tier=nightly -->
3. The Vulnerabilities count is visible (may be 0 if the project is genuinely clean).
<!-- docs-uat: id=scans-last-scan-timestamp kind=sql ctx=postgres expect=rows:>0 fixture=seed_demo tier=nightly -->
4. The Last scan timestamp on the Overview tab reflects "now".

   ```sql
   SELECT count(*) FROM scans s
     JOIN projects p ON p.id = s.project_id
    WHERE p.slug = 'portal-web'
      AND s.status = 'succeeded'
      AND s.completed_at IS NOT NULL;
   ```

<!-- docs-uat: id=scans-audit-events kind=manual tier=manual -->
5. The audit log records `target_table=scans&action=create` and `target_table=scans&action=update` events.

## Troubleshooting

:::note Compose service names differ by stack
The commands below target the **production** compose (`docker-compose.yml`),
whose Celery services are `worker` and `beat`. On the **dev** compose
(`docker-compose.dev.yml`) the same services are `celery-worker` and
`celery-beat` — substitute the name and the `-f` file if you are on the dev
stack.
:::

### Scan stuck in `Queued`

No worker has picked it up. Either the worker is down or the queue is saturated.

<!-- docs-uat: id=scans-worker-inspect kind=shell ctx=host tier=manual waiver=operator-docker-compose-command-not-runnable-in-ci -->
```bash
docker-compose -f docker-compose.yml ps worker
docker-compose -f docker-compose.yml logs --tail=200 worker
```

If the worker is unhealthy, restart it:

<!-- docs-uat: id=scans-worker-restart kind=shell ctx=host tier=manual waiver=operator-docker-compose-command-not-runnable-in-ci -->
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

The local Trivy DB may not be in place yet. Confirm on the worker:

<!-- docs-uat: id=scans-worker-trivy-db kind=shell ctx=host tier=manual waiver=operator-docker-compose-command-not-runnable-in-ci -->
```bash
docker-compose -f docker-compose.yml exec worker \
  ls -lh /var/lib/trivy/db/
```

An empty or absent `db/` directory means the boot-time download has not completed. The first download takes 1–3 minutes; the automatic re-match beat repopulates findings on existing scans once the DB lands — no re-scan needed. See [Vulnerability data — Troubleshooting](../admin-guide/vulnerability-data.md#troubleshooting).

### Scan stuck running for ≥ 4 hours

First try **Cancel scan** from the drawer or the `/scans` queue (see [Cancel a scan](#cancel-a-scan)). If the run does not move to `cancelled` — for example because the broker is unreachable — use the on-call playbook for force-cancel + worker inspect:
[On-call runbook → Scan stuck](../admin-guide/oncall-runbook.md#scenario-3--scan-stuck-in-running-for--4-hours).

### "Cancel scan" does nothing / the scan stays running

The cancel request reached the API but the worker did not stop in time:

- If the broker (Redis) was briefly unreachable, the scan is still marked `cancelled` and the workspace is reclaimed by the orphan-workspace cleaner and the worker hard-limit backstop — you do not need to retry.
- If the row still shows `running` after a minute, confirm the worker is up (`docker-compose -f docker-compose.yml ps worker`) and escalate via the [on-call runbook](../admin-guide/oncall-runbook.md#scenario-3--scan-stuck-in-running-for--4-hours).

### "This scan already finished and can no longer be cancelled"

The scan reached a terminal state (`succeeded` / `failed` / `cancelled`) between the moment the page loaded and the moment you clicked **Cancel scan**. Reload the queue to see the up-to-date status — no action is needed.

### A second scan won't start — the **Scan** button is greyed out

The project already has a `queued` or `running` scan. Only one active scan per project is allowed. Open the in-progress chip in the project header (or the row in the global queue) to see the existing run, wait for it to finish, or **Cancel** it before starting another. See [Only one scan at a time per project](#from-the-ui).

### A completed scan's drawer shows a spinner that never finishes

Older builds (pre-P1) left the **Finalizing** step's spinner animating after the scan had already reached `succeeded`. The fix freezes the spinner on the terminal state when the drawer is opened on a completed run. If you still see the symptom, force-reload the project page to refresh the cached scan record.

### Detected (first-party) licenses are missing

The **Detected** licenses come from scancode and are best-effort. They may be absent when:

- scancode is not installed in the worker image (the scan still succeeds with **declared** licenses only — non-fatal). Confirm with `docker-compose -f docker-compose.yml logs worker | grep scancode_stage_skipped`.
- The first-party tree exceeds the `SCANCODE_MAX_FILES` ceiling, scancode timed out, or the result was too large — all log a warning and fall back to declared-only.
- The relevant code lives inside an excluded directory (`node_modules`, `vendor`, `.git`, `dist`, `build`, `out`, `target`, `.venv`, …). Those are skipped by design — see [Components & licenses → Detected vs. declared](./components-and-licenses.md#declared-vs-detected).

## Roadmap

Items tracked for later releases.

- Branch-override field on the project-level **Scan** trigger — planned. (The Source / Container kind-selection dialog shipped in this release — see [Trigger a scan → From the UI](#from-the-ui).)

## See also

- [Components & licenses](./components-and-licenses.md)
- [Vulnerabilities](./vulnerabilities.md)
- [GitHub Actions](../ci-integration/github-actions.md)
- [Vulnerability data (Trivy DB)](../admin-guide/vulnerability-data.md)
