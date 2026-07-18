---
id: vex
title: VEX documents — export & import
description: Export the project's triage as a standalone OpenVEX or CycloneDX VEX document, import an external one to auto-apply its statements, and drive both from the UI.
sidebar_label: VEX documents
sidebar_position: 5
---

# VEX documents — export & import

A VEX (Vulnerability Exploitability eXchange) document records *which CVEs
actually affect your product*. Inside the portal that record lives on each
finding as its [VEX status](./vulnerabilities.md#vex-state-machine); this page
covers moving it across the boundary as a **standards document** — exporting
your triage for downstream consumers, and importing someone else's (or a
previously exported) document back onto your findings.

:::note Audience
Engineers exchanging triage with suppliers or downstream consumers. Export
requires `developer` or higher; import is a bulk-triage action and requires
`team_admin`.
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

<!-- docs-uat: id=vulns-vex-export-api kind=api auth=admin url=/v1/projects/${PROJECT_ID}/vex?format=openvex expect=status:200 tier=nightly -->
Export the VEX document over the API:

<!-- docs-uat: id=vulns-api-vex-export kind=shell ctx=host tier=manual waiver=example-curl-placeholder-host-and-api-key -->
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

Import obeys the same [VEX state machine](./vulnerabilities.md#vex-state-machine) as the manual
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

<!-- docs-uat: id=vulns-api-vex-import kind=shell ctx=host tier=manual waiver=example-curl-placeholder-host-and-api-key -->
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

