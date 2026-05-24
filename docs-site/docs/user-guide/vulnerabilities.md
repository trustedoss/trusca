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
- **Affected** — the upstream-reported affected range with the project's component version highlighted, plus the **fixed version** — the version that remediates this CVE *for this component* — when the scan pipeline could determine one. See [Fixed version — the version that remediates the CVE](#fixed-version--the-version-that-remediates-the-cve). The affected component also carries its **dependency depth** (v2.2): whether it is a **direct** dependency you declared (depth `1`) or a **transitive** one pulled in by another package (depth `2+`). A CVE in a direct dependency is usually yours to fix by bumping the declared version; a CVE in a transitive dependency is fixed by upgrading the direct parent that requires it — see [Direct vs. transitive (dependency depth)](./components-and-licenses.md#dependency-depth).
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

## Fixed version — the version that remediates the CVE

The finding drawer's **Affected** section shows a **fixed version** next to each affected component: the version you can upgrade *that component* to so it no longer carries *that CVE*. It answers the first question every triager asks — "what do I bump it to?".

### It is per-(component × CVE), not per-CVE

A single CVE is often patched at **different versions across different packages**, and a single package can be patched at **different versions for different CVEs**. So the fixed version is stored on the individual finding (the `(component, CVE)` pairing), not on the CVE globally. Two components affected by the same CVE can legitimately show two different fixed versions — that is expected, not a bug.

### Where the value comes from

The scan pipeline collects the fixed version from the **Dependency-Track findings** for your scan, in priority order:

1. **Structured patched-version lists** DT attaches to the finding (the lowest patched version wins).
2. **CycloneDX VEX `affects[].versions[]`** entries marked `status: fixed`.
3. The advisory's free-text **recommendation** ("Upgrade to 2.17.1 or later"), from which the portal extracts the concrete version.

The collected string is validated before it is stored — control characters, oversized values, range operators (`^`, `>=`), and anything that is not a plausible version token are rejected to "unknown" rather than persisted.

### When it is blank

The fixed version shows `—` (and the API returns `null`) when:

- DT reported no fix for this component / CVE (the upstream advisory has no patched version yet — a true zero-day or an as-yet-unfixed CVE), **or**
- the finding was discovered by a scan that ran **before v2.2** shipped this collection. Re-scan the project to backfill it.

A blank fixed version means **"no fix version is known"**, not "no fix exists" — always confirm against the upstream advisory before concluding a CVE is unfixable.

### Read it from the API

The fixed version appears as `fixed_version` on the finding detail's affected components and on the component drawer's nested CVE references:

```bash
# finding detail — fixed_version on each affected component
curl -sS \
  -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
  "https://trustedoss.example.com/v1/vulnerability_findings/${FINDING_ID}"
```

```json
{
  "cve_id": "CVE-2021-44228",
  "affected_components": [
    {
      "name": "log4j-core",
      "version": "2.14.1",
      "purl": "pkg:maven/org.apache.logging.log4j/log4j-core@2.14.1",
      "fixed_version": "2.17.1"
    }
  ]
}
```

:::note Upgrade recommendations build on this
The fixed version is the input to the **upgrade recommendation (recommended version)** (v2.2): once each finding knows its fix version, the portal computes the minimal safe bump per component. See [Upgrade recommendation (recommended version)](#upgrade-recommendation-recommended-version).
:::

## Upgrade recommendation (recommended version)

While the [fixed version](#fixed-version--the-version-that-remediates-the-cve) answers "what patches *this* CVE", the **recommended version** answers the next question — "what one version do I bump this component to so it is clean?". It is the **minimum safe upgrade**: the lowest version that resolves **all** of the component's open CVEs at once.

The finding drawer shows it in a **Recommended upgrade** panel, above the references and affected components.

### How it is computed

A single component can carry several open CVEs, each fixed at its own version. The recommended version is the **semantic-version maximum** of those per-CVE [fixed versions](#fixed-version--the-version-that-remediates-the-cve) — the lowest version that is at least every individual fix:

- Component `log4j-core@2.14.1` has two open CVEs, fixed at `2.16.0` and `2.17.1`. The recommended version is **`2.17.1`** — bumping to it clears both.

Only **open** findings count. CVEs you have dispositioned (`Not affected`, `False positive`, `Fixed`) are excluded — exactly the same set the [build gate](#severity-model) considers, so the recommendation never tells you to chase a CVE you already closed.

### Priority signals

The panel also surfaces three signals so you can tell a "fix this now" upgrade from a "fix it eventually" one:

- **Direct dependency** — the component is one you declared yourself (graph depth `1`), so you can bump it in your own manifest immediately. A transitive dependency shows no badge — you fix it by upgrading the direct parent that pulls it in (see [Direct vs. transitive](./components-and-licenses.md#dependency-depth)).
- **Highest severity** — the most severe CVE among the component's open findings.
- **Highest EPSS** — the highest [exploitation probability](#epss--exploitation-probability) among them.

These signals **order** the recommendations (a direct, high-EPSS, critical upgrade is the one to do first); they never change the recommended *version* itself.

### When there is no recommendation

The portal deliberately declines to recommend a version — and says why — rather than suggest a misleading partial upgrade:

- **No known fix version** — at least one of the component's open CVEs has no [fixed version](#fixed-version--the-version-that-remediates-the-cve) (a true zero-day, or a finding scanned before v2.2). Bumping to the maximum of the *known* fixes would falsely imply the component is fully clean, so the panel shows a "no recommendation" hint instead.
- **Unparseable fix versions** — every available fix string was malformed and could not be compared.

A "no recommendation" state is informational, not an error — confirm the un-fixed CVEs against their upstream advisories.

### In the CI build-gate comment

The SCA PR comment the [build gate](../ci-integration/github-actions.md) posts includes a **Recommended upgrades** section listing the highest-priority bumps (direct and most severe first), each as `component current → recommended` with the CVEs it resolves. It only appears when there is at least one actionable upgrade.

### Read it from the API

The finding detail (`GET /v1/vulnerability_findings/{finding_id}`) carries an `upgrade_recommendation` object:

```json
{
  "cve_id": "CVE-2021-44228",
  "upgrade_recommendation": {
    "recommended_version": "2.17.1",
    "reason": "ok",
    "direct": true,
    "max_severity": "critical",
    "max_epss": 0.974,
    "finding_count": 2
  }
}
```

`reason` is `ok` (a version was computed), `no_fix_version`, `unparseable_version`, or `no_open_findings`; `recommended_version` is `null` for every value except `ok`.

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

## Export a VEX document

Beyond the VEX state embedded in a CycloneDX **SBOM**, the portal can export a
standalone **VEX document** built purely from the project's current finding
triage. A VEX (Vulnerability Exploitability eXchange) document tells downstream
consumers *which CVEs actually affect your product* — so a consumer can suppress
the noise from CVEs you have already analyzed as `not_affected` or `fixed`.

Two formats are supported:

| Format | Query value (`format=`) | MIME | Use case |
|---|---|---|---|
| **OpenVEX 0.2.0** | `openvex` | `application/json` | The minimal, vendor-neutral OpenVEX schema. Default. |
| **CycloneDX 1.5 VEX** | `cyclonedx` | `application/json` | A CycloneDX BOM carrying only `vulnerabilities[]` + analysis — pairs with a CycloneDX SBOM. |

The document is built from the **latest succeeded** scan's findings. A project
with no succeeded scan (or no findings) still exports a valid, empty VEX
document (HTTP 200) so downstream tooling can parse it.

### Status mapping

Each internal VEX state maps to the target format's status vocabulary. The
free-text justification you entered during triage is carried verbatim into a
free-text field — it is **never** force-fit onto the OpenVEX `justification`
enum (whose members have precise legal meaning the portal cannot infer from
arbitrary analyst prose).

| Portal state | OpenVEX `status` | CycloneDX `analysis.state` |
|---|---|---|
| **New** | `under_investigation` | `in_triage` |
| **Analyzing** | `under_investigation` | `in_triage` |
| **Exploitable** | `affected` | `exploitable` |
| **Not affected** | `not_affected` | `not_affected` |
| **False positive** | `not_affected` | `false_positive` |
| **Suppressed** | `not_affected` | `not_affected` |
| **Fixed** | `fixed` | `resolved` |

The justification text lands in OpenVEX `impact_statement` and in CycloneDX
`analysis.detail`.

### Byte-stable output

Like the SBOM export, the VEX export is **byte-stable**: re-exporting the same
scan produces identical bytes, so the document can be signed, cached, and
diffed across releases. Statements are sorted by `(CVE id, purl)`, the document
id is derived deterministically from the scan id, and the timestamp reflects the
scan's persisted completion time (not the moment of export).

### Download from the API

```bash
# OpenVEX (default)
curl -sS -L -OJ \
  -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
  "https://trustedoss.example.com/v1/projects/${PROJECT_ID}/vex?format=openvex"

# CycloneDX VEX
curl -sS -L -OJ \
  -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
  "https://trustedoss.example.com/v1/projects/${PROJECT_ID}/vex?format=cyclonedx"
```

`format` accepts `openvex` or `cyclonedx`. The file name is
`vex-<project-slug>.<ext>`.

| Status | Meaning |
|---|---|
| `200` | VEX document download. |
| `401` | Not authenticated — supply a valid token. |
| `404` | Project does not exist, or the caller is not a member of its team (existence-hidden, same posture as the SBOM export). |
| `422` | Unknown `format` — use `openvex` or `cyclonedx`. |

:::note Access
Downloading the VEX document requires `developer` or higher. Cross-team callers
receive `404`, not `403`, so a non-member cannot tell whether the project exists.
:::

## Import a VEX document (consume)

The portal can also **import** an external VEX document (OpenVEX or CycloneDX
VEX) and auto-apply its statements to your findings, suppressing triage noise.
This is the inverse of [exporting a VEX document](#export-a-vex-document):
export captures your triage as a standards document; import applies someone
else's (or a previously-exported) document back onto your findings.

Typical uses:

- A vendor or upstream maintainer publishes a VEX document saying a CVE is
  **not affected** in their package — import it instead of re-triaging by hand.
- You exported a VEX document, edited it in another tool, and want the decisions
  back in the portal.
- A CI step generated a VEX document you want to consume on the next sync.

### Permissions

VEX import is a **bulk-triage** action — a single upload can transition many
findings — so it requires **`team_admin`** within the project's team (the same
bar as moving a finding *into* `Suppressed`). A `developer` who is a team member
receives `403`; a non-member receives `404` (existence-hidden, same posture as
export).

### How matching works

Each VEX statement is matched to a finding by **vulnerability id** (CVE/GHSA/OSV
name) **+ component purl** against your project's **latest succeeded** scan. A
statement that resolves to no finding (the CVE isn't in this scan, or the purl
doesn't match) is **skipped with a reason** — it never errors the whole import.

### Status mapping (VEX → portal)

The import reverse-maps each VEX status to a single canonical portal state:

| OpenVEX `status` | CycloneDX `analysis.state` | Portal state |
|---|---|---|
| `not_affected` | `not_affected` | **Not affected** |
| — | `false_positive` | **False positive** |
| `affected` | `exploitable` | **Exploitable** |
| `fixed` | `resolved` | **Fixed** |
| `under_investigation` | `in_triage` | **Analyzing** |

`under_investigation` / `in_triage` map to **Analyzing** (not `New`): `New` is
the discovery inbox and nothing transitions *into* it.

### Legal transitions are preserved

Import obeys the same [VEX state machine](#vex-state-machine) as the manual
workflow. Because every verdict routes through `Analyzing`, importing (say)
`not_affected` onto a finding that is still **New** applies the **legal two-step
path** `New → Analyzing → Not affected` automatically, and the audit log records
**both** steps. The justification from the VEX document (`impact_statement` /
`analysis.detail`) is preserved on the finding.

### Idempotency & round-trip

Importing the same document twice is safe: a finding already in the target state
is **skipped** (`already_at_target`), not re-written. Exporting your triage and
immediately re-importing it is a **no-op** — the portal's export/import
round-trip is status-stable.

### Import from the API

```bash
curl -sS -X POST \
  -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
  -F "upload=@vex.openvex.json;type=application/json" \
  "https://trustedoss.example.com/v1/projects/${PROJECT_ID}/vex/import"
```

The response is a JSON summary:

```json
{
  "format": "openvex",
  "matched": 12,
  "applied": 9,
  "skipped": 3,
  "errors": [
    {
      "vulnerability": "CVE-2024-0001",
      "product": "pkg:npm/left-pad@1.0.0",
      "reason": "unknown_component",
      "detail": "CVE-2024-0001 has no finding on pkg:npm/left-pad@1.0.0 in the latest scan"
    }
  ]
}
```

- `matched` — findings a statement resolved to.
- `applied` — findings whose status actually changed.
- `skipped` — findings/statements deliberately not applied (no-op, unknown
  vuln/purl, …).
- `errors[].reason` — one of `unknown_vulnerability`, `unknown_component`,
  `ambiguous_match`, `unmapped_status`, `illegal_transition`,
  `already_at_target`, `forbidden_transition`, `malformed_statement`.

| Status | Meaning |
|---|---|
| `200` | Import ran — see the summary (even when 0 applied). |
| `401` | Not authenticated. |
| `403` | Authenticated, member of the team, but not `team_admin`. |
| `404` | Project missing or caller not a team member (existence-hidden). |
| `413` | The uploaded document exceeds the size limit (`VEX_IMPORT_MAX_BYTES`, default 8 MiB). |
| `422` | The document is not valid JSON, or is neither OpenVEX nor CycloneDX VEX. Body is `application/problem+json`. |

## VEX in the UI

Everything above is also available without the API, from the **Vulnerabilities tab**
toolbar.

### Export and import buttons

- **Export VEX** — two buttons, **OpenVEX** and **CycloneDX VEX**. Click either
  to download the project's current triage as a standalone VEX document. The
  download goes through your authenticated session (the token never appears in
  the URL), the same as the SBOM and PDF report downloads. Export is a read, so
  any `developer` (or higher) can use it.
- **Import VEX** — opens a dialog where you choose an OpenVEX or CycloneDX VEX
  JSON file and upload it. The format is auto-detected. After the import runs the
  dialog shows a summary panel with three counts — **Matched** (findings a
  statement resolved to), **Applied** (findings whose status actually changed),
  and **Skipped** — plus a per-statement list of skip reasons for anything that
  did not apply (unknown CVE/component, illegal transition, already at target, …).
  Import is a bulk-triage action: the button is **only enabled for `team_admin`**
  (and `super_admin`). A `developer` sees it disabled with a tooltip explaining
  the requirement. A `403`, `413`, or `422` from the server is shown inline as a
  plain-language message — the dialog never leaves you guessing.

### Filter: VEX-suppressed only

The toolbar has a **VEX-suppressed only** checkbox. Turn it on to keep only the
findings on the current page whose status was set by a VEX import
(`analysis_source = vex_import`) — handy for eyeballing exactly what a document you
just imported changed. The toggle is mirrored into the URL (`?vex_suppressed=1`)
so it survives a reload and can be shared as a deep link. Rows set by a VEX import
also carry a small **VEX** badge next to their status, paired with the label (not
color alone) so the provenance is visible at a glance.

### Provenance badge in the drawer

Open a finding whose status came from an import and the drawer shows a **VEX
provenance** panel: the consuming document's author, id (`@id` /
`serialNumber`), timestamp, the VEX status the matching statement carried, when
the import ran, and the imported justification. All of these fields come from the
uploaded document and are rendered strictly as **text** — the portal never
interprets them as HTML, so a justification or author containing markup is shown
verbatim and is inert.

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
- Standalone **Fix availability** drawer section — today the fix version surfaces as `fixed_version` inside the **Affected** section (real data since v2.2 — see [Fixed version](#fixed-version--the-version-that-remediates-the-cve)), and the per-component minimum safe bump surfaces in the **Recommended upgrade** panel (v2.2 — see [Upgrade recommendation](#upgrade-recommendation-recommended-version)).

## See also

- [Components & licenses](./components-and-licenses.md)
- [Approvals](./approvals.md)
- [DT connector](../admin-guide/dt-connector.md)
- [GitHub Actions — gating on CVEs](../ci-integration/github-actions.md)
