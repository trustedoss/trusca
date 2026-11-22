# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""A Docker double for the BomLens engine runner, shared by unit and integration tests.

:class:`FakeDocker` records every ``docker run`` the runner builds and plays
each container's part on the host folders its ``--mount`` arguments name
(bind mounts, so the host side is a plain path). What a container does is a
:class:`Behaviour`: an exit code, an out-of-memory kill, a hang past its time
limit, a refusal to stop, and an effect on its mounted folders.

The plan and result files the effects copy are real BomLens output
(``tests/fixtures/bomlens/``); only the container runtime is simulated.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from integrations.bomlens.runner import ContainerState

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "bomlens"
PLAN_FIXTURES = FIXTURES.parent / "bomlens-plan"

_VALUE_OPTIONS = frozenset(
    {
        "--name",
        "--label",
        "--security-opt",
        "--pids-limit",
        "--cap-drop",
        "--cap-add",
        "--memory",
        "--memory-swap",
        "--cpus",
        "--network",
        "--user",
        "--entrypoint",
        "--workdir",
        "--mount",
        "--env",
    }
)
_FLAG_OPTIONS = frozenset({"--read-only"})


@dataclass
class Run:
    """One ``docker run`` as the runner built it."""

    label: str
    argv: list[str]
    options: list[tuple[str, str | None]]
    mounts: dict[str, dict[str, str]]
    env: dict[str, str]
    image: str
    args: list[str]
    #: Names passed as ``--env NAME`` with the value in the docker CLI's own
    #: environment (``Docker.create(env=...)``), not on the command line.
    by_name: frozenset[str] = frozenset()

    def option_values(self, name: str) -> list[str]:
        return [v for k, v in self.options if k == name and v is not None]

    def has_flag(self, name: str) -> bool:
        return any(k == name for k, _ in self.options)

    def host(self, target: str) -> Path:
        return Path(self.mounts[target]["src"])


def parse_run(argv: list[str], process_env: Mapping[str, str] | None = None) -> Run:
    """Parse ``argv`` as Docker would. A value-less ``--env NAME`` takes its
    value from ``process_env`` (the CLI's environment); one missing there is
    an error, since the container would silently go without it."""
    options: list[tuple[str, str | None]] = []
    index = 0
    while index < len(argv) and argv[index].startswith("--"):
        name = argv[index]
        if name in _FLAG_OPTIONS:
            options.append((name, None))
            index += 1
        elif name in _VALUE_OPTIONS:
            options.append((name, argv[index + 1]))
            index += 2
        else:
            raise AssertionError(f"unexpected docker run option {name!r}")
    image, args = argv[index], argv[index + 1 :]
    mounts: dict[str, dict[str, str]] = {}
    for name, value in options:
        if name == "--mount" and value is not None:
            fields: dict[str, str] = {}
            for part in value.split(","):
                key, _, val = part.partition("=")
                fields[key] = val if val else "true"
            mounts[fields["dst"]] = fields
    env: dict[str, str] = {}
    by_name: set[str] = set()
    for k, v in options:
        if k != "--env" or v is None:
            continue
        if "=" in v:
            name, value = v.split("=", 1)
        else:
            name = v
            assert process_env is not None and name in process_env, f"--env {name} has no value"
            value = process_env[name]
            by_name.add(name)
        env[name] = value
    container_name = next(v for k, v in options if k == "--name" and v is not None)
    return Run(
        label=container_name.rsplit("-", 1)[-1],
        argv=argv,
        options=options,
        mounts=mounts,
        env=env,
        image=image,
        args=args,
        by_name=frozenset(by_name),
    )


def docker_mount_record(fields: dict[str, str]) -> dict[str, Any]:
    """One ``--mount`` as Docker records it in ``HostConfig.Mounts``."""
    record: dict[str, Any] = {
        "Type": fields.get("type", ""),
        "Source": fields.get("src", ""),
        "Target": fields.get("dst", ""),
    }
    if fields.get("readonly") == "true":
        record["ReadOnly"] = True
    options: dict[str, Any] = {}
    if fields.get("volume-nocopy") == "true":
        options["NoCopy"] = True
    if fields.get("volume-subpath"):
        options["Subpath"] = fields["volume-subpath"]
    if options:
        # As a real daemon records it (Docker 29: {"NoCopy": true, "Subpath": ...}).
        record["VolumeOptions"] = options
    return record


Effect = Callable[[Run], None]


@dataclass
class Behaviour:
    exit_code: int = 0
    oom: bool = False
    hang: bool = False
    stuck: bool = False
    output: bytes = b""
    effect: Effect | None = None
    raise_in_logs: BaseException | None = None
    #: ``docker run`` itself fails (the container may exist, created but not started).
    fail_start: bool = False


@dataclass
class _Container:
    run: Run
    behaviour: Behaviour
    killed: bool = False
    removed: bool = False


@dataclass
class FakeDocker:
    """A :class:`integrations.bomlens.runner.Docker` that plays scripted containers."""

    behaviours: dict[str, list[Behaviour]] = field(default_factory=dict)
    #: The worker's own networks (names and IDs), as ``own_networks`` reports.
    networks: frozenset[str] | None = frozenset({"trustedoss", "0f3c9a2b7d1e"})
    leftovers: list[str] = field(default_factory=list)
    api_version: str | None = "1.53"
    #: What ``requested_mounts`` reports per container; by default exactly what
    #: was asked (a daemon that honours every option).
    record_mounts: Any = None
    started: list[str] = field(default_factory=list)
    engine_list: list[Any] = field(default_factory=list)
    runs: list[Run] = field(default_factory=list)
    #: The extra environment each ``create`` gave the docker CLI, in order.
    process_envs: list[dict[str, str]] = field(default_factory=list)
    pulled: list[str] = field(default_factory=list)
    killed: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    containers: dict[str, _Container] = field(default_factory=dict)

    def behave(self, label: str, *behaviours: Behaviour) -> FakeDocker:
        self.behaviours[label] = list(behaviours)
        return self

    def runs_of(self, label: str) -> list[Run]:
        return [r for r in self.runs if r.label == label]

    # -- Docker protocol ----------------------------------------------------

    def ensure_image(self, image: str, timeout: int) -> None:  # noqa: ARG002
        self.pulled.append(image)

    def server_api_version(self) -> str | None:
        return self.api_version

    def create(self, argv: list[str], env: Mapping[str, str] | None = None) -> str:
        run = parse_run(argv, env)
        self.process_envs.append(dict(env or {}))
        self.runs.append(run)
        queue = self.behaviours.get(run.label) or [Behaviour()]
        behaviour = queue.pop(0) if len(queue) > 1 else queue[0]
        container_id = f"c{len(self.runs)}"
        self.containers[container_id] = _Container(run=run, behaviour=behaviour)
        if behaviour.fail_start:
            from integrations.bomlens.runner import BomLensEngineError

            raise BomLensEngineError("an engine container could not be created")
        return container_id

    def requested_mounts(self, container: str) -> list[dict[str, Any]]:
        run = self.containers[container].run
        recorded = [docker_mount_record(fields) for fields in run.mounts.values()]
        if self.record_mounts is not None:
            return list(self.record_mounts(recorded))
        return recorded

    def start(self, container: str) -> None:
        self.started.append(container)
        entry = self.containers[container]
        if entry.behaviour.effect is not None:
            entry.behaviour.effect(entry.run)

    def engine_containers(self) -> list[Any]:
        return list(self.engine_list)

    def stream_logs(
        self,
        container: str,
        timeout: int,
        line_callback: Any,  # noqa: ARG002
        stage: str,  # noqa: ARG002
    ) -> bytes:
        behaviour = self.containers[container].behaviour
        if behaviour.raise_in_logs is not None:
            raise behaviour.raise_in_logs
        if behaviour.hang:
            raise subprocess.TimeoutExpired(cmd="docker logs", timeout=timeout, output=b"partial")
        return behaviour.output

    def wait(self, container: str, timeout: int) -> int | None:  # noqa: ARG002
        entry = self.containers[container]
        if entry.behaviour.stuck or (entry.behaviour.hang and not entry.killed):
            return None
        return self._exit_code(entry)

    def _exit_code(self, entry: _Container) -> int:
        if entry.killed and entry.behaviour.hang:
            return 137
        return 137 if entry.behaviour.oom else entry.behaviour.exit_code

    def kill(self, container: str) -> None:
        self.killed.append(container)
        self.containers[container].killed = True

    def state(self, container: str) -> ContainerState:
        entry = self.containers[container]
        return ContainerState(
            running=entry.behaviour.stuck,
            exit_code=self._exit_code(entry),
            oom_killed=entry.behaviour.oom,
        )

    def remove(self, container: str) -> None:
        self.removed.append(container)
        if container in self.containers:
            self.containers[container].removed = True

    def own_networks(self) -> frozenset[str] | None:
        return self.networks

    def scan_containers(self, scan_hex: str) -> list[str]:  # noqa: ARG002
        return list(self.leftovers)


# -- a backend for the bomlens-runner package ------------------------------------


@dataclass
class BackendRun:
    """One container the package asked its backend to run.

    The same surface as :class:`Run` for the effects and the assertions that
    read what a container was given: ``label``, ``image``, ``env``, ``mounts``
    (by target, with the folder the worker sees as ``src``) and ``host``.
    """

    label: str
    spec: Any
    image: str
    env: dict[str, str]
    mounts: dict[str, dict[str, str]]
    args: list[str]

    def host(self, target: str) -> Path:
        return Path(self.mounts[target]["src"])


class FakeBackend:
    """A :class:`bomlens_runner.backend.Backend` that plays scripted containers.

    The package's orchestrator runs for real: it writes the plan, decides every
    container and checks every result. Only the container platform is replaced,
    and each container's part is a :class:`Behaviour` whose effect copies real
    BomLens output (``tests/fixtures/``) into the folders the spec mounts.
    """

    reset_ownership = True

    def __init__(self, *, workspace: Path | None = None, trivy: Path | None = None) -> None:
        self._workspace = workspace
        self._trivy = trivy
        self.behaviours: dict[str, list[Behaviour]] = {}
        self.runs: list[BackendRun] = []
        self.preflights = 0
        self.leftover_scans: list[str] = []
        self.engine_list: list[Any] = []
        self.removed_engine: list[str] = []
        self.listed_labels: list[dict[str, str]] = []
        self.cancel_seen: list[bool] = []

    @property
    def workspace(self) -> Path:
        """The folder the workspace volume is, by default the one the scan
        settings name (``WORKSPACE_HOST_PATH`` with the bind strategy), read
        when a container runs so a test can build the backend before it sets it."""
        return self._workspace or Path(os.environ["WORKSPACE_HOST_PATH"])

    @property
    def trivy(self) -> Path | None:
        if self._trivy is not None:
            return self._trivy
        env = os.environ.get("TRIVY_CACHE_DIR")
        return Path(env) if env else None

    def behave(self, label: str, *behaviours: Behaviour) -> FakeBackend:
        self.behaviours[label] = list(behaviours)
        return self

    def runs_of(self, label: str) -> list[BackendRun]:
        return [r for r in self.runs if r.label == label]

    # -- Backend protocol ---------------------------------------------------

    def preflight(self) -> None:
        self.preflights += 1

    def remove_leftovers(self, scan_label: str) -> None:
        self.leftover_scans.append(scan_label)

    def list_engine_containers(self, labels: Mapping[str, str] | None = None) -> list[Any]:
        self.listed_labels.append(dict(labels or {}))
        return list(self.engine_list)

    def remove_engine_container(self, name: str) -> bool:
        self.removed_engine.append(name)
        return any(getattr(c, "name", None) == name for c in self.engine_list)

    def run(self, spec: Any, on_line: Callable[[str], None], cancelled: Callable[[], bool]) -> Any:
        from bomlens_runner import ContainerOutcome, ContainerStillRunning, SecretRef

        label = spec.component.removeprefix("engine-")
        mounts: dict[str, dict[str, str]] = {}
        for mount in spec.mounts:
            root = self.trivy if mount.volume == "trivy-db" else self.workspace
            assert root is not None, f"no folder for the {mount.volume} volume"
            local = root / mount.subpath if mount.subpath else root
            mounts[mount.target] = {
                "src": str(local),
                "readonly": "true" if mount.read_only else "",
            }
        env = {k: ("<secret>" if isinstance(v, SecretRef) else v) for k, v in spec.env.items()}
        run = BackendRun(
            label=label, spec=spec, image=spec.image, env=env, mounts=mounts, args=list(spec.args)
        )
        self.runs.append(run)
        self.cancel_seen.append(bool(cancelled()))
        if cancelled():
            return ContainerOutcome(started=False, cancelled=True)
        queue = self.behaviours.get(label) or [Behaviour()]
        behaviour = queue.pop(0) if len(queue) > 1 else queue[0]
        if behaviour.fail_start:
            return ContainerOutcome(started=False, start_error="the container could not be created")
        if behaviour.effect is not None:
            behaviour.effect(run)  # type: ignore[arg-type]
        for line in behaviour.output.decode("utf-8", errors="replace").splitlines():
            on_line(line)
        if behaviour.stuck:
            raise ContainerStillRunning("an engine container was still running after it was killed")
        if behaviour.hang:
            return ContainerOutcome(started=True, exit_code=137, timed_out=True)
        if behaviour.oom:
            return ContainerOutcome(started=True, exit_code=137, oom_killed=True)
        return ContainerOutcome(started=True, exit_code=behaviour.exit_code)


def install_backend(monkeypatch: Any, backend: FakeBackend) -> None:
    """Make the adapter use ``backend`` for its containers."""
    from bomlens_runner.docker import DockerBackend

    from integrations.bomlens import engine

    def factory(options: Any) -> FakeBackend:
        # Built for real first, so the options it refuses (an egress network
        # such as ``host`` or the worker's own) are refused here too.
        DockerBackend(options)
        return backend

    monkeypatch.setattr(engine, "DockerBackend", factory)
    monkeypatch.setattr("tasks.scan_source_bomlens.shutil.which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr(
        "tasks.scan_container_bomlens.shutil.which", lambda _name: "/usr/bin/docker"
    )


# -- effects that copy real BomLens output ------------------------------------


#: What a value of ``CDXGEN_IMAGE_PREFIX`` makes the published image plan,
#: by capture (``fixtures/bomlens-plan/PROVENANCE.md``). No entry: the plan
#: the image writes without the variable.
PREFIX_CAPTURES: dict[str, str] = {"registry.example/mirror/cdxgen/": "node-mirror-prefix"}

#: Marks a stage 1 digest a test wants removed from the plan copy.
NO_DIGEST: str = "-"


def write_plan(
    case: str,
    *,
    image: str | None = None,
    digest: str | None = None,
    stage1_image: str | None = None,
    stage1_digest: str | None = None,
) -> Effect:
    """PLAN effect: copy a captured plan (and its manifest) into ``/host-output``.

    The plan is copied byte for byte. ``image`` / ``digest``, when given,
    replace stage 2's image in the copy, and ``stage1_image`` /
    ``stage1_digest`` stage 1's (a test of the runner's own check;
    ``stage1_digest=NO_DIGEST`` removes it); tests that run a plan as
    captured configure the image it names (:func:`captured_images`).
    """
    changes = {
        "postprocess": (image, digest),
        "generate": (stage1_image, stage1_digest),
    }

    def effect(run: Run) -> None:
        out = run.host("/host-output")
        source = PLAN_FIXTURES / case / "bomlens-plan.json"
        if all(v is None for pair in changes.values() for v in pair):
            shutil.copyfile(source, out / "bomlens-plan.json")
        else:
            document = json.loads(source.read_text())
            for stage in document.get("stages", []):
                new_image, new_digest = changes.get(stage["id"], (None, None))
                stage["image"] = new_image or stage["image"]
                if new_digest == NO_DIGEST:
                    stage["image_digest"] = None
                else:
                    stage["image_digest"] = new_digest or stage["image_digest"]
            (out / "bomlens-plan.json").write_text(json.dumps(document))
        manifest = PLAN_FIXTURES / case / "bomlens-result.json"
        if manifest.exists():
            shutil.copyfile(manifest, out / "bomlens-result.json")
        files = out / "bomlens-plan"
        files.mkdir(exist_ok=True)
        for name in ("build-prep.sh", "identify-conda.py", "read-python-venv.py"):
            (files / name).write_text(f"# placeholder for {name}\n")

    return effect


def plan_as_published(default_case: str = "node-ok") -> Effect:
    """PLAN effect: the capture the published image writes for the run's env.

    Picks the plan by the ``CDXGEN_IMAGE_PREFIX`` the runner gave the PLAN
    container (:data:`PREFIX_CAPTURES`), so a test sees the stage 1 image the
    real image would name for that setting. A prefix with no capture fails
    the test rather than falling back to the plain plan.
    """

    def effect(run: Run) -> None:
        prefix = run.env.get("CDXGEN_IMAGE_PREFIX")
        if prefix is None:
            write_plan(default_case)(run)
            return
        assert prefix in PREFIX_CAPTURES, f"no PLAN capture for CDXGEN_IMAGE_PREFIX={prefix!r}"
        write_plan(PREFIX_CAPTURES[prefix])(run)

    return effect


def captured_images(case: str = "node-ok") -> str:
    """``repo:tag@digest`` of the stage 2 image the captured plan names."""
    document = json.loads((PLAN_FIXTURES / case / "bomlens-plan.json").read_text())
    engine = document["engine"]
    return f"{engine['image']}@{engine['image_digest']}"


def write_sbom(name: str) -> Effect:
    """Stage 1 effect: leave an SBOM at ``/out/<name>``."""

    def effect(run: Run) -> None:
        (run.host("/out") / name).write_text('{"bomFormat": "CycloneDX"}')

    return effect


def write_result(case: str) -> Effect:
    """Stage 2 effect: copy a captured BomLens output folder into ``/host-output``."""

    def effect(run: Run) -> None:
        out = run.host("/host-output")
        for item in (FIXTURES / case).iterdir():
            shutil.copyfile(item, out / item.name)

    return effect


__all__ = [
    "FIXTURES",
    "PLAN_FIXTURES",
    "Behaviour",
    "BackendRun",
    "FakeBackend",
    "FakeDocker",
    "Run",
    "NO_DIGEST",
    "PREFIX_CAPTURES",
    "captured_images",
    "install_backend",
    "plan_as_published",
    "docker_mount_record",
    "parse_run",
    "write_plan",
    "write_result",
    "write_sbom",
]
