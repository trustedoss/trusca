---
id: scheduled-scans
title: Scheduled scans
description: Set a daily or weekly cadence so a project scans itself automatically, at the organization or project level.
sidebar_label: Scheduled scans
sidebar_position: 5
---

# Scheduled scans

A scheduled scan is one TRUSCA starts on its own, on a recurring cadence, with no person clicking **Scan** and no CI job or webhook delivery involved. It is a third way to start a `source` scan, alongside the manual trigger and the [webhook](./webhooks.md) / CI paths.

The contract is opt-in both ways: with no schedule configured anywhere, nothing scans on its own, and a fresh install behaves exactly as it did before this feature existed. Configuration in this release is **API-only**; there is no settings-page UI yet.

:::note Audience
`super_admin` setting an organization-wide default cadence, and `team_admin` (or above, within that project's team) setting or clearing a project's own cadence. `developer` and above can read a project's effective schedule.
:::

## Prerequisites

- A JWT session or [API key](../admin-guide/api-keys.md) for the role the endpoint requires (see [API endpoints](#api-endpoints)).
- The project's or organization's UUID.
- An IANA time zone name if the schedule should not run in UTC (e.g. `Asia/Seoul`).

## How a schedule is defined

A schedule is a fixed point on the clock, not a cron expression: one hour of one day (`daily`), or one hour of one weekday (`weekly`).

| Field | Type | Required | Notes |
|---|---|---|---|
| `is_active` | boolean | no (default `true`) | `false` means this row explicitly scans nothing; see [Project vs. organization](#project-schedule-vs-organization-default). |
| `cadence` | `"daily"` \| `"weekly"` \| `null` | no | `null` means this row decides nothing yet. |
| `hour` | integer 0-23 | required when `cadence` is set | Local hour-of-day the schedule fires, read in `timezone`. |
| `day_of_week` | integer 0-6 | required when `cadence` is `"weekly"`, forbidden when `"daily"` | `0` = Monday … `6` = Sunday. |
| `timezone` | IANA zone name | no (default `"UTC"`) | `hour` and `day_of_week` are read against this zone, not the server's. |

The API rejects an inconsistent combination with `422 Unprocessable Entity`:

- `cadence: "weekly"` with `day_of_week` missing.
- `cadence: "daily"` with `day_of_week` present.
- `cadence` set but `hour` missing.
- A `timezone` that is not a recognized IANA name.

## Project schedule vs. organization default

Two scopes exist, and only one ever governs a given project:

- **Organization default**, one row per organization (`project_id` is null). A super-admin sets it once and it covers every project that has not made its own decision.
- **Project schedule**, one row per project, set by that project's `team_admin` or above. It overrides the organization default the moment it exists.

The fall-through is whole-row, not per-field. A project that has written its own schedule always wins over the organization default, **even if that schedule is `is_active: false`**: an explicit "no automatic scans here" is a decision, and it is never blended with, or overridden by, the organization default. Only a project with **no row of its own** inherits the organization default. A project with neither a row of its own nor an organization default to fall back to has no scheduled scans at all.

Use [`GET /v1/scan-schedules/effective/{project_id}`](#check-what-will-actually-fire) to see which of the two (or neither) governs a given project. Its `source` field answers `"project"`, `"organization"`, or `"none"`.

## What happens when a schedule fires

A due schedule enqueues a scan the same way a webhook delivery does:

- The scan is always `kind: "source"`.
- It passes through the same guards every other scan does: the team's concurrent-scan cap, the workspace disk-usage guard, and the one-active-scan-per-project rule. If a scan is already queued or running for the project, the schedule is skipped for this tick; it does not queue a second one.
- Archived projects are excluded before a schedule is ever evaluated: archiving a project stops its scheduled scans without touching its schedule row.

## Completion notifications

When a schedule-triggered scan reaches a terminal state (`succeeded` or `failed`), every member of the owning team is notified, except service accounts, which have no inbox to deliver to. This is the one place a scheduled scan behaves differently from a manual, webhook, or CI-triggered scan: those are already being watched by whoever (or whatever) started them, so they do not add this extra notification.

The notification uses the same `scan_completed` / `scan_failed` triggers documented in [Notifications → Triggers](../user-guide/notifications.md#triggers); a scheduled scan simply reaches every team member unconditionally instead of only the person who started it. Delivery still goes through your own channel [Preferences](../user-guide/notifications.md#preferences), and if the organization or team has [routing rules](../user-guide/notifications.md#routing-rules) configured, those rules can still add outbound recipients or channels on top.

## The poller

One Celery beat task, fixed at a **15-minute interval**, evaluates every schedule on every tick. The number of configured schedules does not change this: the poller is one task regardless of whether one project or every project in the deployment has a schedule.

A schedule that is due does not refire on every tick within its due window; the poller tracks the last time it triggered each row and fires once per local day (`daily`) or once per local week (`weekly`).

## API endpoints

All paths are under `/v1/scan-schedules`.

| Method | Path | Permission | Description |
|---|---|---|---|
| `PUT` | `/org/{organization_id}` | `super_admin` | Create or replace the organization's default schedule. |
| `PUT` | `/projects/{project_id}` | `team_admin`+ on the project's team | Create or replace that project's own schedule. |
| `GET` | `/projects/{project_id}` | any team member (`developer`+) | Read the project's own schedule row. `404` if the project has none of its own; see [`effective`](#check-what-will-actually-fire) for "what applies here" instead. |
| `DELETE` | `/projects/{project_id}` | `team_admin`+ on the project's team | Remove the project's own schedule so it follows the organization default again. `204` on success, `404` if it had none. |
| `GET` | `/effective/{project_id}` | any team member (`developer`+) | The schedule that actually governs this project, and which scope (`project` / `organization` / `none`) supplied it. |

## Set the organization default

<!-- docs-uat: id=scheduled-scans-org-put kind=shell ctx=host tier=manual waiver=example-host-and-jwt-placeholder -->
```bash
curl -sS -X PUT "https://trustedoss.example.com/v1/scan-schedules/org/<organization-uuid>" \
  -H "Authorization: Bearer ${JWT}" -H "Content-Type: application/json" \
  -d '{"is_active": true, "cadence": "daily", "hour": 3, "timezone": "UTC"}'
```

This runs a `source` scan at 03:00 UTC every day for every project in the organization that has not set its own schedule.

## Set a project's own schedule

<!-- docs-uat: id=scheduled-scans-project-put kind=shell ctx=host tier=manual waiver=example-host-and-jwt-placeholder -->
```bash
curl -sS -X PUT "https://trustedoss.example.com/v1/scan-schedules/projects/<project-uuid>" \
  -H "Authorization: Bearer ${JWT}" -H "Content-Type: application/json" \
  -d '{"is_active": true, "cadence": "weekly", "hour": 9, "day_of_week": 0, "timezone": "Asia/Seoul"}'
```

This project now scans every Monday at 09:00 in `Asia/Seoul`, regardless of what the organization default says.

To opt a project out of the organization default without deleting anything, `PUT` the same shape with `"is_active": false` (`cadence` may stay set or be cleared to `null`; either way the row is authoritative).

## Check what will actually fire

<!-- docs-uat: id=scheduled-scans-effective-get kind=shell ctx=host tier=manual waiver=example-host-and-jwt-placeholder -->
```bash
curl -sS -H "Authorization: Bearer ${JWT}" \
  "https://trustedoss.example.com/v1/scan-schedules/effective/<project-uuid>"
```

```json
{
  "project_id": "<project-uuid>",
  "is_active": true,
  "cadence": "weekly",
  "hour": 9,
  "day_of_week": 0,
  "timezone": "Asia/Seoul",
  "source": "project"
}
```

`source` is `"organization"` when no project row exists and the organization default applies, and `"none"` when neither exists.

## Verify it worked

<!-- docs-uat: id=scheduled-scans-verify-effective kind=manual tier=manual -->
1. `GET /v1/scan-schedules/effective/{project_id}` returns the cadence you expect, with `source` naming the scope that supplied it.
<!-- docs-uat: id=scheduled-scans-verify-scan-appears kind=manual tier=manual -->
2. At the configured local hour, a new scan for the project appears with `kind: "source"` and `scan_metadata.trigger` equal to `"schedule"`.
<!-- docs-uat: id=scheduled-scans-verify-notification kind=manual tier=manual -->
3. When that scan finishes, every member of the project's team (other than service accounts) receives a `scan_completed` or `scan_failed` notification.

## Troubleshooting

### The schedule never fires

Confirm the effective view first: a typo in the project write, or a project row that unexpectedly shadows the organization default, both show up in [`GET /effective/{project_id}`](#check-what-will-actually-fire):

- `source: "none"`: neither the project nor the organization has an active cadence. Set one of them.
- `is_active: false`: this scope has explicitly turned scheduling off. If you expected the organization default to apply, check whether this project has its own row at all (`GET /projects/{project_id}`); if it does, delete it to fall back to the organization default.
- `cadence: null`: a row exists but no cadence was ever written on it.

If the effective view looks correct, check the time zone: `hour` and `day_of_week` are read in the schedule's own `timezone`, not the server's or your browser's.

### The hour passes but no scan appears

The poller only starts a scan when the project is otherwise clear to scan, the same guards a webhook delivery hits:

- A scan for this project was already queued or running at poll time, so this schedule was skipped for the current window. It fires again at its next due window (next day, or next week), not later in the same one.
- The owning team is at its concurrent-scan cap, or the workspace disk guard is tripped. Both clear on their own once capacity frees up or disk usage drops; the schedule does not need to be re-armed.
- The project is archived. Archived projects are excluded from every poll regardless of any schedule row.

### `422 Unprocessable Entity` on a `PUT`

The four checks in [How a schedule is defined](#how-a-schedule-is-defined) are the exhaustive list: a `weekly` cadence without `day_of_week`, a `daily` cadence with `day_of_week` set, a `cadence` with no `hour`, or a `timezone` that is not a valid IANA name. The response body names which one failed.

### `403 Forbidden` on a `PUT` or `DELETE`

The organization endpoint is `super_admin`-only. The project endpoints require `team_admin` or above **on that project's own team**: belonging to a different team, or holding `developer` on the right team, both answer `403`.

### `404 Not Found` on `GET /projects/{project_id}`

This endpoint returns the project's **own** row only, and 404s when the project has none; that is not an error state, it just means the organization default (if any) applies. Use [`GET /effective/{project_id}`](#check-what-will-actually-fire) to see what actually governs the project instead of what it wrote itself.

## See also

- [Webhooks](./webhooks.md): the event-triggered counterpart to a scheduled scan; both share the same scan pipeline and guards.
- [Notifications](../user-guide/notifications.md): channel preferences and organization/team routing rules that apply on top of a completion notification.
- [API keys](../admin-guide/api-keys.md): issuing a non-interactive credential to call these endpoints from a script rather than a signed-in session.
