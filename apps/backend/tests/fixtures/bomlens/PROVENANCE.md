# Provenance of the BomLens engine fixtures

Every file here is unedited output of a published BomLens image, captured on
2026-10-11, 2026-10-12 and 2026-10-15, except the two derived cases listed at
the end.
Four images were used; each case says which. Tests that need a variant
(an unknown contract major, a step status rewritten to test one defence on its
own, a tampered SBOM) copy a case into a temporary directory and change the
copy.

## Image

| | |
|---|---|
| Image | `ghcr.io/haksungjang/bomlens:sha-b826d43` |
| Digest | `sha256:1de6f4d0c8969116347af5d55d69a61b38f3670eda819a63e09a197a53f1ad57` |
| Source commit | `b826d438` on the fork's `main` (engine contract `1.0.0-dev`) |
| Trivy in the image | 0.74.0, database schema 2 updated 2026-10-11T07:05Z |

Each run passed `BOMLENS_IMAGE_REF` and `BOMLENS_IMAGE_DIGEST` with the values
above, so every manifest repeats them.

### Deep-license image

| | |
|---|---|
| Image | `ghcr.io/haksungjang/bomlens-deep-license:sha-5a93586` |
| Digest | `sha256:df45f26755a8c2154a1828dc5009457a76e1fde61f10527c51d933f2231fbc44` (index; linux/arm64 manifest `sha256:ba99f6c38a2d24e2a7058f25db6bbd77daa54ee5766ca2f726aaea0080752727` ran) |
| Source commit | `5a93586f` on the fork's `main` (engine contract `1.0.0`, BomLens #194) |
| scancode in the image | 32.5.0 |

Used for `source-deep-license` and `source-no-components`, both run with
`BOMLENS_IMAGE_REF` and `BOMLENS_IMAGE_DIGEST` set to the tag and index digest
above. The image was pulled by digest (`docker pull
ghcr.io/haksungjang/bomlens-deep-license@sha256:df45...`) and run by digest.

### Contract 1.3.0 images

The BomLens v1.13.5 release, both images built from the same commit:

| | |
|---|---|
| Image | `ghcr.io/sktelecom/bomlens:1.13.5` |
| Digest | `sha256:d05b2f4f2542ba1bc98fd07712fd4308f40205dd9ad28b7a8d15ae614b4b4128` (index; linux/arm64 manifest `sha256:d0c74bf76a5e03a01605c2f06a29d336d5f5a7dbe7d8339a2c3aef8dc3a4ba53` ran) |
| Deep-license image | `ghcr.io/sktelecom/bomlens-deep-license:1.13.5` |
| Deep-license digest | `sha256:d33b4e96041cb346deba2986759f3424e4919642ed54375142c0b656a22d3d37` (index; linux/arm64 manifest `sha256:d715a8ba78c8a44ee50d77518691691e93b1b626b1d979ae40d101fba1267085` ran) |
| Source commit | image label `org.opencontainers.image.revision=21874a5f`, the commit the `v1.13.5` tag in `sktelecom/bomlens` names (engine contract `1.3.0`) |
| Trivy in the image | 0.75.0 |
| scancode in the deep-license image | 32.5.0 |
| Trivy database | schema 2, `UpdatedAt` 2026-10-15T01:13Z (downloaded with Trivy 0.71.2, `trivy image --download-db-only`; the same database the `1.13.4` captures used, so the findings are comparable), mounted read-only at `/trivy-db` (the plan's `trivy-db` role) |

Used for `image-os-licenses`, `source-trivy-db`, `source-trivy-db-stale`,
`image-registry-alpine` (the base image) and `source-deep-license-trivy-db`
(the deep-license image), captured 2026-10-15 18:02 to 18:04 UTC, each run
with `BOMLENS_IMAGE_REF` and `BOMLENS_IMAGE_DIGEST` set to the tag and index
digest of the image that ran and `HOST_UID`/`HOST_GID` set to the capturing
user. Stage 1 of the source cases ran `build-prep.sh` and
`identify-conda.py` from the `1.13.5` `node-ok` plan capture in
`ghcr.io/cdxgen/cdxgen-alpine-node24:13.3.0@sha256:9e9cbff2...`, the image
that plan names, with the plan's `MAVEN_OPTS`.

All five replace captures of the same cases from the `1.13.4` release
(`sha256:59d5a336...` and `sha256:182345b5...`, same database, captured
2026-10-15 05:10 to 05:12 UTC). The steps, statuses, reasons, counts,
details, SBOM components, licences and Trivy findings are the same. The
differences are the image fields in every manifest and SBOM (`bomlens`
tool version `1.13.5`), the run times, the SBOM serial numbers and
timestamps, the Trivy report IDs and creation times, and with them the
output digests in the manifests. The manifest layout, step list and
`data_sources` have no new keys. Stage 1 of the source cases ran with the
plan's new `GOPATH`, `GOMODCACHE` and `GOCACHE` under `/tmp/sbomhome`.

Before that, they replaced captures of the same cases from the `1.13.2` release
(`sha256:e357e634...` and `sha256:516f41c9...`, database `UpdatedAt`
2026-10-11T19:06Z, captured 2026-10-12 07:33 to 07:35 UTC and, for
`image-registry-alpine`, 14:52 UTC). The steps, statuses, reasons, counts
and details are the same, including "the Trivy database is 12 days old (over
9)" for the stale case. So are the SBOM components, their PURLs and
licences, and the Trivy findings: 955 SBOM entries and one advisory
(`DLA-4792-1`) for the distroless image, 15 packages and 48 findings for
Alpine, one component with the same three CVEs for `node`. The differences:

- Every manifest says contract `1.3.0` and lists two new steps, both
  `disabled`: `deep-cve` and, in the source cases, `reachability`. The two
  image manifests also record `input.image` (`source` `registry`, the
  `linux/arm64/v8` manifest digest syft read, and `index_digest`), and every
  manifest has `data_sources.trivy_db`.
- `image-os-licenses`: `vulnerabilities.trivy` is now
  `image_1.0.0_security_trivy.json`, Trivy's report as it came, and is kept
  in place of `image_1.0.0_security.json`. BomLens' combined report
  (`vulnerabilities.all`, not kept) moves `DLA-4792-1` to a new `Advisories`
  list because it names no CVE; Trivy's report still lists it as a finding,
  and that is the report TRUSCA reads.
- The source SBOMs' `bomlens:excluded-paths` adds `**/gradle/wrapper/**`, and
  `tools` reads cdxgen `13.3.0`, Trivy `0.75.0`, syft `1.54.1`.
- The image fields, the run times, the SBOM serial numbers and timestamps,
  the Trivy report IDs and with them the output digests in the manifests.

Before that, the `1.13.2` captures had replaced ones from `sha-34010b6`
(`sha256:019d7126...`) and `sha-1e5ef6b` (`sha256:eeb851f7...`) with the
same result apart from the image fields, the run times and the number of
days in the stale database's detail.

## What is kept from each run

Only the files the adapter reads: `bomlens-result.json`, the `sbom.cyclonedx`
output and the `vulnerabilities.trivy` output. The manifests also list files
that are not stored here (notice-style reports, the file list, the source
snapshot, conformance results). The adapter resolves outputs by role and
checks the digest only of the files it reads, so the missing ones are never
opened.

## Cases

Source fixtures come from `tests/e2e/golden/fixtures/` in this repository.
Every run used `-e UPLOAD_ENABLED=false -e GENERATE_SECURITY=true
-e SECURITY_ENRICH=false -e HOST_OUTPUT_DIR=/host-output`.

| Case | Input | How it was run | Result |
|---|---|---|---|
| `source-ok` | `node` | The two-stage path the BomLens CLI uses: stage 1 `ghcr.io/cdxgen/cdxgen-alpine-node24:13.2.0` running BomLens' `docker/lib/build-prep.sh` from `b826d438` (`sh /tmp/build-prep.sh /app /out/node_1.0.0_bom.json 1.6`), then the image with `MODE=POSTPROCESS -e BOMLENS_RUN_INPUT=node_1.0.0_bom.json -w /host-output` | `succeeded`; 1 component, 3 CVEs on it |
| `image-ok` | `alpine:3.19.0` | `MODE=IMAGE -e TARGET_IMAGE=alpine:3.19.0`, docker socket mounted | `succeeded`; 15 packages, 48 CVEs on 7 of them, plus 80 file entries and the distribution |
| `source-degraded` | `multi-component` | `MODE=SOURCE -e DEEP_LICENSE=true`, no docker socket | `succeeded_degraded`; `generate` degraded (`fallback`, the manifest-only reader), `deep-license` skipped (`not_installed`, the base image has no scancode) |
| `source-vulnerabilities-failed` | `multi-component` | `MODE=SOURCE`, `--network none`, empty Trivy cache | `succeeded_degraded`; `scan-security` failed, the report carries Trivy's `ScanError` |
| `source-run-failed` | `multi-component` | `MODE=SOURCE` with the source mounted read-only | `failed` (exit 1); `generate` failed, no outputs |

`source-run-failed` contains the manifest only, because the run wrote nothing
else.

### Deep-license image cases

Both use the same two-stage path as `source-ok`: stage 1 runs
`docker/lib/build-prep.sh` from `5a93586f` in the language image
(`docker run --rm -v <src>:/app -v <out>:/out -v build-prep.sh:/tmp/build-prep.sh:ro
--entrypoint sh <cdxgen image> /tmp/build-prep.sh /app /out/<name>_1.0.0_bom.json 1.6`),
then the deep-license image runs `-e MODE=POSTPROCESS -e PROJECT_NAME=<name>
-e PROJECT_VERSION=1.0.0 -e BOMLENS_RUN_INPUT=<name>_1.0.0_bom.json -e
UPLOAD_ENABLED=false -e GENERATE_SECURITY=true -e SECURITY_ENRICH=false -e
HOST_OUTPUT_DIR=/host-output -v <out>:/host-output -v <src>:/src -w
/host-output`, with network access for the Trivy database. The licence
registry step ran with a zero-second budget, so it waited for no answer.

| Case | Input | Stage-2 settings | Result |
|---|---|---|---|
| `source-deep-license` | `scancode-mixed-policy` from `tests/e2e/golden/fixtures/`, with two changes to the input: `"license": "MIT"` removed from lodash in `package-lock.json` (so the registry step has a component to ask about) and a `test/package.json` added that declares `mocha` 10.2.0 as a dev dependency (so the non-shipped exclusion has a manifest to leave out). Stage 1: `ghcr.io/cdxgen/cdxgen-alpine-node24:13.2.0` | `-e DEEP_LICENSE=true -e LICENSE_REGISTRY=true -e LICENSE_REGISTRY_BUDGET=0` | `succeeded_degraded`; 1 component, 3 CVEs; `deep-license` ok (Apache-2.0, GPL-3.0-or-later and MPL-2.0 in the three source files, paths relative to the scanned tree); `license-registry` degraded `budget_exhausted` with `budget_reached` 1; the SBOM names `test/package.json` in `bomlens:excluded-manifests` |
| `source-no-components` | A lock-less Poetry `pyproject.toml` (the `python-poetry` golden fixture with `requests = "^2.31"` in place of the exact pin). Stage 1: `ghcr.io/cdxgen/cdxgen-python312:13.2.0`. cdxgen left a `poetry.lock`, `deps.slices.json` and `.chen/` in the source folder; they were removed before stage 2 so it saw the source as committed | defaults | `succeeded_degraded`; 0 components; `generate` degraded `no_components` ("manifests found: pyproject.toml") and `bomlens:sbom-empty-with-manifests` on the SBOM |

`source-deep-license` keeps the scancode result (`license-scan.scancode`) as
well as the SBOM and the Trivy report.

### Contract 1.3.0 cases

Every run used `-e UPLOAD_ENABLED=false -e GENERATE_SECURITY=true
-e SECURITY_ENRICH=false -e HOST_OUTPUT_DIR=/host-output -w /host-output`, the
output folder at `/host-output` and a Trivy cache folder at `/trivy-db`
(`--mount type=bind,src=<trivy cache>,dst=/trivy-db,readonly`).

| Case | Input | How it was run | Result |
|---|---|---|---|
| `image-os-licenses` | `gcr.io/distroless/static-debian12:nonroot@sha256:afa5c872c891853ca7fcf1f12c3edb23f7eeef36189728842dd51042ff57f7ab` | `-e MODE=IMAGE -e TARGET_IMAGE=<the image above> -e PROJECT_NAME=image -e PROJECT_VERSION=1.0.0 -e BOMLENS_TRIVY_DB_MAX_AGE_DAYS=9`, no docker socket (syft and Trivy read the image from the registry), default network | `succeeded`; `os-licenses` ok with `replaced` 5, `hashes_dropped` 0: the five Debian packages carry Trivy's licences |
| `source-trivy-db` | `node` | Stage 1 as for `source-ok`, but running `build-prep.sh` from the `node-ok` plan capture (`tests/fixtures/bomlens-plan/`): `docker run --rm -v <src>:/app -v <out>:/out -v build-prep.sh:/tmp/build-prep.sh:ro -v identify-conda.py:/tmp/identify-conda.py:ro -e HOME=/tmp/sbomhome -e MAVEN_OPTS="-Duser.home=/tmp/sbomhome -Dmaven.repo.local=/tmp/sbomhome/.m2/repository" -e GOPATH=/tmp/sbomhome/go -e GOMODCACHE=/tmp/sbomhome/go/pkg/mod -e GOCACHE=/tmp/sbomhome/go-build -e FETCH_LICENSE=false -e PROJECT_NAME=node -e PROJECT_VERSION=1.0.0 --entrypoint sh ghcr.io/cdxgen/cdxgen-alpine-node24:13.3.0@sha256:9e9cbff2... /tmp/build-prep.sh /app /out/node_1.0.0_bom.json 1.6`, its output saved as `bomlens-stage1.log`. Then stage 2 with `--network none`, the source read-only at `/src`, `-e MODE=POSTPROCESS -e BOMLENS_RUN_INPUT=node_1.0.0_bom.json -e PROJECT_NAME=node -e PROJECT_VERSION=1.0.0 -e BOMLENS_STAGE1_EXIT=0 -e BOMLENS_STAGE1_OOM=false -e BOMLENS_STAGE1_LOG=bomlens-stage1.log -e BOMLENS_TRIVY_DB_MAX_AGE_DAYS=9` | `succeeded`; 1 component, 3 CVEs; `scan-security` ok |
| `source-trivy-db-stale` | `node` | The same stage 1 output and stage 2 command, with a different folder at `/trivy-db`: the same `trivy.db` (a hard link) beside a `db/metadata.json` whose `UpdatedAt`, `NextUpdate` and `DownloadedAt` are twelve days earlier (`UpdatedAt` 2026-10-03T01:13:01Z) | `succeeded_degraded`; the same 3 CVEs in the report, but `scan-security` degraded (`unavailable`, "the Trivy database is 12 days old (over 9)") |

| `source-deep-license-trivy-db` | `scancode-mixed-policy` with the two input changes described for `source-deep-license` (`"license": "MIT"` removed from lodash in `package-lock.json`, a `test/package.json` declaring `mocha` 10.2.0 as a dev dependency) | Stage 1 as for `source-trivy-db`, with `PROJECT_NAME=mixed` and `/out/mixed_1.0.0_bom.json`. Stage 2 as for `source-trivy-db` (`--network none`, the fresh database at `/trivy-db`, `PROJECT_NAME=mixed`, `BOMLENS_RUN_INPUT=mixed_1.0.0_bom.json`), run on the deep-license image with `-e DEEP_LICENSE=true -e LICENSE_REGISTRY=true -e LICENSE_REGISTRY_BUDGET=0` | `succeeded_degraded`; 1 component, 3 CVEs; `deep-license` ok with the same three detections as `source-deep-license`; `license-registry` degraded `budget_exhausted` with `budget_reached` 1; `bomlens:excluded-manifests` names `test/package.json` |

The input of `source-trivy-db-stale` was changed on purpose: the database
date is the one thing that differs between the two source cases. The output
is the engine's, unedited.

`source-deep-license-trivy-db` is the contract 1.3.0 counterpart of
`source-deep-license`: the same input and settings, run on the pinned
deep-license image, without a network and with the database mounted as the
runner mounts it. The steps, statuses, reasons and counts in its manifest
are the same as in `source-deep-license`, and so are the SBOM's component and
properties, the scancode detections and the three CVEs. It keeps the scancode
result as well as the SBOM and the Trivy report.

### Container image scan as the runner runs it

`image-registry-alpine` was captured on 2026-10-15 at 18:03 UTC with the
contract 1.3.0 image above (its linux/arm64 manifest ran), run the way
`BomLensRunner.run_image` runs it for `SCAN_CONTAINER_ENGINE=bomlens`: no
Docker socket, the capturing user rather than root, a read-only root and no
capability.

| Case | Input | How it was run | Result |
|---|---|---|---|
| `image-registry-alpine` | `alpine:3.19.0@sha256:51b67269f354137895d43f3b3d810bfacd3945438e94dc5ac55fdac340352f48` (the index digest; syft read the linux/arm64 image) | `docker run --rm --read-only --user <uid>:<gid> --cap-drop ALL --security-opt no-new-privileges --pids-limit 4096 --memory 4g --memory-swap 4g --cpus 2 --network bridge --mount type=bind,src=<out>,dst=/host-output --mount type=bind,src=<tmp>,dst=/tmp --mount type=bind,src=<trivy cache>,dst=/trivy-db,readonly --workdir /host-output -e MODE=IMAGE -e TARGET_IMAGE=registry:<the image above> -e PROJECT_NAME=image -e PROJECT_VERSION=1.0.0 -e HOST_OUTPUT_DIR=/host-output -e UPLOAD_ENABLED=false -e BOMLENS_IMAGE_REF=ghcr.io/sktelecom/bomlens:1.13.5 -e BOMLENS_IMAGE_DIGEST=sha256:d05b2f4f... -e GENERATE_SECURITY=true -e SECURITY_ENRICH=false -e DEEP_LICENSE=false -e TRIVY_CACHE_DIR=/trivy-db -e TRIVY_SKIP_DB_UPDATE=true -e TRIVY_SKIP_JAVA_DB_UPDATE=true -e TRIVY_OFFLINE_SCAN=true -e TRIVY_NO_PROGRESS=true -e BOMLENS_TRIVY_DB_MAX_AGE_DAYS=16 -e HOST_UID=<uid> -e HOST_GID=<gid> ghcr.io/sktelecom/bomlens:1.13.5@sha256:d05b2f4f...`. The Trivy database at `/trivy-db` is the one in the table above | `succeeded`; 15 packages with their licences, 80 file entries and the distribution in the SBOM; 48 findings of 21 CVEs on 7 packages; the Trivy report's `Metadata.OS` is Alpine 3.19.0 with `EOSL` true; `os-licenses` ok with nothing to replace (no Debian package); the file view was skipped ("no reachable Docker daemon") |

The input appears as `input.target` in the manifest, with the `registry:`
scheme the runner adds. Only the three files the adapter reads were kept.

## Derived cases

`source-no-components-failed-step` is `source-no-components` with one change:
the `generate` step's `detail` reads "manifests found: pyproject.toml; steps
that failed: gradle-dependencies" instead of "manifests found:
pyproject.toml". That is the format BomLens #209 (commit `34010b6b`) writes
when a source that declares dependencies yields none and build-prep stamped a
failed resolver step on the SBOM: `docker/entrypoint.sh` records `generate
degraded no_components "manifests found: <manifests>; steps that failed:
<steps>"`, and the string here is the one `tests/test-engine-contract.sh`
asserts in that commit, verbatim. The manifest is not hashed, so no other
field changes; the contract version and image fields still name the image
that wrote the rest of the run.

It stayed derived after the move to `sha-34010b6` because no unmodified input
was found that makes that image write the detail. The detail needs a source
that declares dependencies, an SBOM with no component, and a resolver step
build-prep marked as failed. The case that motivated #209 no longer gives
one: under the runner's read-only root the Gradle golden fixture now
resolves (see `tests/fixtures/bomlens-plan/PROVENANCE.md`). The same fixture
with stage 1 run without a network did not give one either: build-prep
recorded `gradle-dependencies` as applied and cdxgen still found guava. The
two ways left were running the older image's stage 1 before this image's
stage 2, or writing a build file made to fail; both would make the capture
something other than a run of the pinned images on a real input, so neither
was used.

`source-ok-results-stripped` is `source-ok` with one change: the `Results`
key was removed from `node_1.0.0_security.json` (re-serialised with two-space
indent) and the two manifest entries for that file got the new `sha256` and
`bytes`. Everything else, including the SBOM and every other manifest field,
is the captured run. It stands for a report that is well-formed JSON from
Trivy's schema but carries no result list, which must fail the scan rather
than persist as zero vulnerabilities.

## Container PURLs from the engine comparison run

`engine-comparison-container-purls.json` is not a BomLens output folder. It
holds the component PURLs the portal API returned for the same three images
scanned once by each container engine, in the engine comparison run of
`.github/workflows/golden-nightly.yml` (dispatch run 38290325193, 2026-10-12,
BomLens `1.13.2`). For each image the file keeps the image reference, the
component count and the PURL list each capture recorded: the first page of
the project's component list (50 entries), sorted, as
`tests/e2e/golden/run_golden.py` writes it. Both pages hold the same 50
packages, with or without qualifiers. The BomLens lists are what that
engine stored before qualifiers were dropped for container images
(`?arch=...&distro=debian-12.7`, `&epoch=1`); the in-process lists have none.
Nothing else from the run was kept; it did not include either engine's SBOM.

## When to recapture

When the pinned BomLens image changes. A new image can rename steps or change
the SBOM it writes; the unit test
`test_every_step_and_reason_bomlens_wrote_is_one_this_reader_knows` reads these
manifests, so recapturing is also how a renamed step is found.
