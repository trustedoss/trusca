---
id: vulnerabilities
title: Vulnerabilities
description: Triage CVEs in TrustedOSS Portal — VEX state machine, severity model, suppression flow, and re-detection.
sidebar_label: Vulnerabilities
sidebar_position: 4
---

# Vulnerabilities

The **Vulnerabilities** tab lists every open CVE (Common Vulnerabilities and Exposures) the scan pipeline correlated against the project's components. Findings persist across scans — once a CVE is found, it stays in the project's history with its status and triage notes until the underlying component is removed or upgraded.

![Project detail — Vulnerabilities tab with severity filter and per-row CVE links](/img/screenshots/user-vulns-list.png)

:::note Audience
Engineers triaging individual findings; security leads tracking SLA. Mutating the VEX status requires `developer` or higher; bulk suppression requires `team_admin`.
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

Findings follow the [CycloneDX VEX (Vulnerability Exploitability eXchange)](https://cyclonedx.org/capabilities/vex/) seven-state model. Each finding starts in **New** and transitions as analysts triage it.

| State | Definition | Build gate |
|---|---|---|
| **New** | Just discovered; not triaged. | Counts. |
| **Analyzing** | Triage in progress. | Counts. |
| **Exploitable** | Confirmed exploitable in this project's context. | Counts. |
| **Not affected** | Component is present but the vulnerable code path is unreachable. | Excluded. |
| **False positive** | Detection is wrong (e.g., wrong purl). | Excluded. |
| **Suppressed** | Operator-silenced (`not_affected` with explicit suppression). | Excluded. |
| **Fixed** | Resolved (component upgraded or patch applied). | Excluded. |

Transitions are logged in the audit log with actor, previous status, new status, and the required justification message.

### Required justification

Every transition out of `New` / `Analyzing` requires a free-text justification (≥ 10 chars). The portal stores the justification verbatim — keep it factual ("upgraded lodash to 4.17.21", "vulnerable code path is in `dev_only` module"). The text appears in CycloneDX VEX exports.

## The findings table

Columns:

- **CVE** — the CVE-YYYY-NNNN identifier (plain text; click-through to NVD is on the roadmap).
- **Severity** — color-coded badge.
- **CVSS** — numeric CVSS v3 score from the upstream feed.
- **Title** — short summary from the advisory.
- **Affected** — the affected component (`name@version`).
- **Status** — current VEX status.
- **Discovered** — first time this finding appeared on a scan.

Filters on the inline bar: severity, status, plus a **search** box (free text against CVE ID / title / component) and sort + order controls.

## The drawer — finding detail

Click any row to open:

- **Summary** — title, description, CWE, CVSS vector.
- **References** — vendor advisories, fix commits, exploit databases.
- **Affected** — the upstream-reported affected range with the project's component version highlighted, plus `fixed_version` (the upstream version that ships the fix, when available).
- **Analysis** — VEX status action buttons. **The buttons you see depend on the finding's _current_ state.** The transition matrix (`apps/backend/services/vulnerability_service.py`, the source of truth) routes every terminal decision through the `analyzing` state, so a brand-new finding cannot jump straight to a verdict:
  - **`new`** (just discovered) → **Mark in triage** (`analyzing`) or **Mark suppressed** (`suppressed`). You **cannot** go directly to "not affected" / "exploitable" / "false positive" / "fixed" — triage first.
  - **`analyzing`** (working state) → the five verdicts: **Mark exploitable**, **Mark not affected**, **Mark false positive**, **Mark fixed**, **Mark suppressed**.
  - any **terminal** state (`exploitable` / `not_affected` / `false_positive` / `fixed` / `suppressed`) → **Reopen** back to `analyzing` to re-triage.

  Click a button to open the justification dialog and submit. Moving **into** `suppressed` requires `team_admin` or higher (suppression is gated to keep the audit trail clean); every other transition is `developer` or higher.
- **History** — VEX status-transition timeline (who changed the status, when, with what justification).

![Vulnerability drawer — Analysis section with VEX action buttons and justification textarea](/img/screenshots/user-vulns-drawer-vex.png)

### Walkthrough — opening the Vulnerabilities tab and a finding drawer

The walkthrough below opens a project, switches to **Vulnerabilities**, and clicks the first row to bring up the drawer with the Analysis section ready for triage.

<video controls width="100%" preload="metadata" poster="/img/walkthroughs/walkthrough-cve-triage.gif">
  <source src="/img/walkthroughs/walkthrough-cve-triage.mp4" type="video/mp4" />
  ![Animated walkthrough — opening the Vulnerabilities tab and the finding detail drawer](/img/walkthroughs/walkthrough-cve-triage.gif)
</video>

## Re-detection

When Dependency-Track ingests new CVEs from upstream feeds (NVD, OSV, GitHub Advisory), the periodic resync task re-correlates them against every project's latest scan. New findings appear automatically — no manual action required.

CVE re-detection happens automatically when DT mirrors a new advisory: the next time the Celery beat `dt_findings_resync` task runs (default every hour), affected projects get fresh `vulnerability_findings` rows. There is no in-app banner at v2.0.0; operators monitor `/admin/scans` and the per-project Vulnerabilities tab.

If the **Notify on new CVE** trigger is enabled (see [admin notifications](../admin-guide/dt-connector.md#notifications)), the assigned team or watchers receive an email / Slack / Teams message.

## Suppression vs. not affected vs. fixed

A common point of confusion:

- **Not affected** — you are confident the vulnerable code path does not run. Use sparingly; analysts should be able to point at the file or module.
- **Suppressed** — explicitly silenced for a reason that does not fit the other states (e.g., "internal compensating control"). Use even more sparingly; suppressions should have an expiry date noted in the justification.
- **Fixed** — the component was upgraded / patched, the next scan will (probably) confirm. The portal will auto-promote a `Fixed` finding to closed once the next scan no longer reports it.

## Verify it worked

After triaging:

1. The status badge updates immediately in the table.
2. The audit log records `target_table=vulnerability_findings&action=update` with `previous_status`, `new_status`, `justification` in the diff.
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

## Roadmap (v2.x)

Items the manual previously promised that are not in v2.0.0; tracked for later releases.

- "Last seen" column on the findings table (most recent scan that confirmed the finding) — planned for v2.1.
- Per-component filter and discovered-date range filter on the findings toolbar — planned for v2.1; today the search box covers component lookup.
- Standalone **Fix availability** drawer section — for v2.0.0 the fix version surfaces as `fixed_version` inside the **Affected** section.

## See also

- [Components & licenses](./components-and-licenses.md)
- [Approvals](./approvals.md)
- [DT connector](../admin-guide/dt-connector.md)
- [GitHub Actions — gating on CVEs](../ci-integration/github-actions.md)
