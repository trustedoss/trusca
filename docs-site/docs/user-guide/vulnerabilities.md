---
id: vulnerabilities
title: Vulnerabilities
description: Triage CVEs in TrustedOSS Portal — VEX state machine, severity model, suppression flow, and re-detection.
sidebar_label: Vulnerabilities
sidebar_position: 4
---

# Vulnerabilities

The **Vulnerabilities** tab lists every open CVE the scan pipeline correlated against the project's components. Findings persist across scans — once a CVE is found, it stays in the project's history with its state and triage notes until the underlying component is removed or upgraded.

:::note Audience
Engineers triaging individual findings; security leads tracking SLA. Mutating the VEX state requires `developer` or higher; bulk suppression requires `team_admin`.
:::

## Severity model

| Severity | Color token | CVSS v3 (typical) | Build gate |
|---|---|---|---|
| **Critical** | `#dc2626` | 9.0–10.0 | Exits 1 (default) |
| **High** | `#ea580c` | 7.0–8.9 | Configurable per project |
| **Medium** | `#ca8a04` | 4.0–6.9 | No effect |
| **Low** | `#2563eb` | 0.1–3.9 | No effect |
| **Info** | `#71717a` | — | No effect |

The default policy fails the build only on `Critical`. Project owners can lower the threshold to `High` per project.

## VEX state machine

Findings follow the [CycloneDX VEX](https://cyclonedx.org/capabilities/vex/) seven-state model. Each finding starts in **New** and transitions as analysts triage it.

| State | Definition | Build gate |
|---|---|---|
| **New** | Just discovered; not triaged. | Counts. |
| **Analyzing** | Triage in progress. | Counts. |
| **Exploitable** | Confirmed exploitable in this project's context. | Counts. |
| **Not affected** | Component is present but the vulnerable code path is unreachable. | Excluded. |
| **False positive** | Detection is wrong (e.g., wrong purl). | Excluded. |
| **Suppressed** | Operator-silenced (`not_affected` with explicit suppression). | Excluded. |
| **Fixed** | Resolved (component upgraded or patch applied). | Excluded. |

Transitions are logged in the audit log with actor, previous state, new state, and the required justification message.

### Required justification

Every transition out of `New` / `Analyzing` requires a free-text justification (≥ 10 chars). The portal stores the justification verbatim — keep it factual ("upgraded lodash to 4.17.21", "vulnerable code path is in `dev_only` module"). The text appears in CycloneDX VEX exports.

## The findings table

Columns:

- **CVE** — the CVE ID (e.g. `CVE-2024-12345`). Click to open the upstream NVD entry.
- **Component** — `name@version`.
- **Severity** — color-coded badge.
- **State** — current VEX state.
- **Discovered** — first time this finding appeared on a scan.
- **Last seen** — most recent scan where the finding was confirmed.

Filters on the inline bar: severity, state, component, discovered range.

## The drawer — finding detail

Click any row to open:

- **CVE summary** — title, description, CWE, CVSS vector.
- **Affected versions** — the upstream-reported affected range, with this project's component version highlighted.
- **References** — vendor advisories, fix commits, exploit databases.
- **Fix availability** — whether an upstream fix exists and the version that contains it.
- **Project history** — every scan where the finding appeared, with timestamps.
- **Triage** — VEX state dropdown, justification box, **Save** button. Only `developer` or higher.

## Re-detection

When Dependency-Track ingests new CVEs from upstream feeds (NVD, OSV, GitHub Advisory), the periodic resync task re-correlates them against every project's latest scan. New findings appear automatically — no manual action required.

The **CVE re-detection** banner on the dashboard summarizes the most recent resync run: number of feeds processed, number of new findings, and the run timestamp.

If the **Notify on new CVE** trigger is enabled (see [admin notifications](../admin-guide/dt-connector.md#notifications)), the assigned team or watchers receive an email / Slack / Teams message.

## Suppression vs. not affected vs. fixed

A common point of confusion:

- **Not affected** — you are confident the vulnerable code path does not run. Use sparingly; analysts should be able to point at the file or module.
- **Suppressed** — explicitly silenced for a reason that does not fit the other states (e.g., "internal compensating control"). Use even more sparingly; suppressions should have an expiry date noted in the justification.
- **Fixed** — the component was upgraded / patched, the next scan will (probably) confirm. The portal will auto-promote a `Fixed` finding to closed once the next scan no longer reports it.

## Verify it worked

After triaging:

1. The state badge updates immediately in the table.
2. The audit log records `vuln_finding.update` with `previous_state`, `new_state`, `justification`.
3. Excluded findings stop counting toward the project's risk score.
4. Excluded findings are excluded from the build gate on the next scan.

## Troubleshooting

### Findings reappear after suppression

A finding that comes back as `New` after the next scan was probably suppressed at the **scan** level rather than at the **project** level. The portal pins suppression to the project / component / CVE triple — re-check that the suppression metadata matches.

### Severity changed between scans

Upstream feeds occasionally re-score CVEs (NVD analyst review, vendor advisories). The portal stores the severity at scan time and updates on the next resync. The drawer shows both values when they differ.

### A CVE is missing from the report

Possible causes:

- The component's `purl` does not match Dependency-Track's normalization (rare; Maven `groupId:artifactId` style is the most common culprit). File an issue with the scan report.
- DT was unavailable when the scan ran and the cache did not yet have an entry for that CVE. Run another scan after DT is healthy.
- The CVE is in an ecosystem DT does not yet ingest. Check **/admin/dt → Vulnerability sources**.

## See also

- [Components & licenses](./components-and-licenses.md)
- [Approvals](./approvals.md)
- [DT connector](../admin-guide/dt-connector.md)
- [GitHub Actions — gating on CVEs](../ci-integration/github-actions.md)
