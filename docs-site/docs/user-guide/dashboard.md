---
id: dashboard
title: Dashboard
description: The post-login landing page in TrustedOSS Portal — at-a-glance portfolio severity, license mix, scan-queue state, and recent runs.
sidebar_label: Dashboard
sidebar_position: 0
---

# Dashboard

After you sign in, the portal lands on the **Dashboard** (`/`) — a single-page summary of the parts of your portfolio you can see (every project the active team owns).

![Dashboard — KPI tiles, severity + license distribution, and a recent-scans list at the root URL after sign-in](/img/screenshots/user-dashboard.png)

The page exists to answer the four questions you typically arrive with:

- *Are there any new criticals?* (severity tiles)
- *How many projects am I responsible for, and how many are in flight?* (portfolio + scan-status tiles)
- *Is the license mix shifting?* (license bar)
- *What ran recently?* (recent scans list)

:::note Audience
Any signed-in user. The data scoping follows your team memberships — projects in teams you do not belong to do not contribute. Super-admins see every team's data.
:::

## What's on the page

The Dashboard renders four bands stacked top-to-bottom:

1. **Vulnerabilities by severity** — five tiles (Critical / High / Medium / Low / Info) with the open finding count across every project you can see. The count excludes findings whose VEX status is `Not affected`, `False positive`, `Fixed`, or `Suppressed` — the same exclusions the [build gate](./projects.md#build-gate-verdict-overview-tab) applies.
2. **Portfolio** — six tiles: project count, pending approvals, and four scan-status counts (Queued / Running / Succeeded / Failed) summed across the visible portfolio.
3. **License classification** — a horizontal bar of the four tiers (Permissive / Conditional / Prohibited / Unknown) with a per-tier count legend below.
4. **Recent scans** — the most recent scan rows across the portfolio, each linking to its project detail page. Every row carries the project name, the release tag (when a release snapshot was recorded for the run), the scan kind (`source` / `container`), a status badge, and a relative timestamp.

The page polls the backend's `/v1/dashboard/summary` endpoint and renders skeletons while the first response is in flight. Subsequent reloads use the cached response and refetch in the background.

## Empty state

A brand-new deployment with no projects shows a centered call-to-action ("No projects yet — register your first project to start scanning…") instead of zero-filled tiles. Click the **Register project** button to land on `/projects/new`.

## Error state

If the dashboard endpoint returns a non-2xx response, the page replaces the tile area with a single inline error ("Couldn't load the dashboard. Please try again.") and a retry control. The recent-scans list and the rest of the navigation remain operable — the error is scoped to the summary widget.

## Verify it worked

After signing in for the first time:

1. The header avatar shows your initials and the sidebar highlights **Dashboard**.
2. The severity tiles render five values (zero is fine).
3. The recent-scans list either lists at least one row or shows the empty-state message ("No scans have run yet.").

## See also

- [Projects](./projects.md) — drill into a single project from a Recent-scans row.
- [Scans](./scans.md) — the global queue view that mirrors the scan-status tiles.
- [Approvals](./approvals.md) — the queue the Pending-approvals tile points at.
