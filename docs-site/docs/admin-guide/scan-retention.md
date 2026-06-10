---
id: scan-retention
title: Scan retention
description: How the portal keeps the latest scan per branch / PR, reclaims superseded snapshots after a grace window, preserves release-labelled scans forever, and how to delete a scan by hand.
sidebar_label: Scan retention
sidebar_position: 8
---

# Scan retention

CI and webhook automation trigger a scan on every push, pull request, and merge request. Left unbounded, that produces thousands of near-identical snapshots per project. The portal keeps the history useful and the disk bounded with a **retention model**: only the latest successful scan per target stays *live*, older snapshots are *superseded* and reclaimed after a grace window, and scans you explicitly mark as a release are kept forever.

:::note Audience
`super_admin` and `team_admin` operating a portal that receives automated scans. Familiarity with `.env` editing and `docker-compose restart`. For the per-scan lifecycle (queued → running → succeeded), see [Scans](../user-guide/scans.md).
:::

## The retention model

Every scan carries a **retention target** derived from its project and the **normalized ref** of the branch or PR it ran against. When a newer successful scan lands on the same target, the previous one is **superseded**.

```
target = (project_id, normalized_ref)

scan #1 on main  ──► live
scan #2 on main  ──► live, scan #1 becomes superseded ──► reclaimed after grace
scan #3 on main  ──► live, scan #2 becomes superseded ──► reclaimed after grace
```

- **Live** — the most recent successful scan for a target. Always queryable; never reclaimed by age alone.
- **Superseded** — a previously-live scan replaced by a newer success on the same target. Kept for a grace window (`SCAN_RETENTION_SUPERSEDED_GRACE_DAYS`, default 7 days) so you can diff or roll back, then reclaimed by the sweep.
- **Release** — a scan whose `metadata.release` label is set. **Immutable and permanent** — the sweep never touches it, regardless of age or supersession. See [Keep a scan forever](#keep-a-scan-forever-release-label).
- **Ref-less / failed** — scans with no ref target (ad-hoc UI scans) and failed scans are not part of the supersession chain. They are protected by a per-project floor (`SCAN_RETENTION_KEEP_LAST`, default 30) and an age ceiling (`SCAN_RETENTION_MAX_AGE_DAYS`, default 180).

### Ref normalization

The retention target uses a **normalized** form of the ref so that the same logical branch or PR groups together regardless of how CI spells it. The portal normalizes the ref it receives in `metadata.ref` as follows:

| Incoming ref | Normalized | Notes |
|---|---|---|
| `refs/heads/main` | `main` | Branch refs drop the `refs/heads/` prefix. |
| `refs/pull/12/merge` | `pr-12` | GitHub PR merge refs become `pr-<number>`. |
| `refs/merge-requests/7/head` | `mr-7` | GitLab MR refs become `mr-<iid>`. |
| `main`, `release/2.0` | `main`, `release/2.0` | A bare branch name is kept as-is. |

The [GitHub Action](../ci-integration/github-actions.md#how-the-ref-becomes-a-retention-key) forwards `github.ref` (or the PR number) and the [GitLab CI template](../ci-integration/gitlab-ci.md#how-the-ref-becomes-a-retention-key) forwards `CI_COMMIT_REF_NAME` / the MR IID, so you get correct grouping without configuration. The [Jenkinsfile snippet](../ci-integration/jenkins.md#quick-start) forwards `BRANCH_NAME` the same way.

## Retention policy variables

All four keys are read at runtime via `os.getenv` — edit `.env` and restart the Celery worker and beat services to apply them. The service names differ by stack: on the **production** compose (`docker-compose.yml`) they are `worker` and `beat`; on the **dev** compose (`docker-compose.dev.yml`) they are `celery-worker` and `celery-beat`. See [Environment variables → Scan retention](../reference/env-variables.md#scan-retention) for the canonical reference.

<!-- docs-uat: id=scan-retention-env kind=shell ctx=host tier=manual waiver=env-config-snippet-not-a-command -->
```bash
# In the portal's .env
SCAN_RETENTION_SUPERSEDED_GRACE_DAYS=7    # keep superseded snapshots this long before reclaim
SCAN_RETENTION_KEEP_LAST=30               # per-project floor for ref-less / failed scans
SCAN_RETENTION_MAX_AGE_DAYS=180           # age ceiling for ref-less / failed scans
```

| Key | Default | Effect |
|---|---|---|
| `SCAN_RETENTION_SUPERSEDED_GRACE_DAYS` | `7` | Days a superseded snapshot survives before the sweep reclaims it. Raise it to keep more rollback history per branch. |
| `SCAN_RETENTION_KEEP_LAST` | `30` | Minimum ref-less and failed scans kept **per project**, regardless of age. The sweep never trims below this floor. |
| `SCAN_RETENTION_MAX_AGE_DAYS` | `180` | Among **ref-less successful scans and failed/cancelled scans**, those older than this (and beyond the keep-last floor) are reclaimed. The **live snapshot of a ref** and **release-labelled scans** are exempt — retire, not age, manages those. |

:::caution Lowering a value reclaims sooner
Lowering `SCAN_RETENTION_SUPERSEDED_GRACE_DAYS` or `SCAN_RETENTION_MAX_AGE_DAYS` means the next sweep reclaims more snapshots. The sweep is irreversible — reclaimed scans and their findings are gone. Tune up first, observe disk, then tune down.
:::

## The retention sweep

Reclamation runs as a **Celery beat task every 6 hours**, not synchronously on scan completion. Marking a scan superseded happens immediately when a newer success lands; the disk and database rows are reclaimed on the next sweep that finds the snapshot past its grace window.

Each sweep:

1. Reclaims **superseded** snapshots older than `SCAN_RETENTION_SUPERSEDED_GRACE_DAYS`.
2. Among **ref-less successful scans and all failed/cancelled scans**, keeps the newest `SCAN_RETENTION_KEEP_LAST` per project and reclaims the rest that are older than `SCAN_RETENTION_MAX_AGE_DAYS`.
3. Never touches the **live snapshot of a ref** (managed by retire, not the sweep) or a scan with a `metadata.release` label.

Reclaiming a scan deletes its workspace artefacts (source clone, cdxgen SBOM, scancode output) and its database rows (components, licenses, findings). The audit log records a `scans` `delete` event per reclaimed scan with the reason — `superseded` (step 1) or `aged` (step 2).

## Keep a scan forever (release label)

To pin a scan so retention never reclaims it — for example the scan that backs a tagged release — set a `metadata.release` label when you trigger it. A release-labelled scan is **immutable**: it is exempt from the grace window, the age ceiling, and the keep-last trim.

<!-- docs-uat: id=scan-retention-release-label kind=shell ctx=host tier=manual waiver=example-curl-placeholder-host-and-api-key -->
```bash
curl -sS -X POST \
  "https://trustedoss.example.com/v1/projects/${PROJECT_ID}/scans" \
  -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"kind": "source", "metadata": {"ref": "refs/tags/v2.0.0", "release": "v2.0.0"}}' | jq .
```

The `release` value is a free-form label (a version string is conventional). From CI, set it only on the workflow that runs on a tag push, so day-to-day branch and PR scans stay reclaimable while your release scans accumulate as a permanent compliance record.

:::note Release scans are not superseded
Because release scans are outside the supersession chain, two releases on the same branch both stay live. That is intentional — you want every shipped version's SBOM on record.
:::

## Delete a scan by hand

Use `DELETE /v1/scans/{scan_id}` to reclaim a single scan immediately rather than waiting for the sweep — for example a scan triggered against the wrong project, or a noisy snapshot you do not want in the history.

<!-- docs-uat: id=scan-retention-delete kind=shell ctx=host tier=manual waiver=example-curl-placeholder-host-and-api-key -->
```bash
curl -sS -X DELETE \
  "https://trustedoss.example.com/v1/scans/${SCAN_ID}" \
  -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" | jq .
```

To delete a scan that carries a `metadata.release` label, add `?force=true`:

<!-- docs-uat: id=scan-retention-delete-force kind=shell ctx=host tier=manual waiver=example-curl-placeholder-host-and-api-key -->
```bash
curl -sS -X DELETE \
  "https://trustedoss.example.com/v1/scans/${SCAN_ID}?force=true" \
  -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" | jq .
```

### Authorization and responses

| Condition | Requirement / response |
|---|---|
| Caller role | `developer` or higher on the **owning** team. `force=true` requires `team_admin` or higher. |
| Other team's scan | `404 Not Found` — other teams' scans are existence-hidden. A `404` does not confirm the scan exists. |
| Scan is `queued` or `running` | `409 Conflict` — an active scan cannot be deleted. [Cancel it first](../user-guide/scans.md#cancel-a-scan), then delete. |
| Scan has a `release` label, no `force` | `409 Conflict` — the RFC 7807 body carries `scan_release_protected: true`. Re-issue with `?force=true` as a `team_admin`. |
| `force=true` by a non-`team_admin` | `403 Forbidden` — forcing the delete of a release-protected scan needs `team_admin`. |
| Deleted | `204 No Content`. Artefacts and rows are gone; the audit log records a `scans` `delete` event. |

## Verify it worked

<!-- docs-uat: id=scan-retention-verify-live kind=manual tier=manual -->
1. Trigger two source scans against the same branch. Fetch the older scan with `GET /v1/scans/{id}` — its `superseded_at` is now set. The project's **Releases** list (`GET /v1/projects/{id}/releases`) shows only the newer snapshot; the superseded one is hidden there.
<!-- docs-uat: id=scan-retention-verify-release kind=manual tier=manual -->
2. Trigger a scan with a `release` label and a second scan on the same branch. The release scan's `superseded_at` stays `null` and it remains in the Releases list — it is **not** superseded.
<!-- docs-uat: id=scan-retention-verify-sweep kind=manual tier=manual -->
3. After a superseded scan passes its grace window, the next 6-hourly sweep removes it. Confirm with the audit log: a `scans` `delete` event with reason `superseded`.
<!-- docs-uat: id=scan-retention-verify-delete kind=manual tier=manual -->
4. `DELETE /v1/scans/{scan_id}` on a non-release, terminal scan returns `204` and the scan disappears from the history.

## Troubleshooting

:::info Logs to check first
- `docker-compose -f docker-compose.yml logs --tail=200 beat | grep scan_retention_done` — the last sweep's verdict and per-reason counts (`reclaimed_superseded` / `reclaimed_aged`). The resolved policy is logged as `scan_retention_policy` at the start of each sweep. On the dev compose the service is `celery-beat`, not `beat`.
- Audit log filtered to `scans` `delete` — what was reclaimed and why.
:::

### Two scans on the same branch both stay live

They are not on the same normalized target. Confirm both forwarded the **same** `metadata.ref`. A bare branch name (`main`) and a fully-qualified ref (`refs/heads/main`) normalize to the same target, but a PR merge ref (`refs/pull/12/merge` → `pr-12`) is a distinct target from the base branch — that is intentional.

### A scan I expected to be reclaimed is still here

Check, in order:

- It is the **live** snapshot for its target — live scans are never reclaimed by age until they are superseded or exceed `SCAN_RETENTION_MAX_AGE_DAYS`.
- It carries a `metadata.release` label — release scans are permanent. Inspect the scan's metadata in the UI or API.
- It is within the `SCAN_RETENTION_KEEP_LAST` floor — a ref-less or failed scan among the newest N per project is protected regardless of age.
- The grace window has not elapsed — a superseded scan survives `SCAN_RETENTION_SUPERSEDED_GRACE_DAYS` before the sweep takes it.

### Disk is filling faster than the sweep reclaims

The sweep runs every 6 hours; a heavy CI day can outpace it. Lower `SCAN_RETENTION_SUPERSEDED_GRACE_DAYS` so superseded snapshots reclaim sooner, or delete the worst offenders by hand. For workspace-level cleanup of artefacts, see [Disk & health → What to do when disk fills up](./disk-and-health.md#what-to-do-when-disk-fills-up).

### `409` when deleting a scan

Either the scan is still `queued` / `running` (cancel it first — see [Cancel a scan](../user-guide/scans.md#cancel-a-scan)) or it has a `release` label and you did not pass `?force=true`. The RFC 7807 body's extension field tells you which: `scan_active: true` versus `scan_release_protected: true`.

## See also

- [Scans](../user-guide/scans.md) — the per-scan lifecycle and cancel flow
- [GitHub Actions](../ci-integration/github-actions.md) — forwarding the ref as a retention key
- [GitLab CI](../ci-integration/gitlab-ci.md) — forwarding the ref as a retention key
- [Disk & health](./disk-and-health.md) — workspace artefact cleanup
- [Audit log](./audit-log.md) — reclamation and delete events
- [Environment variables → Scan retention](../reference/env-variables.md#scan-retention)
