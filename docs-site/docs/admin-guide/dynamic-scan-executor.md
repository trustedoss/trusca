---
id: dynamic-scan-executor
title: Dynamic scan executor
description: How the opt-in local_docker executor launches per-environment build sidecars over the host Docker socket, the security defaults that contain an untrusted build, and how to configure it safely on-prem.
sidebar_label: Dynamic scan executor
sidebar_position: 9
---

# Dynamic scan executor

Some projects cannot be analysed by reading their manifests alone — an Android app, for instance, needs its Gradle build to run before cdxgen can see the resolved dependency graph. The **dynamic scan executor** runs the SBOM-generation stage (build-prep + cdxgen) inside a *per-environment* container that already carries the right toolchain. A build is arbitrary code execution, so this page is mostly about containment.

:::note Audience
`super_admin` operating an **on-prem, single-tenant** portal, comfortable editing `.env` and running `docker-compose`. The default executor needs no setup — read this only if you are turning on `SCAN_EXECUTOR=local_docker`. For the per-scan lifecycle, see [Scans](../user-guide/scans.md).
:::

## Two executors

| Executor | What runs the build | When to use |
|---|---|---|
| `inprocess` (default) | The Celery worker runs cdxgen as a worker-local subprocess, exactly as before. | Everything that builds inside the worker image (npm, Maven, pip on the worker's toolchain). |
| `local_docker` | The worker launches a per-environment **sidecar** container (currently the Android SDK image, `sbom-scanner-android-sdk<API>`) over the host Docker socket, runs build-prep + cdxgen there, and collects the resulting CycloneDX SBOM. | On-prem when a project needs a toolchain the worker image does not carry. |

`local_docker` falls back to `inprocess` for any environment that has no routed image and for a worker without the Docker CLI — so turning it on never breaks the scans that already worked.

### Which environments are routed

By default `SCAN_LOCAL_DOCKER_ENVS=android` — **only Android** routes to a sidecar. This is deliberate. We measured the same projects through the all-in-one worker and through the dedicated cdxgen language images: for `node`, `go`, `rust`, `ruby`, `java`, `python`, `php`, and `dotnet` the component counts are **identical** (the worker already carries those toolchains, and cdxgen resolves the transitive graph the same way). Android is the one real gap — the worker has no Android SDK, so the Gradle plugin cannot resolve the dependency graph and yields **0** components, while the SDK sidecar yields the full graph.

So routing those other languages buys you no detection improvement on-prem. If you want each language build to run **isolated** from the worker anyway, widen the set:

<!-- docs-uat: id=dynamic-scan-executor-routed-envs kind=shell ctx=host tier=manual waiver=env-config-snippet-not-a-command -->
```bash
SCAN_LOCAL_DOCKER_ENVS=android,node,go,rust,ruby,java,python,php,dotnet
```

Each routed language pulls its cdxgen image on first use (multi-GB each). The language images are pinned by the `CDXGEN_IMAGE_TAG` (`v12`) tag, so they are accepted without the unpinned-image override; only Android's `:latest` needs `SCAN_ANDROID_IMAGE_TAG` pinned.

:::warning On-prem, single-tenant only
`local_docker` gives the worker access to the host Docker socket, which is **root-equivalent control of the host**. The repositories it scans are untrusted input — a malicious `build.gradle` or Gradle plugin runs as part of the build. Never enable this on a multi-tenant or internet-exposed deployment. The multi-tenant SaaS path is a separate, sandboxed model (see [Limitations](#limitations)).
:::

## What contains the build

These defaults are applied by the executor in code — you do not have to set them to get them. They exist because the sidecar runs untrusted build code.

- **Volume scope.** The strategy is `named` by default: the sidecar mounts **only** `SCAN_WORKSPACE_VOLUME` (the scan tree) and nothing else. The alternative, `volumes_from`, re-mounts *every* worker volume — including the cosign SBOM-signing key — into the untrusted build. It is refused unless you set both `SCAN_VOLUMES_FROM_ACK=1` and `SCAN_WORKER_CONTAINER`. Leave it on `named`.
- **Capabilities.** The sidecar runs with `--cap-drop=ALL` and only the minimal set added back (`CHOWN`, `DAC_OVERRIDE`, `FOWNER`, `SETGID`, `SETUID`), plus `--security-opt no-new-privileges` so the build cannot escalate.
- **Resource bounds.** `--memory 4g`, `--cpus 2`, and `--pids-limit 4096` are on by default. An untrusted build cannot OOM the host or fork-bomb it.
- **Environment.** The sidecar receives only `HOME`, `FETCH_LICENSE`, and `CDXGEN_DEBUG_MODE`. Worker secrets are never passed through.
- **Image pinning.** A `:latest` tag is refused (core rule #9). Pin `SCAN_ANDROID_IMAGE_TAG` to a semver (`v1.0.0`) or a `sha256:<digest>`. For local development only, `SCAN_ALLOW_UNPINNED_IMAGE=1` lifts the refusal.
- **Secret masking.** PEM private-key blocks that appear on the sidecar's stderr are masked before the line reaches the scan log.

## Turn it on (opt-in)

The worker image already ships with the Docker CLI, so the only decisions are how the worker reaches Docker and how the sidecar's network is isolated.

### 1. Give the worker Docker access — through the proxy

You can mount the raw `/var/run/docker.sock` into the worker, but the recommended path routes the worker through **docker-socket-proxy** instead, so the worker can only call the Docker API verbs it actually needs.

Start the proxy with the `local-docker` compose profile (it is defined in `docker-compose.dev.yml`), set the worker's `DOCKER_HOST` to the proxy, and **remove** the raw socket mount from the worker:

<!-- docs-uat: id=dynamic-scan-executor-proxy kind=shell ctx=host tier=manual waiver=env-config-snippet-not-a-command -->
```bash
# Bring up the proxy alongside the stack
docker-compose --profile local-docker up -d

# In the portal's .env — point the worker at the proxy
DOCKER_HOST=tcp://docker-socket-proxy:2375
```

The proxy allows `containers/*` and `images/*` only; it blocks `exec`, `swarm`, `networks`, and `volumes`.

:::caution The proxy does not inspect create payloads
docker-socket-proxy gates which API *verbs* the worker may call, but it does not inspect the body of a container-create request — it cannot reject a `privileged: true` or an extra bind mount. The sidecar hardening above (cap-drop, no-new-privileges, named volume) is what actually constrains the launched container, so it stays essential even behind the proxy.
:::

### 2. Isolate sidecar egress

A Gradle build needs to reach package registries (Google's Maven, Maven Central), so the sidecar keeps internet egress — but it must not be able to reach `postgres`, `redis`, or the backend. Put the sidecar on a **separate** network from the app services by setting `SCAN_SIDECAR_NETWORK` to the dedicated `scan-egress` network (defined in compose):

<!-- docs-uat: id=dynamic-scan-executor-network kind=shell ctx=host tier=manual waiver=env-config-snippet-not-a-command -->
```bash
# In the portal's .env — <project> is the compose project prefix
SCAN_SIDECAR_NETWORK=<project>_scan-egress
```

If you leave `SCAN_SIDECAR_NETWORK` unset, the sidecar lands on the default bridge — where it can reach internal services — and the worker logs a startup **warning**. In production, constrain egress further to only the registries you need (Google, Maven) with a firewall or egress proxy.

### 3. Pin the workspace volume and the image

Set `SCAN_WORKSPACE_VOLUME` to the compose-prefixed volume name, and pin the sidecar image to a fixed version:

<!-- docs-uat: id=dynamic-scan-executor-pins kind=shell ctx=host tier=manual waiver=env-config-snippet-not-a-command -->
```bash
# In the portal's .env
SCAN_EXECUTOR=local_docker
SCAN_WORKSPACE_VOLUME=trustedoss-portal_scan-workspace   # compose-prefixed name
SCAN_ANDROID_IMAGE_TAG=v1.0.0                            # semver or sha256:<digest>
```

Restart the worker after editing `.env` — the executor keys are read at runtime via `os.getenv`.

## Verify it worked

<!-- docs-uat: id=dynamic-scan-executor-verify-routing kind=manual tier=manual -->
1. Trigger a source scan against an Android project. The worker log shows the executor routing to the sidecar (the resolved image tag and `local_docker` strategy are logged at the start of the SBOM stage), not an in-process cdxgen subprocess.
<!-- docs-uat: id=dynamic-scan-executor-verify-sidecar kind=manual tier=manual -->
2. While the scan runs, `docker ps --filter label=trusca.role=scan-sidecar` lists exactly one sidecar. Inspect it — `docker inspect <id>` shows `CapDrop: [ALL]`, the named workspace volume as its only mount, and the `scan-egress` network.
<!-- docs-uat: id=dynamic-scan-executor-verify-sbom kind=manual tier=manual -->
3. The scan reaches `succeeded` with a non-empty component count, and the sidecar is gone from `docker ps` after completion.

## Limitations

:::warning Orphan sidecar after a hard kill
On a graceful scan finish the executor removes its sidecar. On a hard `SIGKILL` of the worker, a sidecar can be orphaned. Identify orphans by the label `trusca.role=scan-sidecar` and remove them by hand; an automatic reaper is a follow-up increment.
:::

- **Multi-tenant SaaS is out of scope.** This model trusts the host boundary, which a single tenant controls. The SaaS path uses a Kubernetes Job per scan with a gVisor sandbox instead — a separate follow-up increment, not this executor.
- **Only the Android environment is routed today.** Every other environment still runs `inprocess`; `local_docker` adds environments incrementally.

## Environment variable reference

These mirror the **Dynamic scan executor** section of `.env.example`. All are read at runtime via `os.getenv` — edit `.env` and restart the worker. See [Environment variables → Scan pipeline](../reference/env-variables.md#scan-pipeline) for the canonical reference.

| Key | Default | Description |
|---|---|---|
| `SCAN_EXECUTOR` | `inprocess` | `inprocess` runs cdxgen as a worker subprocess; `local_docker` launches a per-environment sidecar over the Docker socket (on-prem only). |
| `SCAN_LOCAL_DOCKER_ENVS` | `android` | Comma-separated environments routed to a sidecar. Only `android` is a detection gap; widen for per-build isolation (see below). |
| `SCAN_DOCKER_VOLUME_STRATEGY` | `named` | `named` mounts only the workspace volume into the sidecar; `volumes_from` re-mounts every worker volume (refused without the ack below). |
| `SCAN_WORKSPACE_VOLUME` | — | Required for `named`: the compose-prefixed workspace volume name (e.g. `trustedoss-portal_scan-workspace`). Unset falls back to in-process. |
| `SCAN_WORKSPACE_MOUNT` | `/tmp/trustedoss` | Mount point of the workspace volume inside the sidecar (production: `/workspace`). |
| `SCAN_WORKER_CONTAINER` | — | Required for `volumes_from`: explicit reference to the worker container. |
| `SCAN_VOLUMES_FROM_ACK` | — | Set `1` to accept the `volumes_from` over-share. Not recommended — it exposes the cosign signing key to the build. |
| `SCAN_SIDECAR_PIDS_LIMIT` | `4096` | Process limit on the sidecar (fork-bomb guard). |
| `SCAN_SIDECAR_MEMORY` | `4g` | Memory ceiling on the sidecar (host OOM guard). |
| `SCAN_SIDECAR_CPUS` | `2` | CPU limit on the sidecar. |
| `SCAN_SIDECAR_CAP_DROP` | `ALL` | Linux capabilities dropped before the minimal set is added back. |
| `SCAN_SIDECAR_CAP_ADD` | `CHOWN,DAC_OVERRIDE,FOWNER,SETGID,SETUID` | The minimal capabilities added back for build-prep. |
| `SCAN_SIDECAR_NETWORK` | — | Isolated egress network for the sidecar (recommended: `<project>_scan-egress`). Unset uses the default bridge with a startup warning. |
| `CDXGEN_IMAGE_TAG` | `v12` | cdxgen language-image tag. |
| `CDXGEN_ALLINONE_IMAGE` | `ghcr.io/cyclonedx/cdxgen:v12.5.0` | All-in-one image for mixed / unknown environments. |
| `SCAN_ANDROID_IMAGE_PREFIX` | `ghcr.io/sktelecom/sbom-scanner-android-sdk` | Image prefix for the Android sidecar; the API level is appended. |
| `SCAN_ANDROID_IMAGE_TAG` | `v1.0.0` | Pinned semver or `sha256:<digest>` for the Android image. `:latest` is refused. |
| `SCAN_ALLOW_UNPINNED_IMAGE` | — | Set `1` to allow a `:latest` tag. Development only. |
| `SCAN_ANDROID_API_DEFAULT` | `34` | Fallback Android `compileSdk` when a project does not declare one. |
| `CDXGEN_SPEC_VERSION` | `1.5` | CycloneDX spec version cdxgen emits (set `1.6` for CycloneDX 1.6). Applies to both executors. |
| `CDXGEN_FETCH_LICENSE` | `false` | `true` lets cdxgen resolve component licenses (slower). Applies to both executors. |

## See also

- [Scans](../user-guide/scans.md) — the per-scan lifecycle and progress view
- [Scan retention](./scan-retention.md) — how the SBOMs these scans produce are kept and reclaimed
- [Environment variables → Scan pipeline](../reference/env-variables.md#scan-pipeline)
