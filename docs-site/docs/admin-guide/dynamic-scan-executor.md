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

:::danger `local_docker` no longer runs scans
A worker that can reach the Docker API now refuses every task but the BomLens engine's, so `SCAN_EXECUTOR=local_docker` cannot be used on a scan worker. See [step 1](#give-the-worker-docker-access) below; the rest of this page describes the executor for reference.
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

Or route every environment with the `all` sentinel, rather than enumerating each one:

<!-- docs-uat: id=dynamic-scan-executor-routed-envs-all kind=shell ctx=host tier=manual waiver=env-config-snippet-not-a-command -->
```bash
SCAN_LOCAL_DOCKER_ENVS=all
```

This is the setting to use when the source being scanned is not trusted (for example, a large cohort of public repositories pulled in for validation rather than a customer's own code): a hand-written list is one missed language away from an unsandboxed build running arbitrary code on the worker, and `all` also covers `cpp`, `mixed`, and `unknown`, which fall back to the all-in-one image and are easy to leave off a manual list.

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

### 1. Give the worker Docker access {#give-the-worker-docker-access}

This step can no longer be taken on a worker that runs scans. The worker that runs `local_docker` sidecars needs the Docker API, and that same worker also runs the scanned project's build whenever an environment is not routed, plus every other scan and default-queue task. Code from a scanned repository on a worker with the Docker API can start a privileged container and take the host, so TRUSCA now refuses it:

- A worker where `DOCKER_HOST` is set, a Docker socket is mounted at `/var/run/docker.sock` or `/run/docker.sock`, or the name `docker-socket-proxy` resolves runs the BomLens engine's tasks only. Every other task, the source scan task included, fails with that reason.
- Build preparation, cdxgen and govulncheck refuse to start on such a worker.

A deployment that mounted the socket or set `DOCKER_HOST` on its scan worker for `local_docker` therefore stops scanning until that access is removed; `docker-compose.dev.yml` no longer mounts the socket. For builds isolated in their own containers, use the BomLens engine, which keeps the Docker API on a worker of its own ([Scan engine](#scan-engine)). `local_docker` is not recommended for any deployment and will be removed.

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

## Scan engine (preview) {#scan-engine}

:::info Preview, off by default
`SCAN_ENGINE` is a preview setting. Leave it unset unless you are trying the BomLens engine on a compose deployment. The Helm chart does not run it yet.
:::

`SCAN_EXECUTOR` above decides where cdxgen runs. `SCAN_ENGINE` decides which engine produces the scan result at all: `inprocess` (the default) is the worker's own pipeline of build preparation, cdxgen, scancode and Trivy; `bomlens` hands SBOM generation, licence detection and vulnerability matching to the BomLens image, and TRUSCA keeps everything it does with the result (persistence, signing, licence policy, approvals, the build gate, notifications). `SCAN_EXECUTOR` has no effect on a BomLens scan.

### How a BomLens scan runs {#engine-how-it-runs}

After the worker fetches the source, it runs up to four containers, one after another, and waits for each to exit:

1. **Plan.** The BomLens image in `MODE=PLAN` reads the source (read-only) and writes an execution plan: which cdxgen language image resolves the dependencies, pinned by digest, and with which arguments. The progress view shows this as preparation.
2. **Stage 1.** The language image the plan names resolves the dependencies and writes the first SBOM. This is the only container that runs the scanned project's build tooling, and the only one with network access.
3. **Restore, when stage 1 was killed.** A stage 1 that ran out of memory or past its time limit cannot put the source tree back the way it found it, so the worker runs the same stage 1 again in restore-only mode, without network.
4. **Stage 2.** The BomLens image post-processes the SBOM, detects licences and runs Trivy. It runs whatever stage 1's exit code: when stage 1 left no SBOM, BomLens reads the manifests directly (direct dependencies only) and records SBOM generation as degraded, so the scan succeeds with a warning on the project Overview instead of failing.

When the plan says the project is not supported (an Android project, whose SDK image BomLens does not publish), stages 1 and 3 are skipped and stage 2 runs on its own, with the same degraded result.

The worker then checks the result manifest against the BomLens engine contract (major version 1 only; any other major is refused with the version in the scan error), checks each output file against the size and SHA-256 the manifest recorded, and persists the SBOM and the Trivy report through the same code as an in-process scan. The licenses scancode detected in the project's own files (the `bomlens-deep-license` image) are saved as detected licenses, as the in-process scancode stage saves them. One difference: when that scancode result does not match its recorded SHA-256 or is not valid JSON, the whole scan fails, because the engine's output is checked for integrity first; in process, a scancode problem only marks the step as degraded. A result over `SCANCODE_MAX_RESULT_BYTES` is not read and marks the step as degraded on both paths. Registry license lookups the engine did not make within its time budget appear in the same Overview notice as in-process lookups, and the folders BomLens left out as not shipped (tests, examples, benchmarks) are recorded in the scan metadata under `scope_filter`, with the patterns and the manifest files it skipped. A BomLens step that degraded is listed on the project Overview like any other step. The Overview shows the step and its reason; the engine's own explanation (for SBOM generation that found manifests but no components, the manifests and the resolver steps that failed, such as `gradle-dependencies`) is kept in the scan metadata under `stage_outcomes.<stage>.engine_detail`, up to 300 characters with credentials and control characters removed, and in the worker's `bomlens_stage_degraded` warning. A vulnerability scan that did not produce a result fails the scan rather than reading as zero findings.

### Trivy database age {#engine-trivy-db-age}

The two engines treat an old Trivy database differently. Stage 2 is told how old the worker's database may be: twice the refresh interval `TRIVY_DB_REFRESH_HOURS` in whole days, rounded up, plus two days, and never more than 30 days (16 days with the default weekly refresh), counted from when the database was built. When the database is older, BomLens marks the vulnerability scan as degraded and the scan fails with `the vulnerability scan degraded (unavailable)`. An in-process scan keeps going with an old database and reports the vulnerabilities it knows. With the weekly refresh, one failed refresh leaves a database about 14 days old and BomLens scans go on; a second failure in a row makes them fail until a refresh succeeds. The **Trivy DB** card under `/admin/health` marks the database stale at 7 days and very stale at 14, before scans fail. If BomLens scans start failing this way, refresh the database as described in [Vulnerability data](./vulnerability-data.md#how-the-lifecycle-works).

### What each engine container gets {#engine-containers}

The plan is output of a container that read the scanned source, so the worker treats it as untrusted input and decides how every container runs:

- **Images pinned by digest.** The BomLens images are pinned in the TRUSCA release. A stage 1 image must come from a repository in `SCAN_BOMLENS_STAGE1_REPOSITORIES`, or from under `SCAN_BOMLENS_STAGE1_IMAGE_PREFIX` when that is set, and carry a digest; `:latest` is refused everywhere. Stage 2 takes only its image and its two mount paths from the plan; its user, entrypoint, arguments, working directory and network are fixed by TRUSCA.
- **Mounts from the scan's workspace only.** A plan names a mount by role (source, output, helper file, restore state), never by host path. The worker maps each role to a folder under the scan's own workspace and refuses a path that leaves it or passes through a symbolic link. A mount over a system path, the Docker socket, or the paths TRUSCA mounts itself (`/tmp`, `/trivy-db`) is refused. The one exception is the plan's Trivy database role for stage 2, accepted only read-only at exactly `/trivy-db`; the worker connects its own Trivy database there. Each container is created first and started only when the mounts the daemon recorded are exactly the ones asked for.
- **Read-only root filesystem**, with a scratch `/tmp` from the workspace. Stage 1 can be given a writable root with `SCAN_BOMLENS_STAGE1_READ_ONLY_ROOT=false` (see the table below).
- **The sidecar hardening above**: `--cap-drop ALL` with the minimal set added back, `no-new-privileges`, and the `SCAN_SIDECAR_PIDS_LIMIT`, `SCAN_SIDECAR_MEMORY` and `SCAN_SIDECAR_CPUS` limits, with no swap.
- **No network**, except stage 1, which joins `SCAN_BOMLENS_NETWORK`. The worker refuses `host`, the compose networks `trustedoss` and `trustedoss-docker-api`, any network the worker itself is on (by name or ID), and refuses to run stage 1 at all when it cannot read its own networks. Stage 2 reads the worker's Trivy database read-only instead of downloading one, so the vulnerability data matches what the rest of TRUSCA uses.
- **Only allow-listed environment variables**, never the worker's environment. Pass-through names such as `GOPROXY`, `GOSUMDB` and, for stage 1 only, `HTTP_PROXY`, `HTTPS_PROXY` and `NO_PROXY` (either case) are copied from the engine worker's environment when set. A proxy on the worker's own loopback address (`127.0.0.1`, `localhost`, `::1`) is not passed on, because inside the container that address is the container itself. A proxy URL with credentials in it reaches the build that runs the scanned project's code, so give the build credentials that are scoped to reading packages. The worker names these variables on the `docker create` command line without their values, which it hands to the Docker CLI through its own environment, so the values do not appear in a process listing. They do stay in the engine container's environment, where `docker inspect` on that container shows them until the container is removed.
- **A deadline.** Every engine container carries labels with its scan and the time it must be gone. The engine worker removes it when its scan is no longer running or the deadline has passed: every five minutes, and right away when a scan is cancelled. A container left behind by a worker that was killed is therefore removed within minutes.

### Which worker runs what {#engine-workers}

The Docker API can start a privileged container, so only one worker can reach it, and that worker never runs code from a scanned repository:

| Worker | Queues | Docker API | Runs |
|---|---|---|---|
| `worker-engine` (profile `bomlens-engine`) | `trustedoss.engine` only | through `docker-socket-proxy` | the BomLens scan task and the engine-container reaper |
| `worker-scan` | `trustedoss.scan`, `trustedoss.default` | none | in-process scans (build preparation, cdxgen, scancode, Trivy), container and SBOM scans, reachability (govulncheck) |
| `worker-default` | `trustedoss.default` | none | notifications, backups, retention, catalog refreshes |

The engine worker does not run the in-process pipeline: a source scan goes to it only when `SCAN_ENGINE=bomlens` was set when the scan was queued, and the task it receives can only run the engine. Both directions are also checked on the worker itself. A worker that can reach the Docker API (`DOCKER_HOST` set, a Docker socket at `/var/run/docker.sock` or `/run/docker.sock`, or the name `docker-socket-proxy` resolving) runs the engine's tasks only and refuses any other, and build preparation, cdxgen and govulncheck refuse to start on such a worker; the task fails with that reason instead of running. `worker-engine` also takes no Celery remote control (`CELERY_WORKER_REMOTE_CONTROL=false`), so a broadcast on the broker cannot drive it: a cancelled engine scan stops at its next stage, and the reaper removes its containers.

### Turn on the engine (compose) {#engine-turn-on}

`worker-engine` reaches Docker through the filtered proxy in `docker-compose.yml`, never through the raw socket. The proxy answers container and image calls only and refuses `exec`, volumes, networks, swarm, system and build calls. It does not inspect the body of a container-create request; the limits in the previous sections are applied by the worker. The engine mounts parts of the workspace volume with `volume-subpath` and refuses to run on a Docker API older than 1.45 (Docker Engine 26).

<!-- docs-uat: id=dynamic-scan-executor-engine-proxy kind=shell ctx=host tier=manual waiver=env-config-snippet-not-a-command -->
```bash
# Once: the network stage 1 reaches the package registries on, with
# containers on it unable to reach each other
docker network create --opt com.docker.network.bridge.enable_icc=false \
  trustedoss-scan-egress

# In the portal's .env (<project> is the compose project name)
SCAN_ENGINE=bomlens
SCAN_WORKSPACE_VOLUME=<project>_workspace
SCAN_TRIVY_CACHE_VOLUME=<project>_trivy-cache

# Start the engine worker and its Docker API proxy, and let the API, beat and
# workers read the new values
docker-compose --profile bomlens-engine up -d
```

`SCAN_BOMLENS_NETWORK` defaults to `trustedoss-scan-egress` in compose. To limit stage 1 to the registries you use, add an egress firewall to that network.

:::warning Set `SCAN_ENGINE=bomlens` only with the engine worker running
Source scans queued with `SCAN_ENGINE=bomlens` go to `trustedoss.engine`, which only `worker-engine` consumes. Without it they stay queued, and a project with a queued scan does not start another, so the project's scans stop until the engine worker runs or the queued scans are cancelled. Start the profile before setting `SCAN_ENGINE=bomlens`, and set `SCAN_ENGINE=inprocess` before stopping it.
:::

When several TRUSCA installations share one Docker daemon, give each its own `TRUSCA_INSTANCE_ID`. Engine containers carry it as a label, and each installation's reaper removes only its own.

When `SCANCODE_ENABLED` is on, stage 2 uses the published BomLens variant with scancode (`bomlens-deep-license`), so first-party licence detection still runs.

The scope filter settings carry over: with `SCAN_SCOPE_FILTER_NON_DEPLOYABLE_ENABLED=false` (or `SCAN_SCOPE_FILTER_ENABLED=false`) BomLens keeps the test, example and benchmark trees it otherwise leaves out of the SBOM.

:::tip Run the Docker daemon with user namespaces
The engine's containers run as root inside the container. With the daemon's `userns-remap` setting, or a rootless Docker daemon, that root is an unprivileged user on the host, which limits what a container escape could reach. Both are daemon settings outside TRUSCA; turn one on where the host allows it.
:::

### Licences of the engine images {#engine-image-licences}

The engine images are not part of TRUSCA's images, Helm chart or offline bundle. The engine worker pulls them when a scan needs them and runs each as a separate container. Each image carries its own licence terms, which are summarised in section 4 of `THIRD_PARTY_NOTICES.md`.

| Image | Source | Licence | Notices and source inside the image |
|---|---|---|---|
| `ghcr.io/sktelecom/bomlens` | https://github.com/sktelecom/bomlens | Apache-2.0 | `LICENSE`, `NOTICE` and `THIRD_PARTY_LICENSES.md` in `/usr/local/lib/sbom/notices/` |
| `ghcr.io/sktelecom/bomlens-deep-license` | https://github.com/sktelecom/bomlens | Apache-2.0; adds scancode-toolkit (Apache-2.0 AND CC-BY-4.0) and LGPL components (7-Zip on linux/amd64, intbitset, chardet) | The same notices, and the source for 7-Zip and intbitset in `/usr/share/bomlens/sources/` |
| `ghcr.io/cdxgen/*` (stage 1, chosen by the plan) | https://github.com/CycloneDX/cdxgen | Apache-2.0 for cdxgen; the base image's terms for the rest | Differs by base image. `cdxgen-python312`, for example, is built on SUSE BCI and carries the SUSE BCI licence |

The tag and digest of the two BomLens images are `BOMLENS_IMAGE_DEFAULT` and `BOMLENS_DEEP_LICENSE_IMAGE_DEFAULT` in `apps/backend/core/config.py`. The cdxgen images a BomLens release can choose, with their digests, are listed in `/usr/local/lib/sbom/cdxgen-images.json` inside the BomLens image. TRUSCA does not run the BomLens firmware image.

To read an image's licence labels before you mirror it:

<!-- docs-uat: id=dynamic-scan-executor-engine-image-labels kind=shell ctx=host tier=manual waiver=needs-registry-access -->
```bash
skopeo inspect --override-os linux --override-arch amd64 \
  docker://ghcr.io/cdxgen/cdxgen-python312@sha256:<digest> | grep -i -E 'licenses|eula'
```

If you mirror these images into your own registry and make that registry available to others, you take on the licence obligations of each image, including passing on its notices and offering the corresponding source where its licence requires that.

### Turn the engine off {#engine-turn-off}

Both services have `restart: unless-stopped`, so a later `docker-compose up -d` without the profile leaves them running, and the proxy keeps the Docker API open. Stop and remove them explicitly:

<!-- docs-uat: id=dynamic-scan-executor-engine-off kind=shell ctx=host tier=manual waiver=env-config-snippet-not-a-command -->
```bash
# In the portal's .env
SCAN_ENGINE=inprocess

docker-compose up -d    # the API, beat and workers read SCAN_ENGINE again

# Once the engine worker is idle: remove engine containers still left (a scan
# in flight when the worker stops leaves its container behind). <instance> is
# TRUSCA_INSTANCE_ID, "default" unless you set it.
docker ps -aq --filter label=trusca.role=bomlens-engine \
  --filter label=trusca.instance=<instance> | xargs -r docker rm -f

docker-compose --profile bomlens-engine stop worker-engine docker-socket-proxy
docker-compose --profile bomlens-engine rm -f worker-engine docker-socket-proxy
```

Scans already queued for the engine stay queued until the engine worker runs again; cancel them, or start the profile once more to let them finish.

### Check that it ran {#engine-verify}

<!-- docs-uat: id=dynamic-scan-executor-engine-verify-log kind=manual tier=manual -->
- The `worker-engine` log shows `bomlens_plan` with the detected ecosystem, then one `bomlens_container_exit` per container with its exit code.

<!-- docs-uat: id=dynamic-scan-executor-engine-verify-metadata kind=manual tier=manual -->
- The scan's metadata carries `scan_engine` (the BomLens version, image digest and each step's status) and `scan_engine_run` (the plan status, stage 1's exit code, whether it was killed and whether the source was restored).

### Not in this preview {#engine-not-yet}

- The Helm chart (a Kubernetes Job runner is a later step).
- Reusing the SBOM of an earlier scan with an unchanged dependency set: a BomLens scan always runs the engine.
- Private package-registry credentials for stage 1 (the `/etc/trusca/registry` files the in-process pipeline reads), and the SCANOSS step.
- A proxy that inspects container-create requests, and running the engine's containers under a separate runtime.

## Environment variable reference

These mirror the **Dynamic scan executor** section of `.env.example`. All are read at runtime via `os.getenv` — edit `.env` and restart the worker. See [Environment variables → Scan pipeline](../reference/env-variables.md#scan-pipeline) for the canonical reference.

| Key | Default | Description |
|---|---|---|
| `SCAN_EXECUTOR` | `inprocess` | `inprocess` runs cdxgen as a worker subprocess; `local_docker` launches a per-environment sidecar over the Docker socket (on-prem only). |
| `SCAN_ENGINE` | `inprocess` | Preview. `bomlens` runs source scans on the BomLens engine (see [Scan engine](#scan-engine)). |
| `SCAN_BOMLENS_IMAGE` | pinned in the release | BomLens image for the plan and stage 2. An override must be `repo:tag@sha256:...` with a tag other than `latest`. |
| `SCAN_BOMLENS_DEEP_LICENSE_IMAGE` | pinned in the release | BomLens variant with scancode, used for stage 2 when `SCANCODE_ENABLED` is on. Same pinning rule. |
| `SCAN_BOMLENS_NETWORK` | empty (compose: `trustedoss-scan-egress`) | Network for stage 1, which resolves dependencies from the package registries. Empty refuses stage 1, as do `host`, the compose networks `trustedoss` and `trustedoss-docker-api`, and any network the worker itself is on. |
| `SCAN_BOMLENS_STAGE1_IMAGE_PREFIX` | empty | Where stage 1's cdxgen language images are pulled from: everything before the image name, for example `registry.example/mirror/cdxgen/`. Empty pulls them from `ghcr.io/cdxgen/`. Passed to the execution plan as BomLens's `CDXGEN_IMAGE_PREFIX`; the digests stay the published ones, so the mirror must hold every platform under the same digest. It needs a path after the registry host, and should name a path that holds only the cdxgen images (for a pull-through cache, down to `/<proxy-project>/cdxgen/`). See [Offline install → Stage 1 images](./offline-install.md#bomlens-stage1-images). |
| `SCAN_BOMLENS_STAGE1_REPOSITORIES` | empty (`ghcr.io/cdxgen/`) | Comma-separated repository prefixes, each ending in `/`, that a plan's stage 1 image must start with. Empty allows `ghcr.io/cdxgen/`, or only `SCAN_BOMLENS_STAGE1_IMAGE_PREFIX` when that is set; the image prefix is always allowed. |
| `SCAN_BOMLENS_STAGE1_READ_ONLY_ROOT` | `true` | `false` gives stage 1 a writable root filesystem, which Python's `pip install` build preparation needs; the build then runs with a writable image. |
| `SCAN_BOMLENS_STAGE1_TIMEOUT_SECONDS` | `1800` | Time limit of stage 1 before it is killed and the source is restored. |
| `SCAN_BOMLENS_STAGE2_TIMEOUT_SECONDS` | `1800` | Time limit of stage 2. A stage 2 that is killed fails the scan. |
| `SCAN_TRIVY_CACHE_VOLUME` | empty | Named volume of the worker's Trivy database, mounted read-only into stage 2 (compose: `<project>_trivy-cache`). |
| `SCAN_LOCAL_DOCKER_ENVS` | `android` | Comma-separated environments routed to a sidecar, or `all` to route every one. Only `android` is a detection gap; widen for per-build isolation (see below). |
| `SCAN_DOCKER_VOLUME_STRATEGY` | `named` | `named` mounts only the workspace volume into the sidecar; `volumes_from` re-mounts every worker volume (refused without the ack below, and always refused by the BomLens engine); `bind` is for the BomLens engine on a worker that runs on the Docker host itself. |
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
