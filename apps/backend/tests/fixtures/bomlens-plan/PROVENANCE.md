# Provenance of the BomLens execution plan fixtures

Every `bomlens-plan.json` and `bomlens-result.json` here is unedited output of
the published BomLens image, captured on 2026-10-12 (07:32 UTC) with a plain `docker run`
(no compose). Tests that need a hostile or broken plan copy a capture and
change the copy (`tests/unit/integrations/bomlens/test_plan.py`).

## Image

| | |
|---|---|
| Image | `ghcr.io/sktelecom/bomlens:1.13.2` (the BomLens v1.13.2 release) |
| Index digest | `sha256:e357e634ef6447ed44b57a4aed3090af12b77c1fe29cbc0e567a3f65414f738b` |
| Source commit | image label `org.opencontainers.image.revision=53bbd521` (BomLens #210 to #214 on top of `34010b6b`); the `v1.13.2` tag in `sktelecom/bomlens` has the same tree. Image label `com.sktelecom.sbom.engine-contract=1.2.0` |
| Platform pulled | `linux/arm64` (`sha256:f2950452b8675d7bdd8a8390b93b9f6f4bfbe2d8443a5a1c83457821f8fa32f3`) |

The plans and the result manifests both say contract `1.2.0`. Until BomLens
#208 a plan said `1.0.0` whatever the image (`PLAN_CONTRACT_VERSION` in
`docker/lib/make-plan.sh` was not raised when #203 added the `trivy-db` mount
role); #208 gives the plan the engine contract version. The reader still does
not use the version to decide which mount roles to expect.

`engine-plan-1.json` is `schemas/engine-plan-1.json` from the same commit,
copied byte for byte (unchanged since `1e5ef6b7`; the `v1.13.2` tag's copy
is the same file). The vocabulary test
compares the reader's enums to it.

## How each plan was captured

The flags are the ones the TRUSCA runner gives a PLAN container:

```sh
docker run --rm --read-only --network none --cap-drop ALL \
  --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER --cap-add SETGID --cap-add SETUID \
  --security-opt no-new-privileges \
  --mount type=bind,src=<source>,dst=/src,readonly \
  --mount type=bind,src=<out>,dst=/host-output \
  --mount type=bind,src=<tmp>,dst=/tmp \
  --workdir /host-output \
  -e MODE=PLAN -e PROJECT_NAME=app -e PROJECT_VERSION=1.0 -e HOST_OUTPUT_DIR=/host-output \
  -e FETCH_LICENSE=false \
  -e BOMLENS_IMAGE_REF=ghcr.io/sktelecom/bomlens:1.13.2 \
  -e BOMLENS_IMAGE_DIGEST=sha256:e357e634ef6447ed44b57a4aed3090af12b77c1fe29cbc0e567a3f65414f738b \
  -e HOST_UID=<uid> -e HOST_GID=<gid> \
  ghcr.io/sktelecom/bomlens:1.13.2@sha256:e357e634ef6447ed44b57a4aed3090af12b77c1fe29cbc0e567a3f65414f738b
```

| Case | Source | Exit | Result |
|---|---|---|---|
| `node-ok` | `tests/e2e/golden/fixtures/node` (npm, `lodash` 4.17.21) | 0 | `ok`; stage 1 `ghcr.io/cdxgen/cdxgen-alpine-node24:13.2.0@sha256:1e46a41c...`, stage 2 the image above with the `trivy-db` mount (`/trivy-db`, read-only, optional), `SECURITY_ENRICH=false` in its `env`, and the proxy, `TRIVY_*`, `BOMLENS_TRIVY_DB_MAX_AGE_DAYS`, `EPSS_API` and `KEV_URL` names in its `env_passthrough` |
| `android-unsupported` | BomLens `tests/fixtures/android-kotlin-dsl` at the `v1.13.2` tag (unchanged since `34010b6b`) | 0 | `unsupported`, reason `android-sdk-image`, no stages; the manifest records the `plan` step `skipped` (`not_installed`) and the run `succeeded_degraded` |
| `node-mirror-prefix` | the same `node` source, with `-e CDXGEN_IMAGE_PREFIX=registry.example/mirror/cdxgen/` added (2026-10-12 09:05 UTC) | 0 | `ok`; stage 1 `registry.example/mirror/cdxgen/cdxgen-alpine-node24:13.2.0` with the same digest as `node-ok` (`sha256:1e46a41c...`) |

`node-mirror-prefix` differs from `node-ok` in the stage 1 `image` only; in
its result manifest, the run times and the size and digest of the plan file.
A second run with the value without its final `/`
(`registry.example/mirror/cdxgen`) wrote a byte-identical plan: BomLens adds
the `/` (`docker/lib/source-detect.sh`). The runner gives the same value as
`SCAN_BOMLENS_STAGE1_IMAGE_PREFIX` (`tests/unit/integrations/bomlens/test_stage1_image_prefix.py`).

`cdxgen-images.json` is `/usr/local/lib/sbom/cdxgen-images.json` from the
same image, copied byte for byte with
`docker run --rm --network none --entrypoint cat <image above> /usr/local/lib/sbom/cdxgen-images.json`.
It is the list the offline-install guide tells an operator to read and copy;
`tests/unit/test_bomlens_stage1_prefix_doc_contract.py` runs the guide's
commands on it.

Against the `sha-34010b6` captures these replaced (2026-10-12 04:19 UTC),
the only differences are the image fields (`image`, `image_digest`, and
`version`, which reads `1.13.2` instead of `latest-34010b6`) and the run times,
and with them the digest of the plan file in each result manifest. The
stage 1 image, mounts, `env` and `env_passthrough` are the same, and so is
the `build-prep.sh` the plan copies out. Before that, the `sha-1e5ef6b`
captures differed from `sha-34010b6` only in the contract version of the
plans, the image fields and the run times.

The `bomlens-plan/` folder each run also wrote (stage 1's `build-prep.sh` and
`identify-conda.py`, copied from the image) is not kept: the runner only copies
and mounts those files, and the tests that need them write placeholders.

## The same image, end to end

With the runner and `1.13.2` pinned in `core/config.py`, on a local Docker
daemon (2026-10-12), `tests/integration/scan/test_bomlens_engine_pipeline.py`
passed its three real-image tests (`BOMLENS_ENGINE_DOCKER_TEST=1`,
`BOMLENS_ENGINE_TRIVY_CACHE` naming the database of the fixture captures,
`UpdatedAt` 2026-10-11T19:06Z):

- `test_the_published_images_scan_an_npm_project`: the scan succeeded with
  one component, `CVE-2025-13465` among its findings, the manifest's run
  status `succeeded` and stage 1's exit code 0.
- `test_the_published_images_resolve_a_gradle_project_under_a_read_only_root`:
  `tests/e2e/golden/fixtures/gradle` (guava 32.1.3-jre) came back with
  components and no `generate` degradation.
- `test_root_owned_engine_files_become_deletable_by_the_worker`.

The same three passed with `sha-34010b6` on the same day, where the npm run
also showed PLAN, stage 1, stage 2 and the ownership reset exiting 0, stage 2
reading the worker's Trivy database through the `trivy-db` mount, and three
CVEs persisted. With `sha-1e5ef6b` pinned instead, the Gradle test failed
the way the engine's shadow run did: `generate` degraded (`no_components`,
"manifests found: build.gradle") and no component, because Gradle could not
write the account's `~/.gradle` under the read-only root (BomLens #209).
None of these tests runs the deep-license image.

With `sha-1e5ef6b`, a database 11 days old passed the npm test under the
16-day limit the runner derives from the weekly refresh, and failed it as it
should when the limit was 9 days: `scan-security` was degraded and the scan
failed with "the vulnerability scan degraded (unavailable)". That run was not
repeated with later images; `source-trivy-db-stale` in
`tests/fixtures/bomlens/` is the same result from the pinned image.

The earlier image `sha-d8d3323` was also run through
`ghcr.io/tecnativa/docker-socket-proxy:0.3.0` with the compose file's
settings, and with stage 1 limited to 8 MiB of memory: the kernel killed it
(exit 137, `OOMKilled`), the restore run exited 0, and stage 2 fell back to
the manifest-only reader (`generate` `degraded` (`fallback`), run
`succeeded_degraded`). Those two runs were not repeated with later images.

## Stage 1 from a registry mirror

On 2026-10-12, `ghcr.io/cdxgen/cdxgen-alpine-node24` was copied into a local
`registry:2.8.3` (`localhost:55055`) with the offline-install guide's loop
(`skopeo copy --all --preserve-digests`, limited to that one image); the copy's
index digest read back as `sha256:1e46a41c...`, the published one.
`test_the_published_images_take_stage_1_from_a_mirror` then passed with
`BOMLENS_ENGINE_STAGE1_MIRROR=localhost:55055/cdxgen` (no final `/`): stage 1
ran `localhost:55055/cdxgen/cdxgen-alpine-node24:13.2.0@sha256:1e46a41c...`,
pulled from that registry, exited 0, and the scan succeeded with one
component. `test_the_published_images_scan_an_npm_project` passed in the same
run. Both used a Trivy database downloaded that day (`UpdatedAt`
2026-10-12T07:12Z) through `BOMLENS_ENGINE_TRIVY_CACHE`. Without it the
default `~/.cache/trivy` held no database on that machine, and both tests
failed the same way with "the vulnerability scan failed".
