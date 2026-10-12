# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Read and check a BomLens execution plan (``bomlens-plan.json``, plan contract 1.x).

``MODE=PLAN`` writes the plan for a source scan: which two containers to run,
with which image, arguments, mounts and environment. The format is BomLens'
``schemas/engine-plan-1.json``, described in ``docs/reference/engine-contract.md``.

The plan is output of a container that read the scanned source, so it is
treated as untrusted input. TRUSCA runs only what the plan may ask for:

* Images: stage 1 must name an image from an allowed repository and pin it by
  digest; a tag ``latest`` is refused. Stage 2 must repeat the BomLens image
  TRUSCA itself pinned (the runner checks this against its configuration).
* Mounts: a plan names a mount by role (``source``, ``output``, ``plan-file``,
  ``guard-state``, caches, ``trivy-db``), never by host path. TRUSCA decides the
  host side of every role. A container path that would cover a system
  directory, a mount role in the wrong stage, a writable source in stage 2, a
  writable plan file, a plan file outside ``bomlens-plan/``, or a Trivy
  database mount anywhere but read-only at ``/trivy-db`` is refused.
* User and entrypoint: a numeric ``uid[:gid]`` only, and a shell entrypoint
  only. Nothing in the plan can ask for privileges, devices, capabilities or a
  different network; those flags are not read from the plan at all.
* Environment: only names on a per-stage allow list are kept, and the values
  TRUSCA relies on (``MODE``, ``BOMLENS_RUN_INPUT``, ``HOST_OUTPUT_DIR``,
  ``UPLOAD_ENABLED``) must be what TRUSCA expects. Anything else in ``env`` is
  dropped with a warning; ``env_passthrough`` names are kept only when they are
  on the stage's pass-through allow list.

Fields a later 1.x minor version adds are ignored, as the contract asks of a
reader. A major version other than 1 is refused.
"""

from __future__ import annotations

import json
import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import structlog

from integrations.bomlens.contract import (
    BomLensContractError,
    clip,
    contract_major,
)

log = structlog.get_logger("integrations.bomlens.plan")

#: File name ``MODE=PLAN`` writes into its output folder.
PLAN_NAME: Final = "bomlens-plan.json"

#: Folder the plan copies stage 1's helper files into.
PLAN_FILES_DIR: Final = "bomlens-plan"

#: ``contract.name`` every plan carries.
PLAN_CONTRACT_NAME: Final = "bomlens-engine-plan"

#: A plan is a few KiB. Anything far larger is not a plan this engine wrote.
MAX_PLAN_BYTES: Final = 256 * 1024

#: ``status`` values of plan contract 1.x.
PLAN_STATUSES: Final = ("ok", "unsupported")

#: ``reason`` values of plan contract 1.x (``None`` for ``ok``).
PLAN_REASONS: Final = ("android-sdk-image", "no-pinned-digest")

#: ``stages[].id`` with its stage number, in the order a plan lists them.
STAGE_IDS: Final = (("generate", 1), ("postprocess", 2))

#: ``mounts[].role`` values of plan contract 1.x (``trivy-db`` since BomLens #203).
MOUNT_ROLES: Final = (
    "source",
    "output",
    "plan-file",
    "guard-state",
    "cache-maven",
    "cache-gradle",
    "trivy-db",
)

#: The role stage 2 reads a Trivy database through, and the one container path
#: it may have. TRUSCA connects the worker's own database to it, read-only.
TRIVY_DB_ROLE: Final = "trivy-db"
TRIVY_DB_TARGET: Final = "/trivy-db"

#: ``mounts[].mode`` values.
MOUNT_MODES: Final = ("ro", "rw")

#: Roles each stage may mount. Caches are optional and TRUSCA does not provide
#: them; a plan that requires one is refused (``_check_mounts``).
_STAGE_ROLES: Final[dict[int, frozenset[str]]] = {
    1: frozenset({"source", "output", "plan-file", "guard-state", "cache-maven", "cache-gradle"}),
    2: frozenset({"source", "output", TRIVY_DB_ROLE}),
}

#: Roles TRUSCA provides a host folder or file for.
PROVIDED_ROLES: Final = frozenset({"source", "output", "plan-file", "guard-state", TRIVY_DB_ROLE})

#: Container paths a mount may not cover, or sit under. A mount on one of these
#: would replace part of the image's own system (or reach the Docker socket,
#: which is only under ``/run`` and ``/var/run``).
_FORBIDDEN_TARGET_ROOTS: Final = (
    "/bin",
    "/boot",
    "/dev",
    "/etc",
    "/lib",
    "/lib32",
    "/lib64",
    "/proc",
    "/run",
    "/sbin",
    "/sys",
    "/usr",
    "/var",
)

#: Container paths TRUSCA mounts itself (scratch space, the Trivy database).
#: A plan mount may not be one of them or sit below one, except a stage 1
#: helper file below ``/tmp``, which is where the plan puts build-prep.sh, and
#: the ``trivy-db`` role at exactly ``/trivy-db``.
RESERVED_TARGETS: Final = ("/tmp", TRIVY_DB_TARGET)  # noqa: S108 - container paths

#: Container path: absolute, plain characters, no ``..`` (checked separately).
_TARGET_RE: Final = re.compile(r"^/[A-Za-z0-9._/-]{1,200}$")

#: A plan file's ``source``: a plain name inside ``bomlens-plan/``.
_PLAN_FILE_RE: Final = re.compile(r"^bomlens-plan/[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")

#: An image name without digest: ``registry[:port]/path[:tag]``, lower-case path.
_IMAGE_RE: Final = re.compile(
    r"^[a-z0-9]+(?:[.-][a-z0-9]+)*(?::[0-9]{1,5})?"
    r"(?:/[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*)+"
    r"(?::[A-Za-z0-9_][A-Za-z0-9_.-]{0,127})?$"
)

#: An image digest as Docker writes it.
DIGEST_RE: Final = re.compile(r"^sha256:[0-9a-f]{64}$")

#: ``user``: numeric uid with an optional numeric gid; names are refused.
_USER_RE: Final = re.compile(r"^[0-9]{1,10}(?::[0-9]{1,10})?$")

#: Entrypoints a plan may ask for.
_ENTRYPOINTS: Final = frozenset({"sh", "/bin/sh", "bash", "/bin/bash"})

#: ``output_file``: the name BomLens derives from the project name and version.
_OUTPUT_FILE_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,200}_bom\.json$")

#: Environment variable name.
_ENV_NAME_RE: Final = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")

_MAX_ARGS: Final = 64
_MAX_VALUE_CHARS: Final = 4096

#: ``env`` names each stage may set, by stage number.
STAGE_ENV_ALLOWED: Final[dict[int, frozenset[str]]] = {
    1: frozenset(
        {
            "HOME",
            "MAVEN_OPTS",
            "FETCH_LICENSE",
            "PROJECT_NAME",
            "PROJECT_VERSION",
            "HOST_GOTOOLCHAIN",
        }
    ),
    2: frozenset(
        {
            "MODE",
            "SECURITY_ENRICH",
            "BOMLENS_RUN_INPUT",
            "HOST_OUTPUT_DIR",
            "PROJECT_NAME",
            "PROJECT_VERSION",
            "UPLOAD_ENABLED",
        }
    ),
}

#: The proxy settings stage 1's package managers read, as the in-process
#: pipeline passes them to cdxgen. A proxy on the worker's own loopback
#: address is not passed on (the runner checks the value): inside the
#: container that address is the container itself.
PROXY_ENV_NAMES: Final = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
)

#: How old (days) the mounted Trivy database may be before BomLens records
#: ``scan-security`` as degraded. TRUSCA sets the value from its own refresh
#: interval; it never comes from the worker's environment.
TRIVY_DB_MAX_AGE_ENV: Final = "BOMLENS_TRIVY_DB_MAX_AGE_DAYS"

#: ``env_passthrough`` names each stage may receive from the worker's own
#: environment. ``HOST_UID`` and ``HOST_GID`` are not here: TRUSCA sets them
#: itself from the worker's own user, never from a variable. Stage 2 runs
#: with no network and reads the worker's Trivy database offline, so the
#: proxy, ``TRIVY_*`` (a database mirror and its credentials), ``EPSS_API``
#: and ``KEV_URL`` names a plan lists for it have nothing to do and are left
#: out.
STAGE_PASSTHROUGH_ALLOWED: Final[dict[int, frozenset[str]]] = {
    1: frozenset(
        {
            "BOMLENS_KEEP_BUILD_OUTPUT",
            "BOMLENS_MAVEN_FULL_GRAPH",
            "BOMLENS_ANDROID_FULL_GRAPH",
            "BOMLENS_NODE_FULL_GRAPH",
            "BOMLENS_PHP_FULL_GRAPH",
            "BOMLENS_INCLUDE_NON_SHIPPED",
            "BOMLENS_NO_COPYRIGHT",
            "BOMLENS_NO_CARGO_LICENSE",
            "BOMLENS_PREP_TIMEOUT",
            "GOPROXY",
            "GOSUMDB",
            *PROXY_ENV_NAMES,
        }
    ),
    2: frozenset({"BOMLENS_INCLUDE_NON_SHIPPED", "SRC_TREE_EXCLUDE", TRIVY_DB_MAX_AGE_ENV}),
}


class BomLensPlanRejected(BomLensContractError):
    """The execution plan asks for something TRUSCA will not run."""


@dataclass(frozen=True)
class Mount:
    """One ``mounts[]`` entry."""

    role: str
    target: str
    mode: str
    source: str | None = None
    optional: bool = False


@dataclass(frozen=True)
class Stage:
    """One ``stages[]`` entry, after the checks in this module."""

    id: str
    stage: int
    image: str
    image_digest: str
    user: str | None
    entrypoint: str | None
    args: tuple[str, ...]
    workdir: str | None
    mounts: tuple[Mount, ...]
    env: dict[str, str]
    env_passthrough: tuple[str, ...]
    network_required: bool
    memory_min: str | None = None
    #: ``env`` names the plan set that are not on the allow list (dropped).
    dropped_env: tuple[str, ...] = field(default_factory=tuple)

    def mount(self, role: str) -> Mount | None:
        """The first mount with ``role``, or ``None``."""
        return next((m for m in self.mounts if m.role == role), None)


@dataclass(frozen=True)
class ExecutionPlan:
    """The parsed plan."""

    contract_version: str
    engine_version: str | None
    engine_image: str | None
    engine_image_digest: str | None
    detected_ecosystem: str
    status: str
    reason: str | None
    detail: str | None
    output_file: str
    stages: tuple[Stage, ...]

    @property
    def supported(self) -> bool:
        return self.status == "ok"

    @property
    def generate(self) -> Stage:
        return self.stages[0]

    @property
    def postprocess(self) -> Stage:
        return self.stages[1]


def _reject(message: str) -> BomLensPlanRejected:
    return BomLensPlanRejected(f"BomLens execution plan refused: {message}")


def _obj(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _reject(f"{where} is not an object")
    return value


def _get(mapping: dict[str, Any], key: str, where: str) -> Any:
    if key not in mapping:
        raise _reject(f"{where}.{key} is missing")
    return mapping[key]


def _str(value: Any, where: str) -> str:
    if not isinstance(value, str):
        raise _reject(f"{where} is not a string")
    return value


def _opt_str(value: Any, where: str) -> str | None:
    return None if value is None else _str(value, where)


def _plain_value(value: str, where: str) -> str:
    if len(value) > _MAX_VALUE_CHARS or "\x00" in value or "\n" in value or "\r" in value:
        raise _reject(f"{where} is not a plain value")
    return value


def image_repository(image: str) -> str:
    """``image`` without its tag (the registry port colon is kept)."""
    head, _, last = image.rpartition("/")
    name = last.split(":", 1)[0]
    return f"{head}/{name}" if head else name


def image_tag(image: str) -> str | None:
    """The tag of ``image``, or ``None`` when it has none."""
    last = image.rsplit("/", 1)[-1]
    return last.split(":", 1)[1] if ":" in last else None


def check_image(image: str, digest: str | None, where: str) -> None:
    """Refuse an image that is not a plain, tagged-not-latest, digest-pinned name."""
    if not _IMAGE_RE.match(image):
        raise _reject(f"{where} image {clip(image)!r} is not an image name")
    if image_tag(image) == "latest":
        raise _reject(f"{where} image {clip(image)!r} is tagged latest")
    if digest is None or not DIGEST_RE.match(digest):
        raise _reject(f"{where} image {clip(image)!r} is not pinned by a sha256 digest")


def _check_target(target: str, where: str) -> str:
    if not _TARGET_RE.match(target) or posixpath.normpath(target) != target:
        raise _reject(f"{where} target {clip(target)!r} is not a plain container path")
    if target == "/" or ".." in target.split("/"):
        raise _reject(f"{where} target {clip(target)!r} is not a plain container path")
    for root in _FORBIDDEN_TARGET_ROOTS:
        if target == root or target.startswith(root + "/"):
            raise _reject(f"{where} target {clip(target)!r} covers a system path")
    if "docker.sock" in target:
        raise _reject(f"{where} target {clip(target)!r} names the Docker socket")
    return target


def _parse_mount(raw: Any, stage: int, where: str) -> Mount:
    entry = _obj(raw, where)
    role = _str(_get(entry, "role", where), f"{where}.role")
    if role not in MOUNT_ROLES:
        raise _reject(f"{where}.role {clip(role)!r} is not a mount role")
    if role not in _STAGE_ROLES[stage]:
        raise _reject(f"{where}.role {role!r} is not allowed in stage {stage}")
    target = _check_target(_str(_get(entry, "target", where), f"{where}.target"), where)
    if role == TRIVY_DB_ROLE and target != TRIVY_DB_TARGET:
        raise _reject(
            f"{where} mounts the Trivy database at {clip(target)!r}, not {TRIVY_DB_TARGET}"
        )
    for reserved in RESERVED_TARGETS:
        below = target.startswith(reserved + "/")
        allowed = (
            below and reserved == "/tmp" and role == "plan-file" and stage == 1  # noqa: S108
        ) or (target == reserved == TRIVY_DB_TARGET and role == TRIVY_DB_ROLE)
        if (target == reserved or below) and not allowed:
            raise _reject(
                f"{where} target {clip(target)!r} is under {reserved}, which TRUSCA mounts"
            )
    mode = _str(_get(entry, "mode", where), f"{where}.mode")
    if mode not in MOUNT_MODES:
        raise _reject(f"{where}.mode {clip(mode)!r} is not ro or rw")
    optional = entry.get("optional", False)
    if not isinstance(optional, bool):
        raise _reject(f"{where}.optional is not a boolean")
    source = entry.get("source")
    if role == "plan-file":
        source = _str(source, f"{where}.source")
        if not _PLAN_FILE_RE.match(source):
            raise _reject(f"{where}.source {clip(source)!r} is not a file in {PLAN_FILES_DIR}/")
        if mode != "ro":
            raise _reject(f"{where} mounts a plan file writable")
    elif source is not None:
        raise _reject(f"{where} names a source path for role {role!r}")
    if role == TRIVY_DB_ROLE and mode != "ro":
        raise _reject(f"{where} mounts the Trivy database writable")
    if role == "source" and stage == 2 and mode != "ro":
        raise _reject(f"{where} mounts the source writable in stage 2")
    if role in ("output", "guard-state") and mode != "rw":
        raise _reject(f"{where} mounts the {role} folder read-only")
    if role not in PROVIDED_ROLES and not optional:
        raise _reject(f"{where} requires a {role!r} mount TRUSCA does not provide")
    return Mount(role=role, target=target, mode=mode, source=source, optional=optional)


def _check_mounts(mounts: tuple[Mount, ...], where: str) -> None:
    targets = [m.target for m in mounts]
    if len(set(targets)) != len(targets):
        raise _reject(f"{where} mounts two things at one container path")
    for role in ("source", "output"):
        if sum(1 for m in mounts if m.role == role) != 1:
            raise _reject(f"{where} must mount exactly one {role!r} folder")
    if sum(1 for m in mounts if m.role == "guard-state") > 1:
        raise _reject(f"{where} mounts more than one guard-state folder")
    # One mount under another is fine when the inner one is a plan file (a file
    # over a folder); two folders nested would let one role write the other.
    folders = [m for m in mounts if m.role != "plan-file"]
    for outer in folders:
        for inner in folders:
            if inner is not outer and inner.target.startswith(outer.target + "/"):
                raise _reject(f"{where} nests the {inner.role!r} mount in {outer.role!r}")


def _parse_env(raw: Any, stage: int, where: str) -> tuple[dict[str, str], tuple[str, ...]]:
    entries = _obj(raw, where)
    kept: dict[str, str] = {}
    dropped: list[str] = []
    for name, value in entries.items():
        if not isinstance(name, str) or not _ENV_NAME_RE.match(name):
            raise _reject(f"{where} names a variable {clip(name)!r} that is not a plain name")
        text = _plain_value(_str(value, f"{where}.{name}"), f"{where}.{name}")
        if name in STAGE_ENV_ALLOWED[stage]:
            kept[name] = text
        else:
            dropped.append(name)
    return kept, tuple(dropped)


def _parse_passthrough(raw: Any, stage: int, where: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise _reject(f"{where} is not a list")
    names: list[str] = []
    for index, name in enumerate(raw):
        name = _str(name, f"{where}[{index}]")
        if name in STAGE_PASSTHROUGH_ALLOWED[stage] and name not in names:
            names.append(name)
    return tuple(names)


def _parse_stage(raw: Any, index: int, expected: tuple[str, int], output_file: str) -> Stage:
    where = f"stages[{index}]"
    entry = _obj(raw, where)
    stage_id = _str(_get(entry, "id", where), f"{where}.id")
    number = _get(entry, "stage", where)
    if (stage_id, number) != expected or isinstance(number, bool):
        raise _reject(f"{where} is {clip(stage_id)!r}/{clip(number)}, expected {expected}")
    image = _str(_get(entry, "image", where), f"{where}.image")
    digest = _opt_str(_get(entry, "image_digest", where), f"{where}.image_digest")
    check_image(image, digest, where)
    assert digest is not None  # noqa: S101 - check_image refused None above
    user = _opt_str(entry.get("user"), f"{where}.user")
    if user is not None and not _USER_RE.match(user):
        raise _reject(f"{where}.user {clip(user)!r} is not a numeric uid[:gid]")
    raw_entry = entry.get("entrypoint")
    entrypoint: str | None = None
    if raw_entry is not None:
        if (
            not isinstance(raw_entry, list)
            or len(raw_entry) != 1
            or raw_entry[0] not in _ENTRYPOINTS
        ):
            raise _reject(f"{where}.entrypoint {clip(raw_entry)!r} is not a shell")
        entrypoint = raw_entry[0]
    raw_args = _get(entry, "args", where)
    if not isinstance(raw_args, list) or len(raw_args) > _MAX_ARGS:
        raise _reject(f"{where}.args is not a list of at most {_MAX_ARGS}")
    args = tuple(
        _plain_value(_str(a, f"{where}.args[{i}]"), f"{where}.args[{i}]")
        for i, a in enumerate(raw_args)
    )
    raw_mounts = _get(entry, "mounts", where)
    if not isinstance(raw_mounts, list) or len(raw_mounts) > 16:
        raise _reject(f"{where}.mounts is not a list")
    mounts = tuple(
        _parse_mount(m, number, f"{where}.mounts[{i}]") for i, m in enumerate(raw_mounts)
    )
    _check_mounts(mounts, where)
    workdir = _opt_str(_get(entry, "workdir", where), f"{where}.workdir")
    if workdir is not None and workdir not in {
        m.target for m in mounts if m.role in ("source", "output")
    }:
        raise _reject(f"{where}.workdir {clip(workdir)!r} is not its source or output folder")
    env, dropped = _parse_env(_get(entry, "env", where), number, f"{where}.env")
    passthrough = _parse_passthrough(
        entry.get("env_passthrough"), number, f"{where}.env_passthrough"
    )
    network = entry.get("network") or {"required": False}
    required = _obj(network, f"{where}.network").get("required", False)
    if not isinstance(required, bool):
        raise _reject(f"{where}.network.required is not a boolean")
    resources = entry.get("resources")
    memory_min = resources.get("memory_min") if isinstance(resources, dict) else None
    stage = Stage(
        id=stage_id,
        stage=number,
        image=image,
        image_digest=digest,
        user=user,
        entrypoint=entrypoint,
        args=args,
        workdir=workdir,
        mounts=mounts,
        env=env,
        env_passthrough=passthrough,
        network_required=required,
        memory_min=memory_min if isinstance(memory_min, str) else None,
        dropped_env=dropped,
    )
    if number == 2:
        _check_postprocess_env(stage, output_file)
    return stage


def _check_postprocess_env(stage: Stage, output_file: str) -> None:
    """The stage 2 values TRUSCA relies on must be exactly what it expects."""
    output = stage.mount("output")
    assert output is not None  # noqa: S101 - _check_mounts guarantees one
    expected = {
        "MODE": "POSTPROCESS",
        "BOMLENS_RUN_INPUT": output_file,
        "HOST_OUTPUT_DIR": output.target,
        "UPLOAD_ENABLED": "false",
    }
    for name, value in expected.items():
        if stage.env.get(name) != value:
            raise _reject(
                f"stage 2 sets {name}={clip(stage.env.get(name))!r}, expected {clip(value)!r}"
            )


def parse_plan(document: Any) -> ExecutionPlan:
    """Check a decoded plan and return it as an :class:`ExecutionPlan`."""
    plan = _obj(document, "plan")
    contract = _obj(_get(plan, "contract", "plan"), "contract")
    name = _get(contract, "name", "contract")
    if name != PLAN_CONTRACT_NAME:
        raise _reject(f"contract.name is {clip(name)!r}")
    version = _str(_get(contract, "version", "contract"), "contract.version")
    if contract_major(version) != 1:
        raise _reject(f"plan contract version {clip(version)!r}; this TRUSCA reads 1.x only")
    engine = _obj(_get(plan, "engine", "plan"), "engine")
    run_input = _obj(_get(plan, "input", "plan"), "input")
    if run_input.get("kind") != "source":
        raise _reject(f"input.kind is {clip(run_input.get('kind'))!r}, not source")
    ecosystem = _str(_get(run_input, "detected_ecosystem", "input"), "input.detected_ecosystem")
    status = _str(_get(plan, "status", "plan"), "status")
    if status not in PLAN_STATUSES:
        raise _reject(f"status {clip(status)!r} is not ok or unsupported")
    reason = _opt_str(_get(plan, "reason", "plan"), "reason")
    detail = _opt_str(_get(plan, "detail", "plan"), "detail")
    output_file = _str(_get(plan, "output_file", "plan"), "output_file")
    if not _OUTPUT_FILE_RE.match(output_file):
        raise _reject(f"output_file {clip(output_file)!r} is not a plain SBOM file name")
    raw_stages = _get(plan, "stages", "plan")
    if not isinstance(raw_stages, list):
        raise _reject("stages is not a list")
    if status == "unsupported":
        if raw_stages:
            raise _reject("an unsupported plan lists stages")
        stages: tuple[Stage, ...] = ()
    else:
        if reason is not None:
            raise _reject(f"an ok plan gives a reason {clip(reason)!r}")
        if len(raw_stages) != len(STAGE_IDS):
            raise _reject(f"an ok plan lists {len(raw_stages)} stages, expected 2")
        stages = tuple(
            _parse_stage(raw, i, STAGE_IDS[i], output_file) for i, raw in enumerate(raw_stages)
        )
    for stage in stages:
        if stage.dropped_env:
            log.warning(
                "bomlens_plan_env_dropped",
                stage=stage.id,
                names=[clip(n) for n in stage.dropped_env[:10]],
            )
    return ExecutionPlan(
        contract_version=version,
        engine_version=_opt_str(engine.get("version"), "engine.version"),
        engine_image=_opt_str(engine.get("image"), "engine.image"),
        engine_image_digest=_opt_str(engine.get("image_digest"), "engine.image_digest"),
        detected_ecosystem=ecosystem,
        status=status,
        reason=reason,
        detail=detail,
        output_file=output_file,
        stages=stages,
    )


def load_plan(plan_dir: Path) -> ExecutionPlan:
    """Read and check ``bomlens-plan.json`` from ``plan_dir``.

    The plan must be a regular file directly in ``plan_dir`` (not a symbolic
    link), no larger than :data:`MAX_PLAN_BYTES`.
    """
    path = plan_dir / PLAN_NAME
    if path.is_symlink() or not path.is_file():
        raise _reject("MODE=PLAN wrote no plan file")
    if path.resolve().parent != plan_dir.resolve():
        raise _reject("the plan file is not in the plan folder")
    with path.open("rb") as fh:
        data = fh.read(MAX_PLAN_BYTES + 1)
    if len(data) > MAX_PLAN_BYTES:
        raise _reject("the plan file is larger than any plan")
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise _reject("the plan file is not valid JSON") from exc
    return parse_plan(document)


__all__ = [
    "DIGEST_RE",
    "MAX_PLAN_BYTES",
    "MOUNT_MODES",
    "MOUNT_ROLES",
    "PLAN_CONTRACT_NAME",
    "PLAN_FILES_DIR",
    "PLAN_NAME",
    "PLAN_REASONS",
    "PLAN_STATUSES",
    "PROVIDED_ROLES",
    "PROXY_ENV_NAMES",
    "RESERVED_TARGETS",
    "STAGE_ENV_ALLOWED",
    "STAGE_IDS",
    "STAGE_PASSTHROUGH_ALLOWED",
    "TRIVY_DB_MAX_AGE_ENV",
    "TRIVY_DB_ROLE",
    "TRIVY_DB_TARGET",
    "BomLensPlanRejected",
    "ExecutionPlan",
    "Mount",
    "Stage",
    "check_image",
    "image_repository",
    "image_tag",
    "load_plan",
    "parse_plan",
]
