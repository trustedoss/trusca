---
id: license-data-sources
title: License data sources
description: Where the portal's license identifications, copyright notices, and reference metadata come from — and what each source is allowed to decide.
sidebar_label: License data sources
sidebar_position: 8
---

# License data sources

Four sources contribute to what you see on a license. They are listed in the order the pipeline consults them, and each has a different standing: only the first two ever set a license *identification*, and only this deployment's own catalog sets a *classification*.

| Source | Supplies | Network | Default |
|---|---|---|---|
| The SBOM (cdxgen) | Declared license ids, copyright holders | — | Always |
| Package registries | License ids for 6 ecosystems | Yes | On |
| ClearlyDefined | License ids and copyright holders as a fallback | Yes | **Off** |
| OSORI | Alias spellings, obligation metadata for ~670 licenses | — (vendored) | On |

## The SBOM comes first

`cdxgen` reads the package itself. When it reports a license or a copyright holder, nothing overrides it — it looked at the artifact you actually shipped.

## Package registries fill the gaps

When the SBOM has no license for a component, the portal asks the registry that owns it: Maven Central, PyPI, crates.io, pkg.go.dev, RubyGems, or NuGet. Answers are cached for 24 hours, including "no license found", so a repository full of unpublished packages cannot drive repeated lookups.

Controlled by `LICENSE_FETCH_ENABLED` (default on). Turn it off for an air-gapped install; the portal then reports only what the SBOM carried.

## ClearlyDefined as the last resort

[ClearlyDefined](https://clearlydefined.io) is a curation layer over harvested scan output for tens of millions of package versions. It is consulted only after the registries have declined, and it adds two things they cannot.

**npm.** No registry adapter covers npm, so before this every npm component went straight to "no license found" without a single lookup. ClearlyDefined is the first coverage npm gets.

**Copyright holders.** ClearlyDefined harvests per-file attributions. Where the SBOM carried none — and the NOTICE would otherwise print *"holders not captured in SBOM"* — those attributions fill the gap. They are written only into empty fields; a holder cdxgen extracted is never overwritten.

Off by default. `api.clearlydefined.io` is a host your deployment has not talked to before, and new outbound destinations here opt in explicitly rather than appearing in a release. Enable with:

```bash
CLEARLYDEFINED_ENABLED=true
```

The API is public, unauthenticated, and its curated data is CC0.

:::note What it will not do
A license expression meaning "both apply" — `CC0-1.0 AND MIT`, which is what lodash declares — is not reduced to a single id, because one id cannot say "both". The component keeps no license from this source. Its copyright holders are still collected, since those are what the NOTICE is short of.
:::

## OSORI reference data

[OSORI](https://olis.or.kr/osori) is an open license database built jointly by Korean companies and hosted by the Korea Copyright Commission. The portal ships a **snapshot of it as a file** — no network call at runtime, so an air-gapped install gets the same answers as a connected one.

It is used for two things.

**Alias spellings.** People write `Apache 2`, `The MIT License (MIT)`, `Android-Apache-2.0`. The portal's own rules handle the shapes they were written for and return nothing for the rest rather than guessing. OSORI's curated alias list resolves several hundred more — as lookups, not heuristics. An alias two licenses both claim is discarded rather than resolved: a wrong SPDX id silently changes a component's obligations, which is worse than no answer.

**Obligation metadata beyond the built-in catalog.** The portal classifies 52 licenses as allowed / conditional / forbidden. OSORI describes about 670 — whether distribution requires a notification, how far source disclosure reaches (`NONE` / `LIBRARY` / `EXECUTABLE` / `NETWORK`), and caution items with a 1–5 level.

This appears in the license drawer as a separate, clearly labelled panel. **It is never merged into the category or the summary.** Those are this deployment's classification, pinned by contract tests and read by the build gate; OSORI is an outside reading with no authority over either. The panel earns its place mainly on licenses outside the 52, where the summary above it is empty.

Disable with `OSORI_ENABLED=false` if you would rather show only your own catalog. Point `OSORI_SNAPSHOT_PATH` at a newer file to update between releases.

### Refreshing the snapshot

A maintainer runs:

```bash
python3 apps/backend/scripts/refresh_osori_snapshot.py
```

It rewrites `apps/backend/services/license_osori/osori_snapshot.json`. A failed fetch leaves the existing file untouched — a network outage must not empty the vendored data.

### Attribution

OSORI's data is licensed **ODC-By 1.0**, which requires attribution wherever it appears. The credit is carried inside the snapshot, shown in the license drawer panel, and repeated here.

## What decides what

| Decision | Decided by |
|---|---|
| Which license a component has | SBOM → registry → ClearlyDefined, in that order |
| allowed / conditional / forbidden | This deployment's catalog and license policies — only |
| Whether a build is blocked | The same, via the build gate |
| Copyright holders in the NOTICE | SBOM, then ClearlyDefined for gaps |
| Reference obligation metadata | OSORI, displayed but never acted on |

## See also

- [Vulnerability data sources](./data-sources.md)
- [License policies](./license-policies.md)
- [Components & licenses](../user-guide/components-and-licenses.md)
