---
id: github-actions
title: GitHub Actions
description: Wire TrustedOSS Portal into a GitHub Actions workflow with the trustedoss/scan-action composite action — trigger, poll, gate, comment.
sidebar_label: GitHub Actions
sidebar_position: 1
---

# GitHub Actions

The `trustedoss/scan-action` composite action triggers a TrustedOSS scan, waits for it to finish, evaluates the build gate, and (on pull requests) posts the SCA report back to the PR. It exits non-zero when the gate fails so the PR check turns red and your branch-protection rule blocks the merge.

:::note Audience
Engineers maintaining a GitHub repository that uses GitHub Actions. You need an API key for the portal — see [API keys](../admin-guide/api-keys.md).
:::

## Quick start

```yaml
# .github/workflows/sca.yml
name: TrustedOSS SCA
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
      - name: TrustedOSS SCA scan
        uses: trustedoss/scan-action@v1
        with:
          api-url: https://trustedoss.example.com
          api-key: ${{ secrets.TRUSTEDOSS_API_KEY }}
          project-id: ${{ vars.TRUSTEDOSS_PROJECT_ID }}
```

That's the minimum. The action:

1. Calls `POST /v1/projects/{project-id}/scans` with `kind=source` to enqueue cdxgen + ORT + Dependency-Track.
2. Polls `GET /v1/scans/{scan-id}` every 30 seconds until terminal (`succeeded` / `failed` / `cancelled`), with a 30-minute timeout.
3. Calls `GET /v1/projects/{project-id}/gate-result` and writes the verdict into the workflow's job summary.
4. On `pull_request` events, calls `POST /v1/scans/{scan-id}/post-pr-comment` so the SCA Markdown report shows up as a PR comment.
5. Exits 1 if the gate verdict is `fail`.

## Setup

### 1. Generate an API key

In the portal: **Project Settings → CI/CD → API keys → New API key**. Allowed actions: `scan:trigger`, `scan:read`, `report:download`. See [API keys](../admin-guide/api-keys.md).

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
| `scan-kind` | no | `source` | `source` (cdxgen + ORT + DT) or `container` (Trivy). |
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

Use them in subsequent steps:

```yaml
- name: TrustedOSS SCA scan
  id: sca
  uses: trustedoss/scan-action@v1
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
- uses: trustedoss/scan-action@v1
  with:
    api-url: https://trustedoss.example.com
    api-key: ${{ secrets.TRUSTEDOSS_API_KEY }}
    project-id: ${{ vars.TRUSTEDOSS_PROJECT_ID }}
    fail-on-gate: 'false'
```

The PR comment still posts; the check stays green.

### Container scan

```yaml
- uses: trustedoss/scan-action@v1
  with:
    api-url: https://trustedoss.example.com
    api-key: ${{ secrets.TRUSTEDOSS_API_KEY }}
    project-id: ${{ vars.TRUSTEDOSS_PROJECT_ID }}
    scan-kind: container
```

The portal's project metadata determines which image is scanned (`container_image` field). Container scans run Trivy on the OS layer.

### Both source and container

Run two steps with different `id`s:

```yaml
- name: SCA — source
  uses: trustedoss/scan-action@v1
  with:
    api-url: https://trustedoss.example.com
    api-key: ${{ secrets.TRUSTEDOSS_API_KEY }}
    project-id: ${{ vars.TRUSTEDOSS_PROJECT_ID }}
    scan-kind: source

- name: SCA — container
  uses: trustedoss/scan-action@v1
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
- uses: trustedoss/scan-action@v1
  with:
    api-url: https://trustedoss.example.com
    api-key: ${{ secrets.TRUSTEDOSS_API_KEY }}
    project-id: ${{ vars.TRUSTEDOSS_PROJECT_ID }}
    fail-on-gate: ${{ github.event_name == 'push' && github.ref == 'refs/heads/main' && 'true' || 'false' }}
```

### Pin to a tag

The `@v1` tag floats. Pin to a specific commit for reproducibility:

```yaml
- uses: trustedoss/scan-action@a1b2c3d4e5f6     # v1.2.3
```

## How the PR comment is posted

The action posts via the portal's `POST /v1/scans/{id}/post-pr-comment` endpoint, which uses the portal-side GitHub App / PAT. The action **does not** consume your workflow's `GITHUB_TOKEN` for posting — that simplifies permissions.

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

The API key is valid but does not have the required action allowed. Re-issue the key with `scan:trigger`, `scan:read`, `report:download`. See [API keys](../admin-guide/api-keys.md).

### PR comment did not appear

Three possibilities:

- The workflow was triggered by `push`, not `pull_request` — only PR events get a comment.
- The portal's GitHub App is not installed on the repo. Ask your portal admin to install or extend the App's repo list.
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
