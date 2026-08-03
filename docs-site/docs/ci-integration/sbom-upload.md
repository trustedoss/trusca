---
id: sbom-upload
title: Upload an SBOM
description: Upload a CycloneDX or SPDX SBOM that an external tool already produced — TRUSCA queues a scan that matches CVEs, classifies declared licenses, scores conformance, and runs the build gate.
sidebar_label: Upload an SBOM
sidebar_position: 5
---

# Upload an SBOM

Already have an SBOM (software bill of materials) from another tool? Upload it to an existing TRUSCA project and TRUSCA matches its components against vulnerability data, classifies declared licenses, builds the dependency graph, scores the SBOM's conformance, and runs the build gate — without cloning or scanning your source. Both **CycloneDX-JSON** and **SPDX** (JSON or Tag-Value) are accepted.

The endpoint is `POST /v1/projects/{project_id}/sbom-ingest`. It is asynchronous: a successful request returns `202 Accepted` with a queued scan row, and you poll the scan to read the result.

:::note Audience
Engineers and CI pipelines that produce a CycloneDX JSON SBOM with a tool of their own (for example a build that runs cdxgen) and want TRUSCA to analyze it. You need a TRUSCA API key — see [API keys](../admin-guide/api-keys.md).
:::

:::caution Not a Dependency-Track endpoint
TRUSCA is **not** Dependency-Track API compatible. The Dependency-Track flow — `POST /api/v1/bom` with an `X-Api-Key` header, an `autoCreate` form field, and a base64 `bom` field — does not work here. Use the TRUSCA endpoint, the `Authorization: Bearer` header, and the multipart fields documented below. The project must already exist; there is no auto-create.
:::

## Prerequisites

- A TRUSCA API key in the `tos_<prefix>_<secret>` format. Create one at **/integrations → API keys → New API key**; see [API keys](../admin-guide/api-keys.md) for the scope model.
- The target **project already exists**. Copy its UUID from **Project Settings → CI/CD**. Uploading an SBOM does not create a project.
- The API key's scope covers that project — a `project`-scoped key bound to it, or a `team`-scoped key for a project the team owns.
- A **CycloneDX-JSON** document (supported `specVersion` values are `1.2` through `1.7`; 1.7 adds the ML-BOM fields — see [AI SBOM conformance](../user-guide/ai-sbom-conformance.md)) **or** an **SPDX** document in JSON or Tag-Value form. Trivy auto-detects the format for CVE matching; SPDX is mapped to CycloneDX for component persistence. SPDX RDF/XML is not accepted.
- No scan is currently queued or running for the project (one in-flight scan per project; a second returns `409`).

## Upload an SBOM

Send the document as `multipart/form-data`:

| Field | Required | Example | Description |
|---|---|---|---|
| `sbom` | yes | `@bom.cdx.json` | The CycloneDX JSON SBOM file. |
| `ref` | no | `main` | The git ref the SBOM was produced from (branch name, tag, or full ref). TRUSCA normalizes it into a retention key. |
| `release` | no | `v1.2.3` | A release or version label for the resulting snapshot. |

Authenticate with the API key as a bearer token. The header is `Authorization: Bearer <API_KEY>` — **not** `X-Api-Key`.

```bash
curl -X POST \
  https://trustedoss.example.com/v1/projects/<PROJECT_ID>/sbom-ingest \
  -H "Authorization: Bearer $TRUSTEDOSS_API_KEY" \
  -F "sbom=@bom.cdx.json" \
  -F "ref=main" \
  -F "release=v1.2.3"
```

Substitute `<PROJECT_ID>` with the project UUID and set `TRUSTEDOSS_API_KEY` in your environment. A cdxgen-based pipeline can produce `bom.cdx.json` in a build step and upload it with the command above.

On success the response is `202 Accepted` with the queued scan row:

```json
{
  "id": "3f9a2c10-7b4e-4d2a-9c11-0e8f5d6a1b22",
  "project_id": "<PROJECT_ID>",
  "kind": "sbom",
  "status": "queued",
  "ref": "main",
  "release": "v1.2.3"
}
```

`kind` is always `sbom` for an uploaded SBOM, and `status` starts at `queued`. Keep the `id` — that is the scan id you poll next.

## Watch the scan finish

Poll the scan with the same bearer token until it reaches a terminal state (`succeeded`, `failed`, or `cancelled`). This is the same polling pattern the [GitHub Actions](./github-actions.md) integration uses.

```bash
curl https://trustedoss.example.com/v1/scans/<SCAN_ID> \
  -H "Authorization: Bearer $TRUSTEDOSS_API_KEY"
```

`status` moves `queued → running → succeeded`. A reasonable cadence is one poll every 30 seconds. Once `status` is `succeeded`, open the project in the portal to read components, vulnerabilities, and licenses.

## Read the conformance verdict

When you upload an SBOM, TRUSCA scores its **quality** against a fixed bar before (and regardless of) matching — so a "shell" SBOM with no versions, no package URLs, or no dependency graph is flagged rather than silently producing an empty result. Read the verdict with:

```bash
curl -H "Authorization: Bearer $TRUSTEDOSS_API_KEY" \
  https://trustedoss.example.com/v1/projects/<PROJECT_ID>/scans/<SCAN_ID>/conformance
```

The response is the verdict for that scan:

```json
{
  "scan_id": "<SCAN_ID>",
  "project_id": "<PROJECT_ID>",
  "source_format": "cyclonedx",
  "result": "warn",
  "n_fail": 0,
  "n_warn": 1,
  "component_count": 42,
  "purl_coverage_pct": 100,
  "license_coverage_pct": 96,
  "hash_coverage_pct": 0,
  "checks": [
    { "id": "purl", "label": "PURL coverage (>= 90%)", "required": true, "status": "pass", "detail": "100% (42/42)", "missing": [] },
    { "id": "hash", "label": "Hash coverage (>= 50%, recommended)", "required": false, "status": "warn", "detail": "0% (0/42)", "missing": [] }
  ]
}
```

- **`result`** is `pass`, `warn`, or `fail`. `fail` means a **mandatory** check failed; `warn` means every mandatory check passed but a **recommended** one (license or hash coverage) fell short; `pass` means all checks passed.
- **Mandatory checks**: a timestamp, tool info, a top-level component with name and version, 100% component name+version, PURL coverage at or above `SBOM_CONFORMANCE_PURL_MIN_PCT` (default `90`), no `pkg:generic` placeholders, and a transitive dependency graph.
- **Recommended checks** (warn only): license coverage at or above `SBOM_CONFORMANCE_LICENSE_MIN_PCT` (default `80`) and hash coverage at or above `SBOM_CONFORMANCE_HASH_MIN_PCT` (default `50`).
- **Regulatory field checks** (CycloneDX only, verdict-neutral): five per-component field coverage checks at or above `SBOM_CONFORMANCE_FIELD_MIN_PCT` (default `80`) — see [Regulatory field checks](#regulatory-field-checks-advisory) below. They never change `result`.
- A `fail` verdict does **not** abort the ingest — TRUSCA still matches CVEs and classifies licenses so you get the partial result alongside the concrete reasons. Use the verdict to decide whether to accept a supplier's SBOM or send it back.
- `purl_coverage_pct`, `license_coverage_pct`, and `hash_coverage_pct` are `null` for SPDX Tag-Value documents, which are scored on presence rather than per-package coverage.
- An SBOM with **zero package components** does not fail the coverage checks: with nothing to measure, PURL coverage reports `no packages to measure` and passes instead of scoring 0%. In a CycloneDX document, dataset components (`"type": "data"` — a training dataset in an ML-BOM, say) are excluded from the package-natured checks (name+version, PURL, and four of the regulatory field checks) but still count toward license and checksum coverage, which they can carry. One guard rides along: a document whose components are **all** typed `"data"` is not a plausible ML-BOM, so instead of passing on the empty denominator, its name+version and PURL checks report `all components are typed "data"` and degrade the verdict to `warn`.

When the uploaded document contains a `machine-learning-model` component, `checks[]` additionally carries the 51 advisory G7 AI SBOM minimum-element entries (tagged with `cluster` and `source`) — see [AI SBOM conformance](../user-guide/ai-sbom-conformance.md).

A `404` here means the project is not accessible to you, or the scan has no verdict yet (it is not an ingested SBOM scan, or its ingest has not reached the conformance stage).

### Regulatory field checks (advisory)

On **CycloneDX** documents the verdict carries five additional per-component field checks, named by the field-level regulatory baselines — BSI TR-03183-2 (the German technical guideline for the EU Cyber Resilience Act) and the US NTIA minimum elements. SPDX documents keep the nine checks above.

All five are **advisory and verdict-neutral**: they are `required: false` and additionally excluded from the `n_warn` counter, so they never change the pass / warn / fail result. They describe how well the SBOM would answer a regulator, and feed the [regulatory crosswalk](#regulatory-crosswalk) below. The coverage bar for all five is `SBOM_CONFORMANCE_FIELD_MIN_PCT` (default `80`).

| Check id | What it measures | Measured over |
|---|---|---|
| `hash-algorithm` | Components carrying a **SHA-512** checksum. | All components. |
| `component-creator` | Components naming their creator — `authors`, `publisher`, `supplier`, or `manufacturer`. | Package components. |
| `component-filename` | Components carrying a `bsi:component:filename` property. | Package components. |
| `artifact-uri` | Components carrying a `vcs` or `distribution` external reference (source or distribution URI). | Package components. |
| `file-properties` | Components carrying all three `bsi:component:executable` / `bsi:component:archive` / `bsi:component:structured` properties. | Package components. |

"Package components" means every component except `"type": "data"` (see the zero-package bullet above). `file-properties` has one extra behavior: when **no** component in the document carries the property trio, no producer in the chain inspected the delivered files, so the check reports `requires inspecting the delivered files (no automated source in this scan)` with `source: "na"` — a human-review item, not a coverage failure.

### Regulatory crosswalk

The conformance response cross-references each check to the regulatory documentation requirements its subject touches, across four frameworks:

| Framework | Scope in the crosswalk |
|---|---|
| **BSI TR-03183-2** — SBOM data fields (EU Cyber Resilience Act) | Section-level references (5.1, 5.2.1, 5.2.2, 5.2.4), mapped from eight core checks and all five regulatory field checks. |
| **NTIA** — US SBOM minimum elements (Executive Order 14028) | The seven 2021 data fields, mapped from the timestamp, tool, name+version, PURL, and dependency checks plus `component-creator`. |
| **EU AI Act** — Annex IV technical documentation | Via the [G7 AI SBOM checks](../user-guide/ai-sbom-conformance.md) — ML-BOMs only. |
| **AI Framework Act (Korea)** | Via the G7 checks — ML-BOMs only. |

Two response fields carry the crosswalk:

- Each entry in `checks[]` gains a `regulations` array — `{framework, ref, basis, short, short_ko}` — where `basis` quotes the interpretive ground for the link. A check with no defensible mapping gets an empty array.
- The response gains a top-level `regulatory_crosswalk` block: the disclaimer (`disclaimer` / `disclaimer_ko`) plus one rollup row per framework that has at least one mapped check — `total`, `present` (mapped checks that pass), `gap` (warn with an automated source), `review` (answerable only by a human, `source: "na"`), and the mapped `elements[]`. The scan detail page's conformance panel shows the same per-framework rollup. The block is `null` when nothing maps (an unrecognized-format document).

An excerpt for the example scan above (elements truncated to two):

```json
"regulatory_crosswalk": {
  "disclaimer": "…",
  "disclaimer_ko": "…",
  "frameworks": [
    {
      "id": "bsi-tr-03183-2",
      "title": "BSI TR-03183-2 — SBOM data fields (EU CRA)",
      "short": "BSI TR-03183-2",
      "source": "Regulation (EU) 2024/2847 Annex I Part II(1); BSI TR-03183-2 v2.1.0 (2025-08-20)",
      "total": 13,
      "present": 10,
      "gap": 2,
      "review": 1,
      "elements": [
        { "id": "hash", "label": "Hash coverage (>= 50%, recommended)", "status": "warn", "source": null, "detail": "0% (0/42)", "refs": ["Section 5.2.2"] },
        { "id": "file-properties", "label": "Delivered-file properties (executable/archive/structured)", "status": "warn", "source": "na", "detail": "requires inspecting the delivered files (no automated source in this scan)", "refs": ["Section 5.2.2"] }
      ]
    }
  ]
}
```

The join happens at read time against a vendored catalogue, so verdicts stored by earlier scans pick up mapping updates without a re-scan. A failed mandatory check counts toward a framework's `total` only — a mandatory failure already fails the whole submission, and the crosswalk is not a second verdict.

:::note Not a compliance determination
The crosswalk is a **documentation-preparation aid**. TRUSCA does not certify or determine compliance with the EU Cyber Resilience Act, the EU AI Act, the Korean AI Framework Act, or any other regulation. It covers only the documentation elements an SBOM can carry; obligations an SBOM cannot express — bias and fairness assessment, risk management, human oversight — are out of its scope and must be met through separate documents. The payload carries this disclaimer verbatim, and interpreting the rollup against a specific product's legal obligations is a person's job.
:::

## Verify it worked

After the scan reaches `succeeded`:

- The project's **Components** tab lists the packages from the SBOM, and the component count is greater than zero.
- The **Vulnerabilities** tab shows CVE (Common Vulnerabilities and Exposures) findings that Trivy matched against the components.
- The **Licenses** tab shows the declared licenses carried in the SBOM.
- The **Overview** tab shows the dependency graph and the project risk score.

If the project has a build gate policy, the gate runs on the uploaded SBOM exactly as it does for a source scan.

## What an uploaded SBOM fills in

An uploaded SBOM carries only what the producing tool wrote into it, so TRUSCA can enrich some surfaces and not others.

**Filled in:**

- Component list — every component in the SBOM.
- Vulnerabilities — CVE findings, matched by Trivy against the components by PURL.
- Declared licenses — the license each component declares in the SBOM.
- Dependency graph — built from the SBOM's `dependencies`.
- Build gate — Critical CVEs and forbidden-classification licenses trip the gate, so a CI step that calls this endpoint and then checks the gate can block a build the same way a source scan does.

**Not filled in (these come only from a source or repository scan):**

- Detected licenses — the license texts a source scan finds in the files themselves (scancode). An uploaded SBOM is never cloned or scanned, so there is nothing to detect.
- Registry-concluded licenses — the reconciled license a source scan derives from registry metadata.
- SBOM signature and attestation — an uploaded SBOM is not signed (cosign), so the signature, certificate, and attestation download endpoints have nothing to serve for it.
- Source preservation — no source is fetched or kept.

If you need detected licenses, signing, or source preservation, run a source scan against the repository instead — see [Scans](../user-guide/scans.md).

## Limits

| Limit | Default | Environment variable | Exceeded |
|---|---|---|---|
| Upload size | 32 MiB | `SBOM_INGEST_MAX_BYTES` | `413` |
| Component count | 50,000 | `SBOM_INGEST_MAX_COMPONENTS` | `422` |

An operator can raise or lower either limit per deployment; see [Environment variables](../reference/env-variables.md).

## Errors

All errors are RFC 7807 (Problem Details for HTTP APIs) responses with the `application/problem+json` content type.

| Status | When |
|---|---|
| `403` | The caller is not a member of the project's owning team, or a project-scoped API key targets a different project. |
| `404` | The project does not exist, or it is hidden from the caller (existence-hide). |
| `409` | A scan is already queued or running for this project, or the project is archived. |
| `413` | The upload exceeds the size cap (`SBOM_INGEST_MAX_BYTES`). |
| `415` | The upload's media type and filename are both wrong. Use `application/json` / `application/vnd.cyclonedx+json` / `application/spdx+json` / `text/spdx`, or a `.json` / `.cdx.json` / `.spdx` / `.tag` filename. |
| `422` | The upload is not a valid CycloneDX-JSON or SPDX (JSON/Tag-Value) document — wrong `bomFormat`, an unsupported CycloneDX `specVersion`, malformed `components`/`packages`, more than `SBOM_INGEST_MAX_COMPONENTS`, or too deeply nested. |
| `429` | Rate limited, or the team's concurrent-scan cap is reached. The response carries a `Retry-After` header. |

## Troubleshooting

### `401 Unauthorized`

The bearer token is missing, malformed, or expired. Confirm the header is `Authorization: Bearer <API_KEY>` — TRUSCA does not read an `X-Api-Key` header. Re-paste the key from the API key modal; it is exactly `tos_` + 8 characters + `_` + 32 characters.

### `403 Forbidden`

The API key's scope does not cover the project. Re-issue the key with scope `project` bound to that project, or scope `team` for a project the team owns. See [API keys](../admin-guide/api-keys.md).

### `409 Conflict`

A scan is already queued or running for this project — TRUSCA allows one in-flight scan per project. Wait for it to finish (poll `GET /v1/scans/{scan_id}`), then retry. A `409` also fires when the project is archived; restore it first.

### `415 Unsupported Media Type`

TRUSCA accepts CycloneDX-JSON and SPDX (JSON or Tag-Value). Confirm the upload sets an accepted media type (`application/json`, `application/vnd.cyclonedx+json`, `application/spdx+json`, `text/spdx`) or a recognised filename (`.json`, `.cdx.json`, `.spdx`, `.tag`). SPDX RDF/XML and CycloneDX XML are not accepted here.

### `422 Unprocessable Entity`

The upload is not an ingestible CycloneDX or SPDX SBOM. For CycloneDX, check that `bomFormat` is `CycloneDX` and `specVersion` is between `1.2` and `1.7`; for SPDX, that the document carries `spdxVersion` (JSON) or a `SPDXVersion:` line (Tag-Value). The component/package count must be within `SBOM_INGEST_MAX_COMPONENTS`, and the document must not be pathologically nested. The `detail` field names the specific reason.

### `429 Too Many Requests`

You hit the per-user scan-creation rate limit, or the team reached its concurrent-scan cap. Honor the `Retry-After` header and retry after the stated delay.

## See also

- [GitHub Actions](./github-actions.md) — trigger a source scan and gate the build from a workflow
- [API keys](../admin-guide/api-keys.md) — the `tos_` key format and scope model
- [Scans](../user-guide/scans.md) — source and container scans, and what each one fills in
- [Scan retention](../admin-guide/scan-retention.md) — how `ref` and `release` group and keep scans
- [Environment variables](../reference/env-variables.md) — the ingest size and component limits
