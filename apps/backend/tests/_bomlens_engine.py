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
    if fields.get("volume-subpath"):
        record["VolumeOptions"] = {"Subpath": fields["volume-subpath"]}
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


# -- effects that copy real BomLens output ------------------------------------


def write_plan(case: str, *, image: str | None = None, digest: str | None = None) -> Effect:
    """PLAN effect: copy a captured plan (and its manifest) into ``/host-output``.

    The plan is copied byte for byte. ``image`` / ``digest``, when given,
    replace stage 2's image in the copy (a test of the runner's own check);
    tests that run a plan as captured configure the image it names
    (:func:`captured_images`).
    """

    def effect(run: Run) -> None:
        out = run.host("/host-output")
        source = PLAN_FIXTURES / case / "bomlens-plan.json"
        if image is None and digest is None:
            shutil.copyfile(source, out / "bomlens-plan.json")
        else:
            document = json.loads(source.read_text())
            for stage in document.get("stages", []):
                if stage["id"] == "postprocess":
                    stage["image"] = image or stage["image"]
                    stage["image_digest"] = digest or stage["image_digest"]
            (out / "bomlens-plan.json").write_text(json.dumps(document))
        manifest = PLAN_FIXTURES / case / "bomlens-result.json"
        if manifest.exists():
            shutil.copyfile(manifest, out / "bomlens-result.json")
        files = out / "bomlens-plan"
        files.mkdir(exist_ok=True)
        for name in ("build-prep.sh", "identify-conda.py"):
            (files / name).write_text(f"# placeholder for {name}\n")

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
    "FakeDocker",
    "Run",
    "captured_images",
    "docker_mount_record",
    "parse_run",
    "write_plan",
    "write_result",
    "write_sbom",
]
