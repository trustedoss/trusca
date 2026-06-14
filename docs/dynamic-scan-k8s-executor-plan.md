# KubernetesJobExecutor — implementation plan (deferred until SaaS is ready)

> Status: **planned, not started.** Do this when a multi-tenant SaaS Kubernetes
> environment exists. On-prem dynamic scanning (increments 1–6) is complete and
> shipped; this is increment 7 of the BomLens-style dynamic-scan arc.
> Local plan of record: `~/.claude/plans/bomlens-fluttering-flame.md`.

## Context — why this exists and why it waits

The dynamic-scan executor abstraction (`apps/backend/integrations/scan_executor/`)
already runs the SBOM-generation stage behind a pluggable `ScanExecutor`:

- `inprocess` (default) — cdxgen in the worker process.
- `local_docker` — a per-environment cdxgen **sidecar** over the host Docker
  socket, for **on-prem single-tenant** only (the socket is root-equivalent host
  control, so it is gated, opt-in, and hardened — see
  `docs-site/docs/admin-guide/dynamic-scan-executor.md`).

`local_docker` is deliberately **not** safe for multi-tenant SaaS: the Docker
socket cannot be safely shared across tenants, and a build is arbitrary code
execution. The SaaS answer is **model 2** — run each scan's build in a sandboxed,
ephemeral **Kubernetes Job** with a stronger isolation boundary (gVisor/Kata),
no host socket, restricted egress, and per-Job resource quotas. That needs a real
SaaS K8s cluster to build and verify against, so it is deferred until that
environment exists.

What this buys: the SaaS demo (and any future hosted offering) can run "give us a
git URL / upload and we build + scan it" safely, closing the same Android (and
future) toolchain gaps the on-prem `local_docker` path closes, but with isolation
strong enough for untrusted multi-tenant input.

## Prerequisites (the "SaaS is ready" checklist)

Do not start until all of these exist:

1. A Kubernetes cluster the worker can reach (in-cluster `ServiceAccount` or a
   kubeconfig), with permission to create/watch/delete Jobs in a dedicated
   namespace (e.g. `trusca-scan-jobs`).
2. A sandbox runtime installed and a `RuntimeClass` for it — **gVisor** (`gvisor`)
   or **Kata**. This is the core isolation primitive; without it this executor is
   not meaningfully safer than `local_docker`.
3. A way to exchange the source tree and the resulting SBOM between the worker and
   the Job — either a per-scan `PersistentVolumeClaim` (RWX or RWO with affinity)
   or object storage (S3/R2) with an init/sidecar sync. Decide this first (see
   Open decisions).
4. The per-environment cdxgen images reachable from the cluster (mirror the SKT
   Android image + the cdxgen language images into the SaaS registry; pin by
   digest — increment 6 already forbids `:latest`).
5. `NetworkPolicy` support in the cluster (CNI that enforces it) for egress
   restriction.

## What to reuse (do NOT rebuild)

The executor abstraction was built so this is mostly a new backend, not new
contracts:

- **Interface** — `integrations/scan_executor/base.py`: `SbomGenRequest`
  (`scan_uuid`, `source_dir`, `output_dir`, `detected_env`, `spec_version`,
  `fetch_license`, `verbose`, `timeout_seconds`) → `SbomGenResult`
  (`sbom_path`, `sbom`, `executor`, `image`, `detected_env`). Implement
  `generate_sbom(request, *, prep, stage, line_callback, cancel_check)` exactly as
  `local_docker.py` does. The pipeline seam in `tasks/scan_source.py:341-360` is
  unchanged.
- **Environment detection + image map** — `source_detect.py`
  (`detect_language`, `image_for_env`, `android_image`, `android_compile_sdk`,
  `image_is_pinned`). Identical routing logic.
- **Routed-env set** — `local_docker._routed_envs()` (`SCAN_LOCAL_DOCKER_ENVS`);
  add a parallel `SCAN_K8S_ROUTED_ENVS` or reuse one knob. The increment-5
  finding still holds: on-prem there is no detection delta for
  node/go/rust/ruby/java/python/php/dotnet, but in SaaS the **isolation** is the
  point, so the default routed set here should likely be **all** detected
  languages (every untrusted build wants the sandbox), not just `android`.
- **build-prep** — `build_prep_source.sh` (general, multi-language). Same script,
  delivered to the Job as a ConfigMap or inline `sh -c` (same constraint as
  `local_docker`: the workspace is not on the worker filesystem).
- **Image pinning** — `_resolve_image()` + `image_is_pinned()` + the
  `_UnpinnedImageError` → fallback pattern. Keep it.
- **Cancel/log contracts** — `cancel_check` hook + `line_callback` streaming +
  `_scrub_secrets`/PEM redaction. Same.
- **factory** — `factory.py` already routes `k8s_job` to a fallback; replace that
  branch with `KubernetesJobExecutor()`.

## Design

### New module: `integrations/scan_executor/k8s_job.py`

`KubernetesJobExecutor(ScanExecutor)`:

- **Client**: the official `kubernetes` Python client (in-cluster config via
  `load_incluster_config`, else kubeconfig). Pin the dep; add to backend
  requirements. (Alternative: `kubectl` subprocess like `local_docker` uses the
  docker CLI — heavier to template, prefer the client.)
- **`generate_sbom`**: same shape as `LocalDockerExecutor.generate_sbom` —
  mock-backend short-circuit, routed-env gate (else in-process fallback), then
  `_run_job(request, line_callback)`.
- **`_run_job`**:
  1. Resolve image (`_resolve_image`, reused) + pin guard.
  2. Stage the source tree where the Job can read it (PVC bind / object-store
     upload — see Open decisions).
  3. Build the Job manifest (below), `create_namespaced_job`.
  4. Watch to completion (`watch.Watch().stream` on the Job, or poll
     `read_namespaced_job_status`), tailing the pod's log into `line_callback`
     (`read_namespaced_pod_log` with `follow=True`), honouring `cancel_check`.
  5. On success, fetch the SBOM from the shared medium, `json.load`, return
     `SbomGenResult(..., executor="k8s_job")`.
  6. **finally**: `delete_namespaced_job(..., propagation_policy="Background")`
     (deletes the pod too). This is the SIGTERM-revoke / cancel cleanup — the Job
     is not a worker child, same lesson as the sidecar `docker rm -f`.

### Job manifest — the security spec (already decided in increment 6 review)

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  namespace: trusca-scan-jobs
  name: truscan-<scan_uuid.hex>
  labels: { trusca.role: scan-job, trusca.scan: <hex> }
spec:
  backoffLimit: 0                 # no retries of an untrusted build
  activeDeadlineSeconds: <timeout>
  ttlSecondsAfterFinished: 300    # GC backstop in addition to explicit delete
  template:
    spec:
      runtimeClassName: gvisor    # the isolation primitive (or kata)
      automountServiceAccountToken: false
      restartPolicy: Never
      securityContext:
        runAsNonRoot: true
        seccompProfile: { type: RuntimeDefault }
      containers:
        - name: cdxgen
          image: <pinned per-env image @sha256:...>
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true        # writable: emptyDir for HOME + workspace
            capabilities:
              drop: ["ALL"]
              add: ["CHOWN","DAC_OVERRIDE","FOWNER","SETGID","SETUID"]  # verified minimal set (incr. 6)
          resources:
            limits:   { memory: "4Gi", cpu: "2" }
            requests: { memory: "1Gi", cpu: "500m" }
          env:
            - { name: HOME, value: /tmp/sbomhome }
            # FETCH_LICENSE / CDXGEN_DEBUG_MODE conditionally — curated allow-list ONLY
          command: ["sh","-c", "<build_prep_source.sh>", "build-prep", "<src>", "<out>", "<spec>"]
          volumeMounts:
            - { name: work, mountPath: /workspace }
            - { name: home, mountPath: /tmp/sbomhome }
      volumes:
        - { name: work, persistentVolumeClaim: { claimName: ... } }   # or emptyDir + object sync
        - { name: home, emptyDir: {} }
```

Plus a per-namespace `NetworkPolicy` (egress allow-list to package registries
only — google/maven/npm/crates/pypi; **deny** the internal app namespace,
metadata IP `169.254.169.254`, postgres/redis), and a `ResourceQuota` /
`LimitRange` bounding concurrent Jobs. The minimal cap set
(`CHOWN,DAC_OVERRIDE,FOWNER,SETGID,SETUID`) is the increment-6 live-verified set —
`drop: ALL` alone broke the Android gradle build (0 components); these five
restored the full 67.

### Source/SBOM exchange — Open decision (decide first)

- **(A) Per-scan PVC**: worker writes the cloned/extracted tree to a PVC, Job
  mounts it RW, writes the SBOM back, worker reads it. Needs RWX (or RWO with the
  Job scheduled to the worker's node). Simplest mental model; storage-class
  dependent.
- **(B) Object storage (S3/R2)**: worker uploads a tarball; an init container in
  the Job pulls + unpacks; the cdxgen container writes the SBOM; a final step
  uploads it; worker downloads. No shared filesystem, cloud-portable, but more
  moving parts (init/sidecar). **Likely the SaaS-correct choice** (matches the
  Hetzner/R2 backup posture already in the repo).

Pick one before implementing `_run_job`. The `SbomGenRequest.source_dir` /
`output_dir` are worker paths; the executor translates them to the chosen medium.

## Increment breakdown (when started)

1. **Job manifest + security policy as Helm templates** (`charts/trustedoss/`):
   Job template, `NetworkPolicy`, `ResourceQuota`, `RuntimeClass` assumption
   documented. (devops-engineer.)
2. **`KubernetesJobExecutor`** create/watch/log-tail/delete + source-SBOM exchange
   (the Open decision). Wire `factory.py`. (backend-developer / scan-pipeline-specialist.)
3. **Security review** (Producer-Reviewer, required — same gate as increment 6):
   verify no service-account token mount, egress actually denied to internal
   services, no secret in Job env, pinned images, Job-delete cleanup on cancel.
4. **Docs**: extend `admin-guide/dynamic-scan-executor.md` (EN+KO) with the SaaS
   model 2 section; `.env.example` K8s knobs.

## Verification (when started)

- **kind / minikube** with gVisor (or a documented "gVisor assumed" gap if the
  local cluster can't run it): run a **Python** and an **Android** scan through
  `SCAN_EXECUTOR=k8s_job`; assert the Android Job yields the full graph (the same
  0→67 gap the sidecar closed) and Python parity.
- **NetworkPolicy egress test**: a Job that curls an internal service
  (postgres/redis/metadata IP) must fail; a Job that fetches from the allowed
  registry must succeed.
- **Cancel**: revoke a running scan → assert the Job + pod are deleted (no
  orphan), mirroring the sidecar `docker ps -a` check.
- **Mock parity**: `SCAN_EXECUTOR` default stays `inprocess`; existing scans
  unchanged.

## Carry-over follow-ups (from increment 3/6 reviews — fold in here)

- **Orphan reaper**: the sidecar labels `trusca.role` / `trusca.scan` exist but
  the reaper body was deferred. K8s gives this for free via
  `ttlSecondsAfterFinished` + a label-selector sweep; implement the equivalent
  startup/beat sweep for both executors.
- **`_safe_token` symmetry**: extend the create-payload validation to the image
  ref in `local_docker` (partially done) and mirror in the K8s path (no
  user-controlled field should reach the manifest unvalidated).
- The security reviewer flagged that the K8s path "recurs the same volume/secret
  question with different mechanics (projected volumes / service-account tokens)"
  — `automountServiceAccountToken: false` + no secret env is the answer; verify it
  in the review.

## Open decisions to settle when SaaS is ready

1. Source/SBOM exchange: PVC (A) vs object storage (B). Recommend B for SaaS.
2. Routed-env default for K8s: likely **all** detected languages (isolation is the
   point), unlike on-prem's android-only. Confirm.
3. Sandbox runtime: gVisor vs Kata (cluster-dependent).
4. Namespace-per-tenant vs shared namespace + NetworkPolicy isolation.
5. Whether to retire `local_docker` once `k8s_job` exists, or keep both (on-prem
   vs SaaS). Likely keep both — different deployment targets.
