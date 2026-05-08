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
| **Rejected** | Reviewer. | Component must be removed; the build gate will treat it as forbidden. |

Transitions are recorded in the audit log.

## The approvals queue

Sidebar → **Approvals**. Filters: state, project, license, component, requested-by.

Each row shows:

- Component (`name@version`, `purl`).
- Detected concluded license.
- Affected projects (a single component can be present in many).
- Requested timestamp.
- Reviewer (set when the state moves to Under Review).
- Justification text from the requester (optional but encouraged).

## Requesting approval

When the scan pipeline detects a new conditional-license component, a Pending request is created automatically. No manual action required.

If you need to request approval **before** a scan runs (e.g., adding the dependency in a PR you have not yet pushed):

1. Sidebar → **Approvals** → **New request**.
2. Fill out:
   - **Project** — the project the component will land on.
   - **purl** — the Package URL of the component (e.g. `pkg:maven/org.eclipse.jetty/jetty-server@11.0.20`).
   - **Justification** — why this component is needed.
3. Submit.

## Disposing a request

1. Open the row to slide in the drawer.
2. Click **Claim** — the state moves to Under Review and the reviewer field is set to you.
3. Read the license terms and the obligations the portal lists.
4. Choose **Approve** or **Reject**. Both require a justification (≥ 10 chars).

A successful disposition:

- Locks the verdict on the underlying component for that project.
- Records the verdict in the audit log.
- Updates the project's risk score on the next scan.
- (If notification triggers are enabled) emails the requester and the team.

## Bulk operations

`team_admin` and higher can multi-select rows and apply a bulk verdict. The justification is shared across all selected rows. Use this sparingly — most reviews need component-by-component reasoning.

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
2. The next scan honors the verdict (Rejected → build gate treats as forbidden).
3. The audit log records `approval.update` with `previous_state`, `new_state`, `justification`.
4. The original requester (if any) receives a notification per the team's notification settings.

## Troubleshooting

### Approval queue is empty but conditional-license components exist

The request was already disposed (Approved / Rejected). The default queue view filters to Pending + Under Review. Switch the state filter to **All**.

### Cannot claim a request

You need `team_admin` or higher on the project's owning team. Ask a team admin to delegate, or change the project's owning team.

### Verdict was not applied to the next scan

The next scan must complete after the verdict. If a scan was already in flight when you disposed the request, that scan still reflects the previous state. Trigger a new scan.

## See also

- [Components & licenses](./components-and-licenses.md)
- [Audit log](../admin-guide/audit-log.md)
- [Users & teams — roles](../admin-guide/users-and-teams.md#roles)
