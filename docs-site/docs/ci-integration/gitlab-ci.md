---
id: gitlab-ci
title: GitLab CI
description: Wire TRUSCA into GitLab CI with the include-able templates/gitlab-ci.yml — trigger, poll, gate, comment.
sidebar_label: GitLab CI
sidebar_position: 2
---

# GitLab CI

The portal ships an `include`-able GitLab CI template that mirrors the GitHub Action: it triggers a scan, polls until terminal, and evaluates the build gate. The template is a single job; you can extend or override any field.

:::warning GitLab MR comments — not yet shipped
The portal's PR-comment integration is GitHub-only in this release. The `templates/gitlab-ci.yml` MR-comment job stages a request, but the backend `services/sca_comment.py` only knows how to call `api.github.com` — calling it with a GitLab `repo_full_name` returns 404. Use the build-gate exit code on the GitLab side until the GitLab Notes API client lands.
:::

:::note Audience
Engineers maintaining a GitLab project that uses GitLab CI / CD. You need an API key for the portal — see [API keys](../admin-guide/api-keys.md).
:::

## Quick start

<!-- docs-uat: id=gitlab-quickstart-pipeline kind=manual tier=manual -->
```yaml
# .gitlab-ci.yml
include:
  - remote: 'https://raw.githubusercontent.com/trustedoss/trusca/v0.22.4/templates/gitlab-ci.yml'

variables:
  TRUSTEDOSS_API_URL: 'https://trustedoss.example.com'
  TRUSTEDOSS_PROJECT_ID: '01H7XYZ…'
  # TRUSTEDOSS_API_KEY is a masked CI/CD variable — never put it here.
```

The base template is hidden — extend it from one of your own jobs to materialize it; pipelines that don't extend the base do not auto-trigger SCA. Add a job such as:

```yaml
sca:
  extends: .trustedoss-sca
```

## Setup

### 1. Generate an API key

In the portal: **/integrations → API keys → Create API key**. Pick scope `project` and bind it to the project this pipeline scans (or `team` to cover every project a team owns). Set "What this key may do" to Read and write; this pipeline triggers a scan, which a read-only key (the default) is refused. API keys otherwise inherit the issuing user's role. See [API keys](../admin-guide/api-keys.md).

### 2. Store the key as a masked CI/CD variable

In your GitLab project: **Settings → CI/CD → Variables → Add variable**.

- Key: `TRUSTEDOSS_API_KEY`
- Value: the full key (`tos_<prefix>_<secret>`)
- Type: `Variable`
- Flags: **Masked** (yes), **Protected** (recommended for `main` only)

The masked flag prevents the key from appearing verbatim in job logs.

### 3. Set the URL and project ID

You can put `TRUSTEDOSS_API_URL` and `TRUSTEDOSS_PROJECT_ID` either:

- In `.gitlab-ci.yml` under `variables:` (visible to anyone with read access).
- Or as CI/CD variables (better if you maintain multiple environments).

Either way, only `TRUSTEDOSS_API_KEY` must be masked.

## Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `TRUSTEDOSS_API_URL` | yes | — | Portal base URL. |
| `TRUSTEDOSS_API_KEY` | yes | — | API key (masked CI/CD variable). |
| `TRUSTEDOSS_PROJECT_ID` | yes | — | Project UUID. |
| `TRUSTEDOSS_SCAN_KIND` | no | `source` | `source` or `container`. |
| `TRUSTEDOSS_FAIL_ON_GATE` | no | `true` | If `true`, job exits 1 on gate fail. |
| `TRUSTEDOSS_POLL_TIMEOUT` | no | `1800` | Max seconds to wait for terminal state. |
| `TRUSTEDOSS_POLL_INTERVAL` | no | `30` | Seconds between polls. |
| `TRUSTEDOSS_POST_MR_COMMENT` | no | `true` | Reserved for the upcoming GitLab Notes API integration. In this release the request stages but the backend cannot deliver — see the warning above. |

## Recipes

### Advisory mode

```yaml
include:
  - remote: 'https://raw.githubusercontent.com/trustedoss/trusca/v0.22.4/templates/gitlab-ci.yml'

variables:
  TRUSTEDOSS_API_URL: 'https://trustedoss.example.com'
  TRUSTEDOSS_PROJECT_ID: '01H7XYZ…'
  TRUSTEDOSS_FAIL_ON_GATE: 'false'
```

The job stays green; the MR note still posts.

### Run only on protected branches

Override the rules of the included job:

```yaml
include:
  - remote: 'https://raw.githubusercontent.com/trustedoss/trusca/v0.22.4/templates/gitlab-ci.yml'

.trustedoss-sca:
  rules:
    - if: '$CI_COMMIT_REF_PROTECTED == "true"'
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
```

### Container scan as a separate job

```yaml
include:
  - remote: 'https://raw.githubusercontent.com/trustedoss/trusca/v0.22.4/templates/gitlab-ci.yml'

trustedoss:scan-container:
  extends: .trustedoss-sca
  variables:
    TRUSTEDOSS_SCAN_KIND: 'container'
```

### Pin to a tag

Pin the `include` URL to a release tag (`v0.22.4`) instead of `main` for reproducible pipelines.

## Anatomy of the template (advanced)

If you need to copy and inline the job — for instance because your runner cannot reach GitHub for the `include` — here is the canonical shape:

```yaml
.trustedoss-sca:
  image: alpine:3.20
  stage: test
  before_script:
    - apk add --no-cache curl jq bash ca-certificates
  script:
    - bash -c '
        set -euo pipefail;
        SCAN_ID=$(curl -fsS -X POST
          -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}"
          -H "Content-Type: application/json"
          -d "{\"kind\": \"${TRUSTEDOSS_SCAN_KIND:-source}\"}"
          "${TRUSTEDOSS_API_URL}/v1/projects/${TRUSTEDOSS_PROJECT_ID}/scans"
          | jq -r .id);
        echo "scan_id=$SCAN_ID";
        # Poll until terminal …
        # Evaluate gate, post MR note, exit 0/1
      '
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
    - if: '$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH'
```

The full canonical version lives at [`templates/gitlab-ci.yml`](https://github.com/trustedoss/trusca/blob/main/templates/gitlab-ci.yml). Read it before forking — it handles edge cases (network blip during poll, masked-token rotation) you do not want to re-implement.

## How the ref becomes a retention key

The template forwards the pipeline's ref as scan metadata: `CI_COMMIT_REF_NAME`, which is the branch name on a branch pipeline and the *source branch* name on a `merge_request_event`. The portal normalizes that ref — `refs/heads/main` → `main`, `refs/merge-requests/7/head` → `mr-7`, a bare name passes through — and uses `(project, normalized ref)` as the **retention key**: the latest successful scan for a key stays live and supersedes the previous one.

That ref is also what the worker checks out, so the scan sees the code on that branch rather than the repository's default HEAD. If it has disappeared by the time a worker starts — the branch deleted after a merge, say — the scan falls back to the default branch and records `metadata.ref_fallback` with the ref it wanted.

The template sends the MR IID too, as `metadata.merge_request_iid`, but that is recorded for reference only — it does not become the retention key. An MR's scans therefore group under its source branch name rather than `mr-<iid>`. If you would rather key on the MR, send `refs/merge-requests/$CI_MERGE_REQUEST_IID/head` as the ref in both the trigger and the gate query. That form also makes the worker check out the merge result rather than the source branch tip.

The same ref goes to the gate query, and that is what keeps an MR's verdict its own. Without it the portal answers with the newest succeeded scan of the project's **main line**, so an MR pipeline that just waited out its own scan would be judged by `main`.

No configuration is needed — running the template on branches and MRs gives correct per-branch and per-MR grouping. To keep a scan permanently (for a tagged release), trigger it with a `metadata.release` label; the [Scan retention](../admin-guide/scan-retention.md) page covers the full model and the release exemption.

## Branch / merge protection

To enforce SCA on every MR:

1. **Settings → Repository → Protected branches** — protect `main`.
2. **Settings → Merge requests → Merge checks** — toggle "Pipelines must succeed".

MRs whose SCA job (the one extending `.trustedoss-sca`) is failing cannot be merged.

## Troubleshooting

### `Authorization` header is missing in the included job

GitLab strips empty variables. Confirm `TRUSTEDOSS_API_KEY` is defined for the relevant environment / branch. The variable's "Protected" flag means it is only injected on protected refs — adjust if you also want it on regular MRs.

### MR note is not posted

This is expected in this release — the GitLab Notes API client has not shipped (see the warning at the top of the page). Use the build-gate exit code (`TRUSTEDOSS_FAIL_ON_GATE=true`) on the GitLab side to surface the verdict.

### Job runs out of time at the polling step

`TRUSTEDOSS_POLL_TIMEOUT` defaults to 30 minutes — large repos can exceed that. Raise to 3600 (1 hour) and re-run.

### "Forbidden" on `POST /scans`

Keys carry no per-action permissions, so this is never about a missing `scan:trigger`. It means one of two things: the issuing user is not a member of the project's team, or the key is scoped to a *different* project than `TRUSTEDOSS_PROJECT_ID`. Check the key's scope binding on **/integrations → API keys**.

Note that `GET /gate-result` answers a cross-project probe with 404 rather than 403 — it hides whether the project exists at all. The two endpoints differ on purpose.

## See also

- [GitHub Actions](./github-actions.md)
- [Jenkins](./jenkins.md)
- [Webhooks](./webhooks.md)
- [API keys](../admin-guide/api-keys.md)
- [Scan retention](../admin-guide/scan-retention.md) — how per-branch / per-MR scans are kept and reclaimed
