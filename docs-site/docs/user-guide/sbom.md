---
id: sbom
title: SBOM & reports
description: Export CycloneDX (JSON/XML) and SPDX (JSON/Tag-Value) SBOMs, generate the NOTICE file, and download Excel / PDF reports.
sidebar_label: SBOM & reports
sidebar_position: 5
---

# SBOM & reports

The portal generates **Software Bill of Materials** (SBOM) artifacts from the latest successful scan. Four interchange formats are supported, plus an attribution `NOTICE` file and human-readable Excel / PDF reports.

:::note Audience
Engineers shipping releases, compliance leads filing artifacts, customers fulfilling SBOM requests under [EO 14028](https://www.cisa.gov/topics/cyber-threats-and-advisories/cybersecurity-best-practices/secure-by-design/sbom). Read access via team membership.
:::

## Supported formats

| Format | MIME | Use case |
|---|---|---|
| **CycloneDX 1.6 (JSON)** | `application/vnd.cyclonedx+json` | Modern de-facto standard for SCA tooling. Includes VEX. |
| **CycloneDX 1.6 (XML)** | `application/vnd.cyclonedx+xml` | Same data; XML for legacy tooling. |
| **SPDX 2.3 (JSON)** | `application/spdx+json` | NTIA minimum elements; broadly accepted in regulated industries. |
| **SPDX 2.3 (Tag-Value)** | `text/spdx` | The original SPDX line-based format. |

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
3. Choose the format from the dropdown.
4. Click **Download**.

The file name is `<project-name>-<scan-finished-iso>.sbom.<ext>`.

## Download from the API

```bash
# CycloneDX JSON
curl -sS -L -OJ \
  -H "Authorization: ApiKey ${TRUSTEDOSS_API_KEY}" \
  "https://trustedoss.example.com/api/v1/projects/${PROJECT_ID}/sbom?format=cyclonedx-json"

# SPDX JSON
curl -sS -L -OJ \
  -H "Authorization: ApiKey ${TRUSTEDOSS_API_KEY}" \
  "https://trustedoss.example.com/api/v1/projects/${PROJECT_ID}/sbom?format=spdx-json"
```

`format` accepts: `cyclonedx-json`, `cyclonedx-xml`, `spdx-json`, `spdx-tag-value`.

By default, the export reflects the project's **latest successful scan**. Pass `?scan_id=<uuid>` to pin to a specific scan.

## NOTICE file

For Apache-2.0 §4(d) compliance and similar attribution obligations, the portal auto-generates a `NOTICE.txt` from the project's latest scan.

The file contains:

- A header with the project name and scan timestamp.
- For each component: name, version, license, copyright statement (when ORT extracted one), and a link to the upstream license text.
- Grouped by license to make the redistribution package straightforward.

### Download

- **UI:** Project → **Obligations** tab → **Download NOTICE**.
- **API:**

  ```bash
  curl -sS -L -OJ \
    -H "Authorization: ApiKey ${TRUSTEDOSS_API_KEY}" \
    "https://trustedoss.example.com/api/v1/projects/${PROJECT_ID}/notice"
  ```

The `NOTICE` file is byte-stable across exports — diffable across releases.

## Excel & PDF reports

Human-readable reports for stakeholders who do not consume SBOMs directly.

| Report | Contents |
|---|---|
| **Components Excel** | One row per component: name, version, type, concluded license, classification, open CVE count, fix-available count. |
| **Vulnerabilities Excel** | One row per finding: CVE, component, severity, state, justification, discovered, last-seen. |
| **Compliance PDF** | Risk-score summary, classification distribution, top-10 risky components, obligation list, NOTICE preview. |

### Download

- **UI:** Project → **Reports** menu (top-right of any tab).
- **API:**

  ```bash
  # Components Excel
  curl -sS -L -OJ \
    -H "Authorization: ApiKey ${TRUSTEDOSS_API_KEY}" \
    "https://trustedoss.example.com/api/v1/projects/${PROJECT_ID}/reports/components.xlsx"

  # Vulnerabilities Excel
  curl -sS -L -OJ \
    -H "Authorization: ApiKey ${TRUSTEDOSS_API_KEY}" \
    "https://trustedoss.example.com/api/v1/projects/${PROJECT_ID}/reports/vulnerabilities.xlsx"

  # Compliance PDF
  curl -sS -L -OJ \
    -H "Authorization: ApiKey ${TRUSTEDOSS_API_KEY}" \
    "https://trustedoss.example.com/api/v1/projects/${PROJECT_ID}/reports/compliance.pdf"
  ```

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

### `404` when calling `/sbom`

The project has no successful scan yet. Trigger one — see [Scans](./scans.md).

### Excel report opens with garbled non-ASCII

The report uses UTF-8 with BOM, which Excel honors on Windows. On macOS, open with **Numbers** or convert via `iconv -f UTF-8 -t UTF-16LE`.

### NOTICE file is missing copyrights for some components

ORT extracts copyrights from license headers. Some packages omit them; the NOTICE entry will say "Copyright holder unspecified". Add a manual override in the component drawer if needed.

## See also

- [Components & licenses](./components-and-licenses.md)
- [Vulnerabilities](./vulnerabilities.md)
- [API overview](../reference/api-overview.md)
