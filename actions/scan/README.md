# TrustedOSS SCA Scan — GitHub Action

Composite GitHub Action that triggers a TrustedOSS SCA scan, waits for it to
finish, evaluates the build gate, and (on pull requests) posts the SCA report
back to the PR.

It exits non-zero when the gate fails, so the PR check turns red and the
branch protection rule blocks the merge — the same behaviour every commercial
SCA tool ships, on Apache-2.0 self-hosted infra.

---

## Quick start

```yaml
name: TrustedOSS SCA
on:
  pull_request:
  push:
    branches: [main]

jobs:
  sca:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: TrustedOSS SCA scan
        uses: trustedoss/scan-action@v1
        with:
          api-url: https://trustedoss.example.com
          api-key: ${{ secrets.TRUSTEDOSS_API_KEY }}
          project-id: ${{ vars.TRUSTEDOSS_PROJECT_ID }}
```

That's it. The action:

1. Calls `POST /v1/projects/{project-id}/scans` with `kind=source` to enqueue
   the cdxgen + Trivy pipeline.
2. Polls `GET /v1/scans/{scan-id}` every 30 seconds until the scan reaches
   `succeeded`, `failed`, or `cancelled` (timeout: 30 min).
3. Calls `GET /v1/projects/{project-id}/gate-result` and writes the verdict
   into the workflow's job summary.
4. On `pull_request` events, calls `POST /v1/scans/{scan-id}/post-pr-comment`
   so the SCA Markdown report shows up as a PR comment.
5. Exits 1 if the gate verdict is `fail`.

---

## Inputs

| Name | Required | Default | Description |
|---|---|---|---|
| `api-url` | yes | — | TrustedOSS Portal base URL, e.g. `https://trustedoss.example.com`. Trailing slash OK. |
| `api-key` | yes | — | API key with the `tos_<prefix>_<secret>` shape. **Always** supply via `${{ secrets.* }}`. |
| `project-id` | yes | — | TrustedOSS project UUID. Get it from the project's Settings tab → CI/CD. |
| `scan-kind` | no | `source` | `source` runs cdxgen + Trivy. `container` runs Trivy on the image referenced in project metadata. |
| `fail-on-gate` | no | `true` | If `true`, the job exits 1 when the gate verdict is `fail`. Set to `false` for advisory-only mode. |
| `post-pr-comment` | no | `true` | If `true` (and the workflow was triggered by `pull_request`), posts the SCA report as a PR comment. |
| `poll-timeout-seconds` | no | `1800` | Max seconds to wait for the scan to reach a terminal state. Real source scans typically finish in 1–10 min (cdxgen + Trivy). |
| `poll-interval-seconds` | no | `30` | Seconds between scan-status polls. |

## Outputs

| Name | Description |
|---|---|
| `scan-id` | UUID of the scan that was enqueued and evaluated. |
| `gate` | `pass` or `fail`. |
| `reason` | Human-readable reason when `gate == 'fail'`; empty string otherwise. |
| `critical-cve-count` | Open critical-severity findings on the evaluated scan. |
| `forbidden-license-count` | Distinct components with at least one forbidden-classification license. |

---

## Recipes

### Advisory mode (don't fail the build, just report)

Useful while you're seeding policies and don't want to block PRs yet.

```yaml
- uses: trustedoss/scan-action@v1
  with:
    api-url: https://trustedoss.example.com
    api-key: ${{ secrets.TRUSTEDOSS_API_KEY }}
    project-id: ${{ vars.TRUSTEDOSS_PROJECT_ID }}
    fail-on-gate: 'false'
```

### Container scan instead of source scan

```yaml
- uses: trustedoss/scan-action@v1
  with:
    api-url: https://trustedoss.example.com
    api-key: ${{ secrets.TRUSTEDOSS_API_KEY }}
    project-id: ${{ vars.TRUSTEDOSS_PROJECT_ID }}
    scan-kind: container
```

### Branch on the gate verdict in a follow-up step

```yaml
- id: sca
  uses: trustedoss/scan-action@v1
  with:
    api-url: https://trustedoss.example.com
    api-key: ${{ secrets.TRUSTEDOSS_API_KEY }}
    project-id: ${{ vars.TRUSTEDOSS_PROJECT_ID }}
    fail-on-gate: 'false'

- name: Notify Slack on gate failure
  if: steps.sca.outputs.gate == 'fail'
  run: |
    curl -X POST -H 'Content-Type: application/json' \
      -d "{\"text\":\"SCA gate failed: ${{ steps.sca.outputs.reason }}\"}" \
      ${{ secrets.SLACK_WEBHOOK_URL }}
```

---

## Setup

### 1. Create an API key

In the portal: **Settings → CI/CD → API Keys → New key**. Scope: `scan:run`,
`gate:read`, `pr-comment:post`. Copy the `tos_<prefix>_<secret>` token shown
once at creation time.

### 2. Store secrets in GitHub

In the repo settings → **Secrets and variables → Actions**:

- Secret `TRUSTEDOSS_API_KEY` → the token from step 1.
- Variable `TRUSTEDOSS_PROJECT_ID` → the UUID from the project's Settings tab.

(Project ID is not secret — using `vars.*` instead of `secrets.*` keeps it
visible in the workflow log for debugging.)

### 3. Pin the action version

The example above uses `@v1`, which floats with patch and minor releases. For
maximum reproducibility, pin a commit SHA:

```yaml
- uses: trustedoss/scan-action@<full-commit-sha>
```

---

## Required runner capabilities

- `bash` (default on `ubuntu-*`, `macos-*`).
- `curl` (default on hosted runners).
- `jq` (default on hosted runners; the action checks at startup and prints a
  fatal error with install hints if missing — relevant for self-hosted Alpine
  / `slim` containers).

The action does **not** require Docker.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `401 Authentication required` | `api-key` is empty, expired, or revoked. Check the key list in the portal. |
| `404 project ... not found` | `project-id` is wrong, or the API key's owner is not on the project's team. The portal returns 404 (not 403) on cross-team access by design — see CLAUDE.md "existence-hide pattern". |
| Polling times out at 30 min | The scan is taking longer than 30 min. Bump `poll-timeout-seconds`, or check the Admin → Scans dashboard for stuck workers. |
| `jq is required but was not found` | Self-hosted runner without jq. Add `apt-get install -y jq` (Debian/Ubuntu) or `apk add jq` (Alpine) to your runner image. |
| PR comment not appearing | The portal's GitHub App needs PR write permission on the repo, OR `GITHUB_TOKEN` / `TRUSTEDOSS_GITHUB_TOKEN` must be configured on the portal side. The action itself only calls the portal — it does not call api.github.com. |

---

## Security notes

- The API key is passed as `Authorization: Bearer ...` over HTTPS. It is
  never echoed to stdout/stderr — every `curl` invocation captures only the
  status code and body, not the request headers.
- The action does not log the response body except on non-2xx status, and
  the portal's RFC 7807 problem detail does not include the API key.
- The PR-comment step is non-fatal by design: a 5xx from GitHub.com should
  not turn a passing SCA gate into a failed CI check.

## License

Apache-2.0, same as the portal.
