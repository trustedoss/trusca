"""On-prem sidecar executor — run cdxgen in an environment-specific container.

For an Android source tree the worker has no Android SDK, so the Android Gradle
Plugin cannot resolve the dependency graph and cdxgen yields 0 components. This
executor routes ``detected_env == "android"`` to a one-shot
``sbom-scanner-android-sdk<API>`` sidecar (started via the host Docker socket)
that runs ``gradle :app:dependencies`` + cdxgen and writes the SBOM into the
shared workspace volume, which the worker then continues to process exactly as
before (sign / scancode / trivy / persist).

Every other environment degrades to the in-process executor — increment 5 widens
the routed set. The whole path is gated behind ``SCAN_EXECUTOR=local_docker``
(default ``inprocess``); it is on-prem only (the Docker socket is a host-escape
surface — increment 6 hardens it behind a socket proxy + egress allow-list).

Constraints handled here:
- The workspace is a *named volume*, so the sidecar reaches ``source_dir`` via
  ``--volumes-from`` (path-identical) — see :mod:`._docker_volume`.
- The build-prep script is passed *inline* (``sh -c <script>``) because a script
  on the worker's filesystem cannot be bind-mounted into the sidecar (same
  named-volume constraint).
- Celery's SIGTERM revoke does not reach the sidecar (not a child), so the
  container is force-removed in a ``finally``. A hard SIGKILL can still orphan it;
  a startup reaper is deferred to increment 6.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import structlog

from core.config import scan_backend_mode
from integrations import cdxgen as cdxgen_adapter
from integrations._line_streamer import LineCallback, run_with_line_streaming
from integrations.scan_executor import source_detect
from integrations.scan_executor._docker_volume import DockerVolumeError, volume_run_args
from integrations.scan_executor.base import (
    CancelCheck,
    PrepHook,
    SbomGenRequest,
    SbomGenResult,
    ScanExecutor,
    StageHook,
)
from integrations.scan_executor.inprocess import InProcessExecutor

log = structlog.get_logger("integrations.scan_executor.local_docker")

# Android build-prep, shipped beside this module and passed inline to the sidecar.
_BUILD_PREP_ANDROID = Path(__file__).with_name("build_prep_android.sh")

# Matches the cdxgen adapter's default per-stage timeout.
_DEFAULT_SIDECAR_TIMEOUT = 30 * 60

# Gradle spawns many helper processes; keep the pids cap generous.
_DEFAULT_PIDS_LIMIT = "4096"

# Capability hardening (increment 6). Drop ALL Linux capabilities, then add back
# only the minimal set build tools need. Verified on Colima against the Android
# gradle/AGP build: `--cap-drop=ALL` alone yields 0 components ("No packages
# found"); restoring these five (file ownership + setuid/setgid for process
# forking) restores the full 67-component graph. This removes the ~9 Docker
# default caps a scan never needs (NET_RAW, NET_BIND_SERVICE, MKNOD, SYS_CHROOT,
# KILL, AUDIT_WRITE, SETPCAP, SETFCAP, NET_BIND). Operators can retune both knobs
# for other ecosystems (CLAUDE.md rule #11).
_DEFAULT_CAP_DROP = "ALL"
_DEFAULT_CAP_ADD = "CHOWN,DAC_OVERRIDE,FOWNER,SETGID,SETUID"

# Bound the untrusted build's resources by default so it cannot OOM the host
# (which would crash-loop Postgres on a shared box). pids alone does not bound
# memory. Operators retune via env (rule #11).
_DEFAULT_MEMORY = "4g"
_DEFAULT_CPUS = "2"

# Sidecar labels so an orphan reaper can find exactly our containers.
_LABEL_ROLE = "trusca.role=scan-sidecar"

# Redact a PEM private-key block if an untrusted build echoes one to stderr
# (the streamed log lines pass through the shared credential scrubber, but that
# one does not match PEM blocks — and a key would never be present at all once
# the named-volume default stops sharing /cosign; this is defense in depth).
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)


class _UnpinnedImageError(RuntimeError):
    """The resolved sidecar image is not reproducibly pinned (floating tag)."""


def _allow_unpinned_image() -> bool:
    return os.getenv("SCAN_ALLOW_UNPINNED_IMAGE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _pids_limit() -> str:
    return os.getenv("SCAN_SIDECAR_PIDS_LIMIT", _DEFAULT_PIDS_LIMIT)


def _safe_token(name: str, value: str) -> str:
    """Reject an env value that could smuggle an extra ``docker run`` flag.

    Values flow into the argv as single tokens, but a value containing
    whitespace (``"host --privileged"``) or a leading dash could be re-read as a
    separate flag if a future edit splits it. Fail closed on anything suspicious.
    """
    v = value.strip()
    if not v or v != value or " " in v or "\t" in v or v.startswith("-"):
        raise DockerVolumeError(
            f"unsafe value for {name!r}: must be a single bare token, got {value!r}",
        )
    return v


def _cap_flag_args(env_name: str, default: str, flag: str) -> list[str]:
    """Expand a comma-separated capability list into repeated ``flag`` args."""
    args: list[str] = []
    for cap in os.getenv(env_name, default).split(","):
        cap = cap.strip()
        if cap:
            args += [flag, cap]
    return args


def _security_run_args() -> list[str]:
    """Sidecar isolation flags: no privilege escalation, pids cap, dropped caps."""
    args = [
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        _pids_limit(),
    ]
    args += _cap_flag_args("SCAN_SIDECAR_CAP_DROP", _DEFAULT_CAP_DROP, "--cap-drop")
    args += _cap_flag_args("SCAN_SIDECAR_CAP_ADD", _DEFAULT_CAP_ADD, "--cap-add")
    return args


class LocalDockerExecutor(ScanExecutor):
    """Run the SBOM-generation stage in a per-environment Docker sidecar."""

    name = "local_docker"

    def generate_sbom(
        self,
        request: SbomGenRequest,
        *,
        prep: PrepHook | None = None,
        stage: StageHook | None = None,
        line_callback: LineCallback | None = None,
        cancel_check: CancelCheck | None = None,
    ) -> SbomGenResult:
        # Mock backend (tests / smoke) is an in-process concern — never spawn a
        # real sidecar for it.
        if scan_backend_mode() == "mock":
            return self._fallback(request, prep, stage, line_callback, cancel_check)

        # Increment 3 routes only Android through the sidecar; everything else
        # runs in-process (same prep + cdxgen the worker always used).
        if request.detected_env != "android":
            log.info(
                "local_docker_fallback_inprocess",
                scan_id=str(request.scan_uuid),
                detected_env=request.detected_env,
                reason="env_not_routed",
            )
            return self._fallback(request, prep, stage, line_callback, cancel_check)

        if shutil.which("docker") is None:
            log.warning(
                "local_docker_no_docker_cli_fallback_inprocess",
                scan_id=str(request.scan_uuid),
            )
            return self._fallback(request, prep, stage, line_callback, cancel_check)

        # Sidecar owns prep (gradle) internally; advance stages to preserve the
        # progress contract, then run the container.
        if stage is not None:
            stage("prep")
        if stage is not None:
            stage("cdxgen")
        try:
            return self._run_android(request, line_callback=line_callback)
        except (DockerVolumeError, _UnpinnedImageError):
            # Misconfiguration (no workspace volume named / unpinned image / unsafe
            # env value) must not fail the scan — degrade to in-process and warn.
            log.warning(
                "local_docker_config_unsafe_fallback_inprocess",
                scan_id=str(request.scan_uuid),
                exc_info=True,
            )
            # Stages already advanced; fallback re-advances harmlessly (idempotent).
            return self._fallback(request, prep, stage, line_callback, cancel_check)

    # ----------------------------------------------------------------- helpers

    def _fallback(
        self,
        request: SbomGenRequest,
        prep: PrepHook | None,
        stage: StageHook | None,
        line_callback: LineCallback | None,
        cancel_check: CancelCheck | None,
    ) -> SbomGenResult:
        return InProcessExecutor().generate_sbom(
            request,
            prep=prep,
            stage=stage,
            line_callback=line_callback,
            cancel_check=cancel_check,
        )

    def _run_android(
        self, request: SbomGenRequest, *, line_callback: LineCallback | None
    ) -> SbomGenResult:
        api = source_detect.android_compile_sdk(request.source_dir)
        image = source_detect.android_image(api)
        # Supply-chain: refuse to run an unpinned (`:latest`) third-party image
        # unless the operator explicitly allows it (dev). Pin via
        # SCAN_ANDROID_IMAGE_TAG=<semver|sha256:…> in production (CLAUDE.md rule #9).
        if not source_detect.image_is_pinned(image) and not _allow_unpinned_image():
            raise _UnpinnedImageError(
                f"refusing to run unpinned image {image!r}; pin via "
                "SCAN_ANDROID_IMAGE_TAG (semver or sha256:<digest>) or set "
                "SCAN_ALLOW_UNPINNED_IMAGE=1 for dev",
            )
        request.output_dir.mkdir(parents=True, exist_ok=True)
        sbom_path = request.output_dir / "cdxgen.cdx.json"
        container = f"truscan-{request.scan_uuid.hex}"
        timeout = request.timeout_seconds or _DEFAULT_SIDECAR_TIMEOUT

        cmd = self._docker_cmd(
            request, image=image, sbom_path=sbom_path, container=container
        )
        log.info(
            "local_docker_android_start",
            scan_id=str(request.scan_uuid),
            image=image,
            compile_sdk=api,
            container=container,
        )

        try:
            completed = run_with_line_streaming(
                cmd,
                timeout_seconds=timeout,
                cwd=None,
                env=None,  # the docker CLI inherits the worker env (PATH/DOCKER_HOST)
                line_callback=line_callback,
                stage="cdxgen",
            )
        except subprocess.TimeoutExpired as exc:
            raise cdxgen_adapter.CdxgenTimeout(
                f"android sidecar exceeded {timeout}s for scan {request.scan_uuid}",
            ) from exc
        finally:
            # --rm handles the normal exit; this reclaims a container left alive by
            # a timeout-killed CLI or an interrupted/cancelled run. Best-effort.
            self._force_remove(container)

        if completed.returncode != 0:
            # The sidecar runs untrusted build code that could echo a secret to
            # stderr; redact PEM private-key blocks before this lands in the
            # persisted scan error.
            stderr = _PRIVATE_KEY_RE.sub(
                "***(private key redacted)***",
                completed.stderr.decode("utf-8", errors="replace")[:1000],
            )
            raise cdxgen_adapter.CdxgenFailed(
                f"android sidecar exited {completed.returncode}: {stderr}",
            )
        if not sbom_path.exists():
            raise cdxgen_adapter.CdxgenFailed(
                f"android sidecar produced no SBOM at {sbom_path}",
            )

        sbom = _load_sbom(sbom_path)
        log.info(
            "local_docker_android_succeeded",
            scan_id=str(request.scan_uuid),
            components=len(sbom.get("components", [])),
            sbom_size_bytes=sbom_path.stat().st_size,
        )
        return SbomGenResult(
            sbom_path=sbom_path,
            sbom=sbom,
            executor=self.name,
            image=image,
            detected_env="android",
        )

    def _docker_cmd(
        self,
        request: SbomGenRequest,
        *,
        image: str,
        sbom_path: Path,
        container: str,
    ) -> list[str]:
        build_prep = _BUILD_PREP_ANDROID.read_text(encoding="utf-8")
        cmd = ["docker", "run", "--rm", "--name", container]
        # Label so an orphan reaper can target exactly our sidecars.
        cmd += ["--label", _LABEL_ROLE, "--label", f"trusca.scan={request.scan_uuid.hex}"]
        cmd += _security_run_args()

        # Resource bounds default ON (untrusted build must not OOM the host).
        memory = _safe_token(
            "SCAN_SIDECAR_MEMORY", os.getenv("SCAN_SIDECAR_MEMORY", _DEFAULT_MEMORY)
        )
        cmd += ["--memory", memory]
        cpus = _safe_token(
            "SCAN_SIDECAR_CPUS", os.getenv("SCAN_SIDECAR_CPUS", _DEFAULT_CPUS)
        )
        cmd += ["--cpus", cpus]

        # Egress: the build needs package registries (gradle → google/maven), so we
        # cannot block all egress. An unrestricted default bridge also reaches the
        # internal network (postgres/redis) + the internet (exfil/SSRF). Recommend
        # an isolated, allow-listed network via SCAN_SIDECAR_NETWORK; warn when unset.
        network = os.getenv("SCAN_SIDECAR_NETWORK", "").strip()
        if network:
            cmd += ["--network", _safe_token("SCAN_SIDECAR_NETWORK", network)]
        else:
            log.warning(
                "scan_sidecar_unrestricted_egress",
                scan_id=str(request.scan_uuid),
                detail=(
                    "sidecar runs on the default bridge with unrestricted egress; "
                    "set SCAN_SIDECAR_NETWORK to an isolated, allow-listed network"
                ),
            )

        cmd += volume_run_args()
        # Sidecar env is a CURATED ALLOW-LIST — never the worker's environment.
        # Worker secrets (SECRET_KEY / DATABASE_URL / *_WEBHOOK_URL / API keys)
        # MUST NOT reach an untrusted-build sidecar; only these three benign,
        # cdxgen-relevant vars are forwarded. (Enforced by a negative test.)
        cmd += ["-e", "HOME=/tmp/sbomhome"]
        if request.fetch_license:
            cmd += ["-e", "FETCH_LICENSE=true"]
        if request.verbose:
            cmd += ["-e", "CDXGEN_DEBUG_MODE=debug"]

        # --entrypoint sh ... -c <script> <argv0> <src> <out> <spec>
        # Validate the (operator-controlled) image ref as a single bare token too,
        # for defense-in-depth symmetry with the resource knobs.
        cmd += [
            "--entrypoint",
            "sh",
            _safe_token("image", image),
            "-c",
            build_prep,
            "build-prep",
            str(request.source_dir),
            str(sbom_path),
            request.spec_version,
        ]
        return cmd

    def _force_remove(self, container: str) -> None:
        rm_argv = ["docker", "rm", "-f", container]  # noqa: S607 — vetted binary, fixed argv
        try:
            subprocess.run(  # noqa: S603 — fixed argv, no shell
                rm_argv,
                capture_output=True,
                check=False,
                timeout=30,
            )
        except Exception:  # noqa: BLE001 — cleanup is strictly best-effort
            log.warning("local_docker_force_remove_failed", container=container)


def _load_sbom(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)
    return data


__all__ = ["LocalDockerExecutor"]
