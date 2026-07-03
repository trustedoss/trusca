---
id: sbom
title: SBOM
description: Export CycloneDX (JSON/XML) and SPDX (JSON/Tag-Value) SBOMs and generate the NOTICE file in TRUSCA.
sidebar_label: SBOM
sidebar_position: 5
---

# SBOM

The portal generates **Software Bill of Materials** (SBOM) artifacts from the latest successful scan. Four interchange formats are supported, plus an attribution `NOTICE` file.

:::note Export vs. upload
This page is about **exporting** an SBOM that TRUSCA generated from a scan. To go the other way — **upload** an SBOM your own tooling already produced (CycloneDX or SPDX) so TRUSCA matches CVEs and scores its conformance — see [Upload an SBOM](../ci-integration/sbom-upload.md) and [Scans → Received SBOMs](./scans.md#received-sboms-uploaded).
:::

![Project detail — SBOM tab with format selector and last-scan summary](/img/screenshots/user-sbom-tab.png)

:::note Audience
Engineers shipping releases, compliance leads filing artifacts, customers fulfilling SBOM requests under [EO 14028](https://www.cisa.gov/topics/cyber-threats-and-advisories/cybersecurity-best-practices/secure-by-design/sbom). Read access via team membership.
:::

## Supported formats

| Format | Query value (`format=`) | MIME | Use case |
|---|---|---|---|
| **CycloneDX 1.6 (JSON)** | `cyclonedx-json` | `application/vnd.cyclonedx+json` | Modern de-facto standard for SCA tooling. Includes VEX. |
| **CycloneDX 1.6 (XML)** | `cyclonedx-xml` | `application/vnd.cyclonedx+xml` | Same data; XML for legacy tooling. |
| **SPDX 2.3 (JSON)** | `spdx-json` | `application/spdx+json` | NTIA minimum elements; broadly accepted in regulated industries. |
| **SPDX 2.3 (Tag-Value)** | `spdx-tv` | `text/spdx` | The original SPDX line-based format. |

Both formats are produced from the same internal model, so component lists are identical (modulo format-specific fields).

## What's included per component

Each component carries its name, version, Package URL (PURL), and the licenses
detected for it:

- **Licenses** — populated from the scan's license findings. CycloneDX uses the
  per-component `licenses` array (preferring the *concluded* verdict, then
  *declared*, then *detected*); SPDX fills `licenseDeclared` and
  `licenseConcluded` as SPDX license expressions. Components with no detected
  license — and licenses with no SPDX identifier (ORT `LicenseRef-*`) — emit the
  spec sentinel `NOASSERTION` in SPDX (CycloneDX still carries the license
  name). `copyrightText` is currently always `NOASSERTION`.
- **Top-level version** — `metadata.component.version` reflects the scanned
  release: if the scan was submitted with a `release` label (e.g. `v1.2.3`),
  that label is used; otherwise the scan id is used as a stable fallback.

## Byte-stable output

All four exports are **byte-stable**: re-exporting the same scan produces identical bytes. This makes diffing, signing, and caching trivial.

The portal achieves byte-stability by:

- Sorting components by `purl` (lexicographic).
- Sorting license expressions alphabetically within each component.
- Pinning `serialNumber` (CycloneDX) / `documentNamespace` (SPDX) to a deterministic value derived from `(project_id, scan_id)`.
- Omitting timestamps from the body (the SBOM's metadata records the scan finish time, which is stable per scan).

## Download from the UI

1. Open the project.
2. Click the **SBOM** tab.
3. Click one of the four format buttons (CycloneDX JSON, CycloneDX XML, SPDX JSON, SPDX Tag-Value) to download.

![SBOM tab — four format download buttons (CycloneDX JSON/XML, SPDX JSON/Tag-Value)](/img/screenshots/user-sbom-format-buttons.png)

The file name is `sbom-<project-slug>.<ext>`.

## Download from the API

<!-- docs-uat: id=sbom-cyclonedx-api kind=api auth=admin url=/v1/projects/${PROJECT_ID}/sbom?format=cyclonedx-json expect=status:200 tier=nightly -->
The API serves the SBOM in CycloneDX JSON:

<!-- docs-uat: id=sbom-spdx-api kind=api auth=admin url=/v1/projects/${PROJECT_ID}/sbom?format=spdx-json expect=status:200 tier=nightly -->
…and in SPDX JSON (same endpoint, different `format`):

<!-- docs-uat: id=sbom-api-download kind=shell ctx=host tier=manual waiver=example-curl-placeholder-host-and-api-key -->
```bash
# CycloneDX JSON
curl -sS -L -OJ \
  -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
  "https://trustedoss.example.com/v1/projects/${PROJECT_ID}/sbom?format=cyclonedx-json"

# SPDX JSON
curl -sS -L -OJ \
  -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
  "https://trustedoss.example.com/v1/projects/${PROJECT_ID}/sbom?format=spdx-json"
```

`format` accepts: `cyclonedx-json`, `cyclonedx-xml`, `spdx-json`, `spdx-tv`.

Endpoint always exports the **latest succeeded** scan's SBOM; pinning to a specific historical scan id is on the roadmap.

:::caution Audit evidence — pin scans externally
The SBOM export always reflects the latest succeeded scan. External
auditors typically ask for the SBOM at a specific release point
(e.g. "what shipped on 2026-01-15?"). Until historical-scan pinning
lands, capture the SBOM artifact at each release boundary and
store it in your release archive. Treat the portal as the *current*
SBOM, not the *historical* one.
:::

## NOTICE file

For Apache-2.0 §4(d) compliance and similar attribution obligations, the portal auto-generates a NOTICE attribution body from the project's latest scan.

The file contains:

- A header with the project name and generation timestamp.
- One section per detected license, listing the components (`name @ version`)
  under that license, each with a copyright line.
- Each license section's attribution obligations (e.g. *attribution*,
  *no-endorsement*) with a short description and a policy reference link.
- A closing **License Texts** section with the full text of every license
  observed in the project.
- A **License review needed** section listing any component whose license
  carries an AI-specific restriction flag (behavioral-use or non-commercial);
  see [AI license review flags](./components-and-licenses.md#ai-license-review-flags).

### Copyright lines

Each component entry carries the copyright statement the scan's SBOM recorded
for it (`cdxgen` reads it from package metadata). When the SBOM recorded no
copyright holder, the line is never left blank: it falls back to an explicit
note that the SBOM carries no copyright holder, pointing at the component's
registry URL so you can retrieve the statement from the source. A manual
per-component override in the component drawer is on the [roadmap](#roadmap).

### License texts

The document closes with a **License Texts** section reproducing the full
canonical text of each license that appears in the project. The portal bundles
the SPDX (Software Package Data Exchange) text for 32 well-known licenses —
MIT, Apache-2.0, and the BSD, GPL / LGPL / AGPL, MPL, EPL, and CDDL families,
among others. A license whose text is not bundled is not silently dropped: its
entry falls back to the license's reference-URL link to the canonical text.
With this section the NOTICE artifact itself satisfies the catalog's
`license_text_inclusion_required` obligation — see the
[obligation catalog](../reference/obligation-catalog.md#structured-obligation-fields).

### Supported formats

The NOTICE endpoint accepts a `format` query value (default `text`):

| Format | Query value (`format=`) | MIME | Extension | Use case |
|---|---|---|---|---|
| **Plain text** | `text` | `text/plain` | `.txt` | Drop into a release tarball's `NOTICE` file. The default. |
| **Markdown** | `markdown` | `text/markdown` | `.md` | Render in a docs site or PR description. |
| **HTML** | `html` | `text/html` | `.html` | A self-contained document (inline `<style>`, no scripts) for an attribution page. |

The output is byte-stable across exports for a given scan and format — diffable across releases.

### Download

- **UI:** Project → **Obligations** tab → pick a format (**text** or **HTML**) → **Download NOTICE**. The browser saves `NOTICE-<project>.<ext>`. The markdown variant is available from the API.
- **API:**

  ```bash
  # Plain text (default)
  curl -sS -L -OJ \
    -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
    "https://trustedoss.example.com/v1/projects/${PROJECT_ID}/notice?format=text&download=true"

  # HTML
  curl -sS -L -OJ \
    -H "Authorization: Bearer ${TRUSTEDOSS_API_KEY}" \
    "https://trustedoss.example.com/v1/projects/${PROJECT_ID}/notice?format=html&download=true"
  ```

  `format` accepts `text`, `markdown`, `html`. Pass `download=true` so the response carries `Content-Disposition: attachment` and `-OJ` saves it under the server-supplied file name (`NOTICE-<project>.<ext>`); omit it to stream the body inline.

## VEX exports

CycloneDX SBOMs include the project's VEX state for every finding. SPDX does not have a native VEX representation, so SPDX exports omit per-finding state; pair an SPDX export with a separate CycloneDX VEX document if your downstream consumer expects it.

Each entry in the SBOM's `vulnerabilities[]` array carries the CVE id, its
source database, the VEX analysis, and `affects[].ref` pointing at the
affected component's `bom-ref` within the same document (so consumers can
join findings to components without parsing PURLs).

The VEX states map directly to CycloneDX's `analysis.state`; the analyst's
free-text note (when present) is carried in `analysis.detail`:

| Portal state | CycloneDX VEX `state` | `analysis.detail` |
|---|---|---|
| `New` | `in_triage` | (none) |
| `Analyzing` | `in_triage` | analyst note |
| `Exploitable` | `exploitable` | analyst note |
| `Not affected` | `not_affected` | analyst note |
| `False positive` | `false_positive` | analyst note |
| `Suppressed` | `not_affected` | analyst note |
| `Fixed` | `resolved` | analyst note |

The closed CycloneDX `analysis.justification` enum (`code_not_present`, …) is
**never** emitted: its members have a precise meaning that cannot be inferred
from free-form analyst prose, so the note stays in `analysis.detail`.

## Verify it worked

<!-- docs-uat: id=sbom-cyclonedx-validate kind=manual tier=manual -->
1. The downloaded SBOM passes a validator — for CycloneDX, run [`cyclonedx validate`](https://github.com/CycloneDX/cyclonedx-cli):

   ```bash
   cyclonedx validate --input-file checkout-service.sbom.json
   ```

<!-- docs-uat: id=sbom-spdx-validate kind=manual tier=manual -->
2. SPDX validates with [`spdx-tools`](https://github.com/spdx/tools-python):

   ```bash
   pyspdxtools -i checkout-service.sbom.json
   ```

<!-- docs-uat: id=sbom-byte-identical kind=manual tier=manual -->
3. Re-downloading the same scan produces a byte-identical file:

   ```bash
   sha256sum checkout-service.sbom.json checkout-service.sbom.json.again
   # → identical hashes
   ```

## Troubleshooting

### Empty SBOM when no scan has succeeded yet

If the project has no succeeded scan yet, the export still returns a valid SBOM document with empty `components`/`packages` lists (HTTP 200) so downstream tooling can parse it.

### `422` from `/sbom?format=…`

The query string used a value the API does not accept. Use one of the four canonical query values from the table above — in particular, **the SPDX Tag-Value format is `spdx-tv` (not `spdx-tag-value`)**.

### `404` for a project you cannot access

The SBOM and NOTICE endpoints **existence-hide**: a caller who is not a member
of the project's team receives `404` (not `403`), the same response as a
project id that does not exist. This is deliberate — unlike the project-detail
endpoint (which returns `403`), the SBOM/NOTICE bodies expose structural detail
(component names, versions), so the endpoints refuse to confirm a project even
exists to a non-member. Join the owning team to get access.

### A copyright line shows a registry link instead of a holder

The scan's SBOM recorded no copyright holder for that component — `cdxgen`
found none in the package metadata. The NOTICE never leaves the line blank; it
notes the missing holder and points at the component's registry URL instead,
so you can retrieve the copyright statement from the upstream source directly.
A manual per-component override in the component drawer is on the
[roadmap](#roadmap). The SPDX export is a separate surface and still emits
`copyrightText: NOASSERTION`.

## Compliance evidence trail {#compliance-evidence-trail}

External auditors typically ask portal operators five questions. This
table tells you which are answerable today and which require
workarounds.

| Auditor question | v0.10.0 answer source | Limitation |
|------------------|----------------------|------------|
| "Show me the SBOM as of release X" | Manual archive; portal only retains latest | Historical pinning on the roadmap |
| "Who downloaded the SBOM / NOTICE in the last quarter?" | `structlog` (Loki / journald) — not `audit_logs` | Audit-row promotion on the roadmap |
| "Show me when GPL was first detected on project X" | `audit_logs` on `scans.create` + per-scan `vulnerability_findings.create` | Yes — full evidence chain |
| "Show me every approval verdict in 2026 Q1" | `audit_logs` on `component_approvals.update` + `decision_note` | Yes — full evidence chain |
| "Prove no audit row was tampered with" | Append-only trigger (migration 0012) | Super-admin role still has bypass — review [audit-log hardening](../admin-guide/audit-log.md#schema) |

## Supplier submission compatibility

The export satisfies common corporate supplier SBOM requirements (e.g.
[SK Telecom's supplier guide](https://sktelecom.github.io/guide/supply-chain/for-suppliers/requirements/)):
standard format/version (CycloneDX, SPDX 2.3), ISO-8601 timestamp, tool
metadata, per-component name + version + PURL, licenses, and transitive
dependencies (when the scanned source includes or can resolve lockfiles).

Two caveats to be aware of before submitting:

- **`pkg:generic/` PURLs are rejected by some programs.** A generic PURL means
  the scanner could not classify the component's ecosystem; supply lockfiles /
  build artifacts so cdxgen can assign an ecosystem-specific type.
- **Licenses without an SPDX id** appear as `NOASSERTION` in SPDX expressions
  (the CycloneDX `license.name` still carries the label).

## Roadmap

Items the manual previously promised that are not in this release; tracked for later releases.

- The **vulnerability PDF report** _is_ implemented in this release — see [Vulnerabilities → Download a PDF report](./vulnerabilities.md#download-a-pdf-report) (`GET /v1/projects/{id}/vulnerability-report.pdf`). Still **not** implemented: the **Excel** reports (Components Excel, Vulnerabilities Excel) and the **Compliance PDF**; there are no `/v1/projects/{id}/reports/...` endpoints for those, and they will land in a later release. Stakeholders who need a tabular view today should consume the SBOM (CycloneDX JSON) via their preferred tooling.
- Manual copyright override in the component drawer for NOTICE assembly — planned.
- Historical-scan pinning on the SBOM and NOTICE exports — planned.
- Promote SBOM / NOTICE downloads from `structlog` events to `audit_logs` rows — planned.

## See also

- [Verify SBOM signatures (cosign)](../reference/sbom-signature-verification.md) — prove the SBOM is intact and signed by this deployment
- [Components & licenses](./components-and-licenses.md)
- [Vulnerabilities](./vulnerabilities.md)
- [API overview](../reference/api-overview.md)
