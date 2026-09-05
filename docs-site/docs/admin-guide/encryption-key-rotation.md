---
title: Rotating the encryption key
sidebar_position: 12
---

# Rotating the encryption key

`GITHUB_APP_ENCRYPTION_KEY` encrypts every secret this deployment stores at
rest. The name is narrower than the job: it covers GitHub App private keys and
webhook secrets, per-project git credentials, private registry passwords, and
project webhook secrets.

The variable takes a comma-separated list. The **first** key encrypts; **every**
key can decrypt. That is what makes a rotation possible without downtime, and
it is also what makes it possible to lose data by removing a key one step too
early.

## The one thing that cannot be undone

Removing a key while rows are still encrypted under it makes those rows
permanently unreadable. There is no recovery path that does not involve
finding the key again.

Nothing looks wrong when it happens. The application starts, every row is
present, and the failure arrives later as a webhook that stops being accepted
or a registry that stops authenticating.

So the sequence below ends with a check, and the check is not optional.

## The sequence

### 1. Add the new key in front

<!-- docs-uat: id=key-rotation-generate kind=shell ctx=host tier=manual waiver=prints-a-key-that-must-not-be-recorded -->
```bash
# Generate one
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Put it first, keep the current one:

```
GITHUB_APP_ENCRYPTION_KEY=<new-key>,<current-key>
```

Restart. From this moment every new write uses the new key and both keys can
read, so nothing breaks and no secret needs re-entering.

### 2. Re-encrypt what is already stored

<!-- docs-uat: id=key-rotation-rewrite kind=manual tier=manual -->
```bash
docker-compose -f docker-compose.yml exec backend \
  env MODE=rewrite python scripts/reencrypt_secrets.py
```

Safe to interrupt and safe to repeat. Rows already on the newest key are
skipped, so a second run continues rather than starting over.

A row that somebody changed while the pass was running is reported as "changed
under us" and left alone. That is correct: an application write already used
the newest key, and rewriting it from the value read earlier would put back
the secret they replaced.

### 3. Confirm nothing is left

<!-- docs-uat: id=key-rotation-count kind=manual tier=manual -->
```bash
docker-compose -f docker-compose.yml exec backend \
  env MODE=count python scripts/reencrypt_secrets.py
```

This must say `Nothing is on an older key` before you go on. It exits non-zero
otherwise, so it can gate a script.

The same count appears in the backend's boot log as `key_rotation.stale_at_boot`
whenever more than one key is configured. The person who runs the re-encryption
and the person who edits the environment are often not the same person, and the
command's output was only ever on the first one's terminal.

### 4. Remove the old key

```
GITHUB_APP_ENCRYPTION_KEY=<new-key>
```

Restart.

## How long to keep the old key

**As long as you keep backups taken before the rotation.**

A backup carries ciphertext written under whatever key was current when it was
taken. Restoring one brings that ciphertext back, and a key that is no longer
in the list cannot open it. If `BACKUP_RETENTION_DAYS` is 90, the old key is
needed for 90 days after the rotation, not until step 4 finishes.

Keeping it costs nothing: a key that is not first never encrypts anything.

`scripts/restore.sh` runs the count after a restore and says so if the restored
data needs a key this deployment no longer has.

## When a row cannot be opened at all

If either command reports rows that "could not be opened by any configured
key", a key that was in use has already been removed. Running the rewrite will
not help; the missing key has to go back into the list first.

The report names the column and the row id so you can tell what is affected.
Until the key comes back, those specific secrets are unreadable, and the
feature that uses them fails: webhook deliveries are refused, a private
registry pull fails to authenticate, a GitHub App cannot sign.

## Adding a new encrypted column

`apps/backend/core/encrypted_columns.py` lists every column holding ciphertext
and the key that opens it. Rotation walks that list, so a column missing from
it is a column rotation skips, and its rows stay on a key the operator is about
to remove.

A contract test fails if the list and the code disagree, or if a column changes
which key opens it without a migration that re-encrypts the existing rows.
