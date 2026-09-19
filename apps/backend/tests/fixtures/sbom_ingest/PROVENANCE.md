# SBOM ingest fixture provenance

Where the documents in this directory came from, and what was changed. A
fixture written to match the code's expectations agrees with the code by
construction, so the ones that decide behaviour start as real tool output.

## `aibom-owasp-1_7.json`

CycloneDX 1.7 ML-BOM produced by the OWASP AIBOM Generator 1.0.2 (named in the
document's own `metadata.tools`). Unmodified. The document's subject is the
generating job (`type: application`) and the model sits in `components[]`,
which is the shape that generator emits.

## `aibom-model-subject-1_7.json`

The same document with one change: the `machine-learning-model` component was
moved from `components[]` into `metadata.component`, so the document is *about*
the model rather than about the job that produced it. Nothing else was touched
— the model object, the tools block and every other field are byte-identical to
the source above.

It exists because that shape is what issue #53 was about, and no real ML-BOM in
this repository had it. Both shapes are legitimate CycloneDX: the spec says
`metadata.component` is the component the BOM describes, and a supplier
publishing a model publishes a BOM about the model.

The move is a structural edit rather than a recording, and it is honest about
what it pins: the reading of `metadata.component`, not the generator's output
format. The values it asserts on are the generator's.

## `aibom-datasets-1_7.json`, `aibom-review-flags-1_7.json`

Hand-built around real model and dataset identifiers (`distilbert-base-uncased-
finetuned-sst-2-english`, `stanfordnlp/sst2`, `Llama-2-7b`) with the license
strings those publishers actually declare. They exercise dataset dependency
edges and the license-verdict families; the identifiers and licenses are real,
the document scaffolding is not.

## `real_cyclonedx_large_js_2544.cdx.json`

`cdxgen 12.2.0` run against a real, unrelated local JavaScript monorepo's
already-`npm install`ed dependency tree (a Docusaurus site plus a few remark
lint plugins, nothing to do with this product). Unmodified except
`metadata.component` (name/purl/bom-ref), renamed to
`unrelated-third-party-js-monorepo` so the fixture cannot be mistaken for data
about this product; every one of its 2,544 `components[]` entries and the
`dependencies[]` graph are byte-identical to cdxgen's own output.

It exists for #398 (bulk catalog upsert): the repository's hardening rule
against synthetic density-hiding fixtures asks for a real large SBOM, and no
project on the machine that produced this fixture had a real, already-resolved
dependency tree at true enterprise scale (5,000-20,000 components per #398).
2,544 is the largest that was available without a fresh multi-package
`npm install` (blocked behind this repo's supply-chain-review gate for new
packages) or downloading a third-party SBOM; true 10k+-scale verification is
covered by the fixture below rather than pretended away with a hand-built one.

## `real_cyclonedx_large_multi_10198.cdx.json.gz`

Five real `cdxgen 12.2.0` runs merged into one document, gzipped (about 10 MB
raw, 1.2 MB compressed). Each run was `cdxgen -t js|ruby|python
--no-install-deps --no-babel --no-validate` against a shallow clone of a
public repository, so it reads the committed lockfiles and installs nothing:

- `backstage/backstage` (Apache-2.0), 5,553 components from its yarn workspaces
- `gitlab-org/gitlab-foss` (MIT), 1,674 (`yarn.lock`, `Gemfile.lock`)
- `apache/superset` (Apache-2.0), 3,103 from `superset-frontend` and 1,990 from `docs`
- `home-assistant/core` (Apache-2.0), 1,250 from its pinned requirements files

No single repository reaches 10,000. The largest, Backstage, has 5,553. The
merge keeps the first `components[]` entry per purl, byte-identical to
cdxgen's output, which gives 10,198 unique components from 12,570 before
deduplication. `dependencies[]` is merged by `ref` as the union of `dependsOn`.
`metadata.component` is renamed to `unrelated-third-party-multi-repo-composite`
and `metadata.properties` is dropped, so the document cannot be mistaken for
data about this product.

What it is not: one real project. Density is real per repository (the
per-package dependency fan-out is cdxgen's own), but the cross-repository
overlap is not what a single project would have. Only 1,344 components carry
license data, because lockfile-only runs do not fetch registry metadata. It
exercises count and edge volume, not license-resolution behaviour.

It exists for #462: `test_batching_holds_past_ten_thousand_real_components`
pins that catalog, `scan_components` and edge inserts stay batched at this
scale. Mutating the catalog prefetch to the per-row path makes it fail with
21,740 statements for 10,198 rows.

## The rest

`realistic.cdx.json`, `realistic-trivy-sbom.json`, `centos7-rpm-no-os.cdx.json`,
`supplier-file-mixed.json`, `supplier-files-only.json` predate this file. They
back the core conformance and component-persistence paths rather than any
AI-specific reading.
