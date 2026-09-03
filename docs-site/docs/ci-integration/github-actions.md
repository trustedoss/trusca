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
Use the in-repo composite action at `actions/scan/action.yml` directly via `uses: trustedoss/trusca/actions/scan@v0.22.4` (referenced from this monorepo). A standalone Marketplace publication is on the roadmap.
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
- **The project id** of the portal project this repository maps to — the last
  path segment of its portal URL. Setup step 3 below shows where.

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
      contents: read          # the action needs nothing beyond checkout
    steps:
      - uses: actions/checkout@v4
      - name: TRUSCA SCA scan
        uses: trustedoss/trusca/actions/scan@v0.22.4
        with:
          api-url: https://trustedoss.example.com
          api-key: ${{ secrets.TRUSTEDOSS_API_KEY }}
          project-id: ${{ vars.TRUSTEDOSS_PROJECT_ID }}
```

That's the minimum. The action:

1. Calls `POST /v1/projects/{project-id}/scans` with `kind=source` to enqueue cdxgen + scancode + Trivy.
2. Polls `GET /v1/scans/{scan-id}` every 30 seconds until terminal (`succeeded` / `failed` / `cancelled`), with a 30-minute timeout.
3. Calls `GET /v1/projects/{project-id}/gate-result?ref=<github.ref>` and writes the verdict into the workflow's job summary.
4. On `pull_request` events, calls `POST /v1/scans/{scan-id}/post-pr-comment` so the SCA Markdown report shows up as a PR comment.
5. Exits 1 if the gate verdict is `fail`.

The `ref` in step 3 is what keeps a pull request's verdict its own. Ask for a project's gate result without one and the portal answers with the newest succeeded scan of its **main line**, so a PR build that just waited out its own scan would be judged by `main` — blocked by a critical CVE it never introduced, or passed while carrying one of its own. The action sends the same `github.ref` it sent when triggering, and the portal normalizes both ends the same way (`refs/pull/12/merge` and `pr-12` are one key), so the verdict always describes the code that was scanned.

A ref with no succeeded scan yields the no-signal `pass` rather than another branch's findings. That is deliberate: naming a branch means that branch or nothing.

## Setup

### 1. Generate an API key

In the portal: **/integrations → API keys → Create API key**. Pick scope `project` and bind it to the project CI will scan (or `team` if you intend one key to cover every project owned by a team). Set "What this key may do" to Read and write; this action triggers a scan, which a read-only key (the default) is refused. API keys otherwise inherit the issuing user's role. See [API keys](../admin-guide/api-keys.md) for the scope and permission-breadth model.

### 2. Store the key in GitHub

In your repo: **Settings → Secrets and variables → Actions → New repository secret**.

- Name: `TRUSTEDOSS_API_KEY`
- Value: the full key (`tos_<prefix>_<secret>`)

### 3. Store the project ID as a variable

In the same screen, switch to **Variables** and add:

- Name: `TRUSTEDOSS_PROJECT_ID`
- Value: the project's UUID — the last path segment of its portal URL (`/projects/<uuid>`).

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
| `image-ref` | when `scan-kind: container` | — | Image the portal pulls, e.g. `ghcr.io/acme/api:1.4.0`. Push it before this step runs. |
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
| `malicious-component-count` | Distinct components the malicious-package snapshot flags. These block regardless of severity — remove the package and rotate any credentials the build could reach. |
| `epss-outcome` | What the EPSS axis of the gate was able to judge. `not_configured`: no threshold set, so the axis was off by choice. `evaluated`: every open finding carried an EPSS score, so `epss-gate-count` is a complete answer. `partial`: some open findings carried no score, so a `0` there is not proof nothing would have tripped the threshold. `no_data`: not one open finding carried a score, so the axis decided nothing at all and the pass is the absence of a verdict. Empty on a portal too old to report it. |
| `component-outcome` | What the scan's SBOM ended up containing. `components_found` is the ordinary case. `empty_no_manifests` and `empty_with_manifests` both mean the scan produced no components, so a passing gate reflects the absence of anything to judge rather than a clean result: the first is expected for a build system TRUSCA does not read, the second points at a scan failure. Empty on a portal too old to report it. |

Use them in subsequent steps:

```yaml
- name: TRUSCA SCA scan
  id: sca
  uses: trustedoss/trusca/actions/scan@v0.22.4
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
- uses: trustedoss/trusca/actions/scan@v0.22.4
  with:
    api-url: https://trustedoss.example.com
    api-key: ${{ secrets.TRUSTEDOSS_API_KEY }}
    project-id: ${{ vars.TRUSTEDOSS_PROJECT_ID }}
    fail-on-gate: 'false'
```

The PR comment still posts; the check stays green.

### Container scan

```yaml
- uses: trustedoss/trusca/actions/scan@v0.22.4
  with:
    api-url: https://trustedoss.example.com
    api-key: ${{ secrets.TRUSTEDOSS_API_KEY }}
    project-id: ${{ vars.TRUSTEDOSS_PROJECT_ID }}
    scan-kind: container
    image-ref: ghcr.io/acme/api:${{ github.sha }}
```

Container scans run Trivy on the image's OS packages. `image-ref` is required — the portal has no default image for a project, and a container scan without one is rejected at trigger time rather than failing later in a worker.

The portal pulls the image itself, so push it before this step runs. A tag that exists only in the runner's local Docker daemon is not reachable, and a private registry needs credentials configured on the portal rather than on the runner.

### Both source and container

Run two steps with different `id`s:

```yaml
- name: SCA — source
  uses: trustedoss/trusca/actions/scan@v0.22.4
  with:
    api-url: https://trustedoss.example.com
    api-key: ${{ secrets.TRUSTEDOSS_API_KEY }}
    project-id: ${{ vars.TRUSTEDOSS_PROJECT_ID }}
    scan-kind: source

- name: SCA — container
  uses: trustedoss/trusca/actions/scan@v0.22.4
  with:
    api-url: https://trustedoss.example.com
    api-key: ${{ secrets.TRUSTEDOSS_API_KEY }}
    project-id: ${{ vars.TRUSTEDOSS_PROJECT_ID }}
    scan-kind: container
    image-ref: ghcr.io/acme/api:${{ github.sha }}
```

Either step failing fails the job by default.

Both steps scan the same portal project, and the portal allows one active scan per `(project, ref)` — so run them in sequence, as above, rather than as parallel jobs. Run in parallel, the second would attach to the first instead of starting its own.

### Gate by branch

Apply the gate only on `main`, advisory on PRs:

```yaml
- uses: trustedoss/trusca/actions/scan@v0.22.4
  with:
    api-url: https://trustedoss.example.com
    api-key: ${{ secrets.TRUSTEDOSS_API_KEY }}
    project-id: ${{ vars.TRUSTEDOSS_PROJECT_ID }}
    fail-on-gate: ${{ github.event_name == 'push' && github.ref == 'refs/heads/main' && 'true' || 'false' }}
```

### Gate the build on EPSS (optional)

The build gate evaluates Critical CVEs and forbidden licenses by default. You can add an EPSS dimension so a CVE with a high predicted exploitation probability fails the build **even when it is not Critical** — useful for catching the small set of findings most likely to be attacked.

This is an operator-side, org-wide switch, not a workflow input: set the `GATE_EPSS_THRESHOLD` environment variable on the portal (`.env`), then restart the backend. It is disabled by default; leaving it unset preserves the existing Critical-CVE / forbidden-license gate exactly as before.

<!-- docs-uat: id=gha-epss-threshold-env kind=shell ctx=host tier=manual waiver=env-config-snippet-not-a-command -->
```bash
# In the portal's .env (not your CI workflow), a value from 0 to 1:
GATE_EPSS_THRESHOLD=0.5
```

With the threshold set, the gate also fails when any open finding has `epss_score >= GATE_EPSS_THRESHOLD`. The gate result then carries two extra fields, `epss_gate_count` (offending findings) and `epss_threshold` (the configured value), and the action exposes `epss-gate-count` as an [output](#outputs). Findings without an EPSS value never trip the gate (a missing score cannot satisfy `>=`). See [`GATE_EPSS_THRESHOLD`](../reference/env-variables.md#build--policy-gate) for the full reference and [EPSS — exploitation probability](../user-guide/vulnerabilities.md#epss--exploitation-probability) for the concept.

#### When the threshold cannot be evaluated

`epss_gate_count` is `0` both when nothing scored above the threshold and when
nothing was scored at all, and those are not the same result. EPSS values are
filled by a daily sync that is **off by default**
(`EPSS_REFRESH_ENABLED`), so on a deployment that has never turned it on, or
one whose mirror is unreachable, every open finding has a NULL score and the
threshold you configured decides nothing. Before this was reported, that read
as a passing build.

The `epss-outcome` [output](#outputs) says which case you are in, and the
action writes a job-summary row and a warning annotation for `no_data` and
`partial`. `GATE_EPSS_ON_MISSING_DATA` on the portal decides what the verdict
should be:

<!-- docs-uat: id=gha-epss-on-missing-data kind=shell ctx=host tier=manual waiver=env-config-snippet-not-a-command -->
```bash
# Default. An undecided EPSS axis lets the build through, which is what
# every deployment did before this option existed.
GATE_EPSS_ON_MISSING_DATA=allow

# Fail the build instead, so a configured threshold cannot be ignored.
GATE_EPSS_ON_MISSING_DATA=block
```

`block` applies to `no_data` only. It deliberately does not fire on `partial`,
because EPSS does not score every CVE and gaps are normal even with a healthy
sync: an option that fires on a normal state is one nobody can leave switched
on, and a control that has been switched off protects nothing.

### Pin to a tag

A release tag names one commit today, but a tag can be moved or deleted.
Pin to the commit itself for reproducibility:

```yaml
- uses: trustedoss/trusca/actions/scan@176bc3f0632bf0cf209c443da308e3d863dfde44  # v0.22.4
```

## What the ref does {#how-the-ref-becomes-a-retention-key}

The action automatically forwards the workflow's ref as scan metadata: `github.ref` (`refs/heads/<branch>`) on a push, or the PR number (`refs/pull/<n>/merge`) on a `pull_request` event. That value does two jobs.

The portal checks it out. The worker fetches the ref you sent and scans that tree, so a pull request's scan sees the pull request's dependencies. If the ref has disappeared by the time a worker picks the scan up — merged or force-pushed while it queued — the scan falls back to the default branch and records `metadata.ref_fallback` saying so.

It is also the retention key. The portal normalizes the ref — `refs/heads/main` → `main`, `refs/pull/12/merge` → `pr-12` — and uses `(project, normalized ref)` to group scans: the latest successful scan for a key stays live and supersedes the previous one.

You do not configure anything for this — running the action on `push` and `pull_request` gives correct per-branch and per-PR grouping out of the box. To keep a scan permanently (for a tagged release), trigger it with a `metadata.release` label; the [Scan retention](../admin-guide/scan-retention.md) page covers the full model and the release exemption.

## How the PR comment is posted

The PR comment is posted **server-side by the portal**, not by your workflow. After the action uploads the SCA results, the portal evaluates the build gate and — if comment posting is enabled — calls `https://api.github.com` directly using a GitHub PAT stored in the portal's environment (`GITHUB_TOKEN` or `TRUSTEDOSS_GITHUB_TOKEN`). Your workflow never forwards `secrets.GITHUB_TOKEN` to the portal. A first-class GitHub App with portal-stored installation tokens is on the roadmap.

The comment is idempotent: re-running the workflow on the same PR updates the existing comment in place. The portal finds it by the marker `<!-- trustedoss-sca-bot -->` in the comment body. It scans the PR's most recent 500 comments looking for that marker, so on a thread longer than that it posts a new comment instead of updating.

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

Timing out stops the *waiting*, not the scan — the portal keeps running it. Re-running the workflow right away therefore attaches to that same scan rather than starting a second one, and finishes as soon as it does.

### "Attaching to the scan already running for this ref"

Not an error. The portal permits one active scan per `(project, ref)`, so when a run finds its ref already busy it waits on the existing scan instead of failing.

The usual cause is the workflow's own previous run. `cancel-in-progress: true` cancels the *runner*, but the scan it triggered keeps going server-side, so the replacement run collides with a scan nobody is watching. Attaching turns that into a normal wait.

If the scan it attached to was triggered from a different commit on the same branch, its verdict describes that commit. Re-run once it finishes to grade the newer one.

### Trigger is rate-limited (`429`)

The action honours `Retry-After` and retries up to four times before failing. One API key shared across many repositories is the usual cause — the limit is per key, so give each repository its own.

### `403 Forbidden` from the action

The API key's scope does not cover the project it is calling. Re-issue the key with scope `project` (preferred) bound to that project, or scope `team` if it must reach every project owned by a team. Verify the project belongs to the scope-bound team. See [API keys](../admin-guide/api-keys.md).

### PR comment did not appear

Three possibilities:

- The workflow was triggered by `push`, not `pull_request` — only PR events get a comment.
- The portal's `GITHUB_TOKEN` / `TRUSTEDOSS_GITHUB_TOKEN` env is unset, expired, or lacks write access to the target repo's pull requests. The comment is posted by the portal with its own credentials, not by the workflow's `GITHUB_TOKEN`, so granting the job more permission changes nothing. Operators rotate or extend the token in the portal `.env` and restart the backend.
- The PR number reaching the portal was wrong or absent. The action reads it from `github.event.pull_request.number`, which is empty outside `pull_request` events.

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
