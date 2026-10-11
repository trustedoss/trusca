# Provenance of the BomLens execution plan fixtures

Every `bomlens-plan.json` and `bomlens-result.json` here is unedited output of
the published BomLens image, captured on 2026-10-11 with a plain `docker run`
(no compose). Tests that need a hostile or broken plan copy a capture and
change the copy (`tests/unit/integrations/bomlens/test_plan.py`).

## Image

| | |
|---|---|
| Image | `ghcr.io/haksungjang/bomlens:sha-d8d3323` |
| Index digest | `sha256:8b44d05a0baf66e11dcbabb52d7d9842cb11a5d0a4ec419b80de7f51233e05c5` |
| Source commit | `d8d33233` on the fork's `main` (PR #195, `MODE=PLAN`), plan contract `1.0.0` |
| Platform pulled | `linux/arm64` (`sha256:fab31322d4465b992df42585b6c55d6742146fbca08a751d15878846b28613ff`) |

`engine-plan-1.json` is `schemas/engine-plan-1.json` from the same commit,
copied byte for byte. The vocabulary test compares the reader's enums to it.

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
  -e BOMLENS_IMAGE_REF=ghcr.io/haksungjang/bomlens:sha-d8d3323 \
  -e BOMLENS_IMAGE_DIGEST=sha256:8b44d05a0baf66e11dcbabb52d7d9842cb11a5d0a4ec419b80de7f51233e05c5 \
  -e HOST_UID=<uid> -e HOST_GID=<gid> \
  ghcr.io/haksungjang/bomlens:sha-d8d3323@sha256:8b44d05a0baf66e11dcbabb52d7d9842cb11a5d0a4ec419b80de7f51233e05c5
```

| Case | Source | Exit | Result |
|---|---|---|---|
| `node-ok` | `tests/e2e/golden/fixtures/node` (npm, `lodash` 4.17.21) | 0 | `ok`; stage 1 `ghcr.io/cdxgen/cdxgen-alpine-node24:13.2.0@sha256:1e46a41c...`, stage 2 the image above |
| `android-unsupported` | BomLens `tests/fixtures/android-kotlin-dsl` at `d8d33233` | 0 | `unsupported`, reason `android-sdk-image`, no stages; the manifest records the `plan` step `skipped` (`not_installed`) and the run `succeeded_degraded` |

The `bomlens-plan/` folder each run also wrote (stage 1's `build-prep.sh` and
`identify-conda.py`, copied from the image) is not kept: the runner only copies
and mounts those files, and the tests that need them write placeholders.

## The same image, end to end

With the runner and this image, the npm project above ran on a local Docker
daemon, directly and through `ghcr.io/tecnativa/docker-socket-proxy:0.3.0`
with the compose file's settings: PLAN, stage 1, stage 2 and the ownership
reset all exited 0, the manifest read `succeeded`, and the scan persisted one
component with three CVEs (`tests/integration/scan/test_bomlens_engine_pipeline.py`,
`test_the_published_images_scan_an_npm_project`, run with
`BOMLENS_ENGINE_DOCKER_TEST=1`). With stage 1 limited to 8 MiB of memory the
kernel killed it (exit 137, `OOMKilled`), the restore run exited 0, and stage
2 fell back to the manifest-only reader: `generate` `degraded` (`fallback`),
run `succeeded_degraded`.
