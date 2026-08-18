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
| **Rejected** | Reviewer. | Component should be removed; verdict is recorded for audit. See the [Rejected verdict caveat](#rejected-verdict) — the build gate does **not** auto-block on a Rejected verdict in this release. |

Transitions are recorded in the audit log.

## The approvals queue

Sidebar → **Approvals**. Filters: status (state) and a date range against `requested_at`.

Each row shows:

- **Component** — the component's display name with the package URL (`purl`) on a second monospace line; falls back to the first 8 characters of the component UUID when the row's underlying component name could not be resolved.
- **Project** — the project the request is scoped to, rendered as a click-through link to `/projects/{id}` with the project name (one row per project even when the same component appears in many). Falls back to the project UUID prefix when the name is unavailable. The link stops click propagation so opening the project does not also open the row's drawer.
- **Status** — Pending / Under Review / Approved / Rejected.
- **Requested by** — the user (or system) that created the request.
- **Requested at** — the request timestamp.
- **Actions** — the disposition controls for your role (drawer entry, etc.).

The list endpoint resolves the component and project display fields with two batched `IN(...)` lookups (the `ComponentApproval` model intentionally does not carry cross-domain relationships), so a queue with hundreds of rows still renders in one round-trip.

![/approvals queue — table with Pending / Under Review / Approved / Rejected status badges, component identity, project, requested-by actor, and per-row Actions](/img/screenshots/user-approvals-inbox.png)

## Requesting approval

When the scan pipeline detects a new conditional-license component, a Pending request is created automatically. No manual action required.

The portal exposes a `POST /v1/approvals` endpoint for clients that need to seed a request before a scan runs (e.g., adding the dependency in a PR you have not yet pushed). The matching UI form is deferred — see [Roadmap](#roadmap).

## Disposing a request

1. Open the row to slide in the drawer.
2. Click **Start Review** — the state moves to Under Review and the reviewer field is set to you.
3. Read the license terms and the obligations the portal lists.
4. Choose **Approve** or **Reject**. Both prompt for an optional **decision note** (`decision_note`, ≤ 2000 chars). The note is stored on the approval row for audit.

![Approval drawer — Pending status with Start Review and Reject decision buttons](/img/screenshots/user-approvals-decision-drawer.png)

From **Pending**, **Reject** is also available directly without going through Review — useful when the request is a clear miss.

A successful disposition:

- Locks the verdict on the underlying component for that project.
- Records the verdict in the audit log.
- Updates the project's risk score on the next scan.
- (If notification triggers are enabled) emails the requester and the team.

### Rejected verdict {#rejected-verdict}

:::warning
An approval marked **Rejected** does **not** currently re-classify the
underlying component as forbidden in the build gate — the gate
evaluates the `forbidden` license tier only (see
`apps/backend/services/policy_gate.py`). The Rejected verdict is
recorded on the approval row and in the audit log for evidence, but at
v0.10.0 it does **not** block CI: a subsequent scan still classifies
the component as `conditional` and the build proceeds. Until
promotion-on-rejection lands (the roadmap), enforce the verdict
out-of-band — e.g. open a tracking issue against the project and
remove the dependency in code review.
:::

## Cross-project approvals

When the same component appears in multiple projects, each project gets its own Pending request. A verdict never propagates on its own, because:

- Projects can have different distribution models (closed-source SaaS vs. shipped binary).
- The same license has different obligations depending on the linkage model (LGPL static vs. dynamic).

For components where the answer really is the same everywhere, an organization can rule once instead of asking every team the same question.

### Organization-wide rulings {#organization-verdicts}

A **ruling** is one answer about a component, recorded for the whole organization. It applies to every project that has not decided for itself, and it never overrides one that has.

Only a super admin opens and decides a ruling, because the answer reaches every team. Any member of the organization can read the rulings and their reasons: a component showing as Approved in your project may be inherited, and you should be able to see why.

What members read is the **reason**, not the deliberation. The note an administrator writes while deciding, and the names of the people who requested and decided, are returned only to callers who could have written them. Assume the reason itself is read by everyone in the organization, because it is.

A ruling moves through the same four states as a per-project request, carries a reason (required, unlike the per-project note), and uses the same `If-Match` version so two administrators cannot silently overwrite each other.

**What applies to a project, in order:**

1. The project's own decided approval, if it has one. Approved or Rejected both count.
2. Otherwise the organization's decided ruling, if there is one.
3. Otherwise nothing has been decided.

Only *decided* answers fall through. A ruling still Pending or Under Review is a question, not an answer, and does not reach any project.

The direction is deliberate: a team that reviewed a component in the context of its own use knows something the organization does not, so a local decision wins. Where a team has not decided, the organization is answering a question nobody local answered, which is what a default is for.

**What a ruling does not do.** It does not touch open per-project requests. A team reviewing a component keeps that review, and their answer will win when they reach it. It does not write anything onto projects either: the fallback happens when somebody reads, so a ruling is cheap to make and cheap to change.

**Changing the organization's mind.** A decided ruling is a record and is not edited. Open a new ruling on the same component and decide it; the new answer applies from then on and the old one stays as evidence of what was decided before.

**Where you see it.** A component drawer whose answer came from the organization shows a note saying so, with the reason. A component your team decided shows nothing extra, because the status already tells you.

## Integration with external review systems

The portal can post approval requests to an external system (e.g., Jira) via webhooks. See [admin notifications](../admin-guide/vulnerability-data.md#notifications) — the **approval requested** trigger wires the same event to email, Slack, Teams, and an outbound HTTP POST.

A typical flow:

1. Scan pipeline creates a Pending request → portal POSTs to your Jira automation.
2. Jira creates a ticket and assigns a legal reviewer.
3. Reviewer dispositions in the portal; portal POSTs the verdict back to Jira; Jira closes the ticket.

## Verify it worked

After disposing a request:

<!-- docs-uat: id=approvals-state-badge-updates kind=ui harness=approvalsDispose tier=nightly -->
1. The state badge updates immediately.
<!-- docs-uat: id=approvals-audit-recorded kind=sql ctx=postgres expect=rows:>0 tier=nightly -->
2. The audit log records `target_table=component_approvals&action=update` with `previous_status`, `new_status`, `decision_note` in the diff.

   ```sql
   SELECT count(*) FROM audit_logs
    WHERE target_table = 'component_approvals'
      AND action = 'update'
      AND diff ? 'previous_status'
      AND diff ? 'new_status'
      AND created_at > now() - interval '1 hour';
   ```

<!-- docs-uat: id=approvals-requester-notified kind=sql ctx=postgres expect=rows:>0 retry=10x3s tier=nightly -->
3. The original requester (if any) receives a notification per the team's notification settings.

   ```sql
   SELECT count(*) FROM notifications
    WHERE kind = 'approval_state_changed';
   ```
<!-- docs-uat: id=approvals-reject-no-autoblock kind=manual tier=manual -->
4. **Note**: a Rejected verdict does **not** auto-promote the component to `forbidden` in the next scan's build gate in this release — see the [Rejected verdict caveat](#rejected-verdict) for the manual follow-up.

## Troubleshooting

### Approval queue is empty but conditional-license components exist

The request was already disposed (Approved / Rejected). The default queue view filters to Pending + Under Review. Switch the state filter to **All**.

### Cannot start review on a request

You need `team_admin` or higher on the project's owning team. Ask a team admin to delegate, or change the project's owning team.

### Rejected verdict did not block the next CI build

By design in this release — see the [Rejected verdict caveat](#rejected-verdict). The build gate evaluates the `forbidden` license tier only; the approval verdict does not back-propagate to the underlying license category. To block the build, either remove the dependency or escalate the underlying license to `forbidden` via the classifier-dictionary patch path (Operator-only).

### Approved verdict still warns in the next scan

The state badge update is immediate, but the project's risk score and the conditional-warning surface only refresh after a new scan completes. If a scan was already in flight when you disposed the request, that scan still reflects the previous state. Trigger a new scan.

## Roadmap

Items the manual previously promised that are not in this release; tracked for later releases.

- Filter by project / license / component / requested-by on the queue toolbar — planned; today only **status** + **date range** are exposed.
- License / Reviewer / Justification columns on the queue rows — planned; today these surface inside the drawer only.
- "New request" UI form (Project / purl / Justification) — planned; the `POST /v1/approvals` endpoint is the only way to seed a manual request today.
- Multi-select bulk verdict for `team_admin` reviewers — planned.

## See also

- [Components & licenses](./components-and-licenses.md)
- [Audit log](../admin-guide/audit-log.md)
- [Users & teams — roles](../admin-guide/users-and-teams.md#roles)
