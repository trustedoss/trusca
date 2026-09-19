# Provenance of the golden fixtures

The baselines under `../baselines/` were first recorded from a corpus
repository that no longer exists. `node` and `python-pip` were kept in this
directory; the other thirteen were rebuilt on 2026-09-19 from the component
lists those baselines recorded.

## What is authored and what is captured

Manifests (`package.json`, `pom.xml`, `build.gradle*`, `go.mod`, `Cargo.toml`,
`Gemfile`, `pyproject.toml`) and the source stubs are written for this
repository and carry no third-party text. Every lockfile is the unedited output
of the tool named below, run once against those manifests. No lockfile is
written by hand, and no dependency source or `node_modules` is stored.

| fixture | manifest pins | lock | produced with |
|---|---|---|---|
| `go` | `github.com/google/uuid v1.5.0` | `go.sum` | go 1.25.5, `go mod tidy` |
| `maven` | guava 32.1.3-jre, commons-lang3 3.12.0 | none (Maven has none) | - |
| `gradle`, `gradle-kts` | guava 32.1.3-jre | none | - |
| `node-yarn` | lodash 4.17.21 | `yarn.lock` (Yarn 4 format) | yarn 4.13.0, `install --mode=update-lockfile` |
| `multi-component` | express 4.18.2, commons-lang3 3.12.0 | `package-lock.json` | npm 10.8.2, `install --package-lock-only` |
| `python-poetry` | requests 2.31.0 | none, on purpose | - |
| `ruby` | sinatra 3.1.0 | `Gemfile.lock` | bundler 1.17.2 on ruby 2.6.10, `bundle lock` |
| `rust` | serde 1 with `derive` | `Cargo.lock` | cargo 1.96.1, `cargo generate-lockfile` |
| `scancode-mixed-policy` | lodash 4.17.21 | `package-lock.json` | npm 10.8.2 |

`python-poetry` has no lockfile because its baseline lists one component
(`requests`); a `poetry.lock` would add its transitive dependencies and change
what the fixture tests.

`python-pip` pins all five packages, transitive ones included. It pinned only
`requests` before, and two other packages moved between runs.

## License text in the scancode fixtures

- `scancode-license-files/LICENSE-APACHE` and `LICENSE-MIT` are the SPDX texts
  from `spdx/license-list-data` tag v3.29.0, unedited.
- The other notices are the standard header boilerplate each license asks for
  (Apache-2.0, MIT, BSD-3-Clause, GPL-3.0, MPL-2.0). `scancode-spdx-tags` uses
  only `SPDX-License-Identifier` lines.

## Where the new baselines differ from the old ones

Same input, later resolution: `rust` (serde 1.0.229, syn 3.0.6), `multi-component`
(two transitive patch versions), `ruby` (bundler 1.17.2 resolves
mustermann 3.0.4 and adds `ruby2_keywords`, so 6 components instead of 5, and
license lookups from the registry now return BSD-2-Clause and MIT).

Product changes since the old recording, not investigated:
`source_tree_root_entries` is one higher for `maven`, `gradle`, `gradle-kts`
and `python-poetry`, and two higher for `multi-component`.

The four `scancode-*` baselines and `multi-component` were regenerated after
the worker image's scancode was repaired (issue 487). The four `scancode-*`
baselines are identical to the old recording, so those fixtures produce the same
detections. `multi-component` gained its old detected license
`MIT AND ISC AND BSD-3-Clause`.

Two of the scancode fixtures needed a second pass to match the old recording:
the notice wording has to be standard FSF text for scancode to name
`GPL-3.0-only`, and a README that mentions `SPDX-License-Identifier` is itself
read as an unknown SPDX tag.
