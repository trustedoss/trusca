---
id: approvals
title: Approvals
description: Component approval workflow for conditional licenses — Pending, Under Review, Approved, Rejected — and how to integrate with legal review.
sidebar_label: Approvals
sidebar_position: 6
---

# Approvals

Components carrying a **conditional license** (LGPL, MPL, EPL, CDDL) trigger an approval workflow. The build proceeds, but the component shows up on the **Approvals** page until a reviewer with sufficient authority disposes it.

:::note Audience
Engineers requesting approval; legal / compliance reviewers and `team_admin` role members disposing requests.
:::

## State machine

```
Pending ──► Under Review ──► Approved
                       └──► Rejected
```

| State | Set by | Meaning |
|---|---|---|
| **Pending** | Auto, when a conditional-license component is first detected. | Awaiting a reviewer to claim it. |
| **Under Review** | Reviewer (`team_admin` or higher). | A reviewer has claimed the request and is investigating. |
| **Approved** | Reviewer. | Use of the component is approved subject to the noted obligations. |
| **Rejected** | Reviewer. | Component should be removed; verdict is recorded for audit. See the [Rejected verdict caveat](#rejected-verdict-at-v200) — the build gate does **not** auto-block on a Rejected verdict at v2.0.0. |

Transitions are recorded in the audit log.

## The approvals queue

Sidebar → **Approvals**. Filters: status (state) and a date range against `requested_at`.

Each row shows:

- **Component** — `name@version`.
- **Project** — the project the request is scoped to (one row per project even when the same component appears in many).
- **Status** — Pending / Under Review / Approved / Rejected.
- **Requested by** — the user (or system) that created the request.
- **Requested at** — the request timestamp.
- **Actions** — the disposition controls for your role (drawer entry, etc.).

![Approvals queue with status, component, and last-actor columns.](/img/screenshots/user-approvals-inbox.png)

## Requesting approval

When the scan pipeline detects a new conditional-license component, a Pending request is created automatically. No manual action required.

The portal exposes a `POST /v1/approvals` endpoint for clients that need to seed a request before a scan runs (e.g., adding the dependency in a PR you have not yet pushed). The matching UI form is deferred — see [Roadmap](#roadmap-v2x).

## Disposing a request

1. Open the row to slide in the drawer.
2. Click **Start Review** — the state moves to Under Review and the reviewer field is set to you.
3. Read the license terms and the obligations the portal lists.
4. Choose **Approve** or **Reject**. The reviewer's `decision_note` is optional (max length 2000, no minimum).

A successful disposition:

- Locks the verdict on the underlying component for that project.
- Records the verdict in the audit log.
- Updates the project's risk score on the next scan.
- (If notification triggers are enabled) emails the requester and the team.

### Rejected verdict at v2.0.0 {#rejected-verdict-at-v200}

:::warning
An approval marked **Rejected** does **not** currently re-classify the
underlying component as forbidden in the build gate — the gate
evaluates the `forbidden` license tier only (see
`apps/backend/services/policy_gate.py`). The Rejected verdict is
recorded on the approval row and in the audit log for evidence, but at
v2.0.0 it does **not** block CI: a subsequent scan still classifies
the component as `conditional` and the build proceeds. Until
promotion-on-rejection lands (v2.1 roadmap), enforce the verdict
out-of-band — e.g. open a tracking issue against the project and
remove the dependency in code review.
:::

## Cross-project approvals

When the same component appears in multiple projects, each project gets its own Pending request. The portal does not auto-propagate verdicts across projects because:

- Projects can have different distribution models (closed-source SaaS vs. shipped binary).
- The same license has different obligations depending on the linkage model (LGPL static vs. dynamic).

If you want a verdict to apply globally, mark each project's request explicitly and reference the originating decision in the justification.

## Integration with external review systems

The portal can post approval requests to an external system (e.g., Jira) via webhooks. See [admin notifications](../admin-guide/dt-connector.md#notifications) — the **approval requested** trigger wires the same event to email, Slack, Teams, and an outbound HTTP POST.

A typical flow:

1. Scan pipeline creates a Pending request → portal POSTs to your Jira automation.
2. Jira creates a ticket and assigns a legal reviewer.
3. Reviewer dispositions in the portal; portal POSTs the verdict back to Jira; Jira closes the ticket.

## Verify it worked

After disposing a request:

1. The state badge updates immediately.
2. The audit log records `target_table=component_approvals&action=update` with `previous_status`, `new_status`, `decision_note` in the diff.
3. The original requester (if any) receives a notification per the team's notification settings.
4. **Note**: a Rejected verdict does **not** auto-promote the component to `forbidden` in the next scan's build gate at v2.0.0 — see the [Rejected verdict caveat](#rejected-verdict-at-v200) for the manual follow-up.

## Troubleshooting

### Approval queue is empty but conditional-license components exist

The request was already disposed (Approved / Rejected). The default queue view filters to Pending + Under Review. Switch the state filter to **All**.

### Cannot start review on a request

You need `team_admin` or higher on the project's owning team. Ask a team admin to delegate, or change the project's owning team.

### Rejected verdict did not block the next CI build

By design at v2.0.0 — see the [Rejected verdict caveat](#rejected-verdict-at-v200). The build gate evaluates the `forbidden` license tier only; the approval verdict does not back-propagate to the underlying license category. To block the build, either remove the dependency or escalate the underlying license to `forbidden` via the classifier-dictionary patch path (Operator-only).

### Approved verdict still warns in the next scan

The state badge update is immediate, but the project's risk score and the conditional-warning surface only refresh after a new scan completes. If a scan was already in flight when you disposed the request, that scan still reflects the previous state. Trigger a new scan.

## Roadmap (v2.x)

Items the manual previously promised that are not in v2.0.0; tracked for later releases.

- Filter by project / license / component / requested-by on the queue toolbar — planned for v2.1; today only **status** + **date range** are exposed.
- License / Reviewer / Justification columns on the queue rows — planned for v2.1; today these surface inside the drawer only.
- "New request" UI form (Project / purl / Justification) — planned for v2.1; the `POST /v1/approvals` endpoint is the only way to seed a manual request today.
- Multi-select bulk verdict for `team_admin` reviewers — planned for v2.2.

## See also

- [Components & licenses](./components-and-licenses.md)
- [Audit log](../admin-guide/audit-log.md)
- [Users & teams — roles](../admin-guide/users-and-teams.md#roles)
