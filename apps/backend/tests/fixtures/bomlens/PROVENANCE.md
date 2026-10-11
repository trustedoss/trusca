# Provenance of the BomLens engine fixtures

Every file here is unedited output of the published BomLens image, captured on
2026-10-11. Nothing was written or changed by hand. Tests that need a variant
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

## When to recapture

When the pinned BomLens image changes. A new image can rename steps or change
the SBOM it writes; the unit test
`test_every_step_and_reason_bomlens_wrote_is_one_this_reader_knows` reads these
manifests, so recapturing is also how a renamed step is found.
