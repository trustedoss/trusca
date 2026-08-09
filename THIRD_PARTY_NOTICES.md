# Third-Party Notices

TRUSCA is licensed under the Apache License, Version 2.0 (see [`LICENSE`](LICENSE)).
Its own copyright notice is in [`NOTICE`](NOTICE).

This file carries the attribution notices for third-party material that TRUSCA
either **includes in its source tree** or **bundles in its container images**.
It is distributed with every TRUSCA artifact — source tree, container images
(`/licenses/`), and the Helm chart.

Naming both parties plainly: the components listed here are **not** owned by the
TRUSCA project, and TRUSCA is **not** owned by any of the copyright holders
listed here. Each entry states exactly which files or binaries it covers.

---

## 1. Material vendored into the TRUSCA source tree

### BomLens (formerly sbom-tools) — SK Telecom

- **Source**: https://github.com/sktelecom/bomlens
- **Copyright**: Copyright 2026 SK Telecom Co., Ltd.
- **License**: Apache License, Version 2.0

Attribution notice reproduced from the upstream `NOTICE` file, per Apache-2.0
§4(d), as it stood at `sbom-tools` v1.8.3 — the revision the crosswalk was taken
from; the project has since been renamed BomLens and its NOTICE now opens with
that name. Only the portion pertaining to the material TRUSCA derives is
reproduced. Two kinds of upstream notice are omitted: those covering tools
bundled in *BomLens's own* Docker images, which pertain to no part of TRUSCA,
and those covering datasets that TRUSCA does not receive from BomLens but
fetches from the origin itself and attributes independently below
(endoflife.date, OSV).

```
sbom-tools
Copyright 2026 SK Telecom Co., Ltd.

This product is licensed under the Apache License, Version 2.0 (see LICENSE).
```

**Data files vendored verbatim.** These are copies of upstream data files, taken
as a snapshot at the commit named in each file's own documentation. A snapshot
may lag the current upstream file — being byte-identical to *some* upstream
revision is the contract, not tracking upstream's HEAD.

| TRUSCA path | Upstream path |
|---|---|
| `apps/backend/services/eol/eol_purl_map.json` | `docker/lib/eol-purl-map.json` |

**Data files adapted from upstream.** These began as copies and carry upstream's
structure, element definitions and interpretive judgements, but TRUSCA has
modified them. Per Apache-2.0 §4(b), the changes are stated here.

| TRUSCA path | Upstream path | Changes |
|---|---|---|
| `apps/backend/services/g7_registry.json` | `docker/lib/g7-registry.json` (v2, sbom-tools#306) | Documentation `note` rewritten: it named upstream's jq evaluator and two of its shell scripts, and now describes TRUSCA's Python evaluator. Element definitions unchanged. |
| `apps/backend/services/regulation_crosswalk.json` | `docker/lib/regulation-crosswalk.json` (v1.8.3 line, sktelecom/bomlens#462) | Product name changed to TRUSCA in the user-facing `disclaimer` / `disclaimer_ko` and in the documentation `note`, since TRUSCA is what serves these strings to its own users. `disclaimer_ko` was additionally reworded for Korean style (same meaning, one clause restructured). The `note` was repointed at TRUSCA paths. The US framework was rewritten for the 2026 minimum elements, taking upstream's `cisa-*` mappings and adding a scope caveat about SPDX submissions. Other framework definitions and their mappings are unchanged. |
| `apps/backend/services/cisa_registry.json` | `docker/lib/cisa-registry.json` | Documentation `note` rewritten to describe TRUSCA's Python evaluator. Korean labels say 컴포넌트 rather than 구성요소, matching TRUSCA's own UI vocabulary. The `evidenceGrade` marker is matched by suffix rather than by upstream's exact `bomlens:` name, so both TRUSCA's and upstream's exports are read. Element definitions, sources and jq expressions are unchanged. |
| `apps/backend/services/license_compat.json` | `docker/lib/license-compat.json` | The `_comment` was rewritten to point at TRUSCA's reader and its class definitions; upstream's names its jq and TypeScript consumers. An `uncategorized` outbound row was added: upstream falls through to a generic "no rule for this combination", and TRUSCA can say the more useful thing, that the declared outbound license itself was not recognised. The verdict vocabulary, the sixteen original matrix cells with their reasoning, and both explicit pairs are unchanged. |

The `bomlens:*` property names these files match on are upstream's wire format,
not TRUSCA branding: they identify properties written by upstream tooling, and
TRUSCA reads them so that an SBOM produced by BomLens is interpreted correctly.
They are deliberately left as-is.

**Logic hand-ported** from upstream jq, shell, and TypeScript implementations.
The ports are original TRUSCA code, but the semantics, check definitions, and
data shapes are BomLens's:

| TRUSCA path | Upstream source |
|---|---|
| `apps/backend/services/g7_conformance.py` | `docker/lib/validate-sbom.sh` (`g7_ai_checks()`) |
| `apps/backend/services/cisa_conformance.py` | `docker/lib/validate-sbom.sh` (registry evaluator) + `docker/lib/cisa-registry.json` |
| `apps/backend/services/registry_conformance.py` | `docker/lib/validate-sbom.sh` (registry evaluator, sktelecom/bomlens#639) |
| `apps/backend/services/sbom_conformance.py` | `docker/lib/validate-sbom.sh` |
| `apps/backend/services/eol/eol_catalog.py` | `docker/lib/enrich-eol.sh` |
| `apps/backend/services/malicious/malicious_catalog.py` | `docker/lib/enrich-malicious.sh` |
| `apps/backend/scripts/refresh_malicious_snapshot.py` | `docker/build-malicious-index.py` |
| `apps/backend/services/eol/__init__.py` | package docs for the two files above |
| `apps/backend/services/license_normalize.py` | `docker/lib/spdx-normalize.jq` |
| `apps/backend/services/license_flags.py` | `docker/lib/license-flags.jq` (`license_flag`) |
| `apps/backend/services/license_class.py` | `docker/lib/license-flags.jq` (`license_class`, `class_rank`, `component_license_class`) |
| `apps/backend/services/license_conflict.py` | `docker/lib/license-flags.jq` (`term_verdict`, `expr_verdict`, `component_license_conflict`) + `docker/lib/license-compat.json` |
| `apps/backend/services/regulation_crosswalk.py` | evaluator for the crosswalk data above |
| `apps/backend/services/obligation_service.py` | `docker/lib/generate-notice.sh` |
| `apps/backend/services/license_texts/__init__.py` | `docker/lib/licenses/` (collection layout) |
| `apps/backend/integrations/cocoapods_lockfile.py` | `docker/lib/parse-podfile-lock.py` |
| `apps/backend/integrations/scanoss.py` | `docker/lib/identify-vendored.sh` |
| `apps/backend/integrations/scan_executor/source_detect.py` | `docker/lib/source-detect.sh` |
| `apps/backend/integrations/scan_executor/build_prep_source.sh` | `docker/lib/build-prep.sh` |
| `apps/frontend/src/features/scan/lib/g7Conformance.ts` | `docker/web/frontend/src/lib/conformance.ts` |
| `apps/frontend/src/features/scan/lib/g7Guidance.ts` | `docker/web/frontend/src/lib/g7Guidance.ts` |

Every file above names its upstream origin in its own module docstring; this
table is the aggregate view, and
`apps/backend/tests/unit/test_license_distribution.py` asserts the two stay in
sync — a new vendored or ported file that is missing here fails that test.

The license texts under `apps/backend/services/license_texts/` are the verbatim
published texts of the licenses themselves (Apache-2.0, MIT, GPL, …), each the
work of its own steward — the Apache Software Foundation, the Free Software
Foundation, and so on. BomLens is credited above for the collection layout, not
for the texts.

### endoflife.date

- **Source**: https://endoflife.date (public JSON API)
- **Covers**: `apps/backend/services/eol/eol_snapshot.json`

End-of-life dates are sourced from endoflife.date and vendored as a snapshot,
refreshed per release by `apps/backend/scripts/refresh_eol_snapshot.py`. The
per-row provenance is surfaced in the product through
`component_versions.eol_source`. TRUSCA claims no ownership of this data.

### OSV — malicious-package advisories

- **Source**: https://osv.dev (per-ecosystem bulk archives)
- **Upstream publisher**: the OpenSSF Package Analysis project
  (https://github.com/ossf/malicious-packages), Apache-2.0
- **Covers**: `apps/backend/services/malicious/malicious_snapshot.json`

The `MAL-` advisories that identify packages published to attack their
installers are collected by the OpenSSF Package Analysis project and
distributed through OSV. TRUSCA vendors a reduced snapshot — package
identifier, advisory id, and the affected versions when an advisory names
them — rebuilt per release by
`apps/backend/scripts/refresh_malicious_snapshot.py`. It is not a reproduction
of the upstream database: the advisory text, timeline, and evidence stay with
the source, and the product links out to it rather than restating it.

Every verdict carries the snapshot it came from through
`component_versions.malicious_source`, so a flag reads as "listed in this
snapshot" rather than an open-ended claim about a package. TRUSCA claims no
ownership of this data, and neither OSV nor the OpenSSF Package Analysis
project endorses TRUSCA.

### OSORI — Korea Copyright Commission

- **Source**: https://olis.or.kr/osori (public API, unauthenticated)
- **License**: Open Data Commons Attribution License v1.0 (ODC-By 1.0)
- **Covers**: `apps/backend/services/license_osori/osori_snapshot.json`

OSORI is an open license database built jointly by Korean companies and hosted
by the Korea Copyright Commission. TRUSCA vendors a field-filtered snapshot of
its license table — SPDX identifiers, alias spellings, and obligation metadata —
refreshed by `apps/backend/scripts/refresh_osori_snapshot.py`.

ODC-By 1.0 requires attribution wherever the database or a derivative of it is
used publicly. TRUSCA carries that attribution in three places: this file, the
`_source` field inside the snapshot itself, and the reference panel in the
license drawer where the data is displayed. The data is used as reference
material only — it never contributes to the allowed/conditional/forbidden
classification, which is TRUSCA's own and is fixed by contract tests.

TRUSCA claims no ownership of this data.

### ClearlyDefined

- **Source**: https://api.clearlydefined.io (public API, unauthenticated)
- **License**: CC0-1.0 (curated definitions)
- **Covers**: nothing vendored — queried at scan time, off by default

ClearlyDefined is queried as a fallback when the SBOM and the package registries
produce no license, and for the copyright holders that populate NOTICE files.
Nothing is redistributed with TRUSCA: the data is fetched at scan time by the
installation that requested it, and the integration ships disabled
(`CLEARLYDEFINED_ENABLED`, default off).

CC0-1.0 waives attribution requirements. This entry is recorded anyway because
the data reaches a published artifact — the NOTICE files TRUSCA generates — and
a reader of one of those files is entitled to know where its copyright lines
came from. Per-component provenance is stamped in
`scan_components.raw_data["copyright_source"]`.

---

## 2. Tools bundled in the TRUSCA worker image

The Celery worker image (`ghcr.io/trustedoss/trusca-worker`) ships the SCA
toolchain. These are **separate programs**, invoked at arm's length as separate
processes, and each remains under its own license. TRUSCA does not link against
them.

| Tool | Version | License |
|---|---|---|
| cdxgen | 12.3.3 | Apache-2.0 |
| scancode-toolkit | 32.4.0 | Apache-2.0 |
| scanoss.py | 1.53.1 | MIT |
| Trivy | 0.72.0 | Apache-2.0 |
| cosign | 3.1.1 | Apache-2.0 |
| govulncheck (`golang.org/x/vuln`) | v1.5.0 | BSD-3-Clause |
| Docker CLI (client only) | 29.6.1 | Apache-2.0 |
| Go toolchain | 1.25.12 | BSD-3-Clause |
| Node.js | 20.18.1 | MIT |
| npm | 11.18.0 | Artistic-2.0 |
| Gradle | 8.14.3 | Apache-2.0 |
| Apache Maven | Debian `maven` (3.8.x) | Apache-2.0 |
| Composer | Debian `composer` | MIT |
| PHP CLI | Debian `php-cli` (8.2) | PHP-3.01 |
| Ruby (MRI) | Debian `ruby` (3.1) | Ruby OR BSD-2-Clause |
| Bundler | Debian `ruby-bundler` | MIT |
| Cargo / Rust | Debian `cargo` | MIT OR Apache-2.0 |
| .NET SDK | 8.0 | MIT |
| PostgreSQL client | 17 | PostgreSQL |
| Python | 3.12.13 | PSF-2.0 |
| **Eclipse Temurin JDK** | **21** | **GPL-2.0 WITH Classpath-exception-2.0** |

### GPL source offer (worker image)

The worker image includes **Eclipse Temurin JDK 21**, licensed under GPL-2.0
with the Classpath Exception. The Classpath Exception means code that merely
runs on or links against the JDK — including TRUSCA — is not itself placed
under the GPL. The JDK's own source remains available under the GPL:

- Binaries as shipped: https://packages.adoptium.net/artifactory/deb (`temurin-21-jdk`)
- Corresponding source: https://github.com/adoptium/temurin21-binaries and
  https://github.com/openjdk/jdk21u

The JDK is present only so cdxgen can shell into Maven and Gradle for Java
dependency enumeration. No other GPL-licensed program is bundled in any TRUSCA
image.

---

## 3. Tools bundled in other TRUSCA images

**Backend API image** (`ghcr.io/trustedoss/trusca-backend`): Python 3.12
(PSF-2.0) on Debian bookworm-slim, plus `tini` (MIT), `curl` (curl), and the
Python dependencies pinned in `apps/backend/requirements.txt`.

**Frontend image** (`ghcr.io/trustedoss/trusca-frontend`): nginx 1.27.3
(BSD-2-Clause) on Alpine Linux, serving a static bundle built from the npm
dependencies pinned in `apps/frontend/package-lock.json`.

---

## 4. Application dependencies

TRUSCA's own Python and npm dependency trees run to several hundred packages
and are not enumerated here — a hand-maintained list would drift from the
lockfiles within one release. The authoritative inventory is the CycloneDX SBOM
published with every release (`trusca-<version>.cdx.json`, attached to the
GitHub release and signed with cosign). Lockfiles in the source tree are
`apps/backend/requirements.txt` and `apps/frontend/package-lock.json`.

TRUSCA can also generate this inventory for itself — the `sca-self` and
`dogfood-scan` CI workflows scan this repository with the product.
