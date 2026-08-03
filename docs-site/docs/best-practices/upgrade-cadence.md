---
id: upgrade-cadence
title: Upgrade cadence
description: Keep TRUSCA and its Trivy DB current — reading release notes, forward-only migrations, and the backup-before-upgrade ordering that makes rollback possible.
sidebar_label: Upgrade cadence
sidebar_position: 4
---

# Upgrade cadence

Two things go stale on different clocks: **TRUSCA itself** (features, fixes, migrations) and the **Trivy vulnerability database** it scans against. They are upgraded by different mechanisms and want different cadences. This page helps you decide how often to move each, and how to sequence an upgrade so a bad release is always recoverable.

:::note Audience
`super_admin` operating a deployment. Familiarity with `docker-compose`, the [upgrade wrapper](../installation/upgrade.md), and [backup & restore](../admin-guide/backup-and-restore.md). This is a decision guide — for step-by-step upgrade commands, follow the [upgrade page](../installation/upgrade.md); for recovering a failed upgrade, its [rollback section](../installation/upgrade.md#rollback).
:::

## Two clocks, two mechanisms {#two-clocks}

| What | Moves how | Cadence to aim for | Operator action |
|---|---|---|---|
| **Trivy DB** (which CVEs are known) | Automatic weekly refresh + re-match beat | Keep current continuously | None — just watch it stays fresh |
| **TRUSCA release** (the product) | `scripts/upgrade.sh` | Patch: promptly · Minor: monthly-ish · Major: planned | Read notes, back up, run the wrapper |

The database is the one you do **not** schedule by hand — it refreshes and re-matches existing SBOMs on its own. Your job there is to notice when the refresh stops working, not to trigger it. The product release is the one that needs a human cadence.

### Keep the Trivy DB current {#trivy-db}

The Trivy DB (a bundle of NVD, OSV, GHSA, EPSS, and KEV) downloads on first boot and refreshes weekly; the re-match beat then re-evaluates existing scans against it. All of that is covered operationally in [Vulnerability data](../admin-guide/vulnerability-data.md). For upgrade purposes, two habits matter:

- **Watch the freshness.** The `/admin/health` **Vulnerability data** card shows the last refresh and a `fresh` / `stale` / `very_stale` state. A stale DB means new CVEs stop landing — recover it via [on-call runbook Scenario 1](../admin-guide/oncall-runbook.md#scenario-1--trivy-db-stale-or-missing). This is independent of product upgrades.
- **Mind tag drift across a product upgrade.** The DB is an OCI artefact pinned to a schema tag. If a TRUSCA upgrade bumps the expected tag, an air-gapped mirror must be refreshed to match or scans return empty results — coordinate the mirror update with the upgrade. See [air-gapped operation](../admin-guide/vulnerability-data.md#air-gapped).

## Read the release notes first {#release-notes}

Never run `scripts/upgrade.sh` blind. Each release ships notes that call out behaviour changes, new admin screens, and any manual migration steps. A recent example: [v0.14.0](../release-notes/v0.14.0.md) changed scan *results* on purpose (runtime-scope SBOM filtering, default on) — an operator who skipped the notes would see the component count drop and think something broke. Browse the **Release notes** section in the sidebar, or the [GitHub releases](https://github.com/trustedoss/trusca/releases) for the full changelog.

Read, in order: the headline (does it change results or just fix bugs?), the upgrade / migration steps, and any new environment variables you need to set before or after the pull.

## Forward-only migrations — why ordering matters {#forward-only}

TRUSCA's Alembic migrations are **forward-only**: there is no `alembic downgrade`. The only way back from a bad migration is to **restore the pre-upgrade backup**. That single fact drives the whole upgrade sequence:

1. **Back up first — always.** `scripts/upgrade.sh` takes a mandatory pre-upgrade backup before it touches anything; there is no flag to skip it. If you run steps by hand, take the backup yourself first. See [forward-only migrations and restore](../admin-guide/backup-and-restore.md#forward-only-migrations-and-restore).
2. **Pull and recreate** only the services whose image hash changed.
3. **Apply migrations** (`alembic upgrade head`).
4. **Health-probe**, then verify.

If the upgrade goes wrong, you do not "downgrade" — you [restore the pre-upgrade backup](../installation/upgrade.md#rollback), which reverts both the database and the workspace to the pre-migration state. A backup taken *after* a bad migration is worthless for rollback, which is exactly why the wrapper backs up first.

:::warning Restore replaces live data
`restore.sh` overwrites the live database and workspace — there is no undo, and it restores to the *pre-upgrade* schema. Point it at the backup the upgrade just took (`ls -td backups/*` prints newest first), not an older one. Confirm the Alembic head in the backup's manifest matches before relying on it.
:::

## Choosing a cadence {#cadence}

Match the urgency to the release type:

| Release type | Recommended cadence | Rationale |
|---|---|---|
| **Patch** (`x.y.Z`) | Promptly — days | Bug and security fixes; low migration risk. Minor / patch upgrades within a major are always supported in place. |
| **Minor** (`x.Y.0`) | Monthly-ish, off-peak | New features, possibly new admin screens or env vars. Read the notes, stage in a quiet window. |
| **Major** (`X.0.0`) | Planned, deliberate | May carry breaking migrations. Do **not** run the wrapper blindly across a major — follow the release's dedicated migration steps. |

A single wrapper run can hop several patch/minor versions because the migration chain is exercised end-to-end in CI, so falling a few patches behind is safe to catch up in one step — see [skipping versions](../installation/upgrade.md#skipping-versions). Major-version hops are the exception: take them one boundary at a time, following each release's notes.

:::tip Upgrade in a quiet window
Pick a moment with no scans in flight — the portal is briefly unavailable while changed services recreate (typically under 30 seconds), and an in-flight scan complicates the rollback story. The global `/scans` queue tells you when it has drained. See [compatibility & policy](../installation/upgrade.md#compatibility--policy).
:::

## Verify it worked

<!-- docs-uat: id=bp-upgrade-cadence-review kind=manual tier=manual -->
Confirm your upgrade discipline holds:

<!-- docs-uat: id=bp-upgrade-cadence-1 kind=manual tier=manual -->
1. Every upgrade is preceded by a fresh backup — you rely on the wrapper's mandatory pre-upgrade backup, or take one by hand before any manual migration.
<!-- docs-uat: id=bp-upgrade-cadence-2 kind=manual tier=manual -->
2. You read the target release's notes for behaviour changes and new env vars **before** pulling, not after results look surprising.
<!-- docs-uat: id=bp-upgrade-cadence-3 kind=manual tier=manual -->
3. The `/admin/health` **Vulnerability data** card reports a recent Trivy DB refresh, independent of when you last upgraded the product.
<!-- docs-uat: id=bp-upgrade-cadence-4 kind=manual tier=manual -->
4. You can name the exact backup you would restore to roll back the most recent upgrade, and its Alembic head matches the pre-upgrade schema.
<!-- docs-uat: id=bp-upgrade-cadence-5 kind=manual tier=manual -->
5. You are current on patches (days-old at most) and have a deliberate plan — not an ad-hoc one — for the next minor or major.

## See also

- [Upgrade](../installation/upgrade.md) — the wrapper, step by step, plus rollback
- [Backup & restore](../admin-guide/backup-and-restore.md#forward-only-migrations-and-restore) — why the backup is the only way back
- [Vulnerability data (Trivy DB)](../admin-guide/vulnerability-data.md) — refresh lifecycle and air-gapped mirrors
- [Release notes — v0.14.0](../release-notes/v0.14.0.md) — an example of a results-changing release
- [On-call runbook — Scenario 1](../admin-guide/oncall-runbook.md#scenario-1--trivy-db-stale-or-missing) — recovering a stale Trivy DB
