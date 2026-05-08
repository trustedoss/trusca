---
id: users-and-teams
title: Users & teams
description: Manage TrustedOSS Portal users and teams — RBAC roles, last-super-admin protection, invitations, and the org-vs-team model.
sidebar_label: Users & teams
sidebar_position: 1
---

# Users & teams

The portal models authorization as one **Organization**, many **Teams**, and three **Roles**. Every user belongs to one or more teams, and projects belong to teams. There is exactly one organization per deployment.

:::note Audience
Super-admins setting up the deployment; team admins managing their team's membership.
:::

## The model

```
Organization (one per deployment)
├── Super Admin            — system-wide admin (you, after install.sh)
├── Team A
│   ├── Team Admin         — manages team settings + members
│   └── Developer          — runs scans, triages findings
└── Team B
    └── ...
```

- **Organization** — the boundary of the deployment. Super-admins are scoped to the org.
- **Team** — projects, scans, and findings live inside a team.
- **User** — a person with an email + password (or OAuth identity for the demo SaaS).

## Roles {#roles}

| Role | Scope | Capabilities |
|---|---|---|
| **`super_admin`** | Org-wide | All admin screens (`/admin/**`). Can create / delete teams. Can edit any project. Can read every audit log. |
| **`team_admin`** | Per team | Manage team membership and team settings. Edit any project owned by the team. Dispose approvals. Manage API keys for the team. |
| **`developer`** | Per team | Read team projects. Create / edit projects. Run and cancel scans. Triage findings (VEX state). Cannot manage members or settings. |

Roles are **additive across teams** — a user can be `team_admin` in one team and `developer` in another. The role is evaluated per project based on the project's owning team.

`super_admin` is **not** a per-team role; it grants org-wide access regardless of team membership.

## Inviting a user

### As a super-admin

1. **/admin/users** → **Invite user**.
2. Email, name, default team, role on that team.
3. Submit.

The invited user receives an email with a one-time invitation link (24-hour expiry). Clicking the link prompts them to set a password (≥ 12 chars, bcrypt cost 12, no NIST-banned passwords).

### As a team admin

You can only invite into teams where you have `team_admin`. The flow is the same minus the team selector.

## Adding an existing user to a team

Users can belong to many teams. To add an existing user:

1. **/admin/teams** (super-admin) or **Team settings → Members** (team admin).
2. **Add member** → search by email → choose role.

The user is added immediately; no email confirmation step is sent (they already have an account).

## Changing a role

1. **/admin/users** → user → **Memberships**.
2. Click **Change role** on the relevant team row.
3. Choose new role → submit.

Audit log records `team_membership.update` with `previous_role` and `new_role`.

## Removing a user from a team

1. **Team settings → Members** → user → **Remove**.

The user loses access to the team's projects but their account remains. To deactivate the account entirely, see [deactivation](#deactivating-a-user).

## Last-super-admin protection

The portal **refuses** to demote or deactivate the last `super_admin` in the organization. If you try, the API returns:

```json
{
  "type": "https://trustedoss.io/problems/last-super-admin",
  "title": "Cannot demote the last super_admin",
  "status": 409,
  "detail": "At least one super_admin must remain in the organization.",
  "instance": "/api/v1/admin/users/01H…/role"
}
```

To replace the last super-admin:

1. Promote a second user to `super_admin` first.
2. Then demote / deactivate the original.

This rule is enforced at the database level (a `CHECK` constraint plus the API's pre-flight check), not just in the UI — there is no way to bypass it through direct SQL without disabling the constraint.

## Deactivating a user

Deactivation revokes all sessions and refresh tokens. The user cannot sign in. Their audit-log entries persist (rows are append-only).

1. **/admin/users** → user → **Deactivate**.
2. Confirm.

Reactivation is a single click on the same screen.

## Deletion vs. deactivation

- **Deactivate** — keeps the user row, breaks the foreign keys cleanly. Default and recommended.
- **Delete** — soft-delete the user. Their account is unrecoverable but their audit-log entries reference the deleted user's UUID. Use for GDPR right-to-erasure requests; otherwise prefer deactivate.

The "Delete" button is hidden behind a typed-email confirmation modal.

## Creating a team

`super_admin` only.

1. **/admin/teams** → **New team**.
2. Name, description, optional default visibility for new projects (`team_only` or `org_wide`).
3. Submit.

The first member of the team is whoever you assign on the next screen.

## Renaming / archiving a team

`super_admin` and the team's `team_admin` can rename. Archiving requires `super_admin` and:

- Hides the team from default lists.
- Disables new project creation.
- Keeps existing projects, scans, and findings readable.

To delete a team, all its projects must first be archived or moved.

## Sessions

| Token | Lifetime | Storage |
|---|---|---|
| **Access token (JWT)** | 30 minutes | Memory (in-app), `Authorization: Bearer …`. |
| **Refresh token** | 7 days, with rotation + reuse detection. | HttpOnly + Secure cookie, SameSite=Lax. |

Reuse detection: if a refresh token is presented twice, the entire token family is invalidated and the user is forced to re-authenticate on every device. This catches refresh-token theft.

## Verify it worked

After inviting a user:

1. **/admin/users** lists the user with `pending` status.
2. The audit log records `user.invite`.
3. After the user activates the link, the status flips to `active`.
4. The user appears in the team's member list with the assigned role.

## Troubleshooting

### Invitation email never arrived

Check `SMTP_*` in `.env`. The email worker logs the SMTP transaction:

```bash
docker-compose -f docker-compose.yml logs --tail=200 worker | grep -i smtp
```

Common causes: missing `SMTP_USER` / `SMTP_PASSWORD`, the SMTP host blocking the worker IP, the recipient's spam filter. Re-issue the invitation from the user row — a fresh link is generated each time.

### Cannot promote my own role

Self-elevation is blocked. Ask another `super_admin` to do it. If you are the only super-admin, sign in as another super-admin (you should always have at least two).

### "User already exists" when inviting

The email is already registered (possibly under a different team). Add them to the team via [Adding an existing user to a team](#adding-an-existing-user-to-a-team) instead.

## See also

- [API keys](./api-keys.md) — service-account credentials
- [Audit log](./audit-log.md)
- [Approvals](../user-guide/approvals.md)
