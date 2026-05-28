---
id: remediation-pull-request
title: Automated remediation PR (npm)
description: Open a pull request on a project's opted-in GitHub repository that bumps its vulnerable npm dependencies, using a GitHub App installation.
sidebar_label: Remediation PR (auto)
sidebar_position: 6
---

# Automated remediation PR (npm)

Where the [remediation dry-run](./remediation-dry-run.md) only *previews* the `package.json` edit, the automated remediation PR actually **opens a pull request** on your project's GitHub repository that applies the bump — branch, commit, and PR — using a short-lived [GitHub App](../admin-guide/github-app.md) installation token.

This is a privileged write to an external repository, so it is **opt-in** and **team-admin only**.

:::caution Opt-in is the only authority over the target repository
The portal will **never** open a PR on a repository you have not explicitly linked to the project. The target repo is derived **only** from the project's opted-in GitHub App installation — there is no request field that lets a caller name a repository. If the project is not opted in, the request is refused with `409`.
:::

## Prerequisites

1. A team admin has registered a [GitHub App credential](../admin-guide/github-app.md) for the team.
2. That credential's installation is **linked to the project** with a `repository_full_name` (the opt-in). The linked installation's credential must not be revoked.

## Endpoint

```
POST /v1/projects/{project_id}/remediation/npm/pull-request
```

Authentication is required (JWT or API key). The caller must be a **team admin** of the project's team. A project you cannot see returns `404` (existence-hide).

### Request body (optional)

```json
{
  "manifest": "{\n  \"dependencies\": { \"lodash\": \"^4.17.20\" }\n}\n"
}
```

| Field | Type | Notes |
| --- | --- | --- |
| `manifest` | string \| null | Raw `package.json` to edit. When omitted, the manifest is best-effort read from the project's latest preserved scan source. **The target repository is not part of this body** — it is derived from the opt-in. |

### Responses

| Status | Meaning |
| --- | --- |
| `201 Created` | A new remediation PR was opened. The body is the PR record. |
| `200 OK` | An existing **open** PR with the same set of bumps was returned (idempotent — no duplicate PR). |
| `204 No Content` | Nothing to remediate (the manifest already satisfies the fixes). |

```json
{
  "id": "…",
  "project_id": "…",
  "ecosystem": "npm",
  "repository_full_name": "acme/widget",
  "head_branch": "trustedoss/remediation-1a2b3c4d",
  "base_branch": "main",
  "pr_number": 42,
  "pr_url": "https://github.com/acme/widget/pull/42",
  "status": "open",
  "package_changes": [
    { "package": "lodash", "from": "4.17.20", "to": "4.17.21" }
  ],
  "created_at": "2026-05-25T12:00:00Z",
  "updated_at": "2026-05-25T12:00:01Z"
}
```

## Idempotency

Each attempt is fingerprinted by its **set of bumps** (`package` → target version). If an **open** PR with the same fingerprint already exists for the project, that PR is returned instead of opening a second one. A `failed` or `superseded` record does not block a fresh attempt.

## What the PR contains

- A new branch `trustedoss/remediation-<short-fingerprint>` off the repo's default branch.
- A single commit editing **only `package.json`**.
- A pull request describing the bumps.

The **lockfile is not edited** — run `npm install` to regenerate `package-lock.json` before merging. The PR body carries this reminder.

## Listing remediation PRs

```
GET /v1/projects/{project_id}/remediation/pull-requests
```

Any team member can read the project's remediation-PR records (newest first), paginated with `page` / `page_size`.

## Using the portal UI

The same flow is available without the API, from the project's **Remediation** tab (`/projects/:id?tab=remediation`):

1. **Preview** — click **Run preview** to compute the dry-run. The proposed bumps are shown as a `package → current → recommended` table, alongside the manifest source (uploaded / preserved scan source / not available) and any warnings — most importantly *"run `npm install` to regenerate package-lock.json"*. A no-change result and a no-manifest result are shown as explicit empty states.
2. **Open remediation PR** — visible only to **team admins**. Clicking it opens (or idempotently returns) the PR and surfaces the result as a link that opens GitHub in a new tab.
   - If the project is **not opted in** (no linked GitHub App installation), the button is replaced with inline guidance instead of failing — a team admin must link a repository first.
   - A non-team-admin sees read-only guidance, not the button.
3. **Remediation pull requests** — the list below shows every PR opened for the project with a status badge (`creating` / `open` / `failed` / `superseded`), the target repository, and the creation time. Each row links out to the PR on GitHub.

## Status lifecycle

| Status | Meaning |
| --- | --- |
| `creating` | the record was persisted before the GitHub writes started (a crash mid-flight leaves a visible trail) |
| `open` | GitHub returned a created PR; `pr_number` / `pr_url` are set |
| `failed` | a GitHub write failed; the attempt is recorded |
| `superseded` | reserved for a future "a newer PR replaces this one" flow |

## Errors

All errors are RFC 7807 `application/problem+json`:

- `401` — authentication required.
- `403` — the caller is a project member but not a team admin.
- `404` — project not found / not accessible.
- `409` — the project is not opted in to automated remediation PRs (link a GitHub App installation with a repository first).
- `422` — the manifest could not be edited, or the stored repository identifier is unusable.
- `502` — a GitHub write (branch / commit / PR) failed. The error reports the GitHub HTTP status only — never a token or response body.
