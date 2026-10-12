# Provenance of the BomLens execution plan fixtures

Every `bomlens-plan.json` and `bomlens-result.json` here is unedited output of
the published BomLens image, captured on 2026-10-12 with a plain `docker run`
(no compose). Tests that need a hostile or broken plan copy a capture and
change the copy (`tests/unit/integrations/bomlens/test_plan.py`).

## Image

| | |
|---|---|
| Image | `ghcr.io/haksungjang/bomlens:sha-34010b6` |
| Index digest | `sha256:019d7126b96f851fd18c1ece54a196691d778dee2493eb22e5a2fb6cc96cc3e0` |
| Source commit | `34010b6b` on the fork's `main` (BomLens #186 to #209); image label `com.sktelecom.sbom.engine-contract=1.2.0` |
| Platform pulled | `linux/arm64` (`sha256:cf741505ec2650ffd04f58bd65f93f0fc452781b64c9931c89a726fa4aff2792`) |

The plans and the result manifests both say contract `1.2.0`. Until BomLens
#208 a plan said `1.0.0` whatever the image (`PLAN_CONTRACT_VERSION` in
`docker/lib/make-plan.sh` was not raised when #203 added the `trivy-db` mount
role); #208 gives the plan the engine contract version. The reader still does
not use the version to decide which mount roles to expect.

`engine-plan-1.json` is `schemas/engine-plan-1.json` from the same commit,
copied byte for byte (unchanged since `1e5ef6b7`). The vocabulary test
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
  -e BOMLENS_IMAGE_REF=ghcr.io/haksungjang/bomlens:sha-34010b6 \
  -e BOMLENS_IMAGE_DIGEST=sha256:019d7126b96f851fd18c1ece54a196691d778dee2493eb22e5a2fb6cc96cc3e0 \
  -e HOST_UID=<uid> -e HOST_GID=<gid> \
  ghcr.io/haksungjang/bomlens:sha-34010b6@sha256:019d7126b96f851fd18c1ece54a196691d778dee2493eb22e5a2fb6cc96cc3e0
```

| Case | Source | Exit | Result |
|---|---|---|---|
| `node-ok` | `tests/e2e/golden/fixtures/node` (npm, `lodash` 4.17.21) | 0 | `ok`; stage 1 `ghcr.io/cdxgen/cdxgen-alpine-node24:13.2.0@sha256:1e46a41c...`, stage 2 the image above with the `trivy-db` mount (`/trivy-db`, read-only, optional), `SECURITY_ENRICH=false` in its `env`, and the proxy, `TRIVY_*`, `BOMLENS_TRIVY_DB_MAX_AGE_DAYS`, `EPSS_API` and `KEV_URL` names in its `env_passthrough` |
| `android-unsupported` | BomLens `tests/fixtures/android-kotlin-dsl` at `34010b6b` | 0 | `unsupported`, reason `android-sdk-image`, no stages; the manifest records the `plan` step `skipped` (`not_installed`) and the run `succeeded_degraded` |

Against the `sha-1e5ef6b` captures these replaced, the only differences are
the contract version of the plans, the image fields and the run times (and
with them the digest of the plan file in each result manifest).

The `bomlens-plan/` folder each run also wrote (stage 1's `build-prep.sh` and
`identify-conda.py`, copied from the image) is not kept: the runner only copies
and mounts those files, and the tests that need them write placeholders.

## The same image, end to end

With the runner and this image on a local Docker daemon (2026-10-12),
`tests/integration/scan/test_bomlens_engine_pipeline.py` passed its three
real-image tests (`BOMLENS_ENGINE_DOCKER_TEST=1`, `BOMLENS_ENGINE_TRIVY_CACHE`
naming a database one day old):

- `test_the_published_images_scan_an_npm_project`: PLAN, stage 1, stage 2 and
  the ownership reset exited 0, stage 2 read the worker's Trivy database
  through the `trivy-db` mount, the manifest read `succeeded`, and the scan
  persisted one component with three CVEs.
- `test_the_published_images_resolve_a_gradle_project_under_a_read_only_root`:
  `tests/e2e/golden/fixtures/gradle` (guava 32.1.3-jre) came back with
  components and no `generate` degradation. With `sha-1e5ef6b` pinned instead,
  the same test failed the way the engine's shadow run did: `generate`
  degraded (`no_components`, "manifests found: build.gradle") and no
  component, because Gradle could not write the account's `~/.gradle` under
  the read-only root (BomLens #209).
- `test_root_owned_engine_files_become_deletable_by_the_worker`.

With `sha-1e5ef6b`, a database 11 days old passed the npm test under the
16-day limit the runner derives from the weekly refresh, and failed it as it
should when the limit was 9 days: `scan-security` was degraded and the scan
failed with "the vulnerability scan degraded (unavailable)". That run was not
repeated with `sha-34010b6`; `source-trivy-db-stale` in
`tests/fixtures/bomlens/` is the same result from this image.

The earlier image `sha-d8d3323` was also run through
`ghcr.io/tecnativa/docker-socket-proxy:0.3.0` with the compose file's
settings, and with stage 1 limited to 8 MiB of memory: the kernel killed it
(exit 137, `OOMKilled`), the restore run exited 0, and stage 2 fell back to
the manifest-only reader (`generate` `degraded` (`fallback`), run
`succeeded_degraded`). Those two runs were not repeated with later images.
