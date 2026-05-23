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
- **EPSS** — the EPSS probability rendered as a percentage (for example `97.3%`). CVEs without an EPSS value show `—`. See [EPSS — exploitation probability](#epss--exploitation-probability).
- **Title** — short summary from the advisory.
- **Affected** — the affected component (`name@version`).
- **Status** — current VEX status.
- **Discovered** — first time this finding appeared on a scan.

Filters on the inline bar: severity, status, an **EPSS threshold** filter (`min_epss`), plus a **search** box (free text against CVE ID / title / component) and sort + order controls. The sort control includes **EPSS** (`sort=epss`); rows without an EPSS value sort last.

## The drawer — finding detail

Click any row to open:

- **Summary** — title, description, CWE, CVSS vector, and the **EPSS score and percentile** when Dependency-Track supplies them (otherwise `—`). See [EPSS — exploitation probability](#epss--exploitation-probability).
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

## EPSS — exploitation probability

The portal surfaces the [EPSS (Exploit Prediction Scoring System)](https://www.first.org/epss/) score next to CVSS so you can tell *severe* CVEs apart from *likely-to-be-attacked* CVEs.

### EPSS vs. CVSS — what each one answers

- **CVSS** measures **severity** — the theoretical impact if a CVE is exploited. It does not say whether anyone is, or will, exploit it.
- **EPSS** measures the **probability of real-world exploitation** in the next 30 days, as a number from `0` to `1`.

The two are complementary. It is common to find a CVE with CVSS `9.8` (Critical) and an EPSS of `0.01` — severe on paper, but with a low predicted chance of being attacked. Sorting and filtering by EPSS lets you concentrate on the small set of findings that are *actually* dangerous and cut the noise.

:::caution EPSS is best-effort
EPSS data is collected during the Dependency-Track resync and is present **only for CVEs that DT supplies an EPSS value for**. Findings without an EPSS value show `—` in the UI and `null` in the API — treat a missing EPSS as "unknown", not "low". EPSS never replaces CVSS or your VEX triage; it is one more signal.
:::

### How the portal displays EPSS

- **Score** — rendered as a percentage. An EPSS of `0.973` shows as `97.3%`.
- **Percentile** — rendered as "top N%". A finding in the 99th percentile shows as roughly "top 1%", meaning its score is higher than ~99% of all scored CVEs.
- **Missing** — `—` (the CVE has no EPSS value from DT).

The score and percentile appear in the findings table's **EPSS** column and in the drawer's **Summary** section.

### Sort and filter by EPSS

- **Sort** — pick **EPSS** in the toolbar's sort control (descending puts the most-likely-exploited findings on top). Findings without an EPSS value always sort last (`NULLS LAST`), regardless of order.
- **Filter** — set the **EPSS threshold** (`min_epss`, a value from `0` to `1`) to show only findings with `epss_score >= min_epss`. For example, `min_epss=0.5` hides everything the model predicts has under a 50% chance of exploitation. Findings with no EPSS value are excluded by the threshold filter (a missing score cannot satisfy `>=`).

### Read EPSS from the API

`GET /v1/projects/{id}/vulnerabilities` returns `epss_score` and `epss_percentile` on every finding (both `null` when DT supplied no value). The same fields appear on the finding detail (`GET /v1/vulnerability_findings/{finding_id}`) and on the nested `VulnerabilityRef`.

Sort by EPSS, highest first:

```bash
curl -sS \
  -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
  "https://trustedoss.example.com/v1/projects/${PROJECT_ID}/vulnerabilities?sort=epss&order=desc"
```

Return only findings the model predicts have at least a 50% exploitation probability:

```bash
curl -sS \
  -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
  "https://trustedoss.example.com/v1/projects/${PROJECT_ID}/vulnerabilities?min_epss=0.5"
```

A finding in the response looks like this (other fields omitted):

```json
{
  "cve_id": "CVE-2021-44228",
  "severity": "critical",
  "cvss_score": 10.0,
  "epss_score": 0.974,
  "epss_percentile": 0.999,
  "status": "new"
}
```

:::tip Gate the build on EPSS
EPSS can also drive the CI build gate, so a high-probability CVE fails the build even when it is not Critical. See [Gate the build on EPSS](../ci-integration/github-actions.md#gate-the-build-on-epss-optional).
:::

## Download a PDF report

The portal renders a project-level **vulnerability PDF report** from the latest successful scan: a risk summary, the severity and license distribution, the vulnerabilities grouped by severity (with CVE id and CVSS), and the component list. It is generated on demand — there is no batch job to schedule.

### Download from the UI

1. Open the project.
2. Click the **Vulnerabilities** tab.
3. Click **Download PDF report** in the toolbar (top right). The button shows **Generating…** while the document renders, then the download starts.

The file name is `vulnerability-report-<project>.pdf`. Any inline error from the last attempt appears beside the button.

### Download from the API

```bash
curl -sS -L -OJ \
  -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
  "https://trustedoss.example.com/v1/projects/${PROJECT_ID}/vulnerability-report.pdf"
```

The response is `application/pdf` with `Content-Disposition: attachment` (the `-OJ` flags tell curl to save it under the server-supplied file name). The report always reflects the **latest succeeded** scan — pinning to a specific historical scan id is not supported at v2.0.0.

| Status | Meaning |
|---|---|
| `200` | PDF download. |
| `401` | Not authenticated — supply a valid token. |
| `404` | Project does not exist, or the caller is not a member of its team (existence-hidden, same posture as the SBOM export). |
| `500` | The PDF renderer failed; the body is `application/problem+json`. Retry, then check the worker image (see Troubleshooting). |

:::note Access
Downloading the report requires `developer` or higher. Cross-team callers receive `404`, not `403`, so a non-member cannot tell whether the project exists.
:::

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

### PDF report download returns `500`

The PDF is rendered in-request with weasyprint. A `500` (with an `application/problem+json` body) means the renderer is unavailable — most often the backend image predates the weasyprint dependency. Rebuild the backend image and retry; if it persists, file an issue with the project id and the request timestamp.

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
