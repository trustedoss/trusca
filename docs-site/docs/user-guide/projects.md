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
Engineers and team leads who scan their own services. Requires sign-in. The role on the project's team must be `developer` or higher to create / archive; `team_admin` to change visibility or delete.
:::

## Anatomy of a project

| Field | Description |
|---|---|
| **Name** | Display label (free text). Must be unique within a team. |
| **Repository URL** | Git URL the scan pipeline clones from. HTTPS or SSH supported. Private repos require credentials — see [Private repos](#private-repositories). |
| **Default branch** | The branch the scan pipeline checks out (usually `main`). |
| **Visibility** | `team-only` (default — visible only to members of the owning team) or `org-wide` (visible to every signed-in user in the organization). |
| **Owning team** | The team the project belongs to. Defaults to your active team; super-admins can reassign. |
| **Container image** | Optional. If set, container scans (`Trivy`) target this reference (`<registry>/<image>:<tag>`). |
| **Tags** | Free-form labels. Useful for grouping in the dashboard portfolio view. |

## Adding a project — UI

1. Sign in.
2. Click **Projects** in the sidebar.
3. Click **New project** in the top-right.
4. Fill out the form:
   - **Name** (required)
   - **Repository URL** (required for source scans)
   - **Default branch** — defaults to `main`
   - **Visibility** — defaults to `team-only`
   - **Container image** (optional)
5. Click **Create**.

You land on the project's **Overview** tab. From here you can run the first scan — see [Scans](./scans.md).

## Adding a project — API

```bash
curl -sS -X POST https://trustedoss.example.com/api/v1/projects \
  -H "Authorization: ApiKey ${TRUSTEDOSS_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "checkout-service",
    "repository_url": "https://github.com/acme/checkout-service.git",
    "default_branch": "main",
    "visibility": "team_only",
    "container_image": "ghcr.io/acme/checkout-service:latest"
  }' | jq .
```

The response includes the project's UUID — keep it; it is the value you wire into the GitHub Action's `project-id` input and the GitLab CI variable.

## Visibility

- **`team_only`** (default) — only members of the owning team see the project, its scans, and its findings.
- **`org_wide`** — any signed-in user in the organization can read the project. Writes still require a role on the owning team.

Changing visibility is a privileged action. The audit log records the actor and the previous value.

:::caution Visibility downgrade
Switching `org_wide` → `team_only` can hide projects that other teams depend on. Confirm the change with stakeholders before flipping the toggle.
:::

## Tags

Tags help group projects in the dashboard's portfolio view. Use them for environment (`prod`, `staging`), language stack (`go`, `node`), or compliance scope (`pci-dss`, `hipaa`).

Tag changes are non-destructive and never block a scan.

## Archive vs. delete

- **Archive** — keeps the project, its history, scans, and findings, but hides it from default lists and disables new scans. Useful when a service is retired but you still need its compliance trail.
- **Delete** — permanently removes the project and everything below it. Cannot be undone. Audit-log entries persist (rows are append-only) but they reference the deleted project's UUID, not its name.

The **Delete** button is hidden behind a typed-name confirmation modal to prevent accidents.

## Private repositories

Source scans clone the repository from inside the worker container. Authentication options:

- **HTTPS + Personal Access Token** — set the URL to `https://<token>@github.com/acme/checkout-service.git`. The token is stored encrypted at rest and never returned by the API.
- **SSH deploy key** — generate a deploy key in `Project Settings → Repository`, add it as a read-only deploy key in your Git host.

For `org_wide` projects, prefer SSH deploy keys — embedded HTTPS tokens leak credentials if the URL is logged.

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
3. The audit log (`/admin/audit`) records `project.create` with your `user_id`.

## Troubleshooting

### "Repository URL is invalid"

The wizard validates the URL shape (`https://...`, `git@...`, or `ssh://...`). It does **not** verify reachability — that happens at scan time. If the URL is rejected at form submission, double-check for typos.

### "Project name already in use"

Names are unique per team. Either rename the existing project or add a suffix (`checkout-service-legacy`).

### Forbidden when creating a project

Your role on the owning team is below `developer`. Ask a team admin to invite you with the right role — see [Users & teams](../admin-guide/users-and-teams.md).

## See also

- [Scans](./scans.md) — run your first scan
- [Vulnerabilities](./vulnerabilities.md) — triage findings
- [Components & licenses](./components-and-licenses.md) — read the component list
- [Users & teams](../admin-guide/users-and-teams.md) — role model
