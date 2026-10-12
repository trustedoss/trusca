# Third-Party Notices

TRUSCA is licensed under the Apache License, Version 2.0 (see [`LICENSE`](LICENSE)).
Its own copyright notice is in [`NOTICE`](NOTICE).

This file carries the attribution notices for third-party material that TRUSCA
either **includes in its source tree** or **bundles in its container images**.
It is distributed with every TRUSCA artifact — source tree, container images
(`/licenses/`), and the Helm chart. Section 4 also lists images that TRUSCA
can run but does not distribute.

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
bundled in *BomLens's own* Docker images, which TRUSCA does not include in
any artifact it distributes (it can run those images as separate containers;
see section 4), and those covering datasets that TRUSCA does not receive from BomLens but
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
| `apps/backend/services/g7_registry.json` | `docker/lib/g7-registry.json` (v2, sbom-tools#306) | Documentation `note` rewritten: it named upstream's jq evaluator and two of its shell scripts, and now describes TRUSCA's Python evaluator. Element definitions unchanged. The four top-level declaration keys (`appliesWhen`, `subject`, `subjectLabel`, `emptySubjectDetail`) are upstream's verbatim; TRUSCA does not execute their jq but implements the same bindings in `services/g7_conformance.py`, and a contract test pins the two label strings against it. |
| `apps/backend/services/regulation_crosswalk.json` | `docker/lib/regulation-crosswalk.json` (v1.8.3 line, sktelecom/bomlens#462) | Product name changed to TRUSCA in the user-facing `disclaimer` / `disclaimer_ko` and in the documentation `note`, since TRUSCA is what serves these strings to its own users. `disclaimer_ko` was additionally reworded for Korean style (same meaning, one clause restructured). The `note` was repointed at TRUSCA paths. The US framework was rewritten for the 2026 minimum elements, taking upstream's `cisa-*` mappings and adding a scope caveat about SPDX submissions. Other framework definitions and their mappings are unchanged. |
| `apps/backend/services/cisa_registry.json` | `docker/lib/cisa-registry.json` | Documentation `note` rewritten to describe TRUSCA's Python evaluator. Korean labels say 컴포넌트 rather than 구성요소, matching TRUSCA's own UI vocabulary. The `evidenceGrade` marker is matched by suffix rather than by upstream's exact `bomlens:` name, so both TRUSCA's and upstream's exports are read. Element definitions, sources and jq expressions are unchanged. |
| `apps/backend/services/license_compat.json` | `docker/lib/license-compat.json` | The `_comment` was rewritten to point at TRUSCA's reader and its class definitions; upstream's names its jq and TypeScript consumers. An `uncategorized` outbound row was added: upstream falls through to a generic "no rule for this combination", and TRUSCA can say the more useful thing, that the declared outbound license itself was not recognised. The verdict vocabulary, the sixteen original matrix cells with their reasoning, and both explicit pairs are unchanged. |
| `apps/backend/services/ai_risk_knowledge.json` | `docker/lib/ai-risk-knowledge.json` | Only the documentation `_note` was rewritten: it named upstream's shell scripts and their tag lint, and now describes TRUSCA's Python evaluator and states which keys TRUSCA reads. The license terms, their ids and match patterns, the verdicts and per-scenario overrides, the conditions and their labels, and both disclaimers are unchanged. `hfLicenseTags`, `datasetTagSignals` and `weightFormats` are kept although TRUSCA evaluates neither of the axes that read them, so the file stays diffable against upstream. |

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
| `apps/backend/services/ai_risk_assessment.py` | `docker/lib/assess-ai-risk.sh` (license and dataset axes) + `docker/lib/ai-risk-knowledge.json` |
| `apps/backend/services/regulation_crosswalk.py` | evaluator for the crosswalk data above |
| `apps/backend/services/obligation_service.py` | `docker/lib/generate-notice.sh` |
| `apps/backend/services/license_texts/__init__.py` | `docker/lib/licenses/` (collection layout) |
| `apps/backend/services/os_context.py` | `docker/lib/enrich-os-context.py` |
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

### EPSS (Exploit Prediction Scoring System)

- **Source**: https://epss.empiricalsecurity.com/epss_scores-current.csv.gz (daily CSV, unauthenticated)
- **Steward**: the EPSS Special Interest Group at FIRST (https://www.first.org/epss)
- **Scores generated and published by**: Empirical Security
- **License**: none declared. FIRST publishes the scores freely and requests attribution; see below
- **Covers**: nothing vendored. Fetched at runtime into `vulnerabilities.epss_score` and `vulnerabilities.epss_percentile`

EPSS estimates the probability that a CVE will be exploited in the next 30
days. TRUSCA stores the score and the percentile per CVE and uses them for the
Vulnerabilities tab's sort and filter, the `sort=priority` ranking, the REST
API, the Excel and PDF reports, and the optional `GATE_EPSS_THRESHOLD` build
gate.

Nothing is redistributed with TRUSCA. Each installation downloads the daily
CSV itself, at most once a day, into its own database; the file is not vendored
into the source tree, the container images, or any release artifact. The
refresh is operator-controlled (`EPSS_REFRESH_ENABLED`, off by default) and can
be pointed at an internal mirror for air-gapped deployments. The bulk CSV is
deliberately the only ingest path: FIRST's own guidance is that the lookup API
"should not be used for bulk downloads or to keep a local copy of all scores in
sync", and that the daily CSV is the right mechanism for that.

FIRST asks that attribution be provided where possible when EPSS data is used
in publications or products. TRUSCA carries that attribution here, in the
data-sources reference page, and in the module docstring of the feed client.
The citation FIRST supplies is:

    Jay Jacobs, Sasha Romanosky, Benjamin Edwards, Michael Roytman,
    Idris Adjerid (2021), Exploit Prediction Scoring System,
    Digital Threats Research and Practice, 2(3)

TRUSCA claims no ownership of this data, and neither FIRST nor Empirical
Security endorses TRUSCA.

### Inter — the interface typeface

- **Source**: https://github.com/rsms/inter/releases/tag/v4.1 (`web/` directory)
- **Copyright**: Copyright (c) 2016 The Inter Project Authors (https://github.com/rsms/inter)
- **License**: SIL Open Font License 1.1 (OFL-1.1)
- **Covers**: `apps/frontend/public/fonts/Inter-{Regular,Medium,SemiBold,Bold}.woff2`

The four weights the interface renders with. Taken from the upstream release
rather than a font CDN: OFL-1.1 §2 requires the copyright notice and the
licence to accompany every copy, and the upstream binaries carry both in their
`name` table (IDs 0 and 13), so each file a browser downloads satisfies the
licence by itself. Whether a CDN's re-subsetted files still carry those
records could not be established.

The licence text is served beside the fonts at `/fonts/Inter-LICENSE.txt`, a
byte-for-byte copy of the release's `LICENSE.txt`. The copy under
`apps/backend/services/license_texts/` is deliberately not reused for this:
it is the text the product shows users about *their* dependencies and carries
no copyright line, which §2 requires.

Inter declares no Reserved Font Name, so nothing here constrains the family
name. "Inter" is a trademark of rsms; naming it is the attribution OFL-1.1 §4
permits, and `font-family: "Inter"` must match the name table to work at all.
Neither is the kind of product-name mention the editorial policy is about.

### JetBrains Mono — the monospace typeface

- **Source**: https://github.com/JetBrains/JetBrainsMono/releases/tag/v2.304 (`fonts/webfonts/`)
- **Copyright**: Copyright 2020 The JetBrains Mono Project Authors (https://github.com/JetBrains/JetBrainsMono)
- **License**: SIL Open Font License 1.1 (OFL-1.1)
- **Covers**: `apps/frontend/public/fonts/JetBrainsMono-{Regular,Medium,SemiBold}.woff2`

The three weights used for identifiers, purls, log output and version
strings. Same provenance reasoning as Inter above, and the licence text is at
`/fonts/JetBrainsMono-OFL.txt`.

JetBrains Mono declares no Reserved Font Name. "JetBrains Mono" is a trademark
of JetBrains s.r.o.; it is named here because OFL-1.1 §2 requires the
attribution and because the family name is functional.

OFL-1.1 §5 keeps modified fonts under OFL. It reaches the font files and
nothing else: TRUSCA remains Apache-2.0.

---

## 2. Tools bundled in the TRUSCA worker image

The Celery worker image (`ghcr.io/trustedoss/trusca-backend-worker`) ships the SCA
toolchain. These are **separate programs**, invoked at arm's length as separate
processes, and each remains under its own license. TRUSCA does not link against
them.

| Tool | Version | License |
|---|---|---|
| cdxgen | 12.3.3 | Apache-2.0 |
| scancode-toolkit | 32.4.0 | Apache-2.0 AND CC-BY-4.0 |
| scanoss.py | 1.53.1 | MIT |
| Trivy | 0.72.0 | Apache-2.0 |
| cosign | 3.1.2 | Apache-2.0 |
| govulncheck (`golang.org/x/vuln`) | v1.5.0 | BSD-3-Clause |
| Docker CLI (client only) | 29.9.0 | Apache-2.0 |
| Go toolchain | 1.26.9 | BSD-3-Clause |
| Node.js | 20.x (NodeSource `nodejs`) | MIT |
| npm | 11.18.0 | Artistic-2.0 |
| Gradle | 8.14.6 | Apache-2.0 |
| Apache Maven | Debian `maven` (3.8.x) | Apache-2.0 |
| Composer | Debian `composer` | MIT |
| PHP CLI | Debian `php-cli` (8.2) | PHP-3.01 |
| Ruby (MRI) | Debian `ruby` (3.1) | Ruby OR BSD-2-Clause |
| Bundler | Debian `ruby-bundler` | MIT |
| Cargo / Rust | Debian `cargo` | MIT OR Apache-2.0 |
| .NET SDK | 8.0 | MIT |
| PostgreSQL client | 17 | PostgreSQL |
| Python | 3.12.14 | PSF-2.0 |
| **Eclipse Temurin JDK** | **21** | **GPL-2.0 WITH Classpath-exception-2.0** |

scancode-toolkit's code is licensed under Apache-2.0 and its license and
copyright detection data under CC-BY-4.0
(https://creativecommons.org/licenses/by/4.0/legalcode). Attribution, as the
upstream `NOTICE` and `README.rst` give it:

```
Copyright (c) nexB Inc. and others. All rights reserved.
ScanCode is a trademark of nexB Inc.
SPDX-License-Identifier: Apache-2.0 AND CC-BY-4.0
See https://github.com/nexB/scancode-toolkit for support or download.
See https://aboutcode.org for more information about nexB OSS projects.
```

scancode-toolkit runs from its own virtualenv (`/opt/scancode-venv`), and some
of the Python packages installed there with it carry LGPL or GPL-family terms.
They are dependencies of scancode-toolkit, not of TRUSCA, and TRUSCA reaches
them only by running `scancode` as a separate process.

| Component (in `/opt/scancode-venv`) | Version | License | Form |
|---|---|---|---|
| **p7zip** (`7z`, `7z.so`, from the `extractcode-7z` wheel) | **16.02** (wheel 16.5.210531) | **LGPL-2.1-or-later, with the unRAR restriction** | prebuilt binaries, amd64 image only |
| **intbitset** | **4.1.2** | **LGPL-3.0-or-later** | C extension |
| **chardet** | **5.2.0** | **LGPL-2.1-or-later** | pure Python |
| gemfileparser2 | 0.9.4 | GPL-3.0-or-later OR MIT | pure Python |
| text-unidecode | 1.3 | Artistic License OR GPL (per its package metadata) | pure Python |

The `extractcode-7z` wheel declares its license as `apache-2.0 AND lgpl-2.1
and unrar and brian-gladman-3-clause`: Apache-2.0 for its Python wrapper, the
rest for the p7zip binaries. The p7zip license texts (`License.txt`,
`copying.txt`, `unRarLicense.txt`) ship inside the wheel at
`/opt/scancode-venv/lib/python3.12/site-packages/extractcode_7z/licenses/p7zip/`.
On arm64 the `extractcode-7z` wheel (16.5.210525) contains no binary, and the
arm64 image has no `7z`.

### LGPL corresponding source (worker image)

The source for the LGPL object code listed above is inside the worker image, on
both architectures, in `/usr/share/trusca/sources/`:

| File | Covers | Upstream |
|---|---|---|
| `p7zip-16.02/p7zip_16.02_src_all.tar.bz2` | `7z`, `7z.so` (release source) | https://downloads.sourceforge.net/project/p7zip/p7zip/16.02/p7zip_16.02_src_all.tar.bz2 (project page https://sourceforge.net/projects/p7zip/) |
| `p7zip-16.02/p7zip_16.02+dfsg-6.debian.tar.xz` | `7z`, `7z.so` (patches `12-CVE-2016-9296.patch`, `13-CVE-2017-17969.patch`) | https://snapshot.debian.org/archive/debian/20180601T000000Z/pool/main/p/p7zip/p7zip_16.02%2Bdfsg-6.debian.tar.xz |
| `p7zip-16.02/15-Enhanced-encryption-strength.patch.zip` | `7z`, `7z.so` (AES patch, applied with `-p4`) | https://github.com/aonez/Keka/files/2940620/15-Enhanced-encryption-strength.patch.zip |
| `p7zip-16.02/README` | how the files above fit together | written for the image |
| `intbitset-4.1.2/intbitset-4.1.2.tar.gz` | intbitset C extension | https://pypi.org/project/intbitset/4.1.2/ (repository https://github.com/inveniosoftware-contrib/intbitset) |

The p7zip binaries were built by the `extractcode-7z` maintainers, not by
TRUSCA. The `INSTALL_RECEIPT.json` in the wheel's license directory names the
Homebrew formula `p7zip`, stable version 16.02. The closest public record of
that build is the formula at revision 2, Homebrew/homebrew-core commit
`80dcd3648433f2cd9956871b3b393953ff3d54c2` (`Formula/p7zip.rb`). It builds from
the release tarball and applies the two Debian patches and the AES patch
listed above; the bundled files carry the sha256 values that formula pins. The
image build checks that the shipped `7z` reports p7zip 16.02. The wheel's
packaging is in https://github.com/aboutcode-org/scancode-plugins
(`builtins/extractcode_7z-linux`).

chardet is pure Python: the installed modules in
`/opt/scancode-venv/lib/python3.12/site-packages/chardet/` are its source.
Upstream is https://pypi.org/project/chardet/5.2.0/. The worker image's system
Python also has chardet 6.0.0.post1 (LGPL-2.1-or-later, pure Python, in
`/usr/local/lib/python3.12/site-packages/chardet/`), installed as a dependency
of the test tool diff-cover; upstream is https://pypi.org/project/chardet/6.0.0.post1/.

### GPL source offer (worker image)

The worker image includes **Eclipse Temurin JDK 21**, licensed under GPL-2.0
with the Classpath Exception. The Classpath Exception means code that merely
runs on or links against the JDK — including TRUSCA — is not itself placed
under the GPL. The JDK's own source remains available under the GPL:

- Binaries as shipped: https://packages.adoptium.net/artifactory/deb (`temurin-21-jdk`)
- Corresponding source: https://github.com/adoptium/temurin21-binaries and
  https://github.com/openjdk/jdk21u

The JDK is present only so cdxgen can shell into Maven and Gradle for Java
dependency enumeration.

Apart from the JDK and the scancode dependencies listed above, the GPL-family
software in TRUSCA images is the operating system of the base images: Debian
bookworm packages in the backend and worker images, and Alpine Linux packages
in the frontend image. On Debian each package's license is at
`/usr/share/doc/<package>/copyright`, and the distributions publish the
source: https://sources.debian.org and https://gitlab.alpinelinux.org/alpine/aports.

---

## 3. Tools bundled in other TRUSCA images

**Backend API image** (`ghcr.io/trustedoss/trusca-backend`): Python 3.12
(PSF-2.0) on Debian bookworm-slim, plus `tini` (MIT), `curl` (curl), and the
Python dependencies pinned in `apps/backend/requirements.txt`.

**Frontend image** (`ghcr.io/trustedoss/trusca-frontend`): nginx 1.27.3
(BSD-2-Clause) on Alpine Linux, serving a static bundle built from the npm
dependencies pinned in `apps/frontend/package-lock.json`.

---

## 4. Images TRUSCA runs but does not distribute

With `SCAN_ENGINE=bomlens`, the engine worker pulls the images below when a
scan needs them and runs each as a separate container through the Docker API.
TRUSCA does not link against them, and they are not part of any TRUSCA image,
the Helm chart, or the offline install bundle. Each image carries its own
license terms. Anyone who mirrors these images and passes them on to others
takes on the license obligations of each image.

**BomLens**: `ghcr.io/sktelecom/bomlens` (the execution plan and stage 2)
and `ghcr.io/sktelecom/bomlens-deep-license` (stage 2 when scancode
detection is on). The tag and digest TRUSCA runs by default are
`BOMLENS_IMAGE_DEFAULT` and `BOMLENS_DEEP_LICENSE_IMAGE_DEFAULT` in
`apps/backend/core/config.py`.

- Source: https://github.com/sktelecom/bomlens
- License: Apache-2.0
- BomLens's `LICENSE`, `NOTICE` and `THIRD_PARTY_LICENSES.md` are inside both
  images at `/usr/local/lib/sbom/notices/`. `THIRD_PARTY_LICENSES.md` lists the
  tools each image bundles and their licenses.
- The deep-license image adds scancode-toolkit (Apache-2.0 AND CC-BY-4.0) and
  LGPL components (7-Zip on linux/amd64, intbitset, chardet). The source for
  7-Zip and intbitset is inside that image at `/usr/share/bomlens/sources/`;
  chardet is installed as Python source.
- TRUSCA does not run the BomLens firmware image.

**cdxgen language images**: stage 1 runs one image from `ghcr.io/cdxgen/`,
chosen by the execution plan and pinned by digest. The images and digests a
BomLens release can choose are listed in `/usr/local/lib/sbom/cdxgen-images.json`
inside the BomLens image.

- Source: https://github.com/CycloneDX/cdxgen
- License: Apache-2.0 for cdxgen. Each image is built on a different base
  image, and the base image's terms apply to its contents. For example,
  `cdxgen-python312` is built on a SUSE BCI image and carries the SUSE BCI
  license (its `com.suse.eula` label is `sle-bci`).

---

## 5. Application dependencies

TRUSCA's own Python and npm dependency trees run to several hundred packages
and are not enumerated here — a hand-maintained list would drift from the
lockfiles within one release. The authoritative inventory is the CycloneDX SBOM
published with every release (`trusca-<version>.cdx.json`, attached to the
GitHub release and signed with cosign). Lockfiles in the source tree are
`apps/backend/requirements.txt` and `apps/frontend/package-lock.json`.

TRUSCA can also generate this inventory for itself — the `sca-self` and
`dogfood-scan` CI workflows scan this repository with the product.
