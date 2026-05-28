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

## The Users page

The `/admin/users` page lists every account in the deployment with role badges, activation status, last-sign-in timestamp, and team membership counts. Search by email or name; filter by role and status.

![Admin Users page — search/filter toolbar and the user table with role + status columns](/img/screenshots/admin-users-list.png)

The companion `/admin/teams` page enumerates teams and the projects + members each owns:

![Admin Teams page — per-team rows with member and project counts](/img/screenshots/admin-teams-list.png)

## Onboarding a new user

In this release the portal does not send invitation emails. New users join by **self-registering** at `/register` with their corporate email; the password policy is enforced at registration (≥ 12 chars, bcrypt cost 12, no NIST-banned passwords).

After they register, a `super_admin` adds them to the right team and assigns the role:

1. Ask the user to register at `/register`.
2. Once they appear under **/admin/users**, open the user drawer.
3. Use **Add to team** (or the team's **Members → Add member** flow) to grant team membership at the chosen role.

## Onboarding teammates

The portal does not send invitation emails in this release. The flow is:

1. **Admin** creates the team at `/admin/teams → New team`. (No need
   to copy the team UUID for the UI flow below — the admin matches
   teammates by email, not by team id. Scripted mass onboarding is
   the only path that needs the id; see `GET /v1/admin/teams` and
   the bulk recipe at the bottom of this section.)
2. **Teammate** self-registers at `https://<your-host>/register`.
3. **Admin** opens the teammate's row at `/admin/users → <user>`,
   drawer → **Memberships** → **Add to team**, picks the team and a
   role (`developer` is the safe default).

Result: the teammate now sees the team's projects on next login.
Mass onboarding can be scripted via
`POST /v1/admin/teams/{team_id}/members {user_id, role}` once each
teammate has registered.

## Adding an existing user to a team

Users can belong to many teams. To add an existing user:

1. **/admin/teams** (super-admin) or **Team settings → Members** (team admin).
2. **Add member** → search by email → choose role.

The user is added immediately; no email confirmation step is sent (they already have an account).

## Changing a user's role

The drawer at **/admin/users → user** exposes a **Role** dropdown plus a **Memberships** section. A user can hold a different role in each team they belong to (`team_admin` in team A, `developer` in team B); the Memberships list shows every assignment and edits them in place. The role dropdown sets the role for the team selected in the Memberships list (or the user's global role when promoting to `super_admin`).

1. **/admin/users** → user → **Role**.
2. Choose the new role → submit.

The audit log records the change as a `users` write with the role diff under `diff` (the audit row's `target_table` is `users`).

## Removing a user from a team

1. **Team settings → Members** → user → **Remove**.

The user loses access to the team's projects but their account remains. To deactivate the account entirely, see [deactivation](#deactivating-a-user).

## Last-super-admin protection

The portal **refuses** to demote or deactivate the last active `super_admin` in the organization. The pre-flight check runs inside a `SELECT … FOR UPDATE` transaction, so concurrent demote attempts are serialized rather than racing. If you try, the API returns:

```json
{
  "type": "about:blank",
  "title": "Last Super Admin Protected",
  "status": 422,
  "detail": "At least one active super_admin must remain in the organization.",
  "instance": "/v1/admin/users/01H…/role",
  "last_super_admin_protected": true
}
```

The `last_super_admin_protected: true` extension lets clients distinguish this guard from generic 422 validation failures.

To replace the last super-admin:

1. Promote a second user to `super_admin` first.
2. Then demote / deactivate the original.

The guard is enforced in two layers:

1. **API layer** — a `SELECT … FOR UPDATE` row-locked count inside `admin_user_service` rejects the demote / deactivate before commit.
2. **DB layer** — a PostgreSQL trigger (`trg_last_super_admin`, migration `0013`) raises `SQLSTATE 23514` for any `UPDATE`/`DELETE` on the `users` table that would leave zero active super-admins, including direct `psql` writes that bypass the API. The same `last_super_admin_protected` Problem Details extension is surfaced regardless of which layer caught the bypass.

## Recovering a deactivated super-admin {#recovering-a-deactivated-super-admin}

If the **last** super-admin row gets flipped to `is_active=false` despite the [last-super-admin protection](#last-super-admin-protection) — for example, an integration test against the deployment's database tripped `deactivate_user`, or another super-admin demoted you before promoting a replacement — re-run the same bootstrap script `scripts/install.sh` used at install time. It detects the existing row and lifts `is_active` back to `true` without touching the stored password.

```bash
docker-compose -f docker-compose.yml exec -T \
  -e ADMIN_EMAIL="admin@example.com" \
  -e ADMIN_PASSWORD="<existing password — 12+ chars>" \
  backend python -m scripts.create_super_admin
```

What the script does, in order:

1. Looks up the row by `ADMIN_EMAIL`.
2. If the row exists **and** is a `super_admin` **and** is inactive → flips `is_active` back to `true`, commits, and prints `super admin <email> reactivated`. The stored password hash is **not** rewritten (a re-run stays non-destructive when you simply forgot the row was disabled).
3. If the row exists and is already active → prints `super admin <email> already exists — noop` and exits 0.
4. If the row exists but is **not** a `super_admin` → prints an error and exits non-zero. Promote or replace the row manually before re-running.
5. If no row matches → creates a fresh super-admin with the supplied password.

:::caution Password is left intact on reactivation
The `ADMIN_PASSWORD` value is **only** used when the row is being created. On the reactivation path the password hash stays as it was — supply the current password, not a new one. If you have lost the password too, follow [Reset your password](../user-guide/auth-and-profile.md#reset-your-password) **after** reactivation lifts `is_active`, or use the operator-side `/admin/users/{id}/password-reset` endpoint from a second super-admin account.
:::

:::note Why a re-run, not a UI button?
Reactivating the *last* admin from the UI would be a bootstrap paradox — there is no admin available to click the button. The script runs inside the backend container with database credentials, so it is the safe recovery hatch even when no super-admin can sign in. The action is idempotent: running it on an already-active row is a no-op.
:::

## Deactivating a user

Deactivation revokes all sessions and refresh tokens. The user cannot sign in. Their audit-log entries persist (rows are append-only).

1. **/admin/users** → user → **Deactivate**.
2. Confirm.

Reactivation is a single click on the same screen.

Deactivation is the only off-boarding action available in this release — there is no separate user-delete operation. To handle a GDPR erasure request, deactivate the user and contact engineering for a manual purge; a first-class soft-delete with typed-email confirmation is on the roadmap.

## Creating a team

`super_admin` only.

1. **/admin/teams** → **New team**.
2. Name, description, optional default visibility for new projects (`team_only` or `org_wide`).
3. Submit.

The first member of the team is whoever you assign on the next screen.

## Renaming a team

`super_admin` and the team's `team_admin` can rename a team. The team's `name`, `slug`, and `description` are mutable via `PATCH /v1/admin/teams/{team_id}`.

Team archiving (a hidden state that disables new project creation while keeping existing projects readable) is on the roadmap. In this release a team can only be renamed or, with all projects first removed, deleted by a `super_admin`.

## Sessions

| Token | Lifetime | Storage |
|---|---|---|
| **Access token (JWT)** | 30 minutes | Memory (in-app), `Authorization: Bearer …`. |
| **Refresh token** | 7 days, with rotation + reuse detection. | HttpOnly + Secure cookie, SameSite=Lax. |

Reuse detection: if a refresh token is presented twice, the entire token family is invalidated and the user is forced to re-authenticate on every device. This catches refresh-token theft.

## Verify it worked

After onboarding a user:

1. The user can sign in at `/login` with the password they set during registration.
2. **/admin/users** lists the user with `is_active = true`.
3. The audit log records the team-add as a `memberships` insert.
4. The user appears in the team's member list with the assigned role.

## Troubleshooting

### A new user cannot register

Self-registration is open by default. Check that the user is hitting the correct URL (`/register`), the email passes basic format validation, and the chosen password meets the policy (≥ 12 chars, not in the NIST-banned list). Failed registrations log a structured warning on the backend:

```bash
docker-compose -f docker-compose.yml logs --tail=200 backend | grep -i register
```

### Cannot promote my own role

Self-elevation is blocked. Ask another `super_admin` to do it. If you are the only super-admin, sign in as another super-admin (you should always have at least two).

### "User already exists" when adding to a team

The email is already a portal account (possibly already a member of a different team). Use [Adding an existing user to a team](#adding-an-existing-user-to-a-team) — the same flow finds them by email and just attaches the membership.

## Roadmap

The following capabilities are described elsewhere in early docs but are **not** shipped in this release. They are tracked for upcoming minor releases:

- Email-based invitation flow with one-time 24-hour activation links and a `pending` user status.
- Soft-delete user action with typed-email confirmation modal.
- Team archive state (hide-and-disable while preserving read access).

## See also

- [API keys](./api-keys.md) — service-account credentials
- [Audit log](./audit-log.md)
- [Approvals](../user-guide/approvals.md)
