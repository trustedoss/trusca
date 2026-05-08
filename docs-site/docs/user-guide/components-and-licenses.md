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

Columns:

- **Name** — package name (e.g. `lodash`, `org.springframework:spring-web`).
- **Version** — pinned version found in the manifest or lockfile.
- **Type** — ecosystem (`npm`, `maven`, `pypi`, `golang`, `cargo`, `nuget`, `gem`, …).
- **Concluded license** — the license ORT chose after reconciling declared and detected sources. This is the value used by the build gate.
- **Classification** — `Allowed` / `Conditional` / `Forbidden`.
- **Findings** — count of open vulnerabilities for this component (clickable; jumps to the Vulnerabilities tab pre-filtered).

The table is virtualized — projects with thousands of components scroll smoothly.

### Filters

The inline filter bar at the top supports:

- **Classification** (Allowed / Conditional / Forbidden / Unknown).
- **License** — exact license SPDX expression (e.g. `MIT`, `LGPL-2.1-only`).
- **Has open CVE** — toggle.
- **Search** — substring match against `name@version`.

Filters compose. The URL updates so you can share a filtered view.

## The drawer — component detail

Click any row to open a right-side drawer with:

- **Identity** — `purl` (Package URL), upstream homepage, repo URL.
- **All license findings** — declared, detected, and concluded, with the source files ORT attributed each one to.
- **Obligations** — list of obligations triggered by the concluded license (see [Obligations](#obligations)).
- **CVEs** — open and resolved findings, deep-linked to the vulnerability detail.
- **Approval status** — `Pending` / `Under Review` / `Approved` / `Rejected` (see [Approvals](./approvals.md)).
- **Override concluded license** — if the automatic conclusion is wrong, a `team_admin` can override and pin the license. The override carries a reason that lands in the audit log.

Closing the drawer keeps you in place on the table — no full-page navigation.

## License classification

ORT classifies every license into three tiers, defined in `ort/rules.kts`:

| Tier | Severity | Examples | Effect |
|---|---|---|---|
| **Allowed** | — | MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, ISC, CC0-1.0, Unlicense | No build-gate effect. |
| **Conditional** | WARNING | LGPL-2.x, LGPL-3.x, MPL-2.0, EPL-1.x, EPL-2.0, CDDL-1.0 | Triggers the [approval workflow](./approvals.md). Build proceeds. |
| **Forbidden** | ERROR | AGPL-3.0, GPL-2.0, GPL-3.0, SSPL-1.0, BUSL-1.1 | Build gate exits 1 in CI. |

`Unknown` (license could not be parsed) renders as a fourth tier with a yellow badge — these always need human review.

The classification can be tuned per organization by editing `ort/rules.kts` and re-running scans. See the [architecture reference](../reference/architecture.md#ort-rules) for the rule format.

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

The **Obligations** tab on the project page consolidates obligations across components. Click **Generate NOTICE** to download a `NOTICE.txt` summarizing every attribution and license — see [SBOM & reports](./sbom.md#notice-file).

## SPDX expressions

Licenses are identified by [SPDX identifiers](https://spdx.org/licenses/). Compound licenses use the SPDX expression syntax:

- `(MIT OR Apache-2.0)` — dual-licensed; either is acceptable.
- `(GPL-2.0+ WITH Classpath-exception-2.0)` — GPL with an exception.
- `LicenseRef-proprietary` — non-SPDX license, parsed but not classified.

Hovering an expression in the UI shows the SPDX URL for each component license.

## Verify it worked

After a successful scan:

1. Component count matches your expectation (close to the count of pinned dependencies in your lockfile).
2. The classification distribution donut on the Overview tab adds up to 100%.
3. Forbidden-license components, if any, are highlighted in red and have a CTA to the [approvals queue](./approvals.md).

## Troubleshooting

### Many components show `Unknown` license

ORT could not parse the metadata. Common causes:

- The package has no `LICENSE` file and no metadata declaration (rare in well-maintained ecosystems).
- A custom license string ORT does not recognize. Inspect `ort/rules.kts` and consider adding a mapping.
- Source fetch failed for that ecosystem. Check `docker-compose logs worker` for ORT's per-ecosystem warnings.

### Classification looks wrong

The classification is rule-driven. Edit `ort/rules.kts`, restart the worker, re-scan. If the rule itself is correct but the concluded license is wrong, override the conclusion in the component drawer.

### Lockfile not detected

`cdxgen` supports 30+ ecosystems but new ones land regularly. Confirm the project's lockfile is at the repo root or one level below; `cdxgen` does not recurse arbitrarily deep. If the ecosystem is unsupported, file an issue with the pipeline output.

## See also

- [Vulnerabilities](./vulnerabilities.md)
- [Approvals](./approvals.md)
- [SBOM & reports](./sbom.md)
- [Architecture — ORT rules](../reference/architecture.md#ort-rules)
