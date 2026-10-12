# Provenance of the BomLens execution plan fixtures

Every `bomlens-plan.json` and `bomlens-result.json` here is unedited output of
the published BomLens image, captured on 2026-10-12 with a plain `docker run`
(no compose). Tests that need a hostile or broken plan copy a capture and
change the copy (`tests/unit/integrations/bomlens/test_plan.py`).

## Image

| | |
|---|---|
| Image | `ghcr.io/haksungjang/bomlens:sha-1e5ef6b` |
| Index digest | `sha256:eeb851f7cb4e3bc1cd7c81151e0f9a1bd129dfd755bfb92e110d17653b8f9a36` |
| Source commit | `1e5ef6b7` on the fork's `main` (BomLens #186 to #207); image label `com.sktelecom.sbom.engine-contract=1.2.0` |
| Platform pulled | `linux/arm64` (`sha256:5ce76bab7cf602f1babc7589d464d672a38434a70b4045003774967b4a92ee99`) |

The result manifests say contract `1.2.0`. The plans still say `1.0.0`
(`PLAN_CONTRACT_VERSION` in BomLens' `docker/lib/make-plan.sh` was not raised
when #203 added the `trivy-db` mount role), so the reader cannot tell a plan
with that role from one without it by its version, and does not try.

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
  -e BOMLENS_IMAGE_REF=ghcr.io/haksungjang/bomlens:sha-1e5ef6b \
  -e BOMLENS_IMAGE_DIGEST=sha256:eeb851f7cb4e3bc1cd7c81151e0f9a1bd129dfd755bfb92e110d17653b8f9a36 \
  -e HOST_UID=<uid> -e HOST_GID=<gid> \
  ghcr.io/haksungjang/bomlens:sha-1e5ef6b@sha256:eeb851f7cb4e3bc1cd7c81151e0f9a1bd129dfd755bfb92e110d17653b8f9a36
```

| Case | Source | Exit | Result |
|---|---|---|---|
| `node-ok` | `tests/e2e/golden/fixtures/node` (npm, `lodash` 4.17.21) | 0 | `ok`; stage 1 `ghcr.io/cdxgen/cdxgen-alpine-node24:13.2.0@sha256:1e46a41c...`, stage 2 the image above with the `trivy-db` mount (`/trivy-db`, read-only, optional), `SECURITY_ENRICH=false` in its `env`, and the proxy, `TRIVY_*`, `BOMLENS_TRIVY_DB_MAX_AGE_DAYS`, `EPSS_API` and `KEV_URL` names in its `env_passthrough` |
| `android-unsupported` | BomLens `tests/fixtures/android-kotlin-dsl` at `1e5ef6b7` | 0 | `unsupported`, reason `android-sdk-image`, no stages; the manifest records the `plan` step `skipped` (`not_installed`) and the run `succeeded_degraded` |

The `bomlens-plan/` folder each run also wrote (stage 1's `build-prep.sh` and
`identify-conda.py`, copied from the image) is not kept: the runner only copies
and mounts those files, and the tests that need them write placeholders.

## The same image, end to end

With the runner and this image, the npm project above ran on a local Docker
daemon (2026-10-12): PLAN, stage 1, stage 2 and the ownership reset all
exited 0, stage 2 read the worker's Trivy database through the `trivy-db`
mount, the manifest read `succeeded`, and the scan persisted one component
with three CVEs (`tests/integration/scan/test_bomlens_engine_pipeline.py`,
`test_the_published_images_scan_an_npm_project`, run with
`BOMLENS_ENGINE_DOCKER_TEST=1` and `BOMLENS_ENGINE_TRIVY_CACHE` naming a
database one day old). With a database 11 days old the same test failed as it
should: `scan-security` was degraded and the scan failed with "the
vulnerability scan degraded (unavailable)".

The earlier image `sha-d8d3323` was also run through
`ghcr.io/tecnativa/docker-socket-proxy:0.3.0` with the compose file's
settings, and with stage 1 limited to 8 MiB of memory: the kernel killed it
(exit 137, `OOMKilled`), the restore run exited 0, and stage 2 fell back to
the manifest-only reader (`generate` `degraded` (`fallback`), run
`succeeded_degraded`). Those two runs were not repeated with `sha-1e5ef6b`.
