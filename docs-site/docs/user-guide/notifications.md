---
id: notifications
title: Notifications
description: Read, configure, and silence in-app, email, Slack, and Teams notifications for scan, gate, CVE, approval, and disk events.
sidebar_label: Notifications
sidebar_position: 8
---

# Notifications

The notification system tells you about events on projects you care about — scans finishing, gates failing, new CVEs landing on a component you depend on, approvals waiting, and disk pressure on the host. Notifications fan out across **four channels** (in-app, email, Slack, Microsoft Teams) and you decide which channels to receive on a per-trigger basis.

:::note Audience
Any signed-in user. The header bell and `/notifications` page are visible to every role; admins additionally configure the SMTP / Slack / Teams transports under [Disk & system health](../admin-guide/disk-and-health.md).
:::

## The header bell

Every page has a bell icon in the top-right of the header. The badge shows the number of **unread** in-app notifications:

- 0 — no badge.
- 1–99 — exact count.
- 100+ — capped at **99+** so the badge does not break the layout.

![Header bell with unread badge](./img/notifications-bell.png)

Click the bell to open a dropdown with the **five most recent** notifications. Each row shows the title, a one-line summary, and a relative timestamp. Click a row to mark it read, dismiss the dropdown, and navigate to the related page (e.g. the project page for a `scan_finished` event).

The dropdown footer links to the full inbox.

## The Inbox at `/notifications`

`/notifications` is the full list of every notification you have received, newest first. Scroll continuously — the page lazy-loads pages of 25 with infinite scroll.

![Notifications inbox](./img/notifications-inbox.png)

Each row shows:

- **Title** — bold while unread.
- **Body** — one or two lines.
- **Channel icons** — which channels delivered this event for you (in-app always shows; email / Slack / Teams show only if you opted in).
- **Timestamp** — absolute on hover, relative otherwise.
- **Mark read** — a click anywhere on the row marks it read; the row dim-loads.
- **Open** — clicking navigates to the source resource and marks read on the way.

Bulk actions sit in the toolbar: **Mark all as read** (only if there are unreads) and **Filter** (by trigger or by date range).

## Preferences

The **Preferences** tab (top-right of `/notifications`) lets you enable or disable each non-mandatory channel per trigger.

![Notifications preferences](./img/notifications-prefs.png)

| Channel | Toggle | Notes |
|---|---|---|
| In-app | **disabled toggle** (always on) | A tooltip on the toggle explains: *"In-app notifications cannot be disabled — this is your fallback channel."* |
| Email | on by default | Requires `SMTP_*` configured by the operator. |
| Slack | off by default | Requires `SLACK_WEBHOOK_URL` configured. |
| Teams | off by default | Requires `TEAMS_WEBHOOK_URL` configured. |

Changes save immediately. There is no **Save** button — toast feedback confirms each toggle.

## How fresh is the bell?

The frontend polls the unread count every **60 seconds** while the tab is active. Two optimisations keep the channel lean:

- When the browser tab is **hidden** (Page Visibility API reports `hidden`), polling **pauses** to conserve battery and server bandwidth.
- When the tab regains focus, the frontend fires an **immediate** poll so the badge catches up before the next 60-second tick.

If you have multiple portal tabs open, each polls independently — the unread state is server-authoritative, so the counts converge.

## Triggers

Five distinct triggers fire notifications:

| Trigger | When it fires | Default channels |
|---|---|---|
| `scan_finished` | A scan you started, or one on a project you watch, completes (success **or** failure). | in-app, email |
| `gate_failed` | A CI build gate (Critical CVE or forbidden license) fails on a project you watch. | in-app, email, Slack |
| `new_cve` | A new CVE lands on a component already present in one of your scans. | in-app, email |
| `approval_request` | A component requires approval, and you are a designated approver on the project. | in-app, email |
| `disk_pressure` | Workspace disk usage crosses the soft (80 %) or hard (95 %) limit on a host you administer. | in-app, email — admin-only |

Per-trigger channel matrices can be tuned under **Preferences**.

## Verify it worked

- Trigger a scan on a project you own; within seconds of completion, the bell badge increments and `/notifications` shows the new row.
- Open `/notifications` in a second tab and mark a row read; the first tab's bell badge decrements within 60 seconds.
- Disable email for `scan_finished`, run another scan, and confirm the next scan-finished notification arrives in-app only.

## Troubleshooting

- **Bell badge never updates** — the tab may be hidden behind another window. Bring it to the foreground; the immediate poll on focus refreshes the count.
- **Email never arrives** — verify the operator has configured SMTP and that the destination address is your verified email (visible on `/profile`).
- **Slack message never arrives** — confirm the operator has set `SLACK_WEBHOOK_URL` and that the channel still exists. Slack returns 404 silently when a webhook is revoked.

## See also

- [Authentication & profile](./auth-and-profile.md) — your verified email is the destination for email notifications.
- [Integrations](./integrations.md) — webhook secrets are separate from notification channels.
- [Disk & system health](../admin-guide/disk-and-health.md) — operator setup for SMTP / Slack / Teams.
