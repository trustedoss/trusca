---
id: components-and-licenses
title: Components & licenses
description: Browse the components a scan discovered, inspect declared and concluded licenses, and act on the allowed / conditional / forbidden classification.
sidebar_label: Components & licenses
sidebar_position: 3
---

# Components & licenses

After a scan completes, the project's **Components** tab lists every package the pipeline discovered, along with the licenses ORT attached to each one. This page covers reading the table, the license-classification model, and the obligations the portal tracks.

:::note Audience
Engineers triaging dependency hygiene; legal / compliance reviewers reading licenses. Read access requires team membership; mutating actions (suppression, manual concluded license) require `developer` or higher.
:::

## The components table

![Project detail — Components tab with virtualized rows, severity filter, and license category badges](/img/screenshots/user-components-list.png)

Columns:

- **Component** — package name (e.g. `lodash`, `org.springframework:spring-web`).
- **Version** — pinned version found in the manifest or lockfile.
- **License** — the concluded license ORT chose after reconciling declared and detected sources. This is the value used by the build gate.
- **Severity** — the highest severity across this component's open CVEs (carries the license-classification color via the legend).
- **CVEs** — count of open vulnerabilities for this component (clickable; jumps to the Vulnerabilities tab pre-filtered).

The table is virtualized — projects with thousands of components scroll smoothly.

### Filters

The inline filter bar at the top supports:

- **Search** — substring match against `name@version`.
- **Severity** — multi-select badges (Critical / High / Medium / Low / Info).
- **License category** — multi-select (`Allowed` / `Conditional` / `Forbidden` / `Unknown`).
- **Sort** + **order** — column-driven sort with ascending / descending toggle.

Filters compose. The URL updates so you can share a filtered view.

## The drawer — component detail

Click any row to open a right-side drawer with:

- **Identity** — `purl` (Package URL), upstream homepage, repo URL.
- **All license findings** — declared, detected, and concluded, with the source files ORT attributed each one to.
- **Obligations** — list of obligations triggered by the concluded license (see [Obligations](#obligations)).
- **CVEs** — open and resolved findings, deep-linked to the vulnerability detail.

Closing the drawer keeps you in place on the table — no full-page navigation.

For the approval state of a conditional-license component, switch to the project-level [Approvals](./approvals.md) page (the drawer does not surface approval state at v2.0.0). Manual override of the concluded license is also deferred — see [Roadmap](#roadmap-v2x).

## License classification

The **Licenses** tab on a project breaks down the same data by SPDX identifier and tier — a horizontal bar chart on top of the same table the Components tab uses, scoped to license rows:

![Project detail — Licenses tab with a tier horizontal bar chart and a per-license breakdown](/img/screenshots/user-licenses-donut.png)

ORT classifies every license into three tiers:

| Tier | Severity | Examples | Effect |
|---|---|---|---|
| **Allowed** | — | MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, ISC, CC0-1.0, Unlicense | No build-gate effect. |
| **Conditional** | WARNING | LGPL-2.x, LGPL-3.x, MPL-2.0, EPL-1.x, EPL-2.0, CDDL-1.0 | Triggers the [approval workflow](./approvals.md). Build proceeds — including after a **Rejected** verdict; see the [approvals caveat](./approvals.md#rejected-verdict-at-v200). |
| **Forbidden** | ERROR | AGPL-3.0, GPL-2.0, GPL-3.0, SSPL-1.0, BUSL-1.1 | Build gate exits 1 in CI. |

`Unknown` (license could not be parsed, or its SPDX ID was not matched by the classifier — see [below](#why-so-many-unknown)) renders as a fourth tier with a yellow badge — these always need human review.

:::warning Classification source at v2.0.0
The legal-tier classification (`forbidden` / `conditional` / `permissive` / `unknown`) is currently driven by a hard-coded SPDX → tier dictionary in `apps/backend/tasks/scan_source.py` (`_LICENSE_CATEGORY_DEFAULTS`). The `ort/rules.kts` file in the repo is a placeholder — editing it does **not** change classification at v2.0.0. ORT-driven, per-organization rule customization is on the v2.2 roadmap. For one-off overrides today, super-admins can patch the dictionary and restart the worker (an Operator-only path).
:::

### Why so many `unknown`? {#why-so-many-unknown}

:::info
Classification uses exact-match SPDX IDs. Suffix-less variants (`LGPL-3.0` instead of `LGPL-3.0-or-later`) fall through to `unknown`. ORT typically detects with the `-or-later` / `-only` suffix; if a component shows `unknown` despite a well-known SPDX ID, the detector likely emitted a deprecated alias. Fuzzy SPDX normalization is on the v2.1 roadmap.
:::

## Declared vs. detected vs. concluded

ORT distinguishes three levels of confidence:

- **Declared** — license stated in the package's own metadata (e.g. `package.json`, `pom.xml`, `setup.py`).
- **Detected** — license discovered by scanning the package source files.
- **Concluded** — the license ORT settles on after reconciling the two. Conflicts (e.g. declared `MIT`, detected `GPL-3.0`) are flagged and require human review before the conclusion is final.

The concluded license is the one the build gate evaluates. The drawer shows all three so you can audit the trail.

## Obligations

Each license carries **obligations** — duties you must honor when redistributing the component. The portal tracks seven kinds (see [glossary](https://github.com/trustedoss/trustedoss-portal/blob/main/docs/glossary.md)):

- **Attribution** — preserve the upstream copyright notice.
- **NOTICE preservation** — carry the upstream `NOTICE` file (Apache-2.0 §4(d)).
- **Source disclosure** — make the corresponding source available on demand.
- **Copyleft** — release derivative works under the same license terms.
- **Modifications** — state changes prominently in modified files.
- **Dynamic linking** — LGPL-style: end-users must be able to relink against a modified library.
- **No endorsement** — do not use the project name to endorse derivatives without permission.

The **Obligations** tab on the project page consolidates obligations across components. Click **Download NOTICE** to download a `NOTICE.txt` summarizing every attribution and license — see [SBOM](./sbom.md#notice-file).

![Project detail — Obligations tab with the per-component obligations distribution](/img/screenshots/user-obligations-distribution.png)

:::note Obligation kinds at v2.0.0
The obligations catalog covers the seven kinds listed above. Some
AGPL / SSPL / BUSL-specific obligations are **not** modeled as discrete
kinds yet:

- **Network-use disclosure** (AGPL §13, SSPL §13) — required when
  end-users interact with modified software over a network.
- **Patent grant termination** (Apache-2.0 §3, MPL-2.0 §5.2).
- **Trademark restrictions** (Apache-2.0 §6, BSD-4-clause).
- **Field-of-use restrictions** (BUSL-1.1).

For these, see the underlying license text via the component drawer; a
richer obligation taxonomy is on the v2.2 roadmap.
:::

## SPDX expressions

Licenses are identified by [SPDX identifiers](https://spdx.org/licenses/). Compound licenses use the SPDX expression syntax:

- `(MIT OR Apache-2.0)` — dual-licensed; either is acceptable.
- `(GPL-2.0+ WITH Classpath-exception-2.0)` — GPL with an exception.
- `LicenseRef-proprietary` — non-SPDX license, parsed but not classified.

Hovering an expression in the UI shows the SPDX URL for each component license.

## Verify it worked

After a successful scan:

1. Component count matches your expectation (close to the count of pinned dependencies in your lockfile).
2. The classification distribution horizontal bar chart on the Overview tab adds up to 100%.
3. Forbidden-license components, if any, are highlighted in red and have a CTA to the [approvals queue](./approvals.md).

## Troubleshooting

### Many components show `Unknown` license

ORT could not parse the metadata, or the SPDX ID was not in the classifier's exact-match dictionary (see [Why so many `unknown`?](#why-so-many-unknown)). Common causes:

- The package has no `LICENSE` file and no metadata declaration (rare in well-maintained ecosystems).
- A custom license string ORT does not recognize. The component drawer surfaces the raw string for legal review.
- The detector emitted a deprecated SPDX alias (e.g. `LGPL-3.0` instead of `LGPL-3.0-or-later`); the exact-match dictionary does not yet normalize these.
- Source fetch failed for that ecosystem. Check `docker-compose logs worker` for ORT's per-ecosystem warnings.

### Classification looks wrong

The classification at v2.0.0 is driven by the hard-coded `_LICENSE_CATEGORY_DEFAULTS` dictionary in `apps/backend/tasks/scan_source.py` (see [Classification source](#license-classification) above). The `ort/rules.kts` placeholder in the repo has no effect. For a one-off override today, a super-admin can patch the dictionary and restart the worker; the ORT-driven, per-organization customization path is on the v2.2 roadmap. If the dictionary entry is correct but the concluded license is wrong, override the conclusion in the component drawer.

### Lockfile not detected

`cdxgen` supports 30+ ecosystems but new ones land regularly. Confirm the project's lockfile is at the repo root or one level below; `cdxgen` does not recurse arbitrarily deep. If the ecosystem is unsupported, file an issue with the pipeline output.

## Roadmap (v2.x)

Items the manual previously promised that are not in v2.0.0; tracked for later releases.

- Standalone **Type** (ecosystem) and **Classification** columns on the components table — for v2.0.0 the type is encoded inside the `purl` shown in the drawer's Identity row, and the classification surfaces via the **Severity** color legend.
- Exact-SPDX **License** filter and **Has open CVE** toggle — planned for v2.1; the current **License category** multi-select and the search box cover most workflows.
- **Approval status** row inside the component drawer — planned for v2.1; the project-level [Approvals](./approvals.md) page is the source of truth today.
- Manual **Override concluded license** action in the drawer (`team_admin`) — planned for v2.2.
- Fuzzy SPDX normalization for suffix-less variants (`LGPL-3.0` → `LGPL-3.0-or-later`) — planned for v2.1.
- ORT-driven, per-organization rule customization via `ort/rules.kts` — planned for v2.2; today classification is driven by the hard-coded `_LICENSE_CATEGORY_DEFAULTS` dictionary in `apps/backend/tasks/scan_source.py`.

## See also

- [Vulnerabilities](./vulnerabilities.md)
- [Approvals](./approvals.md)
- [SBOM](./sbom.md) — including the [Compliance evidence trail](./sbom.md#compliance-evidence-trail-at-v200)
