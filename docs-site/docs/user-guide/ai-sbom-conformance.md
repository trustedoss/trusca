---
id: ai-sbom-conformance
title: AI SBOM conformance (G7 minimum elements)
description: Upload a CycloneDX 1.7 ML-BOM and TRUSCA checks it against the 51 G7 AI SBOM minimum elements — an advisory, cluster-by-cluster checklist.
sidebar_label: AI SBOM conformance
sidebar_position: 6
---

# AI SBOM conformance (G7 minimum elements)

When an uploaded SBOM (software bill of materials) describes an AI system — it contains at least one CycloneDX `machine-learning-model` component — TRUSCA extends the [conformance verdict](./scans.md#conformance-verdict) with the **G7 AI SBOM minimum elements** checklist: 51 elements in seven clusters, each reported as present, missing, or needing human review. The checklist is advisory throughout; it never changes the overall pass / warn / fail verdict.

:::note Audience
Engineers and compliance leads who receive or produce AI SBOMs (ML-BOMs) and need to judge how complete they are. Assumes you can already [upload an SBOM](../ci-integration/sbom-upload.md) to a TRUSCA project.
:::

## What the G7 minimum elements are

*G7 Software Bill of Materials for AI — Minimum Elements* (May 2026) is a joint baseline led by Germany's Federal Office for Information Security (BSI) and Italy's National Cybersecurity Agency (ACN). It names the information an SBOM for an AI system should carry — not only the software dependencies a classic SBOM lists, but the models, the datasets they were trained on, the infrastructure they run on, and the security and performance facts a consumer needs to assess the system.

The timing is regulatory: the European Union's Artificial Intelligence Act (AI Act) applies its main obligations from August 2, 2026, including technical-documentation duties for high-risk AI systems. The G7 minimum elements are not an AI Act compliance checklist, but they enumerate the inventory facts — models, datasets, licenses, provenance — that such documentation draws on, so scoring an ML-BOM against them is a practical readiness signal.

## Upload an ML-BOM

There is no separate upload path or setting. Send the document through the regular received-SBOM ingest — see [Upload an SBOM](../ci-integration/sbom-upload.md) — and TRUSCA detects the AI content automatically:

- **CycloneDX `specVersion` 1.7 is accepted.** 1.7 is the version that carries the ML-BOM fields (`modelCard`, model parameters, dataset governance). Earlier versions (`1.2`–`1.6`) remain accepted as before.
- **Detection is automatic.** If the document contains at least one component of type `machine-learning-model`, the 51 G7 checks are appended to the scan's conformance verdict. Documents without one get the usual nine core checks only.
- **Any generator works.** Tools such as BomLens or the OWASP AIBOM Generator emit CycloneDX 1.7 ML-BOMs that TRUSCA evaluates out of the box; a hand-assembled document is fine too, as long as it is valid CycloneDX JSON.

## Read the checklist

The checklist appears on the **scan detail page** as its own **G7 AI SBOM minimum elements** section below the nine core checks. A tally headline summarizes coverage — elements present out of the 38 machine-checkable ones, an advisory count, and a human-review count — followed by one card per cluster in the canonical order. Each row pairs the status badge with a **source** badge; where the element is known, the row also offers a correct CycloneDX fragment and a **Learn more** link to the authoritative specification (for example the [CycloneDX ML-BOM capability page](https://cyclonedx.org/capabilities/mlbom/)).

The API returns the same data: the G7 entries in the `checks[]` array of `GET /v1/projects/{project_id}/scans/{scan_id}/conformance` carry extra `cluster`, `source`, `role`, and (when values were extracted) `evidence` fields; the nine core checks omit them.

### The seven clusters

| Cluster | Elements | What it covers |
|---|---|---|
| Metadata | 10 | The SBOM document itself — author, format name and version, generating tool, timestamp, signature, dependency relationships. |
| System level properties | 9 | The AI system as a whole — name, version, producer, component inventory, data flow, intended application area. |
| Models | 14 | Each `machine-learning-model` component — identifier, version, hash, model card, inputs and outputs, training properties, license and its openness facets. |
| Datasets properties | 10 | `data` components — name, identifier, provenance, sensitivity (PII / copyright), license. |
| Infrastructure | 2 | The software dependencies and (via an HBOM link) the hardware the system runs on. |
| Security properties | 4 | Security controls, compliance, cybersecurity policy information, vulnerability referencing. |
| Key performance indicators | 2 | Security metrics and operational performance figures. |

### Element statuses

Each element resolves to one of three outcomes:

- **Pass** (`present`) — the element was found in the document.
- **Advisory warn** (`not present in the SBOM`) — the element is machine-checkable but missing. Unlike a core-check warn, it does not count toward the overall verdict.
- **Human review** (`requires human review (no automated source)`) — the G7 text asks for this element, but no CycloneDX field can prove it. 13 of the 51 elements are in this group (for example *dataset sensitivity* and *security controls*); they always render this way regardless of document quality, and the tally counts them separately as "need human review".

### Source tags

Every element carries a `source` tag recording where a satisfied value comes from:

| Source | UI badge | Meaning |
|---|---|---|
| `auto` | Auto | Read directly from a standard CycloneDX field (for example the model's `purl`). |
| `inferred` | Inferred | Derived from signals rather than a dedicated field (for example a property whose name matches `timestamp`). |
| `declared` | Declared | Present only if a human or a manifest supplied it (for example a document signature or a publisher). |
| `na` | No automated source | No machine-checkable field exists — always reported as "requires human review". |

The `role` field is informational: it names the party the G7 text expects to provide the element (SBOM author, system producer, model producer, dataset creator). It is not a required / optional gate — the G7 document defines no per-role required matrix.

For a few satisfied elements — model identifier, hash algorithm, model license, and openness properties — the verdict also shows the extracted **evidence** values (at most 8 items, each truncated at 200 characters), so you can confirm *which* PURL or license satisfied the check without reopening the document.

### Advisory only

All 51 G7 checks are recommended (`required: false`) and excluded from the verdict's `n_warn` counter. An ML-BOM missing half its G7 elements still gets an overall `pass` if the nine core checks pass. Treat the checklist as a completeness conversation with the SBOM's producer, not as a gate.

## What the tool does not check

The working principle — shared with the OpenChain AI SBOM guidance — is **generate with tools, interpret with humans**. TRUSCA verifies that a field is present and extracts its value; it does not vouch for what the value means:

- **Non-standard license interpretation.** Many model licenses (OpenRAIL variants, bespoke research licenses) have no SPDX identifier and terms that require legal reading. The *Model license* check confirms a license entry exists — whether its terms permit your use is a policy and legal call.
- **Dataset provenance truth.** The *Dataset provenance* check confirms a provenance or governance field is filled in. Whether the stated origin is accurate — and whether the data was lawfully collected — is not machine-verifiable from the SBOM.
- **Human-review elements.** The 13 `na` elements (data flow, dataset sensitivity, security controls, and others) need a person to read the system's actual documentation.

Two operational boundaries also apply:

- **ML components are not CVE-matched.** Trivy skips `machine-learning-model` as an unsupported component type, so vulnerability results for an ingested ML-BOM cover its software dependencies only. A CVE (Common Vulnerabilities and Exposures identifier) will never be attributed to the model itself.
- **A model without a PURL does not appear in the Components tab.** Component persistence requires a package URL. The G7 checks are unaffected — they evaluate the original uploaded document, not the persisted component list.

## Verify it worked

1. Upload a CycloneDX 1.7 document that contains at least one `machine-learning-model` component ([Upload an SBOM](../ci-integration/sbom-upload.md)).
2. When the scan reaches `succeeded`, open its scan detail page. Below the nine core checks, a **G7 AI SBOM minimum elements** section shows the tally headline and seven cluster cards in order (Metadata first, Key performance indicators last).
3. The overall conformance badge is the same as it would be without the ML content — G7 misses alone never turn a `pass` into a `warn`.

## Troubleshooting

### The checklist does not appear

The document has no component with `"type": "machine-learning-model"` — many AI-adjacent SBOMs list only libraries. Check `components[].type` in the uploaded file. Also confirm the scan is an `sbom`-kind scan: source and container scans never get a conformance verdict.

### The upload is rejected with `422`

TRUSCA releases earlier than this feature reject `specVersion: 1.7` at ingest. Upgrade the portal, then re-upload. The `detail` field of the error names the exact reason — see [Upload an SBOM → Troubleshooting](../ci-integration/sbom-upload.md#troubleshooting).

### Many rows say "requires human review"

Expected. 13 of the 51 elements have no automated source (`source: na`) and always ask for human review — this reflects the G7 text, not a defect in your SBOM.

### The model is missing from the Components tab

The model component has no `purl`, so it was not persisted to the component list. Add a package URL (for example `pkg:huggingface/...`) if you want the model inventoried alongside the software components; the G7 checklist works either way.

## See also

- [Upload an SBOM](../ci-integration/sbom-upload.md) — the ingest endpoint, fields, and errors
- [Scans → SBOM upload](./scans.md#received-sboms-uploaded) — how `sbom`-kind scans behave
- [Scans → Conformance verdict](./scans.md#conformance-verdict) — the nine core checks the G7 rows extend
- [SBOM](./sbom.md) — exporting SBOMs that TRUSCA generates
