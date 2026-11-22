# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""A container platform double for the BomLens engine runner, shared by unit and integration tests.

:class:`FakeBackend` implements the ``bomlens-runner`` package's ``Backend``
protocol. The package's orchestrator runs for real and decides every container;
the backend records what it was asked to run and plays each container's part on
the folders the spec mounts. What a container does is a :class:`Behaviour`: an
exit code, an out-of-memory kill, a hang past its time limit, a refusal to stop,
and an effect on its mounted folders.

The plan and result files the effects copy are real BomLens output
(``tests/fixtures/bomlens/``); only the container runtime is simulated.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "bomlens"
PLAN_FIXTURES = FIXTURES.parent / "bomlens-plan"


Effect = Callable[[Any], None]


@dataclass
class Behaviour:
    exit_code: int = 0
    oom: bool = False
    hang: bool = False
    stuck: bool = False
    output: bytes = b""
    effect: Effect | None = None
    #: The container could not be started (an image that could not be pulled).
    fail_start: bool = False


# -- a backend for the bomlens-runner package ------------------------------------


@dataclass
class BackendRun:
    """One container the package asked its backend to run.

    The surface the effects and the assertions use to read what a container
    was given: ``label``, ``image``, ``env``, ``mounts``
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
            behaviour.effect(run)
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

    def factory(options: Any, cli: Any = None) -> FakeBackend:  # noqa: ARG001
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

    def effect(run: BackendRun) -> None:
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

    def effect(run: BackendRun) -> None:
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

    def effect(run: BackendRun) -> None:
        (run.host("/out") / name).write_text('{"bomFormat": "CycloneDX"}')

    return effect


def write_result(case: str) -> Effect:
    """Stage 2 effect: copy a captured BomLens output folder into ``/host-output``."""

    def effect(run: BackendRun) -> None:
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
    "NO_DIGEST",
    "PREFIX_CAPTURES",
    "captured_images",
    "install_backend",
    "plan_as_published",
    "write_plan",
    "write_result",
    "write_sbom",
]
