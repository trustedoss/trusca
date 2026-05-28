---
id: sbom
title: SBOM
description: Export CycloneDX (JSON/XML) and SPDX (JSON/Tag-Value) SBOMs and generate the NOTICE file in TrustedOSS Portal.
sidebar_label: SBOM
sidebar_position: 5
---

# SBOM

The portal generates **Software Bill of Materials** (SBOM) artifacts from the latest successful scan. Four interchange formats are supported, plus an attribution `NOTICE` file.

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
lands (v2.1), capture the SBOM artifact at each release boundary and
store it in your release archive. Treat the portal as the *current*
SBOM, not the *historical* one.
:::

## NOTICE file

For Apache-2.0 §4(d) compliance and similar attribution obligations, the portal auto-generates a NOTICE attribution body from the project's latest scan.

The file contains:

- A header with the project name and scan timestamp.
- For each component: name, version, license, copyright statement (when the scan captured one), and a link to the upstream license text.
- Grouped by license to make the redistribution package straightforward.

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

The VEX states map directly to CycloneDX's `analysis.state`:

| Portal state | CycloneDX VEX `state` | `justification` |
|---|---|---|
| `New` | `in_triage` | (none) |
| `Analyzing` | `in_triage` | analyst note |
| `Exploitable` | `exploitable` | analyst note |
| `Not affected` | `not_affected` | reason text (`code_not_present`, `vulnerable_code_not_in_execute_path`, …) |
| `False positive` | `false_positive` | analyst note |
| `Suppressed` | `not_affected` | reason text |
| `Fixed` | `resolved` | (`fix_version` populated) |

## Verify it worked

1. The downloaded SBOM passes a validator — for CycloneDX, run [`cyclonedx validate`](https://github.com/CycloneDX/cyclonedx-cli):

   ```bash
   cyclonedx validate --input-file checkout-service.sbom.json
   ```

2. SPDX validates with [`spdx-tools`](https://github.com/spdx/tools-python):

   ```bash
   pyspdxtools -i checkout-service.sbom.json
   ```

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

### NOTICE file is missing copyrights for some components

Copyright statements come from package metadata and license headers. Some packages omit them; the NOTICE entry will say "Copyright holder unspecified".

## Compliance evidence trail at v2.0.0 {#compliance-evidence-trail-at-v200}

External auditors typically ask portal operators five questions. This
table tells you which are answerable today and which require
workarounds.

| Auditor question | v2.0.0 answer source | Limitation |
|------------------|----------------------|------------|
| "Show me the SBOM as of release X" | Manual archive; portal only retains latest | Historical pinning on v2.1 roadmap |
| "Who downloaded the SBOM / NOTICE in the last quarter?" | `structlog` (Loki / journald) — not `audit_logs` | Audit-row promotion on v2.1 roadmap |
| "Show me when GPL was first detected on project X" | `audit_logs` on `scans.create` + per-scan `vulnerability_findings.create` | Yes — full evidence chain |
| "Show me every approval verdict in 2026 Q1" | `audit_logs` on `component_approvals.update` + `decision_note` | Yes — full evidence chain |
| "Prove no audit row was tampered with" | Append-only trigger (migration 0012) | Super-admin role still has bypass — review [audit-log hardening](../admin-guide/audit-log.md#schema) |

## Roadmap (v2.x)

Items the manual previously promised that are not in v2.0.0; tracked for later releases.

- The **vulnerability PDF report** _is_ implemented at v2.0.0 — see [Vulnerabilities → Download a PDF report](./vulnerabilities.md#download-a-pdf-report) (`GET /v1/projects/{id}/vulnerability-report.pdf`). Still **not** implemented: the **Excel** reports (Components Excel, Vulnerabilities Excel) and the **Compliance PDF**; there are no `/v1/projects/{id}/reports/...` endpoints for those, and they will land in a later release. Stakeholders who need a tabular view today should consume the SBOM (CycloneDX JSON) via their preferred tooling.
- Manual copyright override in the component drawer for NOTICE assembly — planned for v2.2.
- Historical-scan pinning on the SBOM and NOTICE exports — planned for v2.1.
- Promote SBOM / NOTICE downloads from `structlog` events to `audit_logs` rows — planned for v2.1.

## See also

- [Verify SBOM signatures (cosign)](../reference/sbom-signature-verification.md) — prove the SBOM is intact and signed by this deployment
- [Components & licenses](./components-and-licenses.md)
- [Vulnerabilities](./vulnerabilities.md)
- [API overview](../reference/api-overview.md)
