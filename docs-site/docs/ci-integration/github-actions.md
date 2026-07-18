---
id: github-actions
title: GitHub Actions
description: Wire TRUSCA into a GitHub Actions workflow with the in-repo composite action at actions/scan — trigger, poll, gate, comment.
sidebar_label: GitHub Actions
sidebar_position: 1
---

# GitHub Actions

The TRUSCA composite action triggers a TRUSCA scan, waits for it to finish, evaluates the build gate, and (on pull requests) posts the SCA report back to the PR. It exits non-zero when the gate fails so the PR check turns red and your branch-protection rule blocks the merge.

:::note Audience
Engineers maintaining a GitHub repository that uses GitHub Actions. You need an API key for the portal — see [API keys](../admin-guide/api-keys.md).
:::

:::note Action source
Use the in-repo composite action at `actions/scan/action.yml` directly via `uses: trustedoss/trusca/actions/scan@v0.10.0` (referenced from this monorepo). A standalone Marketplace publication is on the roadmap.
:::

## Before you begin

Three things must exist before the workflow below can run:

- **A portal the runner can reach.** GitHub-hosted runners cannot reach
  `http://localhost:5173` — the [Quickstart](../quickstart.md) demo stack on
  your laptop is not enough. You need a TRUSCA deployment with a
  network-reachable URL (see
  [Install with Docker Compose](../installation/docker-compose.md)); that URL
  becomes `api-url`. Self-hosted runners inside the same network can of course
  use an internal URL.
- **An API key**, issued in the portal under **/integrations → API keys** —
  Setup step 1 below walks through it.
- **The project id** of the portal project this repository maps to, from
  **Project Settings → CI/CD** — Setup step 3 below shows where.

## Quick start

<!-- docs-uat: id=gha-quickstart-workflow kind=manual tier=manual -->
```yaml
# .github/workflows/sca.yml
name: TRUSCA SCA
on:
  pull_request:
  push:
    branches: [main]

jobs:
  sca:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write    # required for PR comments
    steps:
      - uses: actions/checkout@v4
      - name: TRUSCA SCA scan
        uses: trustedoss/trusca/actions/scan@v0.10.0
        with:
          api-url: https://trustedoss.example.com
          api-key: ${{ secrets.TRUSTEDOSS_API_KEY }}
          project-id: ${{ vars.TRUSTEDOSS_PROJECT_ID }}
```

That's the minimum. The action:

1. Calls `POST /v1/projects/{project-id}/scans` with `kind=source` to enqueue cdxgen + scancode + Trivy.
2. Polls `GET /v1/scans/{scan-id}` every 30 seconds until terminal (`succeeded` / `failed` / `cancelled`), with a 30-minute timeout.
3. Calls `GET /v1/projects/{project-id}/gate-result` and writes the verdict into the workflow's job summary.
4. On `pull_request` events, calls `POST /v1/scans/{scan-id}/post-pr-comment` so the SCA Markdown report shows up as a PR comment.
5. Exits 1 if the gate verdict is `fail`.

## Setup

### 1. Generate an API key

In the portal: **/integrations → API keys → New API key**. Pick scope `project` and bind it to the project CI will scan (or `team` if you intend one key to cover every project owned by a team). API keys inherit the issuing user's role in this release — there is no per-key allowed-actions list. See [API keys](../admin-guide/api-keys.md) for the scope model.

### 2. Store the key in GitHub

In your repo: **Settings → Secrets and variables → Actions → New repository secret**.

- Name: `TRUSTEDOSS_API_KEY`
- Value: the full key (`tos_<prefix>_<secret>`)

### 3. Store the project ID as a variable

In the same screen, switch to **Variables** and add:

- Name: `TRUSTEDOSS_PROJECT_ID`
- Value: the UUID from **Project Settings → CI/CD**.

Variables (not secrets) keep the project ID readable in workflow logs — it is not sensitive.

### 4. Add the workflow

Drop `.github/workflows/sca.yml` (above) into the repo. On the next PR, the SCA check appears as a PR status.

## Inputs

| Name | Required | Default | Description |
|---|---|---|---|
| `api-url` | yes | — | Portal base URL, e.g. `https://trustedoss.example.com`. Trailing slash OK. |
| `api-key` | yes | — | API key. **Always** supply via `${{ secrets.* }}`. |
| `project-id` | yes | — | Project UUID. |
| `scan-kind` | no | `source` | `source` (cdxgen + scancode + Trivy) or `container` (Trivy image scan). |
| `fail-on-gate` | no | `true` | If `true`, the job exits 1 when the gate verdict is `fail`. |
| `post-pr-comment` | no | `true` | If `true` (and the workflow was triggered by `pull_request`), posts the SCA report as a PR comment. |
| `poll-timeout-seconds` | no | `1800` | Max seconds to wait for the scan to reach a terminal state. |
| `poll-interval-seconds` | no | `30` | Seconds between scan-status polls. |

## Outputs

| Name | Description |
|---|---|
| `scan-id` | UUID of the scan that was enqueued and evaluated. |
| `gate` | `pass` or `fail`. |
| `reason` | Human-readable reason when `gate == 'fail'`; empty otherwise. |
| `critical-cve-count` | Open critical-severity findings on the evaluated scan. |
| `forbidden-license-count` | Distinct components carrying a forbidden-classification license. |
| `epss-gate-count` | Open findings whose EPSS score met or exceeded the configured EPSS threshold. `0` when the EPSS gate is disabled (the default). See [Gate the build on EPSS](#gate-the-build-on-epss-optional). |

Use them in subsequent steps:

```yaml
- name: TRUSCA SCA scan
  id: sca
  uses: trustedoss/trusca/actions/scan@v0.10.0
  with:
    api-url: https://trustedoss.example.com
    api-key: ${{ secrets.TRUSTEDOSS_API_KEY }}
    project-id: ${{ vars.TRUSTEDOSS_PROJECT_ID }}
    fail-on-gate: 'false'    # collect, don't fail
- name: Branch on the gate verdict
  if: steps.sca.outputs.gate == 'fail'
  run: |
    echo "Critical CVEs: ${{ steps.sca.outputs.critical-cve-count }}"
    echo "Forbidden licenses: ${{ steps.sca.outputs.forbidden-license-count }}"
    exit 1
```

## Recipes

### Advisory mode (don't fail, just report)

Useful while you are seeding policies and don't want to block PRs yet:

```yaml
- uses: trustedoss/trusca/actions/scan@v0.10.0
  with:
    api-url: https://trustedoss.example.com
    api-key: ${{ secrets.TRUSTEDOSS_API_KEY }}
    project-id: ${{ vars.TRUSTEDOSS_PROJECT_ID }}
    fail-on-gate: 'false'
```

The PR comment still posts; the check stays green.

### Container scan

```yaml
- uses: trustedoss/trusca/actions/scan@v0.10.0
  with:
    api-url: https://trustedoss.example.com
    api-key: ${{ secrets.TRUSTEDOSS_API_KEY }}
    project-id: ${{ vars.TRUSTEDOSS_PROJECT_ID }}
    scan-kind: container
```

Container scans run Trivy on the image's OS packages. The action does not carry an image-reference input today, so the portal applies its default image resolution for the project; to scan a specific image reference (`name:tag`), trigger the scan from the UI (**Container** in the [scan dialog](../user-guide/scans.md#scan-a-container-image)) or call the API directly with `metadata.image_ref` (see [Scans → From the API](../user-guide/scans.md#from-the-api)). An `image-ref` action input is on the roadmap.

### Both source and container

Run two steps with different `id`s:

```yaml
- name: SCA — source
  uses: trustedoss/trusca/actions/scan@v0.10.0
  with:
    api-url: https://trustedoss.example.com
    api-key: ${{ secrets.TRUSTEDOSS_API_KEY }}
    project-id: ${{ vars.TRUSTEDOSS_PROJECT_ID }}
    scan-kind: source

- name: SCA — container
  uses: trustedoss/trusca/actions/scan@v0.10.0
  with:
    api-url: https://trustedoss.example.com
    api-key: ${{ secrets.TRUSTEDOSS_API_KEY }}
    project-id: ${{ vars.TRUSTEDOSS_PROJECT_ID }}
    scan-kind: container
```

Either step failing fails the job by default.

### Gate by branch

Apply the gate only on `main`, advisory on PRs:

```yaml
- uses: trustedoss/trusca/actions/scan@v0.10.0
  with:
    api-url: https://trustedoss.example.com
    api-key: ${{ secrets.TRUSTEDOSS_API_KEY }}
    project-id: ${{ vars.TRUSTEDOSS_PROJECT_ID }}
    fail-on-gate: ${{ github.event_name == 'push' && github.ref == 'refs/heads/main' && 'true' || 'false' }}
```

### Gate the build on EPSS (optional)

The build gate evaluates Critical CVEs and forbidden licenses by default. You can add an EPSS dimension so a CVE with a high predicted exploitation probability fails the build **even when it is not Critical** — useful for catching the small set of findings most likely to be attacked.

This is an **operator-side, org-wide** switch, not a workflow input: set the `GATE_EPSS_THRESHOLD` environment variable on the **portal** (`.env`), then restart the backend. It is **disabled by default** — leaving it unset preserves the existing Critical-CVE / forbidden-license gate exactly as before.

<!-- docs-uat: id=gha-epss-threshold-env kind=shell ctx=host tier=manual waiver=env-config-snippet-not-a-command -->
```bash
# In the portal's .env (not your CI workflow), a value from 0 to 1:
GATE_EPSS_THRESHOLD=0.5
```

With the threshold set, the gate also fails when any open finding has `epss_score >= GATE_EPSS_THRESHOLD`. The gate result then carries two extra fields, `epss_gate_count` (offending findings) and `epss_threshold` (the configured value), and the action exposes `epss-gate-count` as an [output](#outputs). Findings without an EPSS value never trip the gate (a missing score cannot satisfy `>=`). See [`GATE_EPSS_THRESHOLD`](../reference/env-variables.md#build--policy-gate) for the full reference and [EPSS — exploitation probability](../user-guide/vulnerabilities.md#epss--exploitation-probability) for the concept.

### Pin to a tag

The `@v1` tag floats. Pin to a specific commit for reproducibility:

```yaml
- uses: trustedoss/trusca/actions/scan@a1b2c3d4e5f6     # v0.10.0
```

## How the ref becomes a retention key

The action automatically forwards the workflow's ref as scan metadata: `github.ref` (`refs/heads/<branch>`) on a push, or the PR number (`refs/pull/<n>/merge`) on a `pull_request` event. The portal normalizes that ref — `refs/heads/main` → `main`, `refs/pull/12/merge` → `pr-12` — and uses `(project, normalized ref)` as the **retention key**: the latest successful scan for a key stays live and supersedes the previous one.

You do not configure anything for this — running the action on `push` and `pull_request` gives correct per-branch and per-PR grouping out of the box. To keep a scan permanently (for a tagged release), trigger it with a `metadata.release` label; the [Scan retention](../admin-guide/scan-retention.md) page covers the full model and the release exemption.

## How the PR comment is posted

The PR comment is posted **server-side by the portal**, not by your workflow. After the action uploads the SCA results, the portal evaluates the build gate and — if comment posting is enabled — calls `https://api.github.com` directly using a GitHub PAT stored in the portal's environment (`GITHUB_TOKEN` or `TRUSTEDOSS_GITHUB_TOKEN`). Your workflow never forwards `secrets.GITHUB_TOKEN` to the portal. A first-class GitHub App with portal-stored installation tokens is on the roadmap.

The comment is **idempotent**: re-running the workflow on the same PR updates the existing comment in place. The marker `<!-- trustedoss-sca -->` identifies it.

## Branch protection

To enforce SCA on every PR:

1. **Settings → Branches → Branch protection rules → Add rule**.
2. Branch name pattern: `main`.
3. Check **Require status checks to pass before merging**.
4. Search and check `sca` (the job name from the workflow above).
5. Save.

Now PRs cannot merge while the SCA check is pending or failing.

## Troubleshooting

### Job times out at "Polling scan status"

Either the worker is overwhelmed (raise `poll-timeout-seconds`) or the scan genuinely hangs. Open the portal's scan in the UI for the live log.

### `403 Forbidden` from the action

The API key's scope does not cover the project it is calling. Re-issue the key with scope `project` (preferred) bound to that project, or scope `team` if it must reach every project owned by a team. Verify the project belongs to the scope-bound team. See [API keys](../admin-guide/api-keys.md).

### PR comment did not appear

Three possibilities:

- The workflow was triggered by `push`, not `pull_request` — only PR events get a comment.
- The portal's `GITHUB_TOKEN` / `TRUSTEDOSS_GITHUB_TOKEN` env is unset, expired, or lacks the `pull-requests: write` permission for the target repo. Operators rotate / extend the PAT in the portal `.env` and bounce the backend.
- The portal could not resolve the PR number from the head SHA. Check the action's log output for `pull_request_number=` — empty means the lookup failed.

### Need to skip on a chore PR

Use a path filter so the workflow does not run when only docs change:

```yaml
on:
  pull_request:
    paths-ignore:
      - 'docs/**'
      - '*.md'
```

## See also

- [GitLab CI](./gitlab-ci.md)
- [Jenkins](./jenkins.md)
- [Webhooks](./webhooks.md) — for non-Action push automation
- [API keys](../admin-guide/api-keys.md)
- [Scan retention](../admin-guide/scan-retention.md) — how per-branch / per-PR scans are kept and reclaimed
