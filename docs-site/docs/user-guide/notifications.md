---
id: notifications
title: Notifications
description: Read, configure, and silence in-app, email, Slack, and Teams notifications for scan, gate, CVE, approval, and disk events.
sidebar_label: Notifications
sidebar_position: 8
---

# Notifications

The notification system tells you about events on projects you care about — scans finishing, gates failing, new CVEs landing on a component you depend on, approvals waiting, and license-policy violations. Notifications fan out across **four channels** (in-app, email, Slack, Microsoft Teams) and you decide globally which channels to receive on.

:::note Audience
Any signed-in user. The header bell and `/notifications` page are visible to every role; admins additionally configure the SMTP / Slack / Teams transports under [Disk & system health](../admin-guide/disk-and-health.md).
:::

## The header bell

Every page has a bell icon in the top-right of the header. The badge shows the number of **unread** in-app notifications:

- 0 — no badge.
- 1–99 — exact count.
- 100+ — capped at **99+** so the badge does not break the layout.

Click the bell to navigate directly to the **`/notifications`** inbox. The bell does not surface a dropdown preview at v2.0.0 — see [Roadmap](#roadmap-v2x).

## The Inbox at `/notifications`

`/notifications` is the full list of every notification you have received, newest first. Pages of 25 are loaded one at a time; use the **Previous** / **Next** controls at the bottom to walk through history.

![Notifications inbox at /notifications — read/unread rows with kind badges and pagination controls](/img/screenshots/user-notifications-inbox.png)

Each row shows:

- **Title** — bold while unread.
- **Body** — one or two lines.
- **Channel icons** — which channels delivered this event for you (in-app always shows; email / Slack / Teams show only if you opted in).
- **Timestamp** — absolute on hover, relative otherwise.
- **Mark read** — a click anywhere on the row marks it read; the row dim-loads.
- **Open** — clicking navigates to the source resource and marks read on the way.

Bulk actions sit in the toolbar: **Mark all as read** (only if there are unreads) and **Filter** (by trigger or by date range).

## Preferences

The **Preferences** tab (top-right of `/notifications`) exposes four **global, per-channel** toggles. The choice applies across every trigger — there is no per-trigger matrix at v2.0.0 (see [Roadmap](#roadmap-v2x)).

![Notifications preferences — per-channel toggles for email, Slack, Teams, and the always-on in-app row](/img/screenshots/user-notifications-prefs.png)

| Channel | Default | Notes |
|---|---|---|
| In-app | on | The fallback channel. Always available; toggle still surfaces for symmetry. |
| Email | on | Requires `SMTP_*` configured by the operator. |
| Slack | off | Requires `SLACK_WEBHOOK_URL` configured. |
| Teams | off | Requires `TEAMS_WEBHOOK_URL` configured. |

Toggles enter a draft state. Click **Save** to persist your changes — the page tracks dirty state and disables Save until something changes.

## How fresh is the bell?

The frontend polls the unread count every **60 seconds** while the tab is active. Two optimisations keep the channel lean:

- When the browser tab is **hidden** (Page Visibility API reports `hidden`), polling **pauses** to conserve battery and server bandwidth.
- When the tab regains focus, the frontend fires an **immediate** poll so the badge catches up before the next 60-second tick.

If you have multiple portal tabs open, each polls independently — the unread state is server-authoritative, so the counts converge.

## Triggers

Six distinct triggers fire notifications:

| Trigger | When it fires |
|---|---|
| `scan_completed` | A scan you started, or one on a project you watch, finishes successfully. |
| `scan_failed` | A scan you started, or one on a project you watch, fails. |
| `cve_detected` | A new CVE lands on a component already present in one of your scans (DT NVD ingest correlates against existing components). |
| `license_violation` | A scan surfaces a forbidden-license component on a project you watch. |
| `approval_pending` | A component requires approval, and you are a designated approver on the project. |
| `policy_gate_failed` | A CI build gate fails (Critical CVE or forbidden license blocks the build). |

Channel selection is global — the **Preferences** tab decides which channels deliver every trigger.

## Verify it worked

- Trigger a scan on a project you own; within seconds of completion, the bell badge increments and `/notifications` shows the new row.
- Open `/notifications` in a second tab and mark a row read; the first tab's bell badge decrements within 60 seconds.
- Globally disable email under **Preferences** and click **Save**; run another scan and confirm the next `scan_completed` notification arrives via in-app only.

## Troubleshooting

- **Bell badge never updates** — the tab may be hidden behind another window. Bring it to the foreground; the immediate poll on focus refreshes the count.
- **Email never arrives** — verify the operator has configured SMTP and that the destination address is your verified email (visible on `/profile`).
- **Slack message never arrives** — confirm the operator has set `SLACK_WEBHOOK_URL` and that the channel still exists. Slack returns 404 silently when a webhook is revoked.

## Roadmap (v2.x)

Items the manual previously promised that are not in v2.0.0; tracked for later releases.

- Header-bell dropdown with the five most recent notifications and a "go to inbox" footer — planned for v2.1; today the bell navigates straight to `/notifications`.
- Infinite scroll on `/notifications` — planned for v2.1; today the inbox uses Previous / Next pagination.
- Per-trigger × per-channel preference matrix (e.g. Slack only for `policy_gate_failed`) — planned for v2.1; today the channel choice is global across all triggers.
- `disk_pressure` notification trigger for admins — planned for v2.2; the disk-pressure event is currently surfaced only on the admin dashboard.

## See also

- [Authentication & profile](./auth-and-profile.md) — your verified email is the destination for email notifications.
- [Integrations](./integrations.md) — webhook secrets are separate from notification channels.
- [Disk & system health](../admin-guide/disk-and-health.md) — operator setup for SMTP / Slack / Teams.
