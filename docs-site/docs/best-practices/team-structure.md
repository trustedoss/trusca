---
id: team-structure
title: Team structure
description: Organize TRUSCA Organizations, Teams, and roles — Super Admin / Team Admin / Developer, team-only vs org-wide project visibility, and when to split a team.
sidebar_label: Team structure
sidebar_position: 3
---

# Team structure

TRUSCA models authorization as one **Organization**, many **Teams**, and three **Roles**. Projects belong to teams; roles are granted per team. The structure decides who can scan what, who can dispose approvals, and who sees which projects — so it is worth designing rather than growing by accident. This page helps you choose a team layout that matches how your engineering org actually works.

:::note Audience
`super_admin` laying out the organization at install time, and `team_admin` deciding whether their team should split. Familiarity with the [RBAC model](../admin-guide/users-and-teams.md). RBAC is Role-Based Access Control — permissions attach to roles, not individuals.
:::

## The model, in one picture {#the-model}

```
Organization (one per deployment)
├── Super Admin        — system-wide: all /admin screens, create/delete teams, edit any project
├── Team A
│   ├── Team Admin     — manages Team A's membership, settings, policy, API keys; disposes approvals
│   └── Developer      — runs scans, triages findings in Team A's projects
└── Team B
    └── ...
```

There is exactly one Organization per deployment. A user can hold a **different role in each team** — `team_admin` in one, `developer` in another — because roles are additive across teams and evaluated per project by the project's owning team. See the full [role capabilities table](../admin-guide/users-and-teams.md#roles).

## Choosing roles {#roles}

Grant the least role that lets someone do their job:

| Role | Give it to | Not for |
|---|---|---|
| **`super_admin`** | The operators who run the deployment — install, upgrade, backup, Trivy DB, org-wide policy. | Ordinary engineers. A super-admin can read every team's data. |
| **`team_admin`** | A team's tech lead or compliance owner — manages members, team policy, API keys, and disposes approvals. | Every engineer on the team. |
| **`developer`** | Engineers who scan and triage. | Anyone who only needs to read a report — reading is already covered by team membership. |

:::caution Keep at least two super-admins
The portal refuses to demote or deactivate the *last* active `super_admin`, but that guard is a floor, not a plan. Always have a second super-admin so an off-boarding or a forgotten password never locks you out of `/admin`. See [last-super-admin protection](../admin-guide/users-and-teams.md#last-super-admin-protection).
:::

Grant `super_admin` sparingly — it is org-wide and bypasses team boundaries. Most compliance leads only need `team_admin` on the teams they own.

## Project visibility: team-only vs org-wide {#visibility}

Each team carries a default visibility for the projects it creates, set at [team creation](../admin-guide/users-and-teams.md#creating-a-team):

| Visibility | Who can see the project | Use when |
|---|---|---|
| **`team_only`** (default) | Members of the owning team only | The default. Projects contain findings a team should triage before wider exposure. |
| **`org_wide`** | Every user in the organization (read) | A shared platform library, a reference SBOM, or a project whose risk posture the whole org should see. |

Visibility governs **read** exposure, not write. Editing, scanning, and disposing approvals still require a role on the owning team regardless of visibility. Start `team_only` and promote a project to `org_wide` deliberately — widening is easy, and you avoid leaking a team's in-progress triage to the whole org by default.

## When to split a team {#when-to-split}

A team is the unit of ownership, visibility, and policy. Split when those three want to diverge — not merely because the headcount grew:

- **Different policy needs.** One group ships a redistributed binary (copyleft is a real risk) and another ships a closed SaaS (copyleft is often fine). A per-team [license policy](../reference/license-policies.md) only helps if they are separate teams.
- **Different approval authority.** Approvals are disposed by a `team_admin` of the *owning* team. If group A must not sign off on group B's conditional-license usage, they need separate teams.
- **Visibility boundaries.** A confidential project should not be readable by unrelated engineers. Separate teams keep `team_only` meaningful.
- **Independent membership churn.** Contractors or a partner team you on/off-board on a different cadence belong in their own team, so a membership change never touches your core team.

Reasons **not** to split: a shared component that several teams consume (make its project `org_wide` instead), or a temporary sub-group (a role grant is lighter than a team). Every extra team is another membership list and policy to keep current — split for a boundary, not for tidiness.

:::note One person, many teams
Because roles are per team, a platform engineer can be `team_admin` on the shared-infra team and `developer` on a product team without a second account. Splitting teams does not fragment a person's access — add the membership at the right role instead.
:::

### A component shared across teams {#shared-components}

When the same dependency shows up in several teams' projects, each project raises its **own** approval request — verdicts do not propagate, because the same license can carry different obligations under different distribution models. That is deliberate; see [cross-project approvals](../user-guide/approvals.md#cross-project-approvals). If you want one ruling to apply everywhere, either dispose each project's request and reference the originating decision, or encode the verdict as a license override in the org-default policy so every team inherits it.

## Verify it worked

<!-- docs-uat: id=bp-team-structure-review kind=manual tier=manual -->
Review the layout you chose:

<!-- docs-uat: id=bp-team-structure-1 kind=manual tier=manual -->
1. Every engineer holds the least role that lets them work — `developer` unless they manage members, policy, or approvals.
<!-- docs-uat: id=bp-team-structure-2 kind=manual tier=manual -->
2. At least two active `super_admin` accounts exist.
<!-- docs-uat: id=bp-team-structure-3 kind=manual tier=manual -->
3. New projects default to `team_only`; any `org_wide` project is a deliberate choice, not the default.
<!-- docs-uat: id=bp-team-structure-4 kind=manual tier=manual -->
4. Teams that need different license policy or different approval authority are actually separate teams, and a person needing access to several teams is added to each at the right role rather than promoted to `super_admin`.
<!-- docs-uat: id=bp-team-structure-5 kind=manual tier=manual -->
5. A shared dependency's approval verdicts are handled per project (or encoded once in the org-default policy) — you are not expecting them to propagate.

## See also

- [Users & teams](../admin-guide/users-and-teams.md) — roles, membership, last-super-admin protection, team creation
- [Users & teams — roles](../admin-guide/users-and-teams.md#roles) — the full capability table
- [Approvals — cross-project approvals](../user-guide/approvals.md#cross-project-approvals) — why verdicts do not propagate
- [Policy design](./policy-design.md) — org-default vs per-team policy
