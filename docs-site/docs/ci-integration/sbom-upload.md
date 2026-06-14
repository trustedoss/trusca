---
id: sbom-upload
title: Upload an SBOM
description: Upload a CycloneDX SBOM that an external tool already produced — TRUSCA queues a scan that matches CVEs, classifies declared licenses, and runs the build gate.
sidebar_label: Upload an SBOM
sidebar_position: 5
---

# Upload an SBOM

Already have a CycloneDX SBOM (software bill of materials) from another tool? Upload it to an existing TRUSCA project and TRUSCA matches its components against vulnerability data, classifies declared licenses, builds the dependency graph, and runs the build gate — without cloning or scanning your source.

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
- A CycloneDX JSON document. Supported `specVersion` values are `1.2` through `1.6`. SPDX is not accepted on this endpoint.
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
- A `fail` verdict does **not** abort the ingest — TRUSCA still matches CVEs and classifies licenses so you get the partial result alongside the concrete reasons. Use the verdict to decide whether to accept a supplier's SBOM or send it back.
- `purl_coverage_pct`, `license_coverage_pct`, and `hash_coverage_pct` are `null` for SPDX Tag-Value documents, which are scored on presence rather than per-package coverage.

A `404` here means the project is not accessible to you, or the scan has no verdict yet (it is not an ingested SBOM scan, or its ingest has not reached the conformance stage).

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
| `415` | The upload is not a CycloneDX JSON media type — the content type and filename are both wrong. Use `application/json` or `application/vnd.cyclonedx+json`, with a `.json` or `.cdx.json` filename. |
| `422` | The upload is not a valid CycloneDX document — not JSON, `bomFormat` is not `CycloneDX`, an unsupported `specVersion`, malformed `components`, or more components than `SBOM_INGEST_MAX_COMPONENTS`. |
| `429` | Rate limited, or the team's concurrent-scan cap is reached. The response carries a `Retry-After` header. |

## Troubleshooting

### `401 Unauthorized`

The bearer token is missing, malformed, or expired. Confirm the header is `Authorization: Bearer <API_KEY>` — TRUSCA does not read an `X-Api-Key` header. Re-paste the key from the API key modal; it is exactly `tos_` + 8 characters + `_` + 32 characters.

### `403 Forbidden`

The API key's scope does not cover the project. Re-issue the key with scope `project` bound to that project, or scope `team` for a project the team owns. See [API keys](../admin-guide/api-keys.md).

### `409 Conflict`

A scan is already queued or running for this project — TRUSCA allows one in-flight scan per project. Wait for it to finish (poll `GET /v1/scans/{scan_id}`), then retry. A `409` also fires when the project is archived; restore it first.

### `415 Unsupported Media Type`

TRUSCA accepts only CycloneDX JSON. Confirm the file is JSON and the upload sets a JSON media type or a `.json` / `.cdx.json` filename. SPDX and CycloneDX XML are not accepted here.

### `422 Unprocessable Entity`

The document is JSON but not an ingestible CycloneDX SBOM. Check that `bomFormat` is `CycloneDX`, that `specVersion` is between `1.2` and `1.6`, and that the component count is within `SBOM_INGEST_MAX_COMPONENTS`. The `detail` field names the specific reason.

### `429 Too Many Requests`

You hit the per-user scan-creation rate limit, or the team reached its concurrent-scan cap. Honor the `Retry-After` header and retry after the stated delay.

## See also

- [GitHub Actions](./github-actions.md) — trigger a source scan and gate the build from a workflow
- [API keys](../admin-guide/api-keys.md) — the `tos_` key format and scope model
- [Scans](../user-guide/scans.md) — source and container scans, and what each one fills in
- [Scan retention](../admin-guide/scan-retention.md) — how `ref` and `release` group and keep scans
- [Environment variables](../reference/env-variables.md) — the ingest size and component limits
