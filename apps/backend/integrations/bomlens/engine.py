# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""TRUSCA's side of the BomLens engine runner (``bomlens-runner``).

The package runs the engine's execution plan, stage 1 and stage 2 or the image
scan in containers and removes them again. This module is the only place that
imports it. It builds the package's policy from TRUSCA's settings, keeps the
checks the package does more loosely than TRUSCA did, and turns its outcomes
and errors into the ones the scan tasks already handle.

What stays here, on purpose:

* the mirror prefix check (:func:`stage1_image_prefix`): the package admits a
  prefix that is only a registry host, which would allow every image in it;
* the refusal of a run whose stage 1 could not start: the package carries on
  with stage 2 then (a reduced scan), TRUSCA fails the scan, as before;
* the refusal of a missing Trivy database, and of the compose networks of the
  database and the Docker API as an egress network;
* the last lines the result container printed, cleaned of credentials and
  control characters, in the error of a run that left no manifest;
* the scan-cancellation poll, which reads the scan's status.

Nothing here reads a result: :mod:`integrations.bomlens.contract` and
:mod:`tasks.bomlens_result` still do, from the folder the package fills.
"""

from __future__ import annotations

import os
import re
import shutil
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import structlog
from bomlens_runner import (
    BomLensError as PackageError,
)
from bomlens_runner import (
    EngineContainer,
    Images,
    RunnerPolicy,
    ScanCancelled,
    SecretRef,
    Timeouts,
    Volume,
    run_image_scan,
    run_source_scan,
)
from bomlens_runner import (
    EngineWroteNoResult as PackageWroteNoResult,
)
from bomlens_runner.docker import DockerBackend, DockerOptions

from core.config import (
    bomlens_deep_license_image,
    bomlens_image,
    bomlens_image_scan_timeout_seconds,
    bomlens_network,
    bomlens_stage1_image_prefix,
    bomlens_stage1_read_only_root,
    bomlens_stage1_repositories,
    bomlens_stage1_timeout_seconds,
    bomlens_stage2_timeout_seconds,
    bomlens_trivy_cache_volume,
    trusca_instance_id,
    workspace_root,
)
from integrations.bomlens import contract
from integrations.bomlens.runner import (
    ERROR_TAIL_LINE_CHARS,
    ERROR_TAIL_LINES,
    image_target,
    is_loopback_proxy,
    output_tail,
    trivy_db_max_age_days,
)

log = structlog.get_logger("integrations.bomlens.engine")

#: Docker networks that are the compose files' own (database, Docker API), on
#: top of the ones the package refuses (``host``, ``none``, ``bridge``,
#: ``default``). An engine container on either would reach TRUSCA's database.
REFUSED_NETWORKS: Final = ("trustedoss", "trustedoss-docker-api")

#: The last lines of each stage's output kept for an error message.
_TAIL_LINES_KEPT: Final = 60
_TAIL_LINE_MAX_CHARS: Final = 4096

#: Names the stage 1 container may take from the worker's environment, when the
#: worker has them set: the proxy, the Go module proxy and the build knobs.
STAGE1_WORKER_ENV: Final = frozenset(
    {
        "BOMLENS_KEEP_BUILD_OUTPUT",
        "BOMLENS_MAVEN_FULL_GRAPH",
        "BOMLENS_ANDROID_FULL_GRAPH",
        "BOMLENS_NODE_FULL_GRAPH",
        "BOMLENS_PHP_FULL_GRAPH",
        "BOMLENS_NO_COPYRIGHT",
        "BOMLENS_NO_CARGO_LICENSE",
        "BOMLENS_PREP_TIMEOUT",
        "GOPROXY",
        "GOSUMDB",
    }
)
_PROXY_ENV: Final = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
)
_PROXY_ADDRESS_ENV: Final = frozenset(n for n in _PROXY_ENV if n.lower() != "no_proxy")

#: What a worker-environment value may be to be handed on.
_VALUE_MAX_CHARS: Final = 1024

#: The secret store name the worker's own environment answers to.
_WORKER_SECRETS: Final = "worker-env"

#: Volume names the package validates as claim names. Docker mounts the names
#: in :class:`~bomlens_runner.docker.DockerOptions`, so these are placeholders.
_WORKSPACE_VOLUME: Final = "workspace"
_TRIVY_VOLUME: Final = "trivy-db"

#: ``SCAN_BOMLENS_STAGE1_IMAGE_PREFIX``: a registry host, then a repository path
#: of at least one part, with or without the final ``/``. A host alone
#: (``localhost``, ``reg.example:5000``) is refused: the allowed-repository check
#: admits everything under the prefix, so it must name a path that holds only
#: the cdxgen images. No scheme, tag, digest, space, upper case or shell
#: character gets through.
_STAGE1_PREFIX_RE: Final = re.compile(
    r"(?P<host>[a-z0-9]+(?:[.-][a-z0-9]+)*(?::[0-9]{1,5})?)"
    r"(?:/[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*)+/?"
)
_STAGE1_PREFIX_MAX_CHARS: Final = 200


#: The package's own error texts carry no value, path or output (its design),
#: so they are kept as written, only bounded.
_MESSAGE_MAX_CHARS: Final = 300


def _message(exc: Exception) -> str:
    text = str(exc)
    return text if len(text) <= _MESSAGE_MAX_CHARS else text[: _MESSAGE_MAX_CHARS - 3] + "..."


class BomLensEngineError(contract.BomLensError):
    """The engine could not be run, or left nothing a scan can use."""


class EngineScanCancelled(Exception):  # noqa: N818 - reads as the event it reports
    """The scan was cancelled while the engine ran."""


class EngineWroteNoResult(BomLensEngineError):
    """The result container exited without a manifest."""


def stage1_image_prefix(value: str) -> str | None:
    """The checked ``SCAN_BOMLENS_STAGE1_IMAGE_PREFIX`` with its final ``/``, or ``None``.

    Empty means the setting is off. Anything the pattern does not match raises,
    before a container starts.
    """
    text = value.strip()
    if not text:
        return None
    if len(text) > _STAGE1_PREFIX_MAX_CHARS or _STAGE1_PREFIX_RE.fullmatch(text) is None:
        # The value is not repeated: it may carry a login, and the message
        # reaches the scan's error and the worker's log.
        raise BomLensEngineError(
            "SCAN_BOMLENS_STAGE1_IMAGE_PREFIX is not in the right form: use a registry host "
            "followed by a repository path (for example registry.example/mirror/cdxgen/)"
        )
    return text if text.endswith("/") else text + "/"


# --------------------------------------------------------------------------
# Policy and backend
# --------------------------------------------------------------------------


def _worker_env(names: frozenset[str] | tuple[str, ...]) -> dict[str, object]:
    """The listed names that the worker's environment sets, fit to hand on.

    A proxy address that names a loopback host is left out (the container is
    not the worker's host). A proxy address with a login in it is passed as a
    reference the Docker backend resolves when it creates the container, so it
    never sits in a command line.
    """
    env: dict[str, object] = {}
    for name in sorted(names):
        value = os.environ.get(name)
        if value is None or value == "":
            continue
        if len(value) > _VALUE_MAX_CHARS or any(c in value for c in "\x00\n\r"):
            log.warning("bomlens_env_passthrough_dropped", name=name)
            continue
        if name in _PROXY_ADDRESS_ENV and is_loopback_proxy(value):
            log.info("bomlens_loopback_proxy_not_passed", name=name)
            continue
        if name in _PROXY_ADDRESS_ENV and "@" in value:
            env[name] = SecretRef(_WORKER_SECRETS, name)
            continue
        env[name] = value
    return env


def _resolve_secret(ref: SecretRef) -> str:
    """Answer a :class:`SecretRef` from the worker's environment."""
    if ref.name != _WORKER_SECRETS:
        raise BomLensEngineError("an engine setting refers to a secret store TRUSCA does not have")
    value = os.environ.get(ref.key)
    if value is None:
        raise BomLensEngineError("an engine setting refers to a variable the worker does not have")
    return value


def _volumes() -> tuple[Volume, Volume | None, DockerOptions]:
    """The workspace and Trivy database volumes and the Docker options for them.

    ``SCAN_DOCKER_VOLUME_STRATEGY`` selects named volumes (compose) or host
    folders (a worker on the Docker host), read per call. ``volumes_from`` has
    no equivalent: it would hand the worker's volumes, the signing key
    included, to containers that run the scanned project's build.
    """
    strategy = os.getenv("SCAN_DOCKER_VOLUME_STRATEGY", "named").strip().lower()
    instance = {"trusca.instance": trusca_instance_id()}
    network = bomlens_network() or None
    if strategy == "named":
        volume = os.getenv("SCAN_WORKSPACE_VOLUME", "").strip()
        if not volume:
            raise BomLensEngineError("SCAN_WORKSPACE_VOLUME is not set")
        trivy = bomlens_trivy_cache_volume()
        if not trivy:
            raise BomLensEngineError(
                "SCAN_TRIVY_CACHE_VOLUME is not set; stage 2 has no Trivy database to read"
            )
        local_root = Path(os.getenv("SCAN_WORKSPACE_MOUNT", "/tmp/trustedoss"))  # noqa: S108
        options = DockerOptions(
            volume_strategy="named",
            workspace=volume,
            trivy_db=trivy,
            egress_network=network,
            refuse_networks=REFUSED_NETWORKS,
            instance_labels=instance,
            secrets=_resolve_secret,
        )
        return (
            Volume(_WORKSPACE_VOLUME, local_root),
            Volume(_TRIVY_VOLUME, Path("/")),
            options,
        )
    if strategy == "bind":
        from integrations.trivy import trivy_cache_dir

        cache = Path(os.path.realpath(trivy_cache_dir()))
        if not (cache / "db").is_dir():
            raise BomLensEngineError("the worker's Trivy database has not been downloaded yet")
        root = Path(workspace_root())
        options = DockerOptions(
            volume_strategy="bind",
            workspace=str(root),
            trivy_db=str(cache),
            egress_network=network,
            refuse_networks=REFUSED_NETWORKS,
            instance_labels=instance,
            secrets=_resolve_secret,
        )
        return Volume(_WORKSPACE_VOLUME, root), Volume(_TRIVY_VOLUME, cache), options
    raise BomLensEngineError(
        f"SCAN_DOCKER_VOLUME_STRATEGY={contract.clip(strategy)!r} is not usable by the BomLens "
        "engine (use named or bind)"
    )


def build_policy(
    *, deep_license: bool, include_non_shipped: bool
) -> tuple[RunnerPolicy, DockerOptions]:
    """The package's policy and Docker options from TRUSCA's settings, read per call."""
    prefix = stage1_image_prefix(bomlens_stage1_image_prefix())
    getuid, getgid = getattr(os, "getuid", None), getattr(os, "getgid", None)
    if getuid is None or getgid is None:
        raise BomLensEngineError("the BomLens engine needs a POSIX worker")
    workspace, trivy_db, options = _volumes()
    stage1_env = _worker_env(STAGE1_WORKER_ENV | frozenset(_PROXY_ENV))
    stage2_env: dict[str, object] = {"BOMLENS_TRIVY_DB_MAX_AGE_DAYS": str(trivy_db_max_age_days())}
    if include_non_shipped:
        stage1_env["BOMLENS_INCLUDE_NON_SHIPPED"] = "true"
        stage2_env["BOMLENS_INCLUDE_NON_SHIPPED"] = "true"
    image_env = _worker_env(frozenset(_PROXY_ENV))
    image_env["BOMLENS_TRIVY_DB_MAX_AGE_DAYS"] = str(trivy_db_max_age_days())
    try:
        images = Images.parse(
            bomlens_image(), bomlens_deep_license_image() if deep_license else None
        )
        policy = RunnerPolicy(
            images=images,
            workspace=workspace,
            trivy_db=trivy_db,
            stage1_repositories=bomlens_stage1_repositories(prefix),
            stage1_image_prefix=prefix,
            timeouts=Timeouts(
                stage1=bomlens_stage1_timeout_seconds(),
                stage2=bomlens_stage2_timeout_seconds(),
                image=bomlens_image_scan_timeout_seconds(),
            ),
            uid=getuid(),
            gid=getgid(),
            stage1_read_only_root=bomlens_stage1_read_only_root(),
            stage1_env=stage1_env,
            stage2_env=stage2_env,
            image_env=image_env,
            deep_license=deep_license,
        )
    except PackageError as exc:
        raise BomLensEngineError(_message(exc)) from None
    return policy, options


# --------------------------------------------------------------------------
# Running
# --------------------------------------------------------------------------


@dataclass
class _Output:
    """What each stage printed: lines go on to the scan log, the end is kept."""

    sink: Callable[[str], Callable[[str, str], None]] | None
    tails: dict[str, deque[str]] = field(default_factory=dict)
    callbacks: dict[str, Callable[[str, str], None]] = field(default_factory=dict)
    #: The stage that printed last: the one a run with no result is about.
    last_stage: str | None = None

    def __call__(self, stage: str, line: str) -> None:
        self.last_stage = stage
        tail = self.tails.setdefault(stage, deque(maxlen=_TAIL_LINES_KEPT))
        tail.append(line[:_TAIL_LINE_MAX_CHARS])
        if self.sink is None:
            return
        callback = self.callbacks.get(stage)
        if callback is None:
            callback = self.callbacks[stage] = self.sink(stage)
        # The package merges stderr into stdout, so every line reads as stdout.
        callback("stdout", line)

    def tail(self, stage: str) -> list[str]:
        """The last cleaned lines of ``stage``'s output."""
        lines = self.tails.get(stage)
        if not lines:
            return []
        return output_tail("\n".join(lines).encode("utf-8", errors="replace"))


@dataclass(frozen=True)
class EngineRun:
    """A finished run: the folder the engine wrote, and how it went.

    ``stage1_window`` is when stage 1 really ran (started and ended, UTC): the
    manifest's ``generate`` step is stamped by stage 2 and says nothing about it.
    """

    output_dir: Path
    verified_dir: Path
    metadata: dict[str, Any]
    run_status: str | None
    stage1_window: tuple[datetime, datetime] | None = None


@dataclass
class RunRecord:
    """What a run did, for the scan's metadata. ``metadata`` is ``None`` when the
    run ended before the package returned (an error), the image is always known."""

    image_ref: str
    image_digest: str
    metadata: dict[str, Any] | None = None


#: The progress labels the scan pipeline shows, by the package's stage names.
_STAGE_LABELS: Final = {"prep": "prep", "cdxgen": "cdxgen", "postprocess": "scancode"}


def _stage_hook(on_stage: Callable[[str], None] | None) -> Callable[[str], None] | None:
    """``on_stage`` under the labels the pipeline shows. A cancellation raised by
    it stops the run as the package's own does."""
    if on_stage is None:
        return None

    def announce(name: str) -> None:
        try:
            on_stage(_STAGE_LABELS.get(name, name))
        except EngineScanCancelled:
            raise ScanCancelled("the scan was cancelled") from None

    return announce


def _cancel_poll(is_cancelled: Callable[[], bool] | None) -> Callable[[], bool] | None:
    """``is_cancelled``, read on every call until it says yes, then not again.

    The package asks about once a second while a container runs, and before it
    starts the next one. Answering the second kind from a cache would let a
    container start after the scan was cancelled, so nothing is cached but the
    answer yes.
    """
    if is_cancelled is None:
        return None
    cancelled = False

    def poll() -> bool:
        nonlocal cancelled
        if not cancelled:
            cancelled = bool(is_cancelled())
        return cancelled

    return poll


def _fresh_verified_dir(scan_dir: Path) -> Path:
    """A new folder for the adapter's verified copies, beside the engine's folder.

    Not inside the folder the containers mount: no container, the ownership
    reset included, has it mounted. Whatever was there (a link included) is
    removed, not followed. Called after every engine container is gone.
    """
    path = scan_dir / "bomlens-verified"
    if path.is_symlink():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def _failure(exc: PackageError, output: _Output) -> Exception:
    """The TRUSCA error for a package error."""
    if isinstance(exc, ScanCancelled):
        return EngineScanCancelled("the scan was cancelled")
    if isinstance(exc, PackageWroteNoResult):
        result_stage = output.last_stage or ""
        tail = output.tail(result_stage)
        log.error("bomlens_engine_no_result", stage=result_stage, output_tail=tail)
        short = " | ".join(line[:ERROR_TAIL_LINE_CHARS] for line in tail[-ERROR_TAIL_LINES:])
        # "BomLens " in front keeps the wording the scan's error always had.
        return EngineWroteNoResult(
            "BomLens " + _message(exc) + (f"; last output: {short}" if short else "")
        )
    return BomLensEngineError(_message(exc))


def engine_backend() -> Any:
    """The package's Docker backend as the settings describe it, for what is not a run.

    The reaper lists and removes engine containers with it. Its options carry
    the instance label, so the backend only ever sees this installation's
    containers.
    """
    _, _, options = _volumes()
    try:
        return DockerBackend(options)
    except PackageError as exc:
        raise BomLensEngineError(_message(exc)) from None


@dataclass
class _Stage:
    """One container as it ended: what the scan's run record keeps."""

    image: str
    started: datetime
    ended: datetime
    exit_code: int | None = None
    oom_killed: bool = False
    timed_out: bool = False
    start_error: str | None = None


class _RecordingBackend:
    """The package's backend, with a record of every container it ran.

    The package returns its own record only when the run ends well. A run that
    fails (a stage 2 that wrote no result, a container that would not stop)
    still has to say how its containers ended, so the record is kept here.
    """

    def __init__(self, inner: Any, *, egress_network: str | None) -> None:
        self._inner = inner
        self._egress_network = egress_network
        self.stages: dict[str, _Stage] = {}

    @property
    def reset_ownership(self) -> bool:
        return bool(self._inner.reset_ownership)

    def preflight(self) -> None:
        self._inner.preflight()

    def remove_leftovers(self, scan_label: str) -> None:
        self._inner.remove_leftovers(scan_label)

    def list_engine_containers(self, labels: Any = None) -> Any:
        return self._inner.list_engine_containers(labels)

    def remove_engine_container(self, name: str) -> bool:
        return bool(self._inner.remove_engine_container(name))

    def run(self, spec: Any, on_line: Callable[[str], None], cancelled: Callable[[], bool]) -> Any:
        if spec.network == "egress" and not self._egress_network:
            # Refused before the container exists, as the stage that needs the
            # package registries must not be put on a network TRUSCA did not choose.
            raise BomLensEngineError(
                "this stage needs the package registries and SCAN_BOMLENS_NETWORK is not set"
            )
        key = spec.component.removeprefix("engine-")
        started = datetime.now(UTC)
        stage = _Stage(image=spec.image, started=started, ended=started)
        self.stages[key] = stage
        try:
            outcome = self._inner.run(spec, on_line, cancelled)
        finally:
            stage.ended = max(started, datetime.now(UTC))
        stage.exit_code = outcome.exit_code
        stage.oom_killed = bool(outcome.oom_killed)
        stage.timed_out = bool(outcome.timed_out)
        stage.start_error = outcome.start_error
        return outcome


def _recording_backend(options: DockerOptions) -> _RecordingBackend:
    """The Docker backend for ``options`` with the record around it.

    The backend refuses an egress network it must not join (``host``, the
    compose networks, an id) when it is built, so that is an error here, before
    any container.
    """
    try:
        return _RecordingBackend(DockerBackend(options), egress_network=options.egress_network)
    except PackageError as exc:
        # Most often the egress network: the package refuses the default
        # ``bridge`` network too, where containers reach each other.
        raise BomLensEngineError(f"{_message(exc)} (check SCAN_BOMLENS_NETWORK)") from None


def _stage1_window(stages: dict[str, _Stage]) -> tuple[datetime, datetime] | None:
    stage = stages.get("stage1")
    return (stage.started, stage.ended) if stage is not None else None


def _run_metadata(stages: dict[str, _Stage], result: Any | None, *, image: bool) -> dict[str, Any]:
    """What the scan's ``scan_engine_run`` records, in the keys it always had.

    The package's own record nests the containers under ``stages``; the keys
    here are the ones the administrator guide names and the scan record kept
    since the first engine release, so they stay. ``result`` is the package's
    record, ``None`` when the run ended before it returned one.
    """
    stage1, stage2, whole = stages.get("stage1"), stages.get("stage2"), stages.get("image")
    if image:
        return {
            "mode": "image",
            "engine_image": whole.image if whole is not None else None,
            "exit_code": whole.exit_code if whole is not None else None,
            "timed_out": bool(whole.timed_out) if whole is not None else False,
        }
    plan_reason = result.plan_reason if result is not None else None
    ecosystem = result.detected_ecosystem if result is not None else None
    return {
        "plan_status": result.plan_status if result is not None else None,
        "plan_reason": contract.clip(plan_reason) if plan_reason else None,
        "detected_ecosystem": contract.clip(ecosystem) if ecosystem else None,
        "stage1_image": stage1.image if stage1 is not None else None,
        "stage1_exit_code": stage1.exit_code if stage1 is not None else None,
        "stage1_oom": bool(stage1.oom_killed) if stage1 is not None else False,
        "stage1_timed_out": bool(stage1.timed_out) if stage1 is not None else False,
        "source_restored": result.source_restored if result is not None else None,
        "stage2_image": stage2.image if stage2 is not None else None,
        "stage2_exit_code": stage2.exit_code if stage2 is not None else None,
    }


def _check_stage1(stages: dict[str, _Stage]) -> None:
    """Refuse a run whose stage 1 container could not be started.

    The package then runs stage 2 on what is left, which is a reduced scan.
    A container that cannot start is also what a changed mount or a missing
    network looks like, so TRUSCA fails the scan instead.
    """
    stage = stages.get("stage1")
    if stage is not None and stage.start_error:
        raise BomLensEngineError("the BomLens stage 1 container could not be started")


def run_source(
    *,
    scan_uuid: uuid.UUID,
    scan_dir: Path,
    source: Path,
    project: str,
    version: str,
    fetch_license: bool,
    deep_license: bool,
    include_non_shipped: bool,
    on_stage: Callable[[str], None] | None = None,
    line_sink: Callable[[str], Callable[[str, str], None]] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    on_finish: Callable[[RunRecord], None] | None = None,
) -> EngineRun:
    """Run the engine on a source tree. Every container is gone when this returns.

    ``on_stage`` is told ``prep``, ``cdxgen`` and ``scancode`` before each step.
    ``on_finish`` is called once, also when the run raised.
    """
    policy, options = build_policy(
        deep_license=deep_license, include_non_shipped=include_non_shipped
    )
    output = _Output(line_sink)
    image = policy.stage2_image
    record = RunRecord(image.ref, image.digest)
    result: Any = None
    try:
        backend = _recording_backend(options)
        try:
            result = run_source_scan(
                policy,
                backend,
                scan_id=scan_uuid.hex,
                project=project,
                version=version,
                scan_dir=scan_dir,
                source=source,
                fetch_license=fetch_license,
                on_stage=_stage_hook(on_stage),
                on_log=output,
                cancel=_cancel_poll(is_cancelled),
            )
        except PackageError as exc:
            raise _failure(exc, output) from None
        finally:
            record.metadata = _run_metadata(backend.stages, result, image=False)
        _check_stage1(backend.stages)
        return EngineRun(
            output_dir=result.output_dir,
            verified_dir=_fresh_verified_dir(scan_dir),
            metadata=record.metadata,
            run_status=result.manifest.run_status if result.manifest else None,
            stage1_window=_stage1_window(backend.stages),
        )
    finally:
        _finish(on_finish, record)


def run_image(
    *,
    scan_uuid: uuid.UUID,
    scan_dir: Path,
    target: str,
    project: str,
    version: str,
    line_sink: Callable[[str], Callable[[str, str], None]] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    on_finish: Callable[[RunRecord], None] | None = None,
) -> EngineRun:
    """Scan a container image with the engine. Every container is gone when this returns.

    ``target`` is what :func:`image_target` gave (``registry:<reference>``); the
    package adds the scheme itself, so it gets the reference alone.
    """
    policy, options = build_policy(deep_license=False, include_non_shipped=False)
    output = _Output(line_sink)
    image = policy.images.engine
    record = RunRecord(image.ref, image.digest)
    result: Any = None
    try:
        backend = _recording_backend(options)
        try:
            result = run_image_scan(
                policy,
                backend,
                scan_id=scan_uuid.hex,
                target=target.removeprefix("registry:"),
                project=project,
                version=version,
                scan_dir=scan_dir,
                on_log=output,
                cancel=_cancel_poll(is_cancelled),
            )
        except PackageError as exc:
            raise _failure(exc, output) from None
        finally:
            record.metadata = {
                **_run_metadata(backend.stages, None, image=True),
                "image_target": target,
            }
        return EngineRun(
            output_dir=result.output_dir,
            verified_dir=_fresh_verified_dir(scan_dir),
            metadata=record.metadata,
            run_status=result.manifest.run_status if result.manifest else None,
        )
    finally:
        _finish(on_finish, record)


def _finish(on_finish: Callable[[RunRecord], None] | None, record: RunRecord) -> None:
    """Hand the record on; a failure of the callback must not replace the run's outcome."""
    if on_finish is None:
        return
    try:
        on_finish(record)
    except Exception:  # noqa: BLE001 - a record of the run must not fail it
        log.warning("bomlens_run_record_failed", exc_info=True)


__all__ = [
    "REFUSED_NETWORKS",
    "BomLensEngineError",
    "EngineContainer",
    "EngineRun",
    "EngineScanCancelled",
    "RunRecord",
    "EngineWroteNoResult",
    "build_policy",
    "engine_backend",
    "image_target",
    "run_image",
    "run_source",
    "stage1_image_prefix",
    "trivy_db_max_age_days",
]
