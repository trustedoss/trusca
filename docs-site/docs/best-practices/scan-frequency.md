---
id: scan-frequency
title: Scan frequency
description: Decide how often to scan TRUSCA projects — scan on source change via CI, and let the Trivy DB re-match beat handle CVE freshness on existing SBOMs.
sidebar_label: Scan frequency
sidebar_position: 1
---

# Scan frequency

How often should a project be scanned? The short answer: **scan when the source changes, not on a clock to chase new CVEs.** A source scan re-reads the dependency tree; new vulnerabilities against an *unchanged* tree are picked up automatically by the Trivy DB re-match beat, without re-scanning. This page helps you set a cadence that keeps results fresh without flooding the queue or the disk.

:::note Audience
`team_admin` and `super_admin` deciding a scanning policy for their teams. Familiarity with CI triggers and the [scan lifecycle](../user-guide/scans.md). This is a decision guide — for recovering a stuck or failed scan, see the [on-call runbook](../admin-guide/oncall-runbook.md).
:::

## Two axes that are often confused {#two-axes}

A finding surfaces from the combination of two independent inputs. Separating them is the whole decision:

| Axis | Changes when | Kept current by |
|---|---|---|
| **The SBOM** (which components you ship) | You add, remove, or bump a dependency — a source change. | A **source scan** re-reading the tree. |
| **The vulnerability data** (which CVEs are known) | Trivy publishes new advisories against components you already ship. | The **Trivy DB refresh + re-match beat** — no re-scan. |

Software Bill of Materials (SBOM) is the machine-readable inventory of components a scan produces. CVE is Common Vulnerabilities and Exposures, the public identifier for a known vulnerability.

The mistake is scheduling nightly re-scans "so we catch new CVEs". You do not need to — TRUSCA already re-matches existing SBOMs against the refreshed database on a beat. Scheduling scans to chase CVE freshness only burns queue slots and disk.

## Scan on source change {#on-source-change}

Trigger a scan on the events that actually change the SBOM:

- **Every pull / merge request** — so a reviewer sees the risk delta of a dependency change before it merges. This is where the [build gate](../ci-integration/github-actions.md) earns its keep.
- **Every push to a protected branch** (`main`, `release/*`) — so the branch's *live* snapshot always reflects what is merged.
- **Tag / release builds** — with a `release` label so the SBOM is kept as a permanent compliance record (see [scan retention](../admin-guide/scan-retention.md#keep-a-scan-forever-release-label)).

The CI templates wire this for you and forward the ref so each branch and PR groups into its own retention target:

- [GitHub Actions](../ci-integration/github-actions.md)
- [GitLab CI](../ci-integration/gitlab-ci.md)
- [Jenkins](../ci-integration/jenkins.md)
- [Webhooks](../ci-integration/webhooks.md) — push / PR events without a full CI job.

:::tip A lockfile change is the real trigger
If your CI is chatty, gate the scan step on changes to dependency manifests (`package-lock.json`, `pom.xml`, `go.mod`, `requirements.txt`, …). A documentation-only commit does not change the SBOM, so it does not need a scan. This trims the queue without missing a single dependency change.
:::

## Let the beat handle CVE freshness {#cve-freshness}

You do **not** schedule scans to detect newly disclosed CVEs against dependencies you already ship. Two background jobs cover that:

1. The **Trivy DB refresh** pulls the updated vulnerability database (weekly by default). See [Vulnerability data](../admin-guide/vulnerability-data.md).
2. The **re-match beat** re-evaluates existing SBOMs against the refreshed data every few hours and raises a `cve_detected` notification for anything new — the user-facing view of this is [Re-detection](../user-guide/vulnerabilities.md#re-detection).

So an idle project with no source changes still gets new-CVE alerts. Cadence is about **source changes**, freshness is about the **database** — and the database is already handled.

:::caution A stale Trivy DB silently starves re-detection
The re-match beat is only as fresh as the database it reads. If the Trivy DB refresh has been failing, new CVEs stop landing even though scans still succeed. This is a data-freshness incident, not a scan-frequency decision — diagnose and recover it with [on-call runbook Scenario 1](../admin-guide/oncall-runbook.md#scenario-1--trivy-db-stale-or-missing). Watch the **Vulnerability data** card on `/admin/health` for staleness.
:::

## A cadence that scales {#cadence}

Match the trigger to the branch's role rather than applying one rule everywhere:

| Branch / event | Recommended trigger | Why |
|---|---|---|
| Pull / merge request | Scan on every PR, gate the merge | Catch a risky dependency before it lands. |
| `main` / protected branches | Scan on every push | Keep the live snapshot honest. |
| Release tag | Scan once, with a `release` label | Permanent SBOM of a shipped version. |
| Long-lived, low-churn service | PR + push only; no schedule | The beat covers new CVEs; source rarely moves. |
| Vendored / third-party imports | Scan when the vendored tree changes | No package manager event fires on its own. |

The one place a **scheduled** scan earns its slot is a project whose dependencies drift *without* a commit — for example a build that resolves a floating version range at build time. There, a weekly scheduled scan re-pins the observed tree. Everything else is better served by source-change triggers plus the re-match beat.

Retention keeps this affordable: only the latest scan per branch or PR stays live, superseded snapshots are reclaimed after a grace window, and release-labelled scans are kept forever. Tune the windows in [scan retention](../admin-guide/scan-retention.md) so a high-PR-volume repository does not fill the disk.

## Verify it worked

<!-- docs-uat: id=bp-scan-frequency-cadence-review kind=manual tier=manual -->
Review your scanning policy against these checks:

<!-- docs-uat: id=bp-scan-frequency-1 kind=manual tier=manual -->
1. Opening a PR that changes a dependency manifest triggers a scan and a build-gate verdict on the PR — not a documentation-only PR.
<!-- docs-uat: id=bp-scan-frequency-2 kind=manual tier=manual -->
2. A push to `main` produces a new live snapshot for the `main` target (the previous one is marked superseded in the project's scan history).
<!-- docs-uat: id=bp-scan-frequency-3 kind=manual tier=manual -->
3. A project with **no** recent source scans still shows new `cve_detected` notifications after a Trivy DB refresh — proof the re-match beat, not a schedule, is carrying CVE freshness.
<!-- docs-uat: id=bp-scan-frequency-4 kind=manual tier=manual -->
4. The `/admin/health` **Vulnerability data** card reports a recent refresh (freshness `fresh`), so the beat has current data to match against.
<!-- docs-uat: id=bp-scan-frequency-5 kind=manual tier=manual -->
5. You are **not** running nightly re-scans purely to catch CVEs — if you are, remove them and rely on the beat.

## See also

- [Scans](../user-guide/scans.md) — the per-scan lifecycle
- [Scan retention](../admin-guide/scan-retention.md) — keeping history useful and disk bounded
- [Vulnerability data (Trivy DB)](../admin-guide/vulnerability-data.md) — refresh + re-match lifecycle
- [Re-detection](../user-guide/vulnerabilities.md#re-detection) — how new CVEs surface without a re-scan
- [On-call runbook — Scenario 1](../admin-guide/oncall-runbook.md#scenario-1--trivy-db-stale-or-missing) — recovering a stale Trivy DB
- [GitHub Actions](../ci-integration/github-actions.md) · [GitLab CI](../ci-integration/gitlab-ci.md) · [Jenkins](../ci-integration/jenkins.md) · [Webhooks](../ci-integration/webhooks.md)
