---
id: projects
title: Projects
description: Register, configure, and archive projects in TrustedOSS Portal — the unit that ties together scans, components, vulnerabilities, and obligations.
sidebar_label: Projects
sidebar_position: 1
---

# Projects

A **project** is the unit of source-tracked software the portal knows about. It owns scans, components, vulnerabilities, license findings, obligations, and a generated `NOTICE` file. Most workflows start by adding a project.

:::note Audience
Engineers and team leads who scan their own services. Requires sign-in. The role on the project's team must be `developer` or higher to create / archive; `team_admin` to change visibility.
:::

## Anatomy of a project

| Field | Description |
|---|---|
| **Name** | Display label (free text). Must be unique within a team. |
| **Description** | Optional free-text summary surfaced on the project list and Overview tab. |
| **Git URL** | Git URL the scan pipeline clones from. HTTPS supported. Private repos require credentials embedded in the URL — see [Private repos](#private-repositories). |
| **Default branch** | The branch the scan pipeline checks out (defaults to `main`). Editable from **Project Settings** after creation. |
| **Visibility** | `team` (the only value accepted at v2.0.0 — visible to members of the owning team). Set automatically on creation; mutable only via PATCH. |
| **Owning team** | The team the project belongs to. Set automatically to your active team on creation. |

## Adding a project — UI

The **Projects** sidebar entry lands on the team-scoped project list — every project that belongs to your active team, with status badges and inline **Scan** actions:

![Projects list — every project in the active team with status badges and inline Scan actions](/img/screenshots/user-projects-list.png)

1. Sign in.
2. Click **Projects** in the sidebar.
3. Click **New project** in the top-right.
4. Fill out the form:
   - **Name** (required)
   - **Description** (optional)
   - **Git URL** (required for source scans)
5. Click **Create**.

   ![New project form — name, description, and Git URL fields](/img/screenshots/user-projects-create-form.png)

You land on the project's **Overview** tab. From here you can run the first scan — see [Scans](./scans.md).

![Project detail — Overview tab with risk gauge and quick actions](/img/screenshots/user-project-detail-overview.png)

The default branch (`main`), visibility (`team`), and owning team (your active team) are set server-side and can be reviewed from **Project Settings**.

## Adding a project — API

```bash
curl -sS -X POST https://trustedoss.example.com/v1/projects \
  -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "checkout-service",
    "description": "Storefront checkout service",
    "git_url": "https://github.com/acme/checkout-service.git"
  }' | jq .
```

The response includes the project's UUID — keep it; it is the value you wire into the GitHub Action's `project-id` input and the GitLab CI variable.

The schema rejects unknown fields (`extra="forbid"`). Only `name`, `description`, and `git_url` are accepted on create. `default_branch` can be set later via `PATCH /v1/projects/{id}`.

To find your `team_id`, ask the team-admin or super-admin to share
it (they see it under `/admin/teams` → row drawer). Developers do
not have direct access to the `/v1/admin/teams` listing at v2.0.0;
a `GET /v1/users/me/memberships` shortcut is on the roadmap.

## Visibility

- **`team`** (default and only accepted value at v2.0.0) — only members of the owning team see the project, its scans, and its findings.

Visibility is set automatically on creation. PATCH currently rejects any value other than `team`. Audit log records the actor on every PATCH.

See [Roadmap](#roadmap-v2x) for `organization` (org-wide read) availability.

## Archive

- **Archive** — keeps the project, its history, scans, and findings, but hides it from default lists and disables new scans. Useful when a service is retired but you still need its compliance trail.

`DELETE /v1/projects/{id}` performs a soft-delete (archive). The portal does not currently expose a permanent-delete operation; audit-log entries persist regardless.

The Archive action lives on **Project Settings → Archive** and uses an inline confirm strip to prevent accidents.

## Private repositories

Source scans clone the repository from inside the worker container. Authentication option supported at v2.0.0:

- **HTTPS + Personal Access Token** — set the URL to `https://<token>@github.com/acme/checkout-service.git`. The token is stored as part of `git_url` and never returned by the API in plaintext form on read endpoints.

:::caution Private repos at v2.0.0
Today the only supported credential model is **HTTPS + PAT
embedded in the git URL**
(`https://<token>@github.com/acme/payment-service.git`). The PAT is
persisted in the project row (the API never returns it in plaintext
on read endpoints, and `git_url` is masked in audit logs).

Implications:
- A leaked DB snapshot still leaks every embedded PAT. Use a
  short-lived PAT with read-only scope.
- SSH keys and GitHub-App installations are on the roadmap for
  v2.1; rotate aggressively in the meantime.
:::

For SSH deploy keys, see [Roadmap](#roadmap-v2x).

## Risk score

Each project surfaces an aggregated **risk score** (0–100) that combines:

- Open vulnerabilities by severity (Critical, High, Medium, Low).
- License classification mix (forbidden licenses dominate the score).
- Time since last scan (older scans depreciate).

The score updates after every scan and after every CVE re-detection. Read it as a relative indicator across your portfolio, not an absolute SLA. Drilling into the project shows the breakdown.

## Verify it worked

After creating a project:

1. The project appears in **Projects** with status **Idle** (no scans yet).
2. The Overview tab shows zero components and zero vulnerabilities.
3. The audit log (`/admin/audit`, super-admin only) records `target_table=projects&action=create` with your `user_id`.

## Troubleshooting

### "Repository URL is invalid"

The wizard validates the URL must start with `http://` or `https://` (HTTPS strongly preferred). `git@…` and `ssh://…` URLs are **not** accepted by the form at v2.0.0; use the HTTPS clone URL. The portal does not verify reachability — that happens at scan time. If the URL is rejected at form submission, double-check for typos.

### "Project name already in use"

Names are unique per team. Either rename the existing project or add a suffix (`checkout-service-legacy`).

### Forbidden when creating a project

Your role on the owning team is below `developer`. Ask a team admin to invite you with the right role — see [Users & teams](../admin-guide/users-and-teams.md).

## Roadmap (v2.x)

Items the manual previously promised that are not in v2.0.0; tracked for later releases.

- `container_image` field on the project (and a Container scan trigger) — planned for v2.1.
- Project tags for portfolio grouping — planned for v2.1.
- `organization` (org-wide) visibility — reserved for v2.2.
- SSH deploy-key generation from **Project Settings** — planned for v2.2.
- Permanent project delete with typed-name confirmation — under design; soft-delete (archive) is currently the only option.
- SSH (`git@…`, `ssh://…`) URL acceptance in the create wizard — planned for v2.1.

## See also

- [Scans](./scans.md) — run your first scan
- [Vulnerabilities](./vulnerabilities.md) — triage findings
- [Components & licenses](./components-and-licenses.md) — read the component list
- [Users & teams](../admin-guide/users-and-teams.md) — role model
