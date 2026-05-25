---
id: obligation-catalog
title: Obligation catalog
description: The structured per-license obligation catalog that powers the Obligations tab and the NOTICE file generator — what each license requires you to do.
sidebar_label: Obligation catalog
sidebar_position: 7
---

# Obligation catalog

A **license** tells you *whether* you may use a component. An **obligation**
tells you *what you must do* in return — reproduce a copyright notice, ship the
license text, disclose source, and so on. TrustedOSS ships a structured,
per-license **obligation catalog** so the **Obligations** tab and the generated
**NOTICE** file are populated from real scans, not just demo seed data.

The catalog covers the ~30 well-known SPDX licenses the portal classifies (the
same set used by the license categoriser). For each license it records the
concrete obligations a consumer must satisfy, derived from the license text.

:::note Not legal advice
The catalog summarises the obligations of common licenses to help you act on
scan results. It is a compliance aid, not legal advice — for the binding terms
always read the canonical license text (each obligation deep-links to it).
:::

## How it is populated

The catalog is **code-resident** (a single source of truth in the backend) and
is materialised into the `obligations` table **on demand** for the licenses a
project's latest scan actually observed:

- When you open the **Obligations** tab or download a **NOTICE**, the portal
  ensures every catalog license present in the scan has its obligation rows.
- Population is **idempotent and additive**: it only ever *adds* missing rows
  and **never overwrites** an obligation you (or a seed) authored by hand for
  the same `(license, kind)` pair.
- Only licenses that appear in the scan are enriched — the catalog is not bulk
  written to every project.

There is no schema change and no migration: obligations already had a table and
a read surface; this feature simply fills it for real scans.

## Structured obligation fields

Every catalog license carries these machine-readable facts:

| Field | Meaning |
| --- | --- |
| `attribution_required` | You must reproduce the author / copyright notices. |
| `license_text_inclusion_required` | You must include the full license text. |
| `copyright_notice_required` | You must preserve copyright notices specifically. |
| `state_changes_required` | You must flag / document the files you modified. |
| `source_disclosure` | Scope of any source-disclosure duty: `none`, `library`, or `network`. |
| `patent_grant` | The license carries an express patent grant. |
| `same_license_required` | A conveyed / derivative work must stay under the same license (copyleft). |
| `notice_file_required` | You must propagate a `NOTICE`/attribution file if one ships. |

### Source-disclosure scope

The `source_disclosure` field distinguishes the copyleft families that most
often trip up compliance:

| Scope | Meaning | Examples |
| --- | --- | --- |
| `none` | No obligation to disclose source. | MIT, BSD, ISC, Apache-2.0 |
| `library` | Source must be available for the **licensed component / library** (and your changes to it), not the whole application. | LGPL, MPL-2.0, EPL, CDDL; also GPL's conveying trigger |
| `network` | Source must be offered to users who interact with the software **over a network**, not only to those who receive a binary. | AGPL-3.0, SSPL-1.0 |

GPL is modelled with `source_disclosure = library` **and**
`same_license_required = true`: its source duty is triggered by *conveying* a
binary (like the weak-copyleft licenses), while its whole-program reach is
carried by `same_license_required`. AGPL extends that trigger to network use,
which is what `network` captures.

## Obligation kinds rendered

The catalog emits obligation rows under these `kind` values (shown in the
Obligations tab distribution and the NOTICE):

- `attribution` — reproduce copyright / author notices.
- `notice` — include the license text / NOTICE file with redistributions.
- `source-disclosure` — make source available (scope per the table above).
- `copyleft` — keep the derivative / conveyed work under the same license.
- `modifications` — mark and document changed files.
- `patent` — the license's express patent grant and its termination terms.

## By license category

The obligation set tracks the `allowed | conditional | forbidden` categories
(the same vocabulary as [license policies](./license-policies.md)):

| Category | Typical obligations |
| --- | --- |
| **Allowed** (permissive) | Attribution + license-text inclusion. Apache-2.0 adds NOTICE-file propagation, a modification notice, and a patent grant. Public-domain dedications (0BSD, CC0-1.0, Unlicense, WTFPL) carry no obligations. |
| **Conditional** (weak copyleft) | Attribution + library-scoped source disclosure + modification notices; LGPL adds a relink/replace right for the library; MPL/EPL/CDDL add an express patent grant. |
| **Forbidden** (strong copyleft / source-available) | Whole-program source disclosure + same-license copyleft + modification notices; AGPL/SSPL extend the duty to network/service use; BUSL is source-available with a use restriction until its Change Date. |

## Examples

| License | Attribution | License text | Patent grant | Source disclosure | Same license |
| --- | --- | --- | --- | --- | --- |
| MIT | yes | yes | no | none | no |
| Apache-2.0 | yes | yes | yes | none | no |
| BSD-3-Clause | yes | yes | no | none | no |
| LGPL-2.1 | yes | yes | no | library | no |
| GPL-3.0 | yes | yes | yes | library | yes |
| AGPL-3.0 | yes | yes | yes | network | yes |
| SSPL-1.0 | yes | yes | no | network | yes |

## Unknown and compound licenses

- An **unknown / custom** SPDX id (for example an ORT `LicenseRef-*`) produces
  **no obligations** — it is skipped rather than guessed.
- A **compound expression** (`MIT OR GPL-3.0-only`,
  `Apache-2.0 WITH LLVM-exception`) resolves to the **union** of the obligations
  of every recognised operand. This is the safe compliance default: you must
  satisfy whatever any constituent license demands. Unrecognised operands are
  ignored.

## Where it surfaces

- **Obligations tab** — one row per `(license, obligation kind)` observed in the
  project's latest scan, with a per-kind distribution chart.
- **NOTICE file** — each credited license renders its obligations (attribution,
  source disclosure, patent, …) instead of an empty placeholder, in text,
  Markdown, or HTML.
