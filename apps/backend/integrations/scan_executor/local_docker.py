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

# Gradle spawns many helper processes; keep the pids cap generous. Increment 6
# tightens resource bounds as part of the hardening pass.
_DEFAULT_PIDS_LIMIT = "4096"


def _pids_limit() -> str:
    return os.getenv("SCAN_SIDECAR_PIDS_LIMIT", _DEFAULT_PIDS_LIMIT)


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
        except DockerVolumeError:
            log.warning(
                "local_docker_volume_unresolved_fallback_inprocess",
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
            stderr = completed.stderr.decode("utf-8", errors="replace")[:1000]
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
        cmd = [
            "docker",
            "run",
            "--rm",
            "--name",
            container,
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            _pids_limit(),
        ]
        memory = os.getenv("SCAN_SIDECAR_MEMORY")
        if memory:
            cmd += ["--memory", memory]
        cpus = os.getenv("SCAN_SIDECAR_CPUS")
        if cpus:
            cmd += ["--cpus", cpus]
        network = os.getenv("SCAN_SIDECAR_NETWORK")
        if network:
            cmd += ["--network", network]

        cmd += volume_run_args()
        cmd += ["-e", "HOME=/tmp/sbomhome"]
        if request.fetch_license:
            cmd += ["-e", "FETCH_LICENSE=true"]
        if request.verbose:
            cmd += ["-e", "CDXGEN_DEBUG_MODE=debug"]

        # --entrypoint sh ... -c <script> <argv0> <src> <out> <spec>
        cmd += [
            "--entrypoint",
            "sh",
            image,
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
