---
id: gitlab-ci
title: GitLab CI
description: Wire TrustedOSS Portal into GitLab CI with the include-able templates/gitlab-ci.yml — trigger, poll, gate, comment.
sidebar_label: GitLab CI
sidebar_position: 2
---

# GitLab CI

The portal ships an `include`-able GitLab CI template that mirrors the GitHub Action: it triggers a scan, polls until terminal, and evaluates the build gate. The template is a single job; you can extend or override any field.

:::note Audience
Engineers maintaining a GitLab project that uses GitLab CI / CD. You need an API key for the portal — see [API keys](../admin-guide/api-keys.md).
:::

:::warning GitLab MR comments — not yet shipped
The portal's PR-comment integration is GitHub-only at v2.0.0. The
`templates/gitlab-ci.yml` MR-comment job stages a request, but the
backend `services/sca_comment.py` only knows how to call
`api.github.com` — calling it with a GitLab `repo_full_name` returns
404. Use the build-gate exit code on the GitLab side until the
GitLab Notes API client lands.
:::

## Quick start

```yaml
# .gitlab-ci.yml
include:
  - remote: 'https://raw.githubusercontent.com/trustedoss/trustedoss-portal/v2.0.0/templates/gitlab-ci.yml'

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

In the portal: **Project Settings → CI/CD → API keys → New API key**.

API keys carry a single `scope` (`org`, `team`, or `project`). There is no per-action allowlist at v2.0.0; any caller authenticated with a key in the right scope can hit any endpoint that accepts an api-key. Per-action capabilities are on the roadmap.

See [API keys](../admin-guide/api-keys.md).

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
| `TRUSTEDOSS_POST_MR_COMMENT` | no | `true` | Reserved for the GitLab Notes API client (not yet shipped — see warning at the top of this page). The flag is parsed but a request to the portal currently fails for GitLab repos. |

## Recipes

### Advisory mode

```yaml
include:
  - remote: 'https://raw.githubusercontent.com/trustedoss/trustedoss-portal/v2.0.0/templates/gitlab-ci.yml'

variables:
  TRUSTEDOSS_API_URL: 'https://trustedoss.example.com'
  TRUSTEDOSS_PROJECT_ID: '01H7XYZ…'
  TRUSTEDOSS_FAIL_ON_GATE: 'false'
```

The job stays green. (MR note posting is GitHub-only at v2.0.0 — see warning at the top of this page.)

### Run only on protected branches

Override the rules of the included job:

```yaml
include:
  - remote: 'https://raw.githubusercontent.com/trustedoss/trustedoss-portal/v2.0.0/templates/gitlab-ci.yml'

.trustedoss-sca:
  rules:
    - if: '$CI_COMMIT_REF_PROTECTED == "true"'
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
```

### Container scan as a separate job

```yaml
include:
  - remote: 'https://raw.githubusercontent.com/trustedoss/trustedoss-portal/v2.0.0/templates/gitlab-ci.yml'

trustedoss:scan-container:
  extends: .trustedoss-sca
  variables:
    TRUSTEDOSS_SCAN_KIND: 'container'
```

### Pin to a tag

Pin the `include` URL to a release tag (`v2.0.0`) instead of `main` for reproducible pipelines.

## Anatomy of the template (advanced)

If you need to copy and inline the job — for instance because your runner cannot reach GitHub for the `include` — here is the canonical shape:

```yaml
# canonical shape — see templates/gitlab-ci.yml for the live version
.trustedoss-sca:
  image: curlimages/curl:8.4.0
  stage: test
  before_script:
    - command -v jq >/dev/null || apk add --no-cache jq
  script:
    - 'curl -fsS -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" -F "...="  ${TRUSTEDOSS_API_URL}/api/v1/scans/source'
    # ... (full version inline at templates/gitlab-ci.yml)
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
    - if: '$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH'
```

The full canonical version lives at [`templates/gitlab-ci.yml`](https://github.com/trustedoss/trustedoss-portal/blob/main/templates/gitlab-ci.yml). Read it before forking — it handles edge cases (network blip during poll, masked-token rotation) you do not want to re-implement.

## Branch / merge protection

To enforce SCA on every MR:

1. **Settings → Repository → Protected branches** — protect `main`.
2. **Settings → Merge requests → Merge checks** — toggle "Pipelines must succeed".

MRs whose SCA job (the one extending `.trustedoss-sca`) is failing cannot be merged.

## Troubleshooting

### `Authorization` header is missing in the included job

GitLab strips empty variables. Confirm `TRUSTEDOSS_API_KEY` is defined for the relevant environment / branch. The variable's "Protected" flag means it is only injected on protected refs — adjust if you also want it on regular MRs.

### MR note is not posted

Expected at v2.0.0 — the portal's PR-comment integration is GitHub-only (see warning at the top of this page). Set `TRUSTEDOSS_POST_MR_COMMENT=false` to suppress the staged request, and rely on the build-gate exit code to enforce policy.

### Job runs out of time at the polling step

`TRUSTEDOSS_POLL_TIMEOUT` defaults to 30 minutes — large repos can exceed that. Raise to 3600 (1 hour) and re-run.

### "Forbidden" on `POST /scans`

The API key's `scope` (`org`, `team`, or `project`) does not cover the project being scanned. Re-issue the key with the correct scope.

## See also

- [GitHub Actions](./github-actions.md)
- [Jenkins](./jenkins.md)
- [Webhooks](./webhooks.md)
- [API keys](../admin-guide/api-keys.md)
