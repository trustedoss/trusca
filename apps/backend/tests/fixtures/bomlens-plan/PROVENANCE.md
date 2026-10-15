# Provenance of the BomLens execution plan fixtures

Every `bomlens-plan.json` and `bomlens-result.json` here is unedited output of
the published BomLens image, captured on 2026-10-15 (05:00 UTC) with a plain
`docker run` (no compose). Tests that need a hostile or broken plan copy a capture and
change the copy (`tests/unit/integrations/bomlens/test_plan.py`).

## Image

| | |
|---|---|
| Image | `ghcr.io/sktelecom/bomlens:1.13.4` (the BomLens v1.13.4 release) |
| Index digest | `sha256:59d5a3363ad658a9b94787cd4c964209371b8f68a83e49adfd5b74b3220f2b47` |
| Source commit | image label `org.opencontainers.image.revision=f098ff64`, the commit the `v1.13.4` tag in `sktelecom/bomlens` names. Image label `com.sktelecom.sbom.engine-contract=1.3.0` |
| Platform pulled | `linux/arm64` (`sha256:b3574b69b246343934d3c9ee56e5fb0155c25fcc98df2ba77da1eec74b3d982b`) |

The plans and the result manifests both say contract `1.3.0`. Until BomLens
#208 a plan said `1.0.0` whatever the image (`PLAN_CONTRACT_VERSION` in
`docker/lib/make-plan.sh` was not raised when #203 added the `trivy-db` mount
role); #208 gives the plan the engine contract version. The reader still does
not use the version to decide which mount roles to expect.

`engine-plan-1.json` is `schemas/engine-plan-1.json` at the `v1.13.4` tag,
copied byte for byte. Against the `v1.13.2` copy it replaced, it adds the
`input_fingerprint` object (at the top level and on each stage) and the six
settings mount roles. The vocabulary test compares the reader's enums to it.

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
  -e BOMLENS_IMAGE_REF=ghcr.io/sktelecom/bomlens:1.13.4 \
  -e BOMLENS_IMAGE_DIGEST=sha256:59d5a3363ad658a9b94787cd4c964209371b8f68a83e49adfd5b74b3220f2b47 \
  -e HOST_UID=<uid> -e HOST_GID=<gid> \
  ghcr.io/sktelecom/bomlens:1.13.4@sha256:59d5a3363ad658a9b94787cd4c964209371b8f68a83e49adfd5b74b3220f2b47
```

| Case | Source | Exit | Result |
|---|---|---|---|
| `node-ok` | `tests/e2e/golden/fixtures/node` (npm, `lodash` 4.17.21) | 0 | `ok`; stage 1 `ghcr.io/cdxgen/cdxgen-alpine-node24:13.3.0@sha256:9e9cbff2...` with the two caches and six settings files as optional mounts, stage 2 the image above with the `trivy-db` mount (`/trivy-db`, read-only, optional), `SECURITY_ENRICH=false` in its `env`, and the proxy, `TRIVY_*`, `BOMLENS_TRIVY_DB_MAX_AGE_DAYS`, `EPSS_API` and `KEV_URL` names in its `env_passthrough` |
| `android-unsupported` | BomLens `tests/fixtures/android-kotlin-dsl` at the `v1.13.4` tag | 0 | `unsupported`, reason `android-sdk-image`, no stages; the manifest records the `plan` step `skipped` (`not_installed`) and the run `succeeded_degraded` |
| `node-mirror-prefix` | the same `node` source, with `-e CDXGEN_IMAGE_PREFIX=registry.example/mirror/cdxgen/` added | 0 | `ok`; stage 1 `registry.example/mirror/cdxgen/cdxgen-alpine-node24:13.3.0` with the same digest as `node-ok` (`sha256:9e9cbff2...`) |

`node-mirror-prefix` differs from `node-ok` in the stage 1 `image` only; in
its result manifest, the size and digest of the plan file. On the `1.13.2`
image, a second run with the value without its final `/`
(`registry.example/mirror/cdxgen`) wrote a byte-identical plan: BomLens adds
the `/` (`docker/lib/source-detect.sh`). The runner gives the same value as
`SCAN_BOMLENS_STAGE1_IMAGE_PREFIX` (`tests/unit/integrations/bomlens/test_stage1_image_prefix.py`).

`cdxgen-images.json` is `/usr/local/lib/sbom/cdxgen-images.json` from the
same image, copied byte for byte with
`docker run --rm --network none --entrypoint cat <image above> /usr/local/lib/sbom/cdxgen-images.json`.
It is the list the offline-install guide tells an operator to read and copy;
`tests/unit/test_bomlens_stage1_prefix_doc_contract.py` runs the guide's
commands on it.

Against the `1.13.2` captures these replaced (2026-10-12 07:32 UTC), the
plans differ in more than the image fields and the contract version:

- `input_fingerprint`, at the top level and on the `generate` stage (sha256
  values only; the unsupported plan has one with `sha256` null and the
  reasons `no-lockfile` and `plan-unsupported`). The reader ignores it.
- Stage 1 runs `cdxgen-alpine-node24:13.3.0` (`sha256:9e9cbff2...`) instead of
  `13.2.0`, with `MAVEN_OPTS` `-Duser.home=/tmp/sbomhome
  -Dmaven.repo.local=/tmp/sbomhome/.m2/repository`, the `cache-maven` mount at
  `/tmp/sbomhome/.m2` instead of `/root/.m2`, and six new optional read-only
  mounts under `/bomlens-settings` (`maven-settings`,
  `maven-settings-security`, `gradle-properties`, `gradle-init-script`,
  `gradle-init-script-kts`, `gradle-init`). The `1.13.2` reader refused every
  such plan, for the unknown roles and for the cache under `/tmp`.
- Stage 1's `env_passthrough` adds `BOMLENS_INCLUDE_DEV_DEPENDENCIES`, which
  is not on TRUSCA's allow list.
- Stage 2's `env` adds `CYCLONEDX_SPEC_VERSION=1.6`, and its
  `env_passthrough` adds `NVD_API_KEY` and stage 1's settings. Stage 2 runs
  with TRUSCA's own environment, so neither reaches the container.
- `env_from_caller` (stage 1, `BOMLENS_GUARD_ID`) and `env_from_stage1`
  (stage 2, now also `BOMLENS_STAGE1_REUSED`) describe what the caller sets;
  the reader ignores them.

The result manifests differ in the contract version, the image fields, the
run times and the plan file's digest and size. Earlier replacements
(`sha-1e5ef6b` to `sha-34010b6` to `1.13.2`) changed only the contract
version, the image fields and the run times.

The `bomlens-plan/` folder each run also wrote (stage 1's `build-prep.sh` and
`identify-conda.py`, copied from the image) is not kept: the runner only copies
and mounts those files, and the tests that need them write placeholders.

## The same image, end to end

With the runner and `1.13.4` pinned in `core/config.py`, on a local Docker
daemon (2026-10-15), `tests/integration/scan/test_bomlens_engine_pipeline.py`
passed its three real-image tests (`BOMLENS_ENGINE_DOCKER_TEST=1`,
`BOMLENS_ENGINE_TRIVY_CACHE` naming the database of the result fixture
captures, `UpdatedAt` 2026-10-15T01:13Z):

- `test_the_published_images_scan_an_npm_project`: the scan succeeded with
  one component, `CVE-2025-13465` among its findings, the manifest's run
  status `succeeded` and stage 1's exit code 0.
- `test_the_published_images_resolve_a_gradle_project_under_a_read_only_root`:
  `tests/e2e/golden/fixtures/gradle` (guava 32.1.3-jre) came back with
  components and no `generate` degradation.
- `test_root_owned_engine_files_become_deletable_by_the_worker`.

The same three passed with `1.13.2` on 2026-10-12, and with `sha-34010b6`
on that day, where the npm run
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

That was the `1.13.2` image, whose plan named `13.2.0`. The mirror test was
not repeated with `1.13.4`; it now expects `cdxgen-alpine-node24:13.3.0`, the
image the `node-ok` plan names.
