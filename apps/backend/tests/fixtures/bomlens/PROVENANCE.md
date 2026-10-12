# Provenance of the BomLens engine fixtures

Every file here is unedited output of a published BomLens image, captured on
2026-10-11 and 2026-10-12, except the one derived case listed at the end.
Three images were used; each case says which. Tests that need a variant
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

### Contract 1.2.0 image

| | |
|---|---|
| Image | `ghcr.io/haksungjang/bomlens:sha-1e5ef6b` |
| Digest | `sha256:eeb851f7cb4e3bc1cd7c81151e0f9a1bd129dfd755bfb92e110d17653b8f9a36` (index; linux/arm64 manifest `sha256:5ce76bab7cf602f1babc7589d464d672a38434a70b4045003774967b4a92ee99` ran) |
| Source commit | `1e5ef6b7` on the fork's `main` (engine contract `1.2.0`, BomLens #186 to #207) |
| Trivy in the image | 0.74.0 |
| Trivy database | schema 2, `UpdatedAt` 2026-10-11T07:05Z, mounted read-only at `/trivy-db` (the plan's `trivy-db` role) |

Used for `image-os-licenses`, `source-trivy-db` and
`source-trivy-db-stale`, captured 2026-10-12 (2026-10-11 23:00 UTC), each run
with `BOMLENS_IMAGE_REF` and `BOMLENS_IMAGE_DIGEST` set to the tag and index
digest above and `HOST_UID`/`HOST_GID` set to the capturing user.

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

### Contract 1.2.0 cases

Every run used `-e UPLOAD_ENABLED=false -e GENERATE_SECURITY=true
-e SECURITY_ENRICH=false -e HOST_OUTPUT_DIR=/host-output -w /host-output`, the
output folder at `/host-output` and a Trivy cache folder at `/trivy-db`
(`--mount type=bind,src=<trivy cache>,dst=/trivy-db,readonly`).

| Case | Input | How it was run | Result |
|---|---|---|---|
| `image-os-licenses` | `gcr.io/distroless/static-debian12:nonroot@sha256:afa5c872c891853ca7fcf1f12c3edb23f7eeef36189728842dd51042ff57f7ab` | `-e MODE=IMAGE -e TARGET_IMAGE=<the image above> -e PROJECT_NAME=image -e PROJECT_VERSION=1.0.0 -e BOMLENS_TRIVY_DB_MAX_AGE_DAYS=9`, no docker socket (syft and Trivy read the image from the registry), default network | `succeeded`; `os-licenses` ok with `replaced` 5, `hashes_dropped` 0: the five Debian packages carry Trivy's licences |
| `source-trivy-db` | `node` | Stage 1 as for `source-ok`, but running `build-prep.sh` from the `node-ok` plan capture (`tests/fixtures/bomlens-plan/`): `docker run --rm -v <src>:/app -v <out>:/out -v build-prep.sh:/tmp/build-prep.sh:ro -e HOME=/tmp/sbomhome -e FETCH_LICENSE=false -e PROJECT_NAME=node -e PROJECT_VERSION=1.0.0 --entrypoint sh ghcr.io/cdxgen/cdxgen-alpine-node24:13.2.0@sha256:1e46a41c... /tmp/build-prep.sh /app /out/node_1.0.0_bom.json 1.6`, its output saved as `bomlens-stage1.log`. Then stage 2 with `--network none`, the source read-only at `/src`, `-e MODE=POSTPROCESS -e BOMLENS_RUN_INPUT=node_1.0.0_bom.json -e PROJECT_NAME=node -e PROJECT_VERSION=1.0.0 -e BOMLENS_STAGE1_EXIT=0 -e BOMLENS_STAGE1_OOM=false -e BOMLENS_STAGE1_LOG=bomlens-stage1.log -e BOMLENS_TRIVY_DB_MAX_AGE_DAYS=9` | `succeeded`; 1 component, 3 CVEs; `scan-security` ok |
| `source-trivy-db-stale` | `node` | The same stage 1 output and stage 2 command, with a different folder at `/trivy-db`: the same `trivy.db` (a hard link) beside a `db/metadata.json` whose `UpdatedAt` is 2026-09-30T07:05Z | `succeeded_degraded`; the same 3 CVEs in the report, but `scan-security` degraded (`unavailable`, "the Trivy database is 11 days old (over 9)") |

The input of `source-trivy-db-stale` was changed on purpose: the database
date is the one thing that differs between the two source cases. The output
is the engine's, unedited.

## Derived case

`source-ok-results-stripped` is `source-ok` with one change: the `Results`
key was removed from `node_1.0.0_security.json` (re-serialised with two-space
indent) and the two manifest entries for that file got the new `sha256` and
`bytes`. Everything else, including the SBOM and every other manifest field,
is the captured run. It stands for a report that is well-formed JSON from
Trivy's schema but carries no result list, which must fail the scan rather
than persist as zero vulnerabilities.

## When to recapture

When the pinned BomLens image changes. A new image can rename steps or change
the SBOM it writes; the unit test
`test_every_step_and_reason_bomlens_wrote_is_one_this_reader_knows` reads these
manifests, so recapturing is also how a renamed step is found.
