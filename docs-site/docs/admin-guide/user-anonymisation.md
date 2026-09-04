---
id: user-anonymisation
title: Erasing a user's personal data
description: How to request, approve and carry out the anonymisation of a user account, what the operation erases, what it deliberately leaves behind, and how a user takes a copy of their own data.
sidebar_label: User anonymisation
sidebar_position: 8.7
---

# Erasing a user's personal data

Anonymising a user strips their name and address from the account, removes their sign-in links and sessions, and clears the client details recorded against their audit rows. It cannot be undone, and it is asked for by one person about another.

This page is the procedure. It also states plainly what the operation does not erase, because an erasure that quietly leaves something behind while reporting success is worse than one that says what it kept.

:::note Audience
`super_admin` for the request and the approval; an operator with the database owner credential for the execution. The two are different people doing different things, and that is deliberate.
:::

## Why it is not one button

The account row cannot simply be deleted. Audit rows reference it, and the reference is what makes them evidence of who did what. Deleting the user would either take those rows with it or leave the trail saying "somebody" where it used to say a name. So the account survives, stripped.

The audit rows themselves are append-only, enforced by a database trigger. Clearing the client details on them needs an exception to that rule, and the exception is a `SECURITY DEFINER` function that only the database owner may execute. The application never holds that credential: `docker-compose.yml` withholds it so a compromise of the running portal cannot rewrite the record of its own actions.

Two consequences follow, and both are the point rather than an inconvenience:

- Approval happens in the product; execution happens on a server, by hand.
- Between the two, nothing is broken and somebody is still waiting.

## The three steps

### 1. Open a request

Any super admin opens a request against the subject. Nothing is erased.

```
POST /v1/user-anonymisation
{ "subject_user_id": "...", "reason": "..." }
```

A super admin cannot open a request against themselves: the control is that two different people agree, and a self-request leaves only one person to convince. Put no contact details in `reason`; it is stored as written.

**A request stays approvable for seven days, and nothing announces its expiry.** No notification is sent, and a lapsed request appears on no screen. The person who opened it will go on believing a decision is still pending while the erasure sits undone and whatever deadline applies to it runs. Put a periodic check of undecided requests into your own operational procedure; the portal will not prompt you.

Opening a fresh request for the same subject retires the lapsed one, so a subject is never permanently blocked by a request nobody decided.

### 2. A second super admin approves

```
POST /v1/user-anonymisation/{request_id}/approval
```

The approver must be someone other than the requester and other than the subject. This is checked in the service and again by a database constraint, so a future second call site cannot skip it by forgetting to.

Approving still erases nothing.

### 3. An operator runs the command

**Approval does not schedule anything. The erasure runs only when a person runs this command, and nothing runs it for them.** Add this step to your own operational procedure; the portal will not do it on a timer.

```bash
docker-compose -f docker-compose.yml exec -T \
  -e DATABASE_URL_OWNER="$(grep ^DATABASE_URL_OWNER= .env | cut -d= -f2-)" \
  -e SUBJECT_USER_ID=... \
  -e CONFIRM=yes \
  backend python -m scripts.anonymise_user
```

The command refuses unless an approved request exists, and the database refuses too, independently. `CONFIRM=yes` is required because two people agreeing about a subject is not the same as an operator meaning to run it now, against this id, on this deployment.

Everything the command does runs in one transaction. A refusal leaves nothing half-done.

### Watching the gap

**Administration → Health** carries an *Anonymisation awaiting execution* panel listing every approved request no operator has run, oldest first, with how long each has waited. A request past seven days is marked overdue: at that point it has spent longer waiting for an operator than it was ever allowed to spend waiting for a decision.

The panel is neutral when empty rather than green. Nothing has been verified; there is simply nothing owed.

## What the two-person rule does and does not stop

Worth being exact, because the wording elsewhere could be read as a stronger promise than the design makes.

The request row is checked by the database, which refuses the scrub unless an approved request exists for that subject. That is a check that a row exists. It is not proof that two people agreed, because the application has to be able to write to that table for the flow to work at all, so anything that reaches SQL through the portal can insert a row saying `approved` and name two real super admins on it.

Three things stand behind it. Requests made through the product leave a `create` and an `update` in the append-only audit log, and the operator command refuses a request that has neither, so a row conjured by direct SQL does not execute. The backlog names the requester and the approver, so an operator has two people to ask before doing something irreversible. And the command is run by a person holding a credential the running portal never receives.

Signing the request row with an application secret was considered and not done. The threat it addresses is an attacker who can write to the database but cannot reach application configuration, and the operator command needs that same key on the same host, so the key ends up on the boundary it is meant to draw. It would also raise the cost of forging a row without changing what the row means, and it would add a failure mode this feature exists to prevent: rotate the key and previously approved erasures stop executing, sitting in the backlog past whatever deadline applies to them.

## What is erased

| Where | What goes |
|---|---|
| `users` | Email (replaced with a non-routable placeholder), full name, password hash. The account is deactivated. |
| `oauth_identities` | Every linked provider, including the provider's own copy of the address. |
| `refresh_tokens`, `password_reset_tokens` | All of them. Live sessions stop working. |
| `saved_searches` | The subject's saved queries, which are free text they wrote. |
| `audit_logs` | `ip` and `user_agent` on rows where the subject is the actor. |
| Personal team | Renamed to `Personal team (<id prefix>)` and its description cleared, for teams under an organisation flagged personal that the subject belongs to. |

## The account survives; the login does not

The row stays because audit entries reference it, and that reference is what keeps the trail saying who rather than somebody. **It stays as a record, not as a way in.** No route authenticates it:

- **Password.** The hash is rotated through the same choke point a password change uses, to a value nobody holds, and the account is deactivated. Whoever knew the old password knows nothing useful.
- **Password reset.** The old address no longer resolves to any account, so a reset request for it is treated exactly like one for an address that was never registered. No token is issued and no mail is sent.
- **OAuth.** The provider links are deleted, so a returning provider cannot find the account by identity. It cannot find it by address either: the address it would match on is no longer stored. Signing in again with the same provider and the same address creates a new, empty account rather than reopening the old one.

## What is retained

Read this section before telling anyone their data has been erased.

**Audit `diff` contents.** An audit row records what a change contained, and a row written before this version can hold the subject's old address inside its `diff`. The trigger exception this operation relies on forbids touching `diff` at all. That is deliberate: an exception wide enough to rewrite diffs would end the immutability of the audit trail, which is the property that makes it evidence. Rows written since the masking was added carry a one-way hash instead of the address, but only where the value sat in a column literally named `email` or `full_name`: the masking keys on the column name. An address that reached a diff some other way, such as inside a JSON list of notification recipients, is there in plain text. And a hash prevents reading an address out of the record while still allowing a specific address to be confirmed by comparison, which is pseudonymisation rather than anonymisation and should be described that way.

**Shared team names and descriptions.** If someone put the subject's address into a shared team's name or description, it stays. Other people navigate by that name, and an erasure about one person is not a licence to rewrite a record a group depends on. Change it deliberately, by hand, if you decide it should change.

**Notification routing rules.** Email recipients configured in routing rules are addresses somebody entered as a destination, not attributes of an account, and they are not touched here. Review them separately.

**Personal teams created before this version.** Those were named after the person. Only the subject's own is renamed by this operation; others keep whatever they were called until the person they belong to is anonymised.

**Backups taken before the erasure.** A backup contains the account as it was. Restoring one brings the email and name back, and brings the request row back as `executed`, so the command will not simply re-run against it. Automated backups keep `BACKUP_RETENTION_DAYS` (7 by default) of history; off-site copies and any point-in-time recovery window are governed separately. Decide, as part of your procedure, whether to discard the affected backups or to redo the erasure after a restore. To redo it, open a fresh request and have it approved again: an executed request does not hold the subject's slot, so a new one can be opened for the same person, and the command runs against that. Do not try to re-run the old one; it is marked executed and will be refused.

A restore is not the only way the old values come back. They persist in the database's own pages until Postgres reclaims them. See [Backup and restore](./backup-and-restore.md).

**API keys the subject created.** Not retained but worth planning for: they stop authenticating the moment the account is deactivated, because key authentication resolves the creating user and refuses an inactive one. A departing person's CI keys therefore die with the erasure. Reissue anything a pipeline depends on under a different creator first.

**The audit rows themselves.** Who did what and when survives. That is the record the operation is careful not to destroy.

## Taking a copy of your own data

Any signed-in user can export what is held about them:

```
GET /v1/users/me/export
```

The response is keyed off the caller's token; there is no user id anywhere in the route, so it cannot be pointed at somebody else.

It contains the account, notification preferences, sign-in methods, team memberships, saved searches, and the caller's own activity record. It does not contain work product: projects, scans, findings and policies are the organisation's records of work done on its behalf, not personal data about the person who did it.

Two limits are stated in the payload rather than hidden:

- **Change contents are omitted from the activity record.** An entry recording a change the caller made to another user would carry that other user's data, and handing it over in the name of one person's rights would breach another's.
- **The activity record is capped at 5,000 entries**, newest first. When it is capped, `activity.truncated` is `true` and `activity.total` gives the real number. If a request needs the complete record, a super admin can extract it from the audit log directly; see [Audit log](./audit-log.md) for the export path and its filters.
