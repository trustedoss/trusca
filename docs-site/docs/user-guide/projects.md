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
| **Visibility** | `team` (the only value accepted in this release — visible to members of the owning team). Set automatically on creation; mutable only via PATCH. |
| **Owning team** | The team the project belongs to. Set automatically to your active team on creation. |

## Adding a project — UI

The **Projects** sidebar entry lands on the team-scoped project list — every project that belongs to your active team, with status badges, severity counts, an inline **Scan** action, and a compact per-project meta row showing `n scans · m releases · last scan <relative time>`:

![/projects list — team-scoped table with name, last-scan status badge, severity counts, and an inline Scan action per row](/img/screenshots/user-projects-list.png)

<!-- screenshot above predates the scan_count/release_count/last_scan_at meta row added by #30; refresh post-merge -->

The meta row sums:

- **scans** — total scans this project has ever run (any status; archived runs included).
- **releases** — count of release snapshots the project has accumulated (see [Releases](#the-releases-tab)).
- **last scan** — relative time since the last scan moved to a terminal status. `—` until the first scan completes.

The list endpoint aggregates these three fields server-side in a single query, so the row is cheap to render even on portfolios of hundreds of projects.

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

### Detail-page tabs

The detail page exposes the following tabs, left-to-right:

| Tab | What it shows |
|---|---|
| **Overview** | Risk axes (Security + License), the [Build gate verdict](#build-gate-verdict-overview-tab), the **Project info** card (Git URL, default branch, owning team, created-at, last-scan time), and recent scans. |
| **Releases** | Snapshots of the project at each terminal scan — the snapshot list, a "view snapshot" pin action, and per-release diff entry points. See [The Releases tab](#the-releases-tab). |
| **Components** | Every component the scan discovered. See [Components & licenses](./components-and-licenses.md). |
| **Vulnerabilities** | Open and triaged CVE findings. See [Vulnerabilities](./vulnerabilities.md). |
| **Licenses** | The same data viewed by SPDX identifier and tier. |
| **Obligations** | Per-component obligations + NOTICE-file generation. See [Components & licenses → Obligations](./components-and-licenses.md#obligations). |
| **SBOM** | CycloneDX / SPDX exports, byte-stable. See [SBOM](./sbom.md). |
| **Reports** | Generate-cards for NOTICE, SBOM, Vulnerability PDF, and VEX **plus** the project's unified download / export history. See [The Reports tab](#the-reports-tab). |
| **Source** | The fetched first-party source tree from the latest succeeded scan, with file-level license findings highlighted. Sits between **Reports** and **Remediation**. |
| **Remediation** | Per-component upgrade recommendations from the latest scan, including the opt-in npm remediation PR flow. |
| **Settings** | Project metadata, archive action, CI-integration helpers. |

:::note Tab order
The **Source** tab used to sit immediately after **Licenses**; it was moved to the right of **Reports** so the data-output cluster (SBOM / Reports / Source) is contiguous. Bookmarks and `?tab=source` deep links continue to work — the slug is unchanged.
:::

## Adding a project — API

<!-- docs-uat: id=projects-api-create kind=shell ctx=host tier=manual waiver=example-curl-placeholder-host-and-api-key -->
```bash
curl -sS -X POST https://trustedoss.example.com/v1/projects \
  -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "team_id": "8f0c1e2a-...your team UUID...",
    "name": "checkout-service",
    "slug": "checkout-service",
    "description": "Storefront checkout service",
    "git_url": "https://github.com/acme/checkout-service.git"
  }' | jq .
```

The response includes the project's UUID — keep it; it is the value you wire into the GitHub Action's `project-id` input and the GitLab CI variable.

**Required fields**: `team_id`, `name`, and `slug`. Optional: `description`, `git_url`, `default_branch`, and `visibility`. The schema rejects unknown fields (`extra="forbid"`), so omitting `team_id` or `slug` returns `422` (`missing: body.team_id`).

Finding your `team_id`: in the UI the project-create form has a team selector, so you never type the UUID. Over the API a `team_admin` or `super_admin` reads it from `GET /v1/admin/teams`; a self-service `GET /v1/users/me/memberships` is on the roadmap. In this release the field is **not** derived from your session — it must be in the body.

## Visibility

- **`team`** (default and only accepted value in this release) — only members of the owning team see the project, its scans, and its findings.

Visibility is set automatically on creation. PATCH currently rejects any value other than `team`. Audit log records the actor on every PATCH.

See [Roadmap](#roadmap) for `organization` (org-wide read) availability.

## Archive

- **Archive** — keeps the project, its history, scans, and findings, but hides it from default lists and disables new scans. Useful when a service is retired but you still need its compliance trail.

`DELETE /v1/projects/{id}` performs a soft-delete (archive). The portal does not currently expose a permanent-delete operation; audit-log entries persist regardless.

The Archive action lives on **Project Settings → Archive** and uses an inline confirm strip to prevent accidents.

## Private repositories

Source scans clone the repository from inside the worker container. Authentication option supported in this release:

- **HTTPS + Personal Access Token** — set the URL to `https://<token>@github.com/acme/checkout-service.git`. The token is stored as part of `git_url` and never returned by the API in plaintext form on read endpoints.

:::caution Private repos in this release
Today the only supported credential model is **HTTPS + PAT
embedded in the git URL**
(`https://<token>@github.com/acme/payment-service.git`). The PAT is
persisted in the project row (the API never returns it in plaintext
on read endpoints, and `git_url` is masked in audit logs).

Implications:
- A leaked DB snapshot still leaks every embedded PAT. Use a
  short-lived PAT with read-only scope.
- SSH keys and GitHub-App installations are on the roadmap for
  ; rotate aggressively in the meantime.
:::

For SSH deploy keys, see [Roadmap](#roadmap).

## Risk score — two axes

The Overview tab now surfaces **two** risk axes on the project gauge instead of one composite number, so the two failure modes can be read independently:

- **Security risk** — driven by the project's open vulnerability mix. The band (Critical / High / Medium / Low / Info) is set by the **most severe** open finding; within the band the score scales as `n / (n + 4)` (non-saturating, so the band itself is the primary signal — adding more findings cannot bump you up a band).
- **License risk** — driven by the project's license-tier mix. **Forbidden** licenses dominate the band; **Conditional** rows raise the score within the band but never promote it to `Critical` on their own (the previous "any conditional component = Risk 100" behaviour was removed in  W1).

The legacy single `risk_score` field is still exposed on the API as `max(security_axis, license_axis)` for back-compat with the build gate and CI integrations; the UI uses the two-axis breakdown.

Both axes refresh after every scan and after every CVE re-detection. Read them as relative indicators across your portfolio, not absolute SLAs — drilling into the project shows the per-axis breakdown.

:::note Old "single risk gauge" screenshots
Screenshots taken before  W1 show a single gauge labelled "Risk". The two-axis card replaces it. The numbers are not strictly comparable between the old single score and either of the two new axes — re-baseline against your portfolio after the upgrade.
:::

## Build gate verdict (Overview tab)

The **Overview** tab shows a **Build gate** card next to the risk gauge. It surfaces the same build-blocking verdict the CI integration computes — so you can read the gate result in the portal without opening a CI log. The card evaluates the project's **latest successful scan**.

The **build gate** (also called the **policy gate**) is the CI-blocking mechanism that exits non-zero when a build carries critical CVEs or forbidden-tier licenses. The concept and how to wire it into a pipeline live in [GitHub Actions → the build gate](../ci-integration/github-actions.md#outputs) and [Glossary → Build gates](../reference/glossary.md#build-gates); this card is the read-only, in-UI view of the same verdict.

The card shows:

| Element | Meaning |
|---|---|
| **Pass / Fail badge** | `Pass` (green, shield-check) when the latest successful scan has no critical CVEs and no forbidden licenses; `Fail` (red, shield-x) otherwise. |
| **Reason** | On `Fail`, a one-line explanation of what tripped the gate. |
| **Critical CVEs** | Count of open critical-severity findings on the evaluated scan. Open = status not in `not_affected`, `fixed`, `false_positive`. |
| **Forbidden licenses** | Count of distinct components carrying at least one forbidden-tier license. |
| **`EPSS ≥ {threshold}`** | Shown **only** when an operator has enabled the EPSS gate (`GATE_EPSS_THRESHOLD` set on the portal). Count of open findings whose EPSS score meets or exceeds the threshold. Hidden when the EPSS gate is disabled (the default). See [Gate the build on EPSS](../ci-integration/github-actions.md#gate-the-build-on-epss-optional). |

:::note No scan yet
A project that has never had a successful scan shows a neutral **No scan yet** state instead of a green pass — there is nothing to evaluate. Run a scan (see [Scans](./scans.md)) and the card fills in.
:::

CVE — Common Vulnerabilities and Exposures; EPSS — Exploit Prediction Scoring System. See the [Glossary](../reference/glossary.md) for both.

The card is read-only — it reflects the verdict but does not change policy. The thresholds (severity floor, EPSS) are operator- and CI-side settings; see [GitHub Actions](../ci-integration/github-actions.md) and [`GATE_EPSS_THRESHOLD`](../reference/env-variables.md#build--policy-gate).

## Project info (Overview tab) {#project-info-card}

The Overview tab has a **Project info** card next to the risk gauges. It collapses the project's identifying metadata into one read-only block, so you do not have to open **Settings** for a quick lookup:

| Field | Source | Notes |
|---|---|---|
| **Git URL** | Project `git_url`. | Click-to-copy. The URL is masked when a personal-access token is embedded (the token segment is rendered as `***`); the raw value never appears on read endpoints. |
| **Default branch** | Project `default_branch`. | Editable from **Settings**. |
| **Owning team** | Project `team`. | Links to the team's `/admin/teams/{id}` admin view when the viewer is `super_admin`. |
| **Created** | Project `created_at`. | Absolute timestamp on hover, relative time on the face. |
| **Last scan** | Project `last_scan_at` (the same value the project list aggregates). | `—` until the first scan reaches a terminal status. |

The card is the same data the project-list meta row exposes, surfaced once at the top of the detail page so the reader does not have to bounce back to the list to remember the project's repo URL.

## The Releases tab {#the-releases-tab}

Every time a scan reaches a terminal `succeeded` status the portal records a **release snapshot** — an immutable point-in-time view of the project (component list, license tier mix, vulnerability findings, scan id) tagged with the scan's completion timestamp. The **Releases** tab on a project lists those snapshots newest-first:

| Column | What it shows |
|---|---|
| **Snapshot** | The scan completion time (`yyyy-mm-dd HH:MM`) + relative time. |
| **Scan kind** | `source` or `container`. |
| **Severity counts** | Critical / High / Medium / Low at snapshot time. |
| **License mix** | Allowed / Conditional / Forbidden bars at snapshot time. |
| **Actions** | **View components** (jumps to the **Components** tab pinned to that scan) and **View snapshot** (pins `?scan=<id>` and reloads the Overview with the snapshot's data). |

Click a release row directly to navigate to the **Components** tab with the snapshot pinned. The pin propagates as a `?scan=<id>` URL parameter so the deep link survives reload and can be shared with a teammate — every tab on the project (Components, Vulnerabilities, Licenses, …) reads from the pinned snapshot until you clear the pin in the breadcrumb. Removing the pin restores the *latest succeeded* scan as the data anchor everywhere.

The companion **Compare** screen (linked from the Releases-tab toolbar) takes two snapshot ids and shows the added / removed components and severities between them — the canonical diff view for "what changed between release X and release Y".

## The Reports tab {#the-reports-tab}

The **Reports** tab is a single landing page that unifies the project's downloadable artifacts:

- Four **generate cards** for **NOTICE**, **SBOM**, **Vulnerability PDF**, and **VEX**. Each card is a deep link — clicking the card's action button switches to the relevant domain tab (Obligations / SBOM / Vulnerabilities / Vulnerabilities) where the actual format chooser and download buttons live. The currently pinned `?scan=` snapshot is preserved across the jump.
- An **export history table** on the right, with columns **When** (relative + absolute timestamp), **Who** (the actor's email — `—` for anonymized rows kept for audit), **Type** (one of NOTICE / SBOM / Vulnerability PDF / VEX), **Format** (the exact format string — `cyclonedx-json`, `spdx-tv`, `openvex`, etc.), **Scan** (first eight characters of the scan id), and **Size** (humanized; `—` when the renderer did not record a size).
- A toolbar **Type** multi-select filter and Prev / Next pagination, both mirrored to the URL as `?rpt_type=<type>` / `?rpt_page=<n>` so a filtered view is reload-safe and link-shareable.

Authorisation is the same posture as the SBOM and PDF exports — any team member with at least `developer` can read history; non-members receive `404` (existence-hide). The history table is **append-only**: there is no edit, delete, or replay action. To re-download an artifact, click the relevant generate card and re-export it from the domain tab.

:::note The Reports tab does not duplicate the domain-tab download UX
Generate cards always deep-link to the domain tab (Obligations, SBOM, Vulnerabilities) rather than spawning a generation dialog inside the Reports tab. The intent is to keep one canonical UX per format and avoid drift between two download surfaces. The added value of the tab is the **history** view across all formats in one place.
:::

## Verify it worked

After creating a project:

<!-- docs-uat: id=projects-appears-idle kind=manual tier=manual -->
1. The project appears in **Projects** with status **Idle** (no scans yet).
<!-- docs-uat: id=projects-overview-zero kind=manual tier=manual -->
2. The Overview tab shows zero components and zero vulnerabilities.
<!-- docs-uat: id=projects-audit-create kind=manual tier=manual -->
3. The audit log (`/admin/audit`, super-admin only) records `target_table=projects&action=create` with your `user_id`.

## Troubleshooting

### "Repository URL is invalid"

The wizard validates the URL must start with `http://` or `https://` (HTTPS strongly preferred). `git@…` and `ssh://…` URLs are **not** accepted by the form in this release; use the HTTPS clone URL. The portal does not verify reachability — that happens at scan time. If the URL is rejected at form submission, double-check for typos.

### "Project name already in use"

Names are unique per team. Either rename the existing project or add a suffix (`checkout-service-legacy`).

### Forbidden when creating a project

Your role on the owning team is below `developer`. Ask a team admin to invite you with the right role — see [Users & teams](../admin-guide/users-and-teams.md).

## Roadmap

Items the manual previously promised that are not in this release; tracked for later releases.

- Project tags for portfolio grouping — planned.
- `organization` (org-wide) visibility — planned.
- SSH deploy-key generation from **Project Settings** — planned.
- Permanent project delete with typed-name confirmation — under design; soft-delete (archive) is currently the only option.
- SSH (`git@…`, `ssh://…`) URL acceptance in the create wizard — planned.

## See also

- [Scans](./scans.md) — run your first scan
- [Vulnerabilities](./vulnerabilities.md) — triage findings
- [Components & licenses](./components-and-licenses.md) — read the component list
- [Users & teams](../admin-guide/users-and-teams.md) — role model
