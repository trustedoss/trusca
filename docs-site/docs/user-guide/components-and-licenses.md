---
id: components-and-licenses
title: Components & licenses
description: Browse the components a scan discovered, inspect declared and concluded licenses, and act on the allowed / conditional / forbidden classification.
sidebar_label: Components & licenses
sidebar_position: 3
---

# Components & licenses

After a scan completes, the project's **Components** tab lists every package the pipeline discovered, along with the licenses the scan attached to each one. This page covers reading the table, the license-classification model, the difference between **declared** and **detected** licenses, and the obligations the portal tracks.

:::note Audience
Engineers triaging dependency hygiene; legal / compliance reviewers reading licenses. Read access requires team membership; mutating actions (suppression, manual concluded license) require `developer` or higher.
:::

## The components table

![Project detail — Components tab with virtualized rows, severity filter, and license category badges](/img/screenshots/user-components-list.png)

Columns:

- **Component** — package name (e.g. `lodash`, `org.springframework:spring-web`).
- **Type** — **Direct** / **Transitive** / `—`. A coloured badge that summarises the dependency graph depth: `Direct` (depth 1, you declared it) vs. `Transitive` (depth 2+, pulled in by another package); `—` for older scans that pre-date this collection or ecosystems whose graph the scanner could not record. See [Direct vs. transitive](#dependency-depth).
- **Version** — pinned version found in the manifest or lockfile.
- **License** — the license attached to the component. For a dependency this is the **declared** license `cdxgen` read from package metadata; see [Declared vs. detected](#declared-vs-detected) for how detected and concluded licenses relate. This is the value used by the build gate.
- **Usage** — **Required** / **Optional** / `—`. The dependency scope `cdxgen` recorded along the *shortest* path to this component (when the same component is reachable via several paths the highest-scope path wins, i.e. `Required` > `Optional`). `—` means the scanner did not emit a scope for this component. Optional dependencies often carry the same legal obligations as required ones, but the **Required** / **Optional** distinction maps to license-compliance burden — an unused `Optional` extra is cheaper to remove than a deeply-required transitive dependency.
- **Severity** — the highest severity across this component's open CVEs (carries the license-classification color via the legend).
- **CVEs** — count of open vulnerabilities for this component (clickable; jumps to the Vulnerabilities tab pre-filtered).

The table is virtualized — projects with thousands of components scroll smoothly.

### Filters

The inline filter bar at the top supports:

- **Search** — substring match against `name@version`.
- **Dependency type** — a three-state segmented control (`Any` / `Direct only` / `Transitive only`). The Direct-only segment maps to `?direct=true` on the API; Transitive-only maps to `?direct=false`.
- **Usage** — multi-select (`Required` / `Optional`). Selecting both is equivalent to no filter; selecting only an unknown value drops to an empty page rather than 422-rejecting (consistent with severity / license-category filter semantics).
- **Severity** — multi-select badges (Critical / High / Medium / Low / Info).
- **License category** — multi-select (`Allowed` / `Conditional` / `Forbidden` / `Unknown`).
- **Sort** + **order** — column-driven sort with ascending / descending toggle.

Filters compose. The URL updates (`?direct=…`, `?dependency_scope=…`, …) so you can share a filtered view.

### Runtime-scope filtering {#runtime-scope-filtering}

The table shows the **deployable runtime set**, not everything the build
resolved. cdxgen records every resolved node — including the test/dev
toolchain (`junit`, `lombok`, `jest`, `eslint`, …) that never ships with the
artifact — so by default the scanner drops those before the SBOM is persisted,
signed and matched against the vulnerability DB:

- **Maven** — components cdxgen tagged `optional` (Maven `test` scope) or
  `excluded` (`provided`/`system` scope) are removed. The filter only runs
  when the SBOM actually carries scope tags; an SBOM without them is kept
  whole, so recall never regresses.
- **npm** — packages the project's `package-lock.json` classifies as
  `dev` are removed. A package the lockfile does not cover is always kept
  (nested manifests in a monorepo are not covered by the root lockfile), so
  the filter only removes components with positive dev-dependency evidence.

The number of excluded components is recorded on the scan, and the SBOM's
`metadata.properties` carries a `trusca:scope_filter` entry with per-ecosystem
counts, so a filtered document self-documents what was removed. SBOMs uploaded
through the ingest API are **never** filtered — an uploaded SBOM is the
supplier's declared truth.

Two caveats worth knowing:

- cdxgen tags Maven `<optional>true</optional>` dependencies the same way as
  `test` scope, so a rare *runtime* optional dependency is dropped with them.
  Set `SCAN_SCOPE_FILTER_MAVEN_ENABLED=false` for such projects.
- Turning the filter off (`SCAN_SCOPE_FILTER_ENABLED=false`) restores the full
  resolved graph on the next scan. See
  [Environment variables](../reference/env-variables.md) for all three toggles.

## Table view vs. graph view

The Components tab has a **Table / Graph** toggle (top-left). **Table** is the default virtualized list above. **Graph** renders the scan's resolved **dependency graph** — every *parent → child* edge the scanner recorded — as an interactive node-link diagram (left-to-right layout), so you can see *how* a package is pulled in rather than just *that* it is present. Each node is coloured by its highest-severity finding (colour is never the only signal — the detail panel and the tree fallback show a severity label too), and a search box highlights matching packages. Clicking a node opens its details beside the canvas.

The choice is mirrored into `?view=graph` so a reload or shared link keeps the graph open; the graph is scoped to the currently pinned `?scan=` snapshot like every other tab.

Two fallbacks keep the view usable at scale:

- A scan whose graph exceeds the server node cap (`DEPENDENCY_GRAPH_MAX_NODES`, default 5000) is **not** materialised — the view shows a banner pointing you back to the table.
- A graph with no recorded edges (a flat component list) or one past the client render cap falls back to a collapsible **dependency tree**.

## The drawer — component detail

Click any row to open a right-side drawer with:

- **Identity** — `purl` (Package URL), upstream homepage, repo URL. The two lines under `purl` carry the component's **Type** (Direct / Transitive / `—`) and **Usage** (Required / Optional / `—`) badges, the same values the row shows.
- **All license findings** — each finding carries a **provenance badge** (**Declared** / **Detected** / **Concluded**); a **Detected** finding also shows the `source_path` — the first-party file scancode found the license in. See [Declared vs. detected](#declared-vs-detected).
- **Obligations** — list of obligations triggered by the component's license (see [Obligations](#obligations)).
- **CVEs** — open and resolved findings, deep-linked to the vulnerability detail.

Closing the drawer keeps you in place on the table — no full-page navigation.

For the approval state of a conditional-license component, switch to the project-level [Approvals](./approvals.md) page (the drawer does not surface approval state in this release). Manual override of the concluded license is also deferred — see [Roadmap](#roadmap).

## Direct vs. transitive (dependency depth) {#dependency-depth}

Starting in this release the pipeline collects the **dependency graph** that `cdxgen` records (which package depends on which) and computes, for each component, its **depth** — the shortest distance from a graph root:

| Depth | Meaning | Label |
|---|---|---|
| `1` | A **direct** dependency — your project declares it in its manifest / lockfile. | **Direct** |
| `2` and up | A **transitive** dependency — pulled in only because a direct dependency (or one of *its* dependencies) requires it. | **Transitive** |
| *(empty)* | No depth was computed for this scan — older older scans that pre-date this collection, or ecosystems where the scan produced a flat component list with no graph. | — |

The drawer shows the component's depth and a **Direct** / **Transitive** label; the component list surfaces the same values so you can tell at a glance which findings you own directly.

:::note Why depth matters
A vulnerability in a **direct** dependency is usually yours to fix — bump the version you declared. A vulnerability in a **transitive** dependency is the responsibility of whichever direct dependency pulls it in; the fix is often "upgrade the direct parent until it stops requiring the vulnerable version." Depth therefore drives remediation prioritisation — shallow, directly-depended components are the cheapest to fix. The upgrade-recommendation feature builds on this signal.
:::

:::info Shallowest path wins
A component can be reached by several paths at once (a "diamond" — two of your dependencies both pull in the same package). The portal reports the **shallowest** path: if `lodash` is both a direct dependency *and* a transitive one, it is shown as **Direct** (depth `1`). The dependency graph itself (every parent → child edge) is stored per scan so future tooling can show the full path.
:::

## License classification

The **Compliance** tab on a project breaks down the same data by SPDX identifier and tier — a horizontal bar chart on top of the same table the Components tab uses, scoped to license rows (with the `Has obligations` toggle flipping the same surface into the obligations view; see [Obligations](#obligations)).

![Project detail — Compliance tab with the tier distribution bar and per-license rows](/img/screenshots/user-licenses-donut.png)

Every license is classified into one of four tiers. The **code value** column
shows the value used in API responses, audit logs, and the build gate;
the **UI label** column is what appears in tables and badges.

| Tier (code value) | UI label | Build-gate effect | Examples |
|---|---|---|---|
| `permissive` | **Allowed** | No build-gate effect. | MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, ISC, CC0-1.0, Unlicense |
| `conditional` | **Conditional** | Triggers the [approval workflow](./approvals.md). Build proceeds — including after a **Rejected** verdict; see the [approvals caveat](./approvals.md#rejected-verdict). | LGPL-2.x, LGPL-3.x, MPL-2.0, EPL-1.x, EPL-2.0, CDDL-1.0 |
| `forbidden` | **Forbidden** | Build gate exits 1 in CI. | AGPL-3.0, GPL-2.0, GPL-3.0, SSPL-1.0, BUSL-1.1 |
| `unknown` | **Unknown** | Surfaced for review; no automatic block. Always needs human review. | License could not be parsed; SPDX ID not matched by the classifier — see [below](#why-so-many-unknown). |

:::warning Classification source
The legal-tier classification (`forbidden` / `conditional` / `permissive` / `unknown`) is driven by a built-in SPDX-to-tier catalog. Per-organization rule customization is on the roadmap. Until then, super-admins can override individual entries in-place and restart the worker — an operator-only path.
:::

### Why so many `unknown`? {#why-so-many-unknown}

:::info
Classification uses exact-match SPDX IDs. Suffix-less variants (`LGPL-3.0` instead of `LGPL-3.0-or-later`) fall through to `unknown`. If a component shows `unknown` despite a well-known SPDX ID, the source likely emitted a deprecated alias. Fuzzy SPDX normalization is on the roadmap.
:::

## AI license review flags {#ai-license-review-flags}

Some licenses restrict *how* you may use a component in ways the four-tier legal classification above does not capture — and that standard open-source compliance tooling often misses. TRUSCA surfaces two such restriction classes as an amber **Review needed** flag, shown next to the license tier badge and filterable on the Compliance tab.

The flag is deliberately narrow. It marks only AI-relevant restrictions the tier model (`permissive` / `conditional` / `forbidden` / `unknown`) does not already express. Ordinary open-source licenses — MIT, Apache-2.0, the BSD family, GPL / LGPL — never carry it.

| Flag (code value) | UI label | What it marks | Example licenses |
|---|---|---|---|
| `behavioral_use` | **Behavioral-use restriction** | The license forbids specific *uses* of the software or model — for example military, surveillance, or discriminatory applications — rather than restricting redistribution. | RAIL (Responsible AI License) and OpenRAIL variants; the Llama, Gemma, and Falcon community model licenses. |
| `non_commercial` | **Non-commercial only** | The license permits research or personal use but forbids commercial use. | CC-BY-NC (Creative Commons Attribution-NonCommercial) and its ShareAlike / NoDerivatives variants; other non-commercial source-available terms. |

:::note The flag reports existence, not applicability
A **Review needed** flag tells you a restriction of that class exists in the license — not that your particular use violates it. Whether a behavioral-use clause or a non-commercial clause actually applies to how your project uses the component is a legal and business judgment a person makes, not the scanner. TRUSCA follows the principle behind the BomLens `license-flags.jq` rules and the OpenChain AI SBOM guidance: the tool flags the *class* of restriction; a human decides whether it applies.
:::

Behavioral-use and non-commercial restrictions travel with AI models and datasets far more than with conventional code, and they sit outside the redistribution-centric logic of the SPDX legal tiers — so a permissive-looking tier alone can hide them. The flag makes the restriction visible without adjudicating it.

### Reading the flag

- On the **Compliance** tab, a flagged license row shows an amber **Review needed** badge next to its tier badge. Narrow the table to just those rows with the **Review needed** filter, alongside the license-category multi-select.
- The flag is advisory: it never blocks a build and never changes the legal tier. A component can be tier `permissive` and still carry a **Review needed** flag.
- Flagged licenses also appear in a dedicated "License review needed" section of the generated NOTICE document — see [SBOM → NOTICE file](./sbom.md#notice-file).

## Declared vs. detected {#declared-vs-detected}

Each license finding has a **kind** that tells you where the license came from. The kind shows as a provenance badge in the components table, the Licenses tab, and the component drawer, and you can filter the Licenses tab by it.

| Kind | Source | What it tells you |
|---|---|---|
| **Declared** | `cdxgen` — read from a dependency's published package metadata (`package.json`, `pom.xml`, `setup.py`, …). | The license the dependency's author *says* it ships under. This is the value the build gate evaluates. Most dependency findings are declared. |
| **Detected** | scancode — scans your project's **first-party** source files directly. Each detected finding carries a `source_path` (the file the license text was found in). | The license actually present in **your own code**. This catches cases the metadata misses — for example a dependency declared `MIT` but with `GPL-3.0`-licensed code copied into your tree. |
| **Concluded** | The multi-ecosystem registry fetcher (Maven Central / PyPI / crates.io / pkg.go.dev), used as a fallback **only** when `cdxgen` produced no SPDX id for a dependency. | A registry-derived license for a dependency whose own metadata was silent. It is *not* the result of reconciling declared and detected — v0.10.0 does not perform automatic reconciliation. |

:::note "Detected" means first-party, not dependency source
scancode runs over your **own** source tree only. Third-party dependency sources are deliberately **not** downloaded — that keeps per-scan runtime within budget. So a dependency's license is **declared** (or **concluded** via the registry fallback), never **detected**; **detected** licenses always describe code in your repository.
:::

:::caution Declared and detected can disagree
A component can carry both a **declared** finding (e.g. `MIT` from metadata) and a **detected** finding (e.g. `GPL-3.0` from a source file). v0.10.0 surfaces both side by side and does **not** auto-reconcile them into a single verdict — review the conflict yourself. A `GPL-3.0` detected inside a project you ship as `MIT` is exactly the kind of contamination the detected scan exists to surface.
:::

### When detected licenses are missing

scancode is **best-effort**. Detected licenses can be absent — which is normal and non-fatal; the scan still succeeds with declared licenses — when:

- scancode is not installed in the worker image.
- The first-party tree exceeds the `SCANCODE_MAX_FILES` ceiling, scancode timed out, or its result was too large.
- The relevant code lives inside an **excluded** directory. To stay within the resource budget, scancode skips directories named `node_modules`, `vendor`, `bower_components`, `.venv`, `venv`, `virtualenv`, `site-packages`, `dist`, `build`, `target`, `out`, `.next`, `.nuxt`, `__pycache__`, `.gradle`, `.git`, `.hg`, `.svn`, `.tox`, `.mypy_cache`, `.pytest_cache`, `.ruff_cache`, `.idea`, and `.vscode` — at any depth. Code committed under one of these names will not produce a detected license.

## Vendored-OSS identification (SCANOSS) {#vendored-oss}

Some projects — C / C++ and embedded trees especially — carry open source **copied straight into the source tree** with no package manifest (a `liblzma`, `openssl`, or `zlib` folder committed under `src/`). `cdxgen` sees only unnamed `pkg:generic` files there and the real OSS goes unrecorded. **SCANOSS** closes that gap: it fingerprints the source files and matches them against a knowledge base of known OSS releases, so a copied file is recorded as a proper component (name + version + purl) with its detected license.

TRUSCA treats only **full-file matches** as components — a snippet match (a few lines copied from elsewhere) is noisy and is skipped, so the components that feed the build gate and NOTICE stay clean. Matched components appear on the Components tab tagged as coming from SCANOSS, and their licenses are **Detected** findings, exactly like scancode's.

:::warning Off by default — sends fingerprints to an external service
SCANOSS is **disabled unless an operator sets `SCANOSS_ENABLED=true`**. When enabled, it sends file **fingerprints** (hashes, never your source code) to `SCANOSS_API_URL` — the free `api.osskb.org` by default. Because a self-hosted portal shouldn't quietly egress data about your code, this is opt-in: turn it on only if that external match is acceptable, or point `SCANOSS_API_URL` at a **self-hosted SCANOSS** instance to keep everything inside your network. See [Environment variables → Scan pipeline](../reference/env-variables.md#scan-pipeline).
:::

Like scancode, the stage is **best-effort**: if SCANOSS is off, the tool is missing, the endpoint is unreachable, or nothing matches, the scan simply continues without vendored-OSS results — it never fails because of this stage.

## Obligations

Each license carries **obligations** — duties you must honor when redistributing the component. The portal tracks seven kinds (see [glossary](https://github.com/trustedoss/trusca/blob/main/docs/glossary.md)):

- **Attribution** — preserve the upstream copyright notice.
- **NOTICE preservation** — carry the upstream `NOTICE` file (Apache-2.0 §4(d)).
- **Source disclosure** — make the corresponding source available on demand.
- **Copyleft** — release derivative works under the same license terms.
- **Modifications** — state changes prominently in modified files.
- **Dynamic linking** — LGPL-style: end-users must be able to relink against a modified library.
- **No endorsement** — do not use the project name to endorse derivatives without permission.

The **Compliance** tab with the **Has obligations** toggle on consolidates obligations across components. Pick a NOTICE format (**text** or **HTML**) on the toolbar and click **Download NOTICE** to save a NOTICE document summarizing every attribution and license. The endpoint also serves a `markdown` variant via the API. The document carries a copyright line per component (falling back to the component's registry URL when the SBOM recorded no holder) and closes with a **License Texts** section embedding the full text of every license in the project — so the `license_text_inclusion_required` obligation is satisfied by the NOTICE itself. See [SBOM → NOTICE file](./sbom.md#notice-file) for the format / MIME / extension table and the section-by-section contents.

![Project detail — Compliance tab with the Has obligations toggle on, showing the per-component obligations distribution](/img/screenshots/user-obligations-distribution.png)

:::note Obligation kinds in this release
The obligations catalog covers the seven kinds listed above. Some
AGPL / SSPL / BUSL-specific obligations are **not** modeled as discrete
kinds yet:

- **Network-use disclosure** (AGPL §13, SSPL §13) — required when
  end-users interact with modified software over a network.
- **Patent grant termination** (Apache-2.0 §3, MPL-2.0 §5.2).
- **Trademark restrictions** (Apache-2.0 §6, BSD-4-clause).
- **Field-of-use restrictions** (BUSL-1.1).

For these, see the underlying license text via the component drawer; a
richer obligation taxonomy is on the roadmap.
:::

## SPDX expressions

Licenses are identified by [SPDX identifiers](https://spdx.org/licenses/). Compound licenses use the SPDX expression syntax:

- `(MIT OR Apache-2.0)` — dual-licensed; either is acceptable.
- `(GPL-2.0+ WITH Classpath-exception-2.0)` — GPL with an exception.
- `LicenseRef-proprietary` — non-SPDX license, parsed but not classified.

Hovering an expression in the UI shows the SPDX URL for each component license.

## Verify it worked

After a successful scan:

<!-- docs-uat: id=components-count-nonzero kind=ui harness=componentsHaveData(portal-web) tier=nightly -->
1. Component count matches your expectation (close to the count of pinned dependencies in your lockfile).
<!-- docs-uat: id=components-classification-sums kind=manual tier=manual -->
2. The classification distribution horizontal bar chart on the Overview tab adds up to 100%.
<!-- docs-uat: id=licenses-forbidden-highlighted kind=ui harness=licensesGridPopulated(portal-web) tier=nightly -->
3. Forbidden-license components, if any, are highlighted in red and have a CTA to the [approvals queue](./approvals.md).

## Troubleshooting

### Many components show `Unknown` license

The license could not be parsed, or the SPDX ID was not in the classifier's exact-match dictionary (see [Why so many `unknown`?](#why-so-many-unknown)). Common causes:

- The package has no `LICENSE` file and no metadata declaration (rare in well-maintained ecosystems).
- A custom license string the classifier does not recognize. The component drawer surfaces the raw string for legal review.
- The source emitted a deprecated SPDX alias (e.g. `LGPL-3.0` instead of `LGPL-3.0-or-later`); the exact-match dictionary does not yet normalize these.
- Metadata fetch failed for that ecosystem. Check `docker-compose logs worker` for `cdxgen` per-ecosystem warnings.

### Classification looks wrong

Classification is driven by the built-in SPDX-to-tier catalog (see [Classification source](#license-classification) above). For a one-off override today, a super-admin can patch the catalog and restart the worker; the per-organization customization path is on the roadmap. If the catalog entry is correct but a detected license disagrees with the declared one, review both findings in the component drawer (see [Declared vs. detected](#declared-vs-detected)).

### Lockfile not detected

`cdxgen` supports 30+ ecosystems but new ones land regularly. Confirm the project's lockfile is at the repo root or one level below; `cdxgen` does not recurse arbitrarily deep. If the ecosystem is unsupported, file an issue with the pipeline output.

## Roadmap

Items the manual previously promised that are not in this release; tracked for later releases.

- Standalone **Type** (ecosystem) and **Classification** columns on the components table — for v0.10.0 the type is encoded inside the `purl` shown in the drawer's Identity row, and the classification surfaces via the **Severity** color legend.
- Exact-SPDX **License** filter and **Has open CVE** toggle — planned; the current **License category** multi-select and the search box cover most workflows.
- **Approval status** row inside the component drawer — planned; the project-level [Approvals](./approvals.md) page is the source of truth today.
- Manual **Override concluded license** action in the drawer (`team_admin`) — planned.
- Fuzzy SPDX normalization for suffix-less variants (`LGPL-3.0` → `LGPL-3.0-or-later`) — planned.
- Per-organization license-classification rule customization — planned; today classification uses the built-in catalog.

## See also

- [Vulnerabilities](./vulnerabilities.md)
- [Approvals](./approvals.md)
- [SBOM](./sbom.md) — including the [Compliance evidence trail](./sbom.md#compliance-evidence-trail)
