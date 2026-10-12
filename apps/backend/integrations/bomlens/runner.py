# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Run BomLens scans through the Docker API.

A source scan runs from its execution plan (``SCAN_ENGINE=bomlens``,
:meth:`BomLensRunner.run`); a container image scan is one ``MODE=IMAGE`` run
(``SCAN_CONTAINER_ENGINE=bomlens``, :meth:`BomLensRunner.run_image`, described
at the end of this docstring).

The worker runs every container itself, through the Docker API (the socket
proxy in the compose files). BomLens decides what to run; TRUSCA decides how:

1. ``MODE=PLAN`` with the BomLens image writes ``bomlens-plan.json``
   (:mod:`integrations.bomlens.plan` checks it as untrusted input).
2. Stage 1, the cdxgen language image the plan names, pinned by digest.
3. When stage 1 was killed (out of memory, a timeout), the same stage 1 again
   with ``BOMLENS_GUARD_RESTORE_ONLY=1``, which puts the source tree back from
   the guard-state folder (the plan's ``on_kill``).
4. Stage 2, the BomLens image in ``POSTPROCESS`` mode, whatever stage 1's exit
   code. It gets that code, whether the kernel killed stage 1 for memory, and
   stage 1's log. When stage 1 left no SBOM, BomLens falls back to a
   manifest-only scan and records ``generate`` as degraded.

A plan that says ``unsupported`` (an Android project, a stage 1 image with no
pinned digest) has no stages. Stage 2 then runs on its own, as after a stage 1
that produced nothing, so the scan ends ``succeeded_degraded`` with the direct
dependencies BomLens can read without a build.

What every container gets, whatever the plan says (security review
2026-10-11):

* Host folders only from the scan's own workspace, below
  ``WORKSPACE_HOST_PATH``, with no symbolic link on the way
  (:func:`confined_path`). The plan names mounts by role; TRUSCA maps each role
  to a folder. Stage 1's helper files are copied out of the plan's output
  folder into one no container can write, before stage 1 runs.
* A read-only root filesystem, a scratch ``/tmp`` from the workspace, the
  sidecar hardening of :mod:`integrations.scan_executor.local_docker`
  (``--cap-drop ALL`` plus the minimal set, ``no-new-privileges``, pids,
  memory and CPU limits), no swap.
* ``--network none``, or, for a stage the plan says needs the package
  registries, the network ``SCAN_BOMLENS_NETWORK`` names. Never ``host``,
  never a network the worker itself is on (the one that reaches PostgreSQL
  and Redis).
* Only allow-listed environment variables (:mod:`integrations.bomlens.plan`)
  and the ones TRUSCA sets itself; never the worker's environment.
* The worker waits for each container to exit, kills it when its time is up,
  and checks it is no longer running before anything reads its output.

A container image scan is one container: the BomLens image in ``MODE=IMAGE``.
syft reads the image straight from its registry (BomLens #200; no Docker
socket is mounted, so the daemon is never asked to pull it), Trivy reads the
Debian packages' licences from the same registry (#206) and matches the SBOM
against the worker's database, mounted read-only. It gets the rules above,
and more: it runs as the worker's own non-root user with every capability
dropped, and the reference it receives is checked here
(:func:`image_target`) and handed over as ``registry:<reference>``, so syft
reads nothing but a registry (no archive, folder or daemon source). It needs
the registries, so it joins ``SCAN_BOMLENS_NETWORK`` under the same checks as
stage 1. No registry credential is passed: see
``tasks.scan_container_bomlens``.
"""

from __future__ import annotations

import contextlib
import ipaddress
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import IO, Any, Final, Protocol
from urllib.parse import urlsplit

import structlog

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
from integrations._line_streamer import LineCallback
from integrations.bomlens.contract import (
    BomLensError,
    clip,
)
from integrations.bomlens.plan import (
    PLAN_FILES_DIR,
    PROXY_ENV_NAMES,
    TRIVY_DB_MAX_AGE_ENV,
    TRIVY_DB_ROLE,
    TRIVY_DB_TARGET,
    ExecutionPlan,
    Mount,
    Stage,
    check_image,
    image_repository,
    load_plan,
)
from integrations.scan_executor.local_docker import safe_token, sidecar_security_args

log = structlog.get_logger("integrations.bomlens.runner")

#: Label on every engine container, so an operator (or a reaper) finds them.
LABEL_ROLE: Final = "trusca.role=bomlens-engine"

#: Label holding the epoch second after which a container may be reaped.
DEADLINE_LABEL: Final = "trusca.deadline"

#: Label naming the TRUSCA installation that started the container.
INSTANCE_LABEL: Final = "trusca.instance"

_INSTANCE_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")


def instance_id() -> str:
    """``TRUSCA_INSTANCE_ID``, checked; refused when it is not a plain token."""
    value = trusca_instance_id()
    if not _INSTANCE_RE.match(value):
        raise BomLensEngineError(f"TRUSCA_INSTANCE_ID={clip(value)!r} is not a plain name")
    return value


#: Seconds a container may outlive its own time limit before the reaper takes
#: it (the kill-and-wait the runner itself does, plus slack).
REAP_MARGIN_SECONDS: Final = 300

#: Lowest Docker API version the engine runs on: ``volume-subpath`` mounts
#: (Docker Engine 26). An older daemon would drop the sub-path and mount the
#: whole workspace volume.
MIN_DOCKER_API: Final = (1, 45)

#: Container path TRUSCA mounts itself. A plan mount may not use it. The Trivy
#: database's path (``TRIVY_DB_TARGET``) is the plan's ``trivy-db`` role, which
#: TRUSCA connects to the worker's own database (plan.py allows that role
#: there and nothing else).
SCRATCH_TARGET: Final = "/tmp"  # noqa: S108 - a container path, not a host temp file

#: Contract-defined container paths of the PLAN run.
PLAN_SOURCE_TARGET: Final = "/src"
PLAN_OUTPUT_TARGET: Final = "/host-output"

#: BomLens' switch that keeps non-shipped trees (tests, examples, benchmarks).
NON_SHIPPED_ENV: Final = "BOMLENS_INCLUDE_NON_SHIPPED"

#: Pass-through names whose value TRUSCA sets from its own configuration
#: (:meth:`BomLensRunner._trusca_env`), never from the worker's environment.
_TRUSCA_SET_ENV: Final = frozenset({NON_SHIPPED_ENV, TRIVY_DB_MAX_AGE_ENV})

#: Proxy names whose value is a proxy address (``NO_PROXY`` is a host list).
_PROXY_ADDRESS_ENV: Final = frozenset(n for n in PROXY_ENV_NAMES if n.lower() != "no_proxy")


#: Upper limit of :func:`trivy_db_max_age_days`. A database older than a month
#: is not one a scan should report from, whatever the refresh interval says.
TRIVY_DB_MAX_AGE_LIMIT_DAYS: Final = 30


def trivy_db_max_age_days() -> int:
    """How old the worker's Trivy database may be before stage 2 degrades it.

    Two refresh intervals (``TRIVY_DB_REFRESH_HOURS`` in whole days, rounded
    up) plus two days: 16 days with the weekly refresh. One failed refresh
    leaves the database one interval older and scans go on; a second failure
    in a row fails them. Past the limit BomLens records ``scan-security`` as
    degraded and the adapter fails the scan (``stages.vulnerability_failure``).
    Capped at :data:`TRIVY_DB_MAX_AGE_LIMIT_DAYS`. Read per call.
    """
    from integrations.trivy import trivy_db_refresh_interval_hours

    hours = trivy_db_refresh_interval_hours()
    # Integer arithmetic: a huge setting must not overflow a float division.
    days = -(-hours // 24) * 2 + 2
    if days > TRIVY_DB_MAX_AGE_LIMIT_DAYS:
        log.warning(
            "bomlens_trivy_db_max_age_capped",
            refresh_hours=clip(hours),
            days=TRIVY_DB_MAX_AGE_LIMIT_DAYS,
        )
        return TRIVY_DB_MAX_AGE_LIMIT_DAYS
    return days


#: Characters of an IPv4 address in any of its legacy spellings.
_LEGACY_IPV4_RE: Final = re.compile(r"^[0-9a-fA-FxX.]{1,64}$")


def is_loopback_proxy(value: str) -> bool:
    """Whether a proxy address names the machine's own loopback interface.

    Inside a container that address is the container itself, so such a proxy
    is not passed on (BomLens' own CLI leaves it out the same way).
    """
    text = value.strip()
    if "://" not in text:
        text = "http://" + text
    try:
        host = urlsplit(text).hostname
    except ValueError:
        return False
    # A fully qualified name may end in a dot ("localhost.", "127.0.0.1.").
    host = (host or "").rstrip(".")
    if not host:
        return False
    if host == "localhost" or host.endswith(".localhost"):
        return True
    address: ipaddress.IPv4Address | ipaddress.IPv6Address
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # The legacy IPv4 spellings resolvers still accept: 127.1,
        # 2130706433, 0x7f000001, 0177.0.0.1.
        if not _LEGACY_IPV4_RE.match(host):
            return False
        try:
            address = ipaddress.IPv4Address(socket.inet_aton(host))
        except OSError:
            return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    # 0.0.0.0 and :: reach the local machine as well.
    return address.is_loopback or address.is_unspecified


#: The stage 1 log stage 2 reads (a file name in the output folder).
STAGE1_LOG_NAME: Final = "bomlens-stage1.log"
_STAGE1_LOG_MAX_BYTES: Final = 2 * 1024 * 1024

#: Time limits of the short runs.
PLAN_TIMEOUT_SECONDS: Final = 300
RESTORE_TIMEOUT_SECONDS: Final = 600
PULL_TIMEOUT_SECONDS: Final = 1800
#: How long a container may take to exit after its log stream ended, and after
#: it was killed.
EXIT_GRACE_SECONDS: Final = 30

#: Exit codes of a container stopped by SIGKILL / SIGTERM.
_KILLED_EXIT_CODES: Final = frozenset({137, 143})

#: Characters allowed in a host path or volume subpath given to ``--mount``,
#: whose value is comma-separated ``key=value`` pairs.
_MOUNT_PATH_RE: Final = re.compile(r"^[A-Za-z0-9._/-]+$")

#: A Docker network name (no ``container:<id>`` form, no ``host``).
_NETWORK_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
#: ``none`` is not a registry network; the last two are the compose files' own
#: networks (database, Docker API), refused by name before the worker's own
#: networks are even compared.
_FORBIDDEN_NETWORKS: Final = frozenset({"host", "none", "trustedoss", "trustedoss-docker-api"})

#: ``PROJECT_NAME`` / ``PROJECT_VERSION`` handed to BomLens: it names the SBOM
#: file after them, so they are reduced to plain characters here.
_PROJECT_TOKEN_RE: Final = re.compile(r"[^A-Za-z0-9._-]+")

#: ``SCAN_BOMLENS_STAGE1_IMAGE_PREFIX``: a registry host, then a repository
#: path of at least one part, with or without the final ``/``. A host alone
#: (``localhost``, ``reg.example:5000``) is refused: the allowed-repository
#: check admits everything under the prefix, so it must name a path that holds
#: only the cdxgen images, not a whole registry. The host and the path
#: follow the same rules as an image name in a plan (plan.py ``_IMAGE_RE``),
#: so a prefix the plan reader would refuse is refused here first, before any
#: container starts. Nothing else gets through: no scheme, tag, digest, space,
#: upper case or shell character.
_STAGE1_PREFIX_RE: Final = re.compile(
    r"(?P<host>[a-z0-9]+(?:[.-][a-z0-9]+)*(?::[0-9]{1,5})?)"
    r"(?:/[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*)+/?"
)
_STAGE1_PREFIX_MAX_CHARS: Final = 200


class BomLensEngineError(BomLensError):
    """The engine could not be run as configured, or a container misbehaved.

    The message names stages, images and settings, never a workspace path.
    """


class EngineScanCancelled(Exception):  # noqa: N818 - reads as the event it reports
    """The scan stopped being ``running`` (cancelled) while the engine ran.

    worker-engine does not take Celery's remote control, so a revoke never
    reaches it; the pipeline checks the scan's status between stages instead
    and stops without writing over the cancellation.
    """


class EngineContainerStillRunning(BomLensEngineError):
    """A container did not stop when told to; its output is not read."""


@dataclass(frozen=True)
class PinnedImage:
    """An image reference with its tag and its digest."""

    ref: str
    digest: str

    @property
    def run_ref(self) -> str:
        return f"{self.ref}@{self.digest}"


def pinned_image(value: str, what: str) -> PinnedImage:
    """Split ``repo:tag@sha256:...`` and refuse anything less pinned.

    No digest, a tag ``latest`` or no tag at all are refused (CLAUDE.md core
    rule #9): the tag is what a person reads, the digest is what runs.
    """
    ref, sep, digest = value.partition("@")
    if not sep:
        raise BomLensEngineError(f"{what} {clip(value)!r} is not pinned by a digest")
    if ":" not in ref.rsplit("/", 1)[-1]:
        raise BomLensEngineError(f"{what} {clip(value)!r} has no tag")
    try:
        check_image(ref, digest, what)
    except BomLensError as exc:
        raise BomLensEngineError(f"{what} {clip(value)!r} is not a pinned image") from exc
    return PinnedImage(ref=ref, digest=digest)


def stage1_image_prefix(value: str) -> str | None:
    """Check ``SCAN_BOMLENS_STAGE1_IMAGE_PREFIX`` and give it its final ``/``.

    Empty means not set (``None``): PLAN then gets no ``CDXGEN_IMAGE_PREFIX``
    and names the images under BomLens's own default, ``ghcr.io/cdxgen/``.
    BomLens adds a missing final ``/`` itself (``source-detect.sh``); it is
    added here too, so the allowed-repository check compares the same prefix
    the plan uses. The first part must name a registry (a dot, a port or
    ``localhost``): Docker would read ``mirror/cdxgen/`` as a Docker Hub
    account, not as a mirror.

    The error names the rule, never the value: a value with credentials in it
    (``user:token@registry...``) would otherwise reach the scan's error
    message and the worker log.
    """
    if not value:
        return None
    # fullmatch: ``$`` would let a final newline through.
    match = _STAGE1_PREFIX_RE.fullmatch(value)
    host = match.group("host") if match else ""
    if (
        match is None
        or len(value) > _STAGE1_PREFIX_MAX_CHARS
        or not ("." in host or ":" in host or host == "localhost")
    ):
        raise BomLensEngineError(
            "SCAN_BOMLENS_STAGE1_IMAGE_PREFIX is not in the right form: a registry host "
            "(with a dot, a port or localhost) and a path of lower-case parts, such as "
            "registry.example/mirror/cdxgen/, with no scheme, credentials, tag or digest"
        )
    return value if value.endswith("/") else f"{value}/"


#: An image reference as Docker's grammar spells it: an optional registry host
#: (with a port), lower-case path components, an optional tag and an optional
#: sha256 digest. Nothing else: no scheme (``docker-archive:``, ``dir:``,
#: ``oci-dir:``), no space, no ``://``.
_REF_HOST: Final = (
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)*(?::[0-9]{1,5})?)"
)
_REF_COMPONENT: Final = r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*"
_IMAGE_REF_RE: Final = re.compile(
    rf"(?:(?P<host>{_REF_HOST})/)?(?P<path>{_REF_COMPONENT}(?:/{_REF_COMPONENT})*)"
    r"(?::(?P<tag>[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}))?"
    r"(?:@(?P<digest>sha256:[0-9a-f]{64}))?"
)
_IMAGE_REF_MAX_CHARS: Final = 512

#: The syft source scheme TRUSCA hands every image reference with.
REGISTRY_SCHEME: Final = "registry:"


def image_target(reference: str) -> str:
    """``registry:<reference>`` for ``MODE=IMAGE``, or refused.

    syft reads a scheme in front of the reference as the kind of source:
    ``docker-archive:``, ``oci-dir:``, ``dir:`` and ``file:`` read a path
    inside the container, ``docker:`` asks a Docker daemon. A container scan
    reference names a registry image, nothing else, so the reference must
    follow Docker's grammar and is passed with the ``registry:`` scheme, which
    makes syft (and Trivy's licence read, ``--image-src remote``) read it from
    the registry and nowhere else, whatever the name looks like (``dir:x`` is
    a valid image name and tag).

    The error names the rule, not the value: it reaches the scan's error
    message, and a malformed value may carry credentials
    (``user:token@registry...``).
    """
    value = reference.strip() if isinstance(reference, str) else ""
    match = _IMAGE_REF_RE.fullmatch(value) if len(value) <= _IMAGE_REF_MAX_CHARS else None
    host = match.group("host") if match else None
    # Docker's rule: the first part is a registry only with a dot, a port or
    # "localhost"; otherwise it is a lower-case path component.
    if (
        match is not None
        and host is not None
        and not ("." in host or ":" in host or host == "localhost")
    ):
        if not re.fullmatch(_REF_COMPONENT, host):
            match = None
    if match is None:
        raise BomLensEngineError(
            "the image reference is not a registry image reference "
            "([registry/]name[:tag][@sha256:digest]); the BomLens engine reads images "
            "from a registry only"
        )
    return f"{REGISTRY_SCHEME}{value}"


@dataclass(frozen=True)
class EngineImages:
    """The images of one run: PLAN always uses ``plan``; stage 2 uses ``stage2``.

    ``stage1_prefix`` is the checked ``SCAN_BOMLENS_STAGE1_IMAGE_PREFIX``, or
    ``None`` when it is not set.
    """

    plan: PinnedImage
    stage2: PinnedImage
    deep_license: bool
    stage1_prefix: str | None = None


def resolve_images(*, deep_license: bool) -> EngineImages:
    """The configured images (``core.config``), checked, read per call."""
    base = pinned_image(bomlens_image(), "SCAN_BOMLENS_IMAGE")
    stage2 = (
        pinned_image(bomlens_deep_license_image(), "SCAN_BOMLENS_DEEP_LICENSE_IMAGE")
        if deep_license
        else base
    )
    return EngineImages(
        plan=base,
        stage2=stage2,
        deep_license=deep_license,
        stage1_prefix=stage1_image_prefix(bomlens_stage1_image_prefix()),
    )


# --------------------------------------------------------------------------
# Host paths
# --------------------------------------------------------------------------


def confined_path(path: Path, root: Path) -> Path:
    """``path`` resolved, refused unless it lies under ``root`` with no symlink.

    ``path`` is taken lexically relative to ``root`` (no ``..``), then every
    component below the real ``root`` is checked with ``lstat``: a symbolic
    link anywhere on the way is refused, even one that would resolve back
    inside. The host side of a mount is resolved by the Docker daemon, outside
    any container, so a link here could hand a container any file on the
    host.
    """
    root_real = Path(os.path.realpath(root))
    try:
        relative = Path(os.path.normpath(path)).relative_to(Path(os.path.normpath(root)))
    except ValueError:
        try:
            relative = Path(os.path.normpath(path)).relative_to(root_real)
        except ValueError as exc:
            raise BomLensEngineError("an engine mount is outside the scan workspace") from exc
    if ".." in relative.parts:
        raise BomLensEngineError("an engine mount is outside the scan workspace")
    current = root_real
    for part in relative.parts:
        current = current / part
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError as exc:
            raise BomLensEngineError("an engine mount does not exist") from exc
        if stat.S_ISLNK(mode):
            raise BomLensEngineError("an engine mount goes through a symbolic link")
    # ``current`` is the real root plus components none of which is a link, so
    # it is already the resolved path; no second resolution is needed.
    return current


class MountStyle(Protocol):
    """How a host path in the workspace becomes a ``--mount`` argument."""

    def mount_args(self, host_path: Path, target: str, *, read_only: bool) -> list[str]: ...

    def trivy_db_args(self) -> list[str]: ...


def _mount_value(**fields: str) -> str:
    for value in fields.values():
        if not _MOUNT_PATH_RE.match(value):
            raise BomLensEngineError("an engine mount path has characters --mount cannot carry")
    return ",".join(f"{k.replace('_', '-')}={v}" for k, v in fields.items())


@dataclass(frozen=True)
class NamedVolumeMounts:
    """The workspace is a named volume (compose): mount sub-paths of it.

    ``volume-subpath`` (Docker Engine 26 and later) mounts one folder or file
    of the volume, and the daemon refuses a sub-path that leaves the volume.
    """

    volume: str
    volume_mount: Path
    trivy_volume: str

    def mount_args(self, host_path: Path, target: str, *, read_only: bool) -> list[str]:
        real_mount = Path(os.path.realpath(self.volume_mount))
        try:
            sub = host_path.relative_to(real_mount)
        except ValueError as exc:
            raise BomLensEngineError(
                "the scan workspace is not inside SCAN_WORKSPACE_MOUNT"
            ) from exc
        value = _mount_value(type="volume", src=self.volume, dst=target, volume_subpath=str(sub))
        return ["--mount", value + (",readonly" if read_only else "")]

    def trivy_db_args(self) -> list[str]:
        if not self.trivy_volume:
            raise BomLensEngineError(
                "SCAN_TRIVY_CACHE_VOLUME is not set; stage 2 has no Trivy database to read"
            )
        value = _mount_value(type="volume", src=self.trivy_volume, dst=TRIVY_DB_TARGET)
        return ["--mount", value + ",readonly"]


@dataclass(frozen=True)
class BindMounts:
    """The worker's paths are the Docker host's paths (a worker on the host)."""

    trivy_cache: Path

    def mount_args(self, host_path: Path, target: str, *, read_only: bool) -> list[str]:
        value = _mount_value(type="bind", src=str(host_path), dst=target)
        return ["--mount", value + (",readonly" if read_only else "")]

    def trivy_db_args(self) -> list[str]:
        if not (self.trivy_cache / "db").is_dir():
            raise BomLensEngineError("the worker's Trivy database has not been downloaded yet")
        real = Path(os.path.realpath(self.trivy_cache))
        value = _mount_value(type="bind", src=str(real), dst=TRIVY_DB_TARGET)
        return ["--mount", value + ",readonly"]


def mount_style() -> MountStyle:
    """The mount style ``SCAN_DOCKER_VOLUME_STRATEGY`` selects, read per call.

    ``volumes_from`` is refused: it would hand every volume of the worker
    (the signing key included) to containers that run the scanned project's
    build.
    """
    strategy = os.getenv("SCAN_DOCKER_VOLUME_STRATEGY", "named").strip().lower()
    if strategy == "named":
        volume = os.getenv("SCAN_WORKSPACE_VOLUME", "").strip()
        if not volume:
            raise BomLensEngineError("SCAN_WORKSPACE_VOLUME is not set")
        safe_token("SCAN_WORKSPACE_VOLUME", volume)
        mount = os.getenv("SCAN_WORKSPACE_MOUNT", "/tmp/trustedoss")  # noqa: S108
        return NamedVolumeMounts(
            volume=volume, volume_mount=Path(mount), trivy_volume=bomlens_trivy_cache_volume()
        )
    if strategy == "bind":
        from integrations.trivy import trivy_cache_dir

        return BindMounts(trivy_cache=trivy_cache_dir())
    raise BomLensEngineError(
        f"SCAN_DOCKER_VOLUME_STRATEGY={clip(strategy)!r} is not usable by the BomLens "
        "engine (use named or bind)"
    )


# --------------------------------------------------------------------------
# Docker
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ContainerState:
    running: bool
    exit_code: int
    oom_killed: bool


class Docker(Protocol):
    """The Docker operations the runner uses (the CLI, or a test double)."""

    def server_api_version(self) -> str | None: ...

    def ensure_image(self, image: str, timeout: int) -> None: ...

    def create(self, argv: list[str], env: Mapping[str, str] | None = None) -> str: ...

    def requested_mounts(self, container: str) -> list[dict[str, Any]]: ...

    def start(self, container: str) -> None: ...

    def stream_logs(
        self, container: str, timeout: int, line_callback: LineCallback | None, stage: str
    ) -> bytes: ...

    def wait(self, container: str, timeout: int) -> int | None: ...

    def kill(self, container: str) -> None: ...

    def state(self, container: str) -> ContainerState: ...

    def remove(self, container: str) -> None: ...

    def own_networks(self) -> frozenset[str] | None: ...

    def scan_containers(self, scan_hex: str) -> list[str]: ...

    def engine_containers(self) -> list[EngineContainer]: ...


@dataclass(frozen=True)
class EngineContainer:
    """An engine container as the reaper sees it (from its labels)."""

    id: str
    scan_hex: str
    deadline: int | None
    instance: str = ""


#: Longest line of container output handed to the scan log. A longer line is
#: cut there (the rest of it is read and dropped, never held).
LOG_LINE_MAX_BYTES: Final = 64 * 1024

#: Container output handed to the scan log per container. Past it the output
#: is still read (the container must not block on a full pipe) and dropped.
LOG_STREAM_MAX_BYTES: Final = 16 * 1024 * 1024

#: How much of the end of a container's output is kept (stage 1's log).
LOG_TAIL_MAX_BYTES: Final = 2 * 1024 * 1024


class _LogTail:
    """The last :data:`LOG_TAIL_MAX_BYTES` of a stream, in a bounded deque."""

    def __init__(self, limit: int = LOG_TAIL_MAX_BYTES) -> None:
        self._chunks: deque[bytes] = deque()
        self._size = 0
        self._limit = limit
        self._lock = threading.Lock()

    def add(self, chunk: bytes) -> None:
        with self._lock:
            self._chunks.append(chunk)
            self._size += len(chunk)
            while self._size > self._limit and self._chunks:
                dropped = self._chunks.popleft()
                self._size -= len(dropped)

    def value(self) -> bytes:
        with self._lock:
            return b"".join(self._chunks)[-self._limit :]


def drain_log_stream(
    stream: IO[bytes],
    *,
    stream_name: str,
    tail: _LogTail,
    budget: list[int],
    budget_lock: threading.Lock,
    callback: LineCallback | None,
) -> None:
    """Read ``stream`` in lines of at most :data:`LOG_LINE_MAX_BYTES`.

    Every chunk goes to ``tail``. Lines go to ``callback`` until the shared
    ``budget`` (bytes, both streams) is spent; after that the stream is still
    read to its end and dropped, so the container never blocks writing.
    A line longer than the cap reaches the callback once, cut, and its
    remainder is dropped.
    """
    continuing = False
    try:
        while True:
            chunk = stream.readline(LOG_LINE_MAX_BYTES)
            if not chunk:
                return
            tail.add(chunk)
            complete = chunk.endswith(b"\n")
            if callback is not None and not continuing:
                with budget_lock:
                    allowed = budget[0] > 0
                    budget[0] -= len(chunk)
                if allowed:
                    text = chunk.decode("utf-8", errors="replace").rstrip("\r\n")
                    if text:
                        try:
                            callback(stream_name, text)
                        except Exception:  # noqa: BLE001 - the drain must keep reading
                            log.warning("bomlens_log_callback_failed", stream=stream_name)
            continuing = not complete
    finally:
        with contextlib.suppress(Exception):
            stream.close()


class DockerCli:
    """:class:`Docker` through the ``docker`` CLI (``DOCKER_HOST`` from the worker)."""

    def _run(
        self, argv: list[str], timeout: int, env: Mapping[str, str] | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        """``docker <argv>``. ``env`` adds variables to the CLI's own
        environment, for the ``--env NAME`` arguments that carry no value."""
        return subprocess.run(  # noqa: S603 - argv built from checked tokens, no shell
            ["docker", *argv],  # noqa: S607 - the docker CLI from PATH
            capture_output=True,
            check=False,
            timeout=timeout,
            env={**os.environ, **env} if env else None,
        )

    def server_api_version(self) -> str | None:
        try:
            done = self._run(["version", "--format", "{{.Server.APIVersion}}"], 30)
        except subprocess.TimeoutExpired:
            return None
        if done.returncode != 0:
            return None
        return done.stdout.decode("utf-8", "replace").strip() or None

    def ensure_image(self, image: str, timeout: int) -> None:
        if self._run(["image", "inspect", "--format", "{{.Id}}", image], 60).returncode == 0:
            return
        try:
            done = self._run(["pull", "--quiet", image], timeout)
        except subprocess.TimeoutExpired as exc:
            raise BomLensEngineError(f"pulling {clip(image)} timed out") from exc
        if done.returncode != 0:
            raise BomLensEngineError(
                f"pulling {clip(image)} failed: "
                f"{done.stderr.decode('utf-8', 'replace').strip()[:200]}"
            )

    def create(self, argv: list[str], env: Mapping[str, str] | None = None) -> str:
        done = self._run(["create", *argv], 120, env)
        if done.returncode != 0:
            raise BomLensEngineError(
                "an engine container could not be created: "
                f"{done.stderr.decode('utf-8', 'replace').strip()[:200]}"
            )
        return done.stdout.decode("utf-8", "replace").strip()

    def requested_mounts(self, container: str) -> list[dict[str, Any]]:
        done = self._run(["inspect", "--format", "{{json .HostConfig.Mounts}}", container], 60)
        if done.returncode != 0:
            raise BomLensEngineError("an engine container could not be inspected")
        try:
            mounts = json.loads(done.stdout.decode("utf-8", "replace") or "null")
        except json.JSONDecodeError as exc:
            raise BomLensEngineError("an engine container could not be inspected") from exc
        return [m for m in mounts or [] if isinstance(m, dict)]

    def start(self, container: str) -> None:
        done = self._run(["start", container], 120)
        if done.returncode != 0:
            raise BomLensEngineError(
                "an engine container did not start: "
                f"{done.stderr.decode('utf-8', 'replace').strip()[:200]}"
            )

    def stream_logs(
        self, container: str, timeout: int, line_callback: LineCallback | None, stage: str
    ) -> bytes:
        """Follow the container's output until it exits; return its tail.

        Raises :class:`subprocess.TimeoutExpired` (with the tail as
        ``output``) when the container is still writing after ``timeout``.
        """
        proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            ["docker", "logs", "--follow", container],  # noqa: S607 - the docker CLI
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert proc.stdout is not None and proc.stderr is not None  # noqa: S101
        tail = _LogTail()
        budget = [LOG_STREAM_MAX_BYTES]
        budget_lock = threading.Lock()
        threads = [
            threading.Thread(
                target=drain_log_stream,
                kwargs={
                    "stream": stream,
                    "stream_name": name,
                    "tail": tail,
                    "budget": budget,
                    "budget_lock": budget_lock,
                    "callback": line_callback,
                },
                name=f"{stage}-{name}-drain",
                daemon=True,
            )
            for stream, name in ((proc.stdout, "stdout"), (proc.stderr, "stderr"))
        ]
        for thread in threads:
            thread.start()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            for thread in threads:
                thread.join(timeout=5)
            raise subprocess.TimeoutExpired(
                cmd="docker logs", timeout=float(timeout), output=tail.value()
            ) from None
        except BaseException:
            proc.kill()
            with contextlib.suppress(Exception):
                proc.wait(timeout=5)
            raise
        for thread in threads:
            thread.join(timeout=30)
        return tail.value()

    def wait(self, container: str, timeout: int) -> int | None:
        try:
            done = self._run(["wait", container], max(timeout, 1))
        except subprocess.TimeoutExpired:
            return None
        try:
            return int(done.stdout.decode().strip())
        except ValueError:
            return None

    def kill(self, container: str) -> None:
        self._run(["kill", container], 60)

    def state(self, container: str) -> ContainerState:
        done = self._run(["inspect", "--format", "{{json .State}}", container], 60)
        if done.returncode != 0:
            raise BomLensEngineError("an engine container could not be inspected")
        try:
            raw = json.loads(done.stdout.decode("utf-8", "replace") or "{}")
        except json.JSONDecodeError as exc:
            raise BomLensEngineError("an engine container could not be inspected") from exc
        return ContainerState(
            running=bool(raw.get("Running")),
            exit_code=int(raw.get("ExitCode", -1)),
            oom_killed=bool(raw.get("OOMKilled")),
        )

    def remove(self, container: str) -> None:
        try:
            self._run(["rm", "--force", container], 60)
        except Exception:  # noqa: BLE001 - cleanup is best-effort
            log.warning("bomlens_container_remove_failed", container=container)

    def scan_containers(self, scan_hex: str) -> list[str]:
        """Engine containers an earlier attempt at this scan left behind."""
        done = self._run(
            [
                "ps",
                "--all",
                "--quiet",
                "--filter",
                f"label={LABEL_ROLE}",
                "--filter",
                f"label={INSTANCE_LABEL}={instance_id()}",
                "--filter",
                f"label=trusca.scan={scan_hex}",
            ],
            60,
        )
        if done.returncode != 0:
            return []
        return [line for line in done.stdout.decode("utf-8", "replace").split() if line]

    def engine_containers(self) -> list[EngineContainer]:
        """This installation's engine containers, with their scan and deadline.

        Filtered by ``trusca.instance``: another installation sharing the
        daemon has scans this database does not know, and its containers are
        not this installation's to judge.
        """
        done = self._run(
            [
                "ps",
                "--all",
                "--no-trunc",
                "--filter",
                f"label={LABEL_ROLE}",
                "--filter",
                f"label={INSTANCE_LABEL}={instance_id()}",
                "--format",
                '{{.ID}}\t{{.Label "trusca.scan"}}\t{{.Label "trusca.deadline"}}'
                '\t{{.Label "trusca.instance"}}',
            ],
            60,
        )
        if done.returncode != 0:
            raise BomLensEngineError("the engine containers could not be listed")
        found: list[EngineContainer] = []
        for line in done.stdout.decode("utf-8", "replace").splitlines():
            parts = line.split("\t")
            if len(parts) != 4 or not parts[0]:
                continue
            deadline = int(parts[2]) if parts[2].isdigit() else None
            found.append(
                EngineContainer(
                    id=parts[0], scan_hex=parts[1], deadline=deadline, instance=parts[3]
                )
            )
        return found

    def own_networks(self) -> frozenset[str] | None:
        """Names and IDs of the networks of the container this worker runs in.

        ``None`` when the worker is not a container this daemon knows (or the
        answer cannot be read); the caller refuses then.
        """
        me = socket.gethostname().strip()
        if not me or not _NETWORK_RE.match(me):
            return None
        try:
            done = self._run(
                [
                    "container",
                    "inspect",
                    "--format",
                    "{{json .NetworkSettings.Networks}}",
                    me,
                ],
                30,
            )
        except subprocess.TimeoutExpired:
            return None
        if done.returncode != 0:
            return None
        try:
            networks = json.loads(done.stdout.decode("utf-8", "replace") or "{}")
        except json.JSONDecodeError:
            return None
        if not isinstance(networks, dict):
            return None
        names: set[str] = set()
        for name, detail in networks.items():
            names.add(str(name))
            if isinstance(detail, dict) and detail.get("NetworkID"):
                names.add(str(detail["NetworkID"]))
        return frozenset(names)


# --------------------------------------------------------------------------
# One container
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ContainerSpec:
    """Everything one ``docker run`` gets; built only by this module."""

    name: str
    image: str
    user: str | None
    entrypoint: str | None
    args: tuple[str, ...]
    workdir: str | None
    mounts: tuple[tuple[Path, str, bool], ...]
    extra_mount_args: tuple[str, ...]
    env: tuple[tuple[str, str], ...]
    network: str
    read_only: bool
    #: Variables whose value comes from the worker's environment (a proxy URL
    #: can carry credentials). ``docker create`` gets only their names
    #: (``--env NAME``); the values reach the docker CLI through its own
    #: process environment, so they never appear on a command line.
    secret_env: tuple[tuple[str, str], ...] = ()
    #: Drop every capability and add none back, instead of the sidecar's
    #: minimal set: for a container that runs as a non-root user.
    drop_all_caps: bool = False

    def container_env(self) -> tuple[list[tuple[str, str]], dict[str, str]]:
        """``(values on the command line, values by name only)``, one per name.

        A name set twice keeps its last value, so TRUSCA's own settings, added
        after the plan's, win. A name TRUSCA sets is never also passed by name.
        """
        plain: dict[str, str] = {}
        for name, value in self.env:
            plain.pop(name, None)
            plain[name] = value
        secret: dict[str, str] = {}
        for name, value in self.secret_env:
            if name not in plain:
                secret[name] = value
        return list(plain.items()), secret


@dataclass(frozen=True)
class ContainerOutcome:
    exit_code: int
    oom_killed: bool
    timed_out: bool
    output: bytes

    @property
    def killed(self) -> bool:
        return self.timed_out or self.oom_killed or self.exit_code in _KILLED_EXIT_CODES


def _resource_args() -> list[str]:
    memory = safe_token("SCAN_SIDECAR_MEMORY", os.getenv("SCAN_SIDECAR_MEMORY", "4g"))
    cpus = safe_token("SCAN_SIDECAR_CPUS", os.getenv("SCAN_SIDECAR_CPUS", "2"))
    # --memory-swap equal to --memory: no swap, so a stage that runs out of
    # memory is killed (and reported as OOM) instead of thrashing the host.
    return ["--memory", memory, "--memory-swap", memory, "--cpus", cpus]


def _without_added_caps(args: list[str]) -> list[str]:
    """``args`` without its ``--cap-add`` pairs, and with ``--cap-drop ALL``."""
    kept: list[str] = []
    index = 0
    while index < len(args):
        if args[index] == "--cap-add":
            index += 2
            continue
        kept.append(args[index])
        index += 1
    pairs = zip(kept, kept[1:], strict=False)
    if not any(flag == "--cap-drop" and value.upper() == "ALL" for flag, value in pairs):
        kept += ["--cap-drop", "ALL"]
    return kept


def docker_run_argv(
    spec: ContainerSpec, mounts: MountStyle, scan_hex: str, deadline: int
) -> list[str]:
    """The ``docker create`` arguments for ``spec``.

    ``deadline`` (epoch seconds) is when the container must be gone; the
    reaper (``tasks.bomlens_engine_reaper``) removes it after that even when
    the task that started it never got to (a hard time limit, a revoke).
    """
    argv = [
        "--name",
        spec.name,
        "--label",
        LABEL_ROLE,
        "--label",
        f"{INSTANCE_LABEL}={instance_id()}",
        "--label",
        f"trusca.scan={scan_hex}",
        "--label",
        f"{DEADLINE_LABEL}={deadline}",
    ]
    security = sidecar_security_args()
    argv += _without_added_caps(security) if spec.drop_all_caps else security
    argv += _resource_args()
    if spec.read_only:
        argv.append("--read-only")
    argv += ["--network", spec.network]
    if spec.user is not None:
        argv += ["--user", spec.user]
    if spec.entrypoint is not None:
        argv += ["--entrypoint", spec.entrypoint]
    if spec.workdir is not None:
        argv += ["--workdir", spec.workdir]
    for host_path, target, read_only in spec.mounts:
        argv += mounts.mount_args(host_path, target, read_only=read_only)
    argv += list(spec.extra_mount_args)
    plain, secret = spec.container_env()
    for name, value in plain:
        argv += ["--env", f"{name}={value}"]
    for name in secret:
        argv += ["--env", name]
    argv.append(spec.image)
    argv += list(spec.args)
    return argv


@dataclass
class RunReport:
    """What happened in one engine run (kept in ``scan_metadata``)."""

    plan_status: str | None = None
    plan_reason: str | None = None
    detected_ecosystem: str | None = None
    stage1_image: str | None = None
    stage1_exit_code: int | None = None
    stage1_oom: bool = False
    stage1_timed_out: bool = False
    restored: bool | None = None
    stage2_image: str | None = None
    stage2_exit_code: int | None = None
    containers: list[str] = field(default_factory=list)
    #: ``source`` (PLAN, stage 1, stage 2) or ``image`` (one ``MODE=IMAGE`` run,
    #: recorded in the ``stage2_*`` fields: it is the BomLens image's run).
    mode: str = "source"
    #: The reference handed to ``MODE=IMAGE`` (:func:`image_target`).
    image_target: str | None = None
    image_timed_out: bool = False

    def as_metadata(self) -> dict[str, object]:
        if self.mode == "image":
            return {
                "mode": "image",
                # Whole: image_target() checked it against the reference
                # grammar and its length, and a cut digest names no image.
                "image_target": self.image_target,
                "engine_image": self.stage2_image,
                "exit_code": self.stage2_exit_code,
                "timed_out": self.image_timed_out,
            }
        return {
            "plan_status": self.plan_status,
            "plan_reason": clip(self.plan_reason) if self.plan_reason else None,
            "detected_ecosystem": clip(self.detected_ecosystem)
            if self.detected_ecosystem
            else None,
            "stage1_image": self.stage1_image,
            "stage1_exit_code": self.stage1_exit_code,
            "stage1_oom": self.stage1_oom,
            "stage1_timed_out": self.stage1_timed_out,
            "source_restored": self.restored,
            "stage2_image": self.stage2_image,
            "stage2_exit_code": self.stage2_exit_code,
        }


@dataclass(frozen=True)
class EngineLayout:
    """The scan's engine folders, all below the scan workspace."""

    scan_dir: Path
    source: Path

    @property
    def base(self) -> Path:
        return self.scan_dir / "bomlens"

    @property
    def plan_dir(self) -> Path:
        return self.base / "plan"

    @property
    def plan_files(self) -> Path:
        return self.base / "plan-files"

    @property
    def output(self) -> Path:
        return self.base / "out"

    @property
    def guard_state(self) -> Path:
        return self.base / "guard-state"

    def scratch(self, name: str) -> Path:
        return self.base / f"tmp-{name}"

    @property
    def verified(self) -> Path:
        """Where the adapter keeps the bytes it checked.

        A sibling of :attr:`base`, not inside it: no container (not even the
        ownership reset, which mounts :attr:`base`) ever has it mounted.
        """
        return self.scan_dir / "bomlens-verified"


StageHook = Callable[[str], None]


class BomLensRunner:
    """Run PLAN, stage 1 (and its restore) and stage 2 for one scan."""

    def __init__(
        self,
        *,
        scan_uuid: uuid.UUID,
        layout: EngineLayout,
        images: EngineImages,
        project_name: str,
        project_version: str,
        fetch_license: bool,
        include_non_shipped: bool = False,
        docker: Docker | None = None,
        mounts: MountStyle | None = None,
        line_callback: Callable[[str], LineCallback | None] | None = None,
    ) -> None:
        self.scan_uuid = scan_uuid
        self.layout = layout
        self.images = images
        self.project_name = _project_token(project_name, "project")
        self.project_version = _project_token(project_version, "0")
        self.fetch_license = fetch_license
        self.include_non_shipped = include_non_shipped
        self.docker: Docker = docker or DockerCli()
        self.mounts: MountStyle = mounts or mount_style()
        self._line_callback = line_callback or (lambda _stage: None)
        self.report = RunReport()
        self._count = 0
        self._root = Path(workspace_root())

    # ------------------------------------------------------------- helpers

    def _host(self, path: Path) -> Path:
        return confined_path(path, self._root)

    def _container_name(self, label: str) -> str:
        self._count += 1
        return f"trusca-bomlens-{self.scan_uuid.hex}-{self._count}-{label}"

    def _owner_env(self) -> list[tuple[str, str]]:
        getuid = getattr(os, "getuid", None)
        getgid = getattr(os, "getgid", None)
        if getuid is None or getgid is None:
            return []
        return [("HOST_UID", str(getuid())), ("HOST_GID", str(getgid()))]

    def _network(self, required: bool) -> str:
        if not required:
            return "none"
        name = bomlens_network()
        if not name:
            raise BomLensEngineError(
                "this stage needs the package registries and SCAN_BOMLENS_NETWORK is not set"
            )
        if not _NETWORK_RE.match(name) or name.lower() in _FORBIDDEN_NETWORKS:
            raise BomLensEngineError(f"SCAN_BOMLENS_NETWORK={clip(name)!r} is not allowed")
        own = self.docker.own_networks()
        if own is None:
            # Fail closed: without the worker's own networks there is no way
            # to tell the configured one is not the database's.
            raise BomLensEngineError(
                "the worker's own networks could not be read, so SCAN_BOMLENS_NETWORK "
                "cannot be checked against them"
            )
        if name in own:
            raise BomLensEngineError(
                f"SCAN_BOMLENS_NETWORK={clip(name)!r} is the worker's own network; "
                "engine containers must not reach TRUSCA's database"
            )
        return name

    def _fresh_dir(self, path: Path) -> Path:
        if path.is_symlink():
            raise BomLensEngineError("an engine folder is a symbolic link")
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True)
        return self._host(path)

    def _passthrough(self, stage: Stage) -> list[tuple[str, str]]:
        return self._passthrough_names(stage.env_passthrough)

    def _passthrough_names(self, names: tuple[str, ...]) -> list[tuple[str, str]]:
        env: list[tuple[str, str]] = []
        for name in names:
            if name in _TRUSCA_SET_ENV:
                # TRUSCA's own setting decides these (_trusca_env), never a
                # variable that happens to be in the worker's environment.
                continue
            value = os.getenv(name)
            if value is None or value == "":
                continue
            if len(value) > 1024 or any(c in value for c in "\x00\n\r"):
                log.warning("bomlens_env_passthrough_dropped", name=name)
                continue
            if name in _PROXY_ADDRESS_ENV and is_loopback_proxy(value):
                log.info("bomlens_loopback_proxy_not_passed", name=name)
                continue
            env.append((name, value))
        return env

    def _trusca_env(self, stage: Stage) -> list[tuple[str, str]]:
        """Settings TRUSCA passes from its own configuration.

        ``BOMLENS_INCLUDE_NON_SHIPPED`` keeps test, example and benchmark
        trees in the SBOM; it follows SCAN_SCOPE_FILTER_NON_DEPLOYABLE_ENABLED
        (and the scope filter's master switch), so turning that filter off
        means the same thing under either engine.

        ``BOMLENS_TRIVY_DB_MAX_AGE_DAYS`` follows TRUSCA's Trivy database
        refresh interval (:func:`trivy_db_max_age_days`).
        """
        env: list[tuple[str, str]] = []
        if self.include_non_shipped and NON_SHIPPED_ENV in stage.env_passthrough:
            env.append((NON_SHIPPED_ENV, "true"))
        if TRIVY_DB_MAX_AGE_ENV in stage.env_passthrough:
            env.append((TRIVY_DB_MAX_AGE_ENV, str(trivy_db_max_age_days())))
        return env

    def _execute(
        self,
        spec: ContainerSpec,
        *,
        timeout: int,
        log_stage: str,
    ) -> ContainerOutcome:
        """Run ``spec`` to its end; the container is gone when this returns."""
        deadline = int(time.time()) + timeout + 2 * EXIT_GRACE_SECONDS + REAP_MARGIN_SECONDS
        argv = docker_run_argv(spec, self.mounts, self.scan_uuid.hex, deadline)
        log.info(
            "bomlens_container_start",
            stage=log_stage,
            image=spec.image,
            network=spec.network,
            container=spec.name,
        )
        try:
            container = self.docker.create(argv, env=spec.container_env()[1])
        except BaseException:
            # A failed create can still leave a container holding the name.
            self.docker.remove(spec.name)
            raise
        self.report.containers.append(spec.name)
        try:
            # Created, not started: what the daemon recorded is checked
            # against what was asked before anything runs (security review on
            # #522, L3). A daemon that dropped a sub-path would mount the whole
            # workspace volume.
            check_requested_mounts(argv, self.docker.requested_mounts(container))
            self.docker.start(container)
            timed_out = False
            output = b""
            try:
                output = self.docker.stream_logs(
                    container, timeout, self._line_callback(log_stage), log_stage
                )
            except subprocess.TimeoutExpired as exc:
                timed_out = True
                output = (exc.output or b"") + (exc.stderr or b"")
            state = self._settle(container, force_kill=timed_out)
        finally:
            # Also reached when Celery's soft time limit or a revoke interrupts
            # the wait: the container must not outlive the task.
            self.docker.remove(container)
        outcome = ContainerOutcome(
            exit_code=state.exit_code,
            oom_killed=state.oom_killed,
            timed_out=timed_out,
            output=output,
        )
        log.info(
            "bomlens_container_exit",
            stage=log_stage,
            exit_code=outcome.exit_code,
            oom_killed=outcome.oom_killed,
            timed_out=outcome.timed_out,
        )
        return outcome

    def _settle(self, container: str, *, force_kill: bool) -> ContainerState:
        """Wait for ``container`` to exit (killing it when needed) and confirm."""
        code = None if force_kill else self.docker.wait(container, EXIT_GRACE_SECONDS)
        if code is None:
            log.warning("bomlens_container_kill", container=container)
            self.docker.kill(container)
            self.docker.wait(container, EXIT_GRACE_SECONDS)
        state = self.docker.state(container)
        if state.running:
            raise EngineContainerStillRunning(
                "an engine container is still running after it was killed; "
                "its output is not read"
            )
        return state

    # ------------------------------------------------------------- stages

    def run_plan(self) -> ExecutionPlan:
        """``MODE=PLAN``: write and check the execution plan."""
        layout = self.layout
        source = self._host(layout.source)
        plan_dir = self._fresh_dir(layout.plan_dir)
        scratch = self._fresh_dir(layout.scratch("plan"))
        self.docker.ensure_image(self.images.plan.run_ref, PULL_TIMEOUT_SECONDS)
        env = [
            ("MODE", "PLAN"),
            ("PROJECT_NAME", self.project_name),
            ("PROJECT_VERSION", self.project_version),
            ("HOST_OUTPUT_DIR", PLAN_OUTPUT_TARGET),
            ("FETCH_LICENSE", "true" if self.fetch_license else "false"),
            # Stage 2's image, so the plan names the one TRUSCA will run.
            ("BOMLENS_IMAGE_REF", self.images.stage2.ref),
            ("BOMLENS_IMAGE_DIGEST", self.images.stage2.digest),
            *self._owner_env(),
        ]
        if self.images.stage1_prefix is not None:
            # Where the plan names stage 1's cdxgen image from: a registry
            # mirror. The digest stays the published one (BomLens #205).
            env.append(("CDXGEN_IMAGE_PREFIX", self.images.stage1_prefix))
        spec = ContainerSpec(
            name=self._container_name("plan"),
            image=self.images.plan.run_ref,
            user=None,
            entrypoint=None,
            args=(),
            workdir=PLAN_OUTPUT_TARGET,
            mounts=(
                (source, PLAN_SOURCE_TARGET, True),
                (plan_dir, PLAN_OUTPUT_TARGET, False),
                (scratch, SCRATCH_TARGET, False),
            ),
            extra_mount_args=(),
            env=tuple(env),
            network="none",
            read_only=True,
        )
        outcome = self._execute(spec, timeout=PLAN_TIMEOUT_SECONDS, log_stage="prep")
        if outcome.timed_out or outcome.exit_code != 0:
            raise BomLensEngineError(
                f"BomLens MODE=PLAN exited {outcome.exit_code}"
                + (" after its time limit" if outcome.timed_out else "")
            )
        plan = load_plan(plan_dir)
        self._check_plan_images(plan)
        self.report.plan_status = plan.status
        self.report.plan_reason = plan.reason
        self.report.detected_ecosystem = plan.detected_ecosystem
        log.info(
            "bomlens_plan",
            stage="prep",
            status=plan.status,
            reason=plan.reason,
            ecosystem=clip(plan.detected_ecosystem),
        )
        return plan

    def _check_plan_images(self, plan: ExecutionPlan) -> None:
        if not plan.supported:
            return
        stage1 = plan.generate
        repo = image_repository(stage1.image) + "/"
        allowed = bomlens_stage1_repositories(self.images.stage1_prefix)
        # Strictly under a prefix: an image named by the prefix path itself
        # (``ghcr.io/cdxgen:13.2.0`` for ``ghcr.io/cdxgen/``) is not in it.
        if not any(repo.startswith(prefix) and len(repo) > len(prefix) for prefix in allowed):
            raise BomLensEngineError(
                f"the plan's stage 1 image {clip(stage1.image)!r} is not from an allowed "
                "repository (SCAN_BOMLENS_STAGE1_REPOSITORIES, "
                "SCAN_BOMLENS_STAGE1_IMAGE_PREFIX)"
            )
        stage2 = plan.postprocess
        if (stage2.image, stage2.image_digest) != (
            self.images.stage2.ref,
            self.images.stage2.digest,
        ):
            raise BomLensEngineError(
                f"the plan's stage 2 image {clip(stage2.image)!r} is not the configured "
                "BomLens image"
            )

    def _copy_plan_files(self, stage: Stage) -> dict[str, Path]:
        """Copy stage 1's helper files out of the plan folder.

        The PLAN container wrote them, and stage 1 could rewrite its output
        folder before the restore run; the copies live in a folder no
        container can write. Only regular files are copied.
        """
        dest_dir = self._fresh_dir(self.layout.plan_files)
        copies: dict[str, Path] = {}
        for mount in stage.mounts:
            if mount.role != "plan-file" or mount.source is None:
                continue
            name = mount.source.split("/", 1)[1]
            src = self.layout.plan_dir / PLAN_FILES_DIR / name
            src_host = self._host(src)
            if not stat.S_ISREG(os.lstat(src_host).st_mode):
                raise BomLensEngineError(f"the plan file {clip(name)!r} is not a regular file")
            dest = dest_dir / name
            shutil.copyfile(src_host, dest, follow_symlinks=False)
            copies[mount.source] = self._host(dest)
        return copies

    def _stage_mounts(
        self, stage: Stage, *, output: Path, plan_files: dict[str, Path], guard: Path | None
    ) -> tuple[tuple[Path, str, bool], ...]:
        mounts: list[tuple[Path, str, bool]] = []
        source = self._host(self.layout.source)
        for mount in stage.mounts:
            host = self._mount_host(
                mount, source=source, output=output, plan_files=plan_files, guard=guard
            )
            if host is None:
                continue
            mounts.append((host, mount.target, mount.mode == "ro"))
        return tuple(mounts)

    @staticmethod
    def _mount_host(
        mount: Mount,
        *,
        source: Path,
        output: Path,
        plan_files: dict[str, Path],
        guard: Path | None,
    ) -> Path | None:
        if mount.role == "source":
            return source
        if mount.role == "output":
            return output
        if mount.role == "plan-file":
            assert mount.source is not None  # noqa: S101 - plan.py requires it
            return plan_files[mount.source]
        if mount.role == "guard-state":
            return guard
        # The Trivy database is not in the workspace; run_stage2 mounts it
        # (MountStyle.trivy_db_args). Anything else is a cache TRUSCA does not
        # provide; plan.py refused it unless optional.
        return None

    def _stage1_spec(
        self, stage: Stage, *, output: Path, plan_files: dict[str, Path], guard: Path, scratch: Path
    ) -> ContainerSpec:
        env = list(stage.env.items()) + self._owner_env() + self._trusca_env(stage)
        # The project name and version are TRUSCA's, whatever the plan wrote.
        env = [(k, v) for k, v in env if k not in ("PROJECT_NAME", "PROJECT_VERSION")]
        env += [("PROJECT_NAME", self.project_name), ("PROJECT_VERSION", self.project_version)]
        if stage.mount("guard-state") is not None:
            env.append(("BOMLENS_GUARD_ID", f"trusca-{self.scan_uuid.hex}"))
        return ContainerSpec(
            name=self._container_name("stage1"),
            image=f"{stage.image}@{stage.image_digest}",
            user=stage.user,
            entrypoint=stage.entrypoint,
            args=stage.args,
            workdir=stage.workdir,
            mounts=self._stage_mounts(stage, output=output, plan_files=plan_files, guard=guard)
            + ((scratch, SCRATCH_TARGET, False),),
            extra_mount_args=(),
            env=tuple(env),
            secret_env=tuple(self._passthrough(stage)),
            network=self._network(stage.network_required),
            read_only=bomlens_stage1_read_only_root(),
        )

    def run_stage1(self, plan: ExecutionPlan) -> ContainerOutcome:
        """Stage 1, then the restore run when it was killed."""
        stage = plan.generate
        output = self._fresh_dir(self.layout.output)
        guard = self._fresh_dir(self.layout.guard_state)
        scratch = self._fresh_dir(self.layout.scratch("stage1"))
        plan_files = self._copy_plan_files(stage)
        spec = self._stage1_spec(
            stage, output=output, plan_files=plan_files, guard=guard, scratch=scratch
        )
        self.report.stage1_image = spec.image
        self.docker.ensure_image(spec.image, PULL_TIMEOUT_SECONDS)
        outcome = self._execute(spec, timeout=bomlens_stage1_timeout_seconds(), log_stage="cdxgen")
        self.report.stage1_exit_code = outcome.exit_code
        self.report.stage1_oom = outcome.oom_killed
        self.report.stage1_timed_out = outcome.timed_out
        self._write_stage1_log(output, outcome)
        if outcome.killed and stage.mount("guard-state") is not None:
            self.report.restored = self._restore_source(spec)
        return outcome

    def _restore_source(self, stage1: ContainerSpec) -> bool:
        """The plan's ``on_kill``: the same stage 1, restoring the source only."""
        # The plan files are re-checked: same folder, same names, still files.
        for host_path, _target, _ro in stage1.mounts:
            self._host(host_path)
        spec = replace(
            stage1,
            name=self._container_name("restore"),
            env=(*stage1.env, ("BOMLENS_GUARD_RESTORE_ONLY", "1")),
            network="none",
        )
        try:
            outcome = self._execute(spec, timeout=RESTORE_TIMEOUT_SECONDS, log_stage="cdxgen")
        except BomLensEngineError:
            log.error("bomlens_source_restore_failed", stage="cdxgen", exc_info=True)
            return False
        restored = outcome.exit_code == 0 and not outcome.timed_out
        if not restored:
            log.error("bomlens_source_restore_failed", stage="cdxgen", exit_code=outcome.exit_code)
        else:
            log.info("bomlens_source_restored", stage="cdxgen")
        return restored

    @staticmethod
    def _write_stage1_log(output: Path, outcome: ContainerOutcome) -> None:
        write_new_file(output / STAGE1_LOG_NAME, outcome.output[-_STAGE1_LOG_MAX_BYTES:])

    def stage2_for(self, plan: ExecutionPlan) -> Stage:
        """Stage 2 as TRUSCA runs it.

        From the plan only the two mount targets are taken (its image was
        checked against the configuration in :meth:`_check_plan_images`).
        Everything else is fixed here: no user or entrypoint override, no
        arguments, the output folder as working directory, no network, and
        TRUSCA's own environment (security review on #522, L2). A plan with no
        stages gets the targets the contract documents.

        The worker's Trivy database is always mounted read-only as the
        ``trivy-db`` role, at the constant ``/trivy-db``, never at a path read
        from the plan. A plan from BomLens #203 on lists that role (plan.py
        accepts it only there, read-only); an earlier plan does not, and stage
        2 still needs the database, which TRUSCA's ``TRIVY_CACHE_DIR`` points
        an earlier image at.
        """
        source_target, output_target = PLAN_SOURCE_TARGET, PLAN_OUTPUT_TARGET
        if plan.supported:
            planned = plan.postprocess
            source_mount, output_mount = planned.mount("source"), planned.mount("output")
            assert source_mount is not None and output_mount is not None  # noqa: S101
            source_target, output_target = source_mount.target, output_mount.target
        return Stage(
            id="postprocess",
            stage=2,
            image=self.images.stage2.ref,
            image_digest=self.images.stage2.digest,
            user=None,
            entrypoint=None,
            args=(),
            workdir=output_target,
            mounts=(
                Mount(role="source", target=source_target, mode="ro"),
                Mount(role="output", target=output_target, mode="rw"),
                Mount(role=TRIVY_DB_ROLE, target=TRIVY_DB_TARGET, mode="ro", optional=True),
            ),
            env={
                "MODE": "POSTPROCESS",
                "BOMLENS_RUN_INPUT": plan.output_file,
                "HOST_OUTPUT_DIR": output_target,
                "PROJECT_NAME": self.project_name,
                "PROJECT_VERSION": self.project_version,
                "UPLOAD_ENABLED": "false",
            },
            env_passthrough=(NON_SHIPPED_ENV, "SRC_TREE_EXCLUDE", TRIVY_DB_MAX_AGE_ENV),
            network_required=False,
        )

    def run_stage2(self, plan: ExecutionPlan, stage1: ContainerOutcome | None) -> ContainerOutcome:
        """Stage 2 (POSTPROCESS), whatever stage 1 did."""
        stage = self.stage2_for(plan)
        if plan.supported:
            output = self._host(self.layout.output)
        else:
            output = self._fresh_dir(self.layout.output)
            note = (
                f"stage 1 did not run: the execution plan is unsupported "
                f"({plan.reason or 'no reason given'})\n"
            )
            write_new_file(output / STAGE1_LOG_NAME, note.encode("utf-8"))
        scratch = self._fresh_dir(self.layout.scratch("stage2"))
        image = self.images.stage2
        exit_code = stage1.exit_code if stage1 is not None else 127
        env = list(stage.env.items()) + self._owner_env() + self._trusca_env(stage)
        env += [
            ("BOMLENS_STAGE1_EXIT", str(exit_code)),
            ("BOMLENS_STAGE1_OOM", "true" if stage1 is not None and stage1.oom_killed else "false"),
            ("BOMLENS_STAGE1_LOG", STAGE1_LOG_NAME),
            ("BOMLENS_IMAGE_REF", image.ref),
            ("BOMLENS_IMAGE_DIGEST", image.digest),
            # TRUSCA's own settings for the run: the vulnerability report is
            # required, nothing is looked up online, and Trivy reads the
            # worker's database read-only instead of downloading one. An image
            # from BomLens #203 on finds the database at the trivy-db mount
            # itself (and only with GENERATE_SECURITY=true); the TRIVY_*
            # values are for an earlier image and say the same thing.
            ("GENERATE_SECURITY", "true"),
            ("SECURITY_ENRICH", "false"),
            ("DEEP_LICENSE", "true" if self.images.deep_license else "false"),
            ("TRIVY_CACHE_DIR", TRIVY_DB_TARGET),
            ("TRIVY_SKIP_DB_UPDATE", "true"),
            ("TRIVY_SKIP_JAVA_DB_UPDATE", "true"),
            ("TRIVY_OFFLINE_SCAN", "true"),
            ("TRIVY_NO_PROGRESS", "true"),
        ]
        spec = ContainerSpec(
            name=self._container_name("stage2"),
            image=image.run_ref,
            user=stage.user,
            entrypoint=stage.entrypoint,
            args=stage.args,
            workdir=stage.workdir,
            mounts=self._stage_mounts(stage, output=output, plan_files={}, guard=None)
            + ((scratch, SCRATCH_TARGET, False),),
            extra_mount_args=tuple(self.mounts.trivy_db_args()),
            env=tuple(env),
            secret_env=tuple(self._passthrough(stage)),
            network=self._network(stage.network_required),
            read_only=True,
        )
        self.report.stage2_image = image.run_ref
        self.docker.ensure_image(image.run_ref, PULL_TIMEOUT_SECONDS)
        outcome = self._execute(
            spec, timeout=bomlens_stage2_timeout_seconds(), log_stage="postprocess"
        )
        self.report.stage2_exit_code = outcome.exit_code
        return outcome

    def verified_dir(self) -> Path:
        """A fresh folder for the adapter's verified copies, refused if a link.

        Created only after every engine container has exited. Any earlier
        entry at that path (a link included) is removed rather than followed.
        """
        return self._fresh_dir(self.layout.verified)

    def check_docker_api(self) -> None:
        """Refuse a daemon older than :data:`MIN_DOCKER_API` (security review, L3)."""
        version = self.docker.server_api_version()
        parsed = _api_version(version)
        if parsed is None or parsed < MIN_DOCKER_API:
            raise BomLensEngineError(
                f"the Docker API is {clip(version) if version else 'unknown'}; the BomLens "
                f"engine needs {MIN_DOCKER_API[0]}.{MIN_DOCKER_API[1]} or later (Docker Engine 26)"
            )

    def remove_leftovers(self) -> None:
        """Remove containers an earlier attempt at this scan left (a killed worker)."""
        for container in self.docker.scan_containers(self.scan_uuid.hex):
            log.warning("bomlens_leftover_container_removed", container=clip(container))
            self.docker.remove(container)

    def reclaim_ownership(self) -> None:
        """Give the engine folders and the source tree back to the worker's user.

        The containers run as root. BomLens hands its own outputs back
        (``HOST_UID``/``HOST_GID``), but the scratch ``/tmp`` and the guard
        state are root's, and the worker could not delete them when the
        workspace is cleaned up. ``chown -R -h`` changes the links themselves,
        never what they point to. Best-effort: a failure leaves the folders to
        the orphan workspace cleaner, with a warning.
        """
        owner = self._owner_env()
        if not owner or owner[0][1] == "0":
            return
        uid_gid = f"{owner[0][1]}:{owner[1][1]}"
        try:
            mounts: list[tuple[Path, str, bool]] = [
                (self._host(self.layout.base), "/w/engine", False)
            ]
            if self.layout.source.exists():
                mounts.append((self._host(self.layout.source), "/w/source", False))
            spec = ContainerSpec(
                name=self._container_name("chown"),
                image=self.images.plan.run_ref,
                user="0:0",
                entrypoint="sh",
                args=("-c", f"chown -R -h {uid_gid} /w/engine /w/source 2>/dev/null; exit 0"),
                workdir=None,
                mounts=tuple(mounts),
                extra_mount_args=(),
                env=(),
                network="none",
                read_only=True,
            )
            self._execute(spec, timeout=PLAN_TIMEOUT_SECONDS, log_stage="postprocess")
        except Exception:  # noqa: BLE001 - cleanup is best-effort
            log.warning("bomlens_reclaim_ownership_failed", exc_info=True)

    def run(self, stage: StageHook | None = None) -> Path:
        """PLAN, stage 1 (+ restore), stage 2. Returns the output folder.

        ``stage`` is told ``prep`` before the plan, ``cdxgen`` before stage 1
        and ``scancode`` before stage 2 (the progress labels the source
        pipeline already shows, in the same order).

        Every container has exited when this returns or raises. The output
        folder is for :func:`tasks.bomlens_result.apply_bomlens_result`.
        """
        announce = stage or (lambda _name: None)
        self.check_docker_api()
        self.remove_leftovers()
        try:
            announce("prep")
            plan = self.run_plan()
            stage1 = None
            if plan.supported:
                announce("cdxgen")
                stage1 = self.run_stage1(plan)
            announce("scancode")
            self.run_stage2(plan, stage1)
        finally:
            self.reclaim_ownership()
        return self._host(self.layout.output)

    # ------------------------------------------------------- image scan

    def _non_root_user(self) -> str:
        """``uid:gid`` of the worker, the user the image scan container runs as.

        The container writes only the output and scratch folders, which the
        worker created, so running as the worker's own user needs no
        capability and leaves no file the worker cannot delete. A worker that
        runs as root has no such user to lend; it is refused rather than
        running the container as root.
        """
        owner = self._owner_env()
        if not owner or owner[0][1] == "0":
            raise BomLensEngineError(
                "the BomLens image scan runs as the worker's own non-root user, "
                "and this worker runs as root"
            )
        return f"{owner[0][1]}:{owner[1][1]}"

    def image_spec(self, target: str, *, output: Path, scratch: Path) -> ContainerSpec:
        """The ``MODE=IMAGE`` container for ``target`` (from :func:`image_target`)."""
        image = self.images.plan
        env = [
            ("MODE", "IMAGE"),
            ("TARGET_IMAGE", target),
            ("PROJECT_NAME", self.project_name),
            ("PROJECT_VERSION", self.project_version),
            ("HOST_OUTPUT_DIR", PLAN_OUTPUT_TARGET),
            ("UPLOAD_ENABLED", "false"),
            ("BOMLENS_IMAGE_REF", image.ref),
            ("BOMLENS_IMAGE_DIGEST", image.digest),
            # The same report settings as stage 2 of a source scan: the
            # vulnerability report is required, nothing is looked up online
            # beyond the image itself, and Trivy matches against the worker's
            # database, read-only, instead of downloading one.
            ("GENERATE_SECURITY", "true"),
            ("SECURITY_ENRICH", "false"),
            ("DEEP_LICENSE", "false"),
            ("TRIVY_CACHE_DIR", TRIVY_DB_TARGET),
            ("TRIVY_SKIP_DB_UPDATE", "true"),
            ("TRIVY_SKIP_JAVA_DB_UPDATE", "true"),
            ("TRIVY_OFFLINE_SCAN", "true"),
            ("TRIVY_NO_PROGRESS", "true"),
            (TRIVY_DB_MAX_AGE_ENV, str(trivy_db_max_age_days())),
            *self._owner_env(),
        ]
        return ContainerSpec(
            name=self._container_name("image"),
            image=image.run_ref,
            user=self._non_root_user(),
            entrypoint=None,
            args=(),
            workdir=PLAN_OUTPUT_TARGET,
            mounts=((output, PLAN_OUTPUT_TARGET, False), (scratch, SCRATCH_TARGET, False)),
            extra_mount_args=tuple(self.mounts.trivy_db_args()),
            env=tuple(env),
            # A proxy for the registry, when the worker has one: by name only.
            secret_env=tuple(self._passthrough_names(PROXY_ENV_NAMES)),
            network=self._network(True),
            read_only=True,
            drop_all_caps=True,
        )

    def run_image(self, target: str) -> Path:
        """One ``MODE=IMAGE`` run of the BomLens image. Returns the output folder.

        ``target`` is :func:`image_target`'s value. The container has exited
        when this returns or raises; its output is for
        :func:`tasks.bomlens_result.apply_bomlens_result`.
        """
        self.report.mode = "image"
        self.report.image_target = target
        self.check_docker_api()
        self.remove_leftovers()
        output = self._fresh_dir(self.layout.output)
        scratch = self._fresh_dir(self.layout.scratch("image"))
        spec = self.image_spec(target, output=output, scratch=scratch)
        self.report.stage2_image = spec.image
        self.docker.ensure_image(spec.image, PULL_TIMEOUT_SECONDS)
        outcome = self._execute(
            spec, timeout=bomlens_image_scan_timeout_seconds(), log_stage="trivy"
        )
        self.report.stage2_exit_code = outcome.exit_code
        self.report.image_timed_out = outcome.timed_out
        if outcome.timed_out:
            raise BomLensEngineError("the BomLens image scan was stopped at its time limit")
        return self._host(self.layout.output)


def _api_version(value: str | None) -> tuple[int, int] | None:
    if not value:
        return None
    parts = value.strip().split(".")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return None
    return int(parts[0]), int(parts[1])


MountKey = tuple[str, str, str, bool, str]


def _requested_from_argv(argv: list[str]) -> set[MountKey]:
    """The mounts ``argv`` asks for, as (type, source, target, read-only, sub-path)."""
    wanted: set[MountKey] = set()
    for flag, value in zip(argv, argv[1:], strict=False):
        if flag != "--mount":
            continue
        fields: dict[str, str] = {}
        for part in value.split(","):
            key, _, val = part.partition("=")
            fields[key] = val if val else "true"
        wanted.add(
            (
                fields.get("type", ""),
                fields.get("src", ""),
                fields.get("dst", ""),
                fields.get("readonly") == "true",
                fields.get("volume-subpath", ""),
            )
        )
    return wanted


def check_requested_mounts(argv: list[str], recorded: list[dict[str, Any]]) -> None:
    """Refuse a container whose recorded mounts differ from the ones asked for."""
    actual: set[MountKey] = set()
    for mount in recorded:
        options = mount.get("VolumeOptions") or {}
        subpath = options.get("Subpath", "") if isinstance(options, dict) else ""
        actual.add(
            (
                str(mount.get("Type", "")),
                str(mount.get("Source", "")),
                str(mount.get("Target", "")),
                bool(mount.get("ReadOnly", False)),
                str(subpath or ""),
            )
        )
    if actual != _requested_from_argv(argv):
        raise BomLensEngineError(
            "the Docker daemon recorded different mounts than the engine asked for; "
            "the container was not started"
        )


def write_new_file(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` as a new regular file, never through what is there.

    A container wrote the folder: whatever sits at ``path`` (a link, a FIFO
    that would block the worker, a directory) is removed first, then the file
    is created with ``O_EXCL | O_NOFOLLOW`` (security review on #522, L1).
    """
    try:
        mode: int | None = os.lstat(path).st_mode
    except FileNotFoundError:
        mode = None
    if mode is not None:
        if stat.S_ISDIR(mode):
            shutil.rmtree(path)
        else:
            path.unlink()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o644)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)


#: Root component names BomLens refuses as placeholders that collide across
#: submissions (its stamp-metadata step); a TRUSCA project may well be
#: called one of them.
_PLACEHOLDER_NAMES: Final = frozenset({"src", "app"})


def _project_token(value: str, default: str) -> str:
    token = _PROJECT_TOKEN_RE.sub("_", value or "").strip("._-")[:100]
    token = token or default
    return f"{token}-project" if token in _PLACEHOLDER_NAMES else token


__all__ = [
    "INSTANCE_LABEL",
    "LABEL_ROLE",
    "REGISTRY_SCHEME",
    "STAGE1_LOG_NAME",
    "TRIVY_DB_TARGET",
    "BindMounts",
    "BomLensEngineError",
    "BomLensRunner",
    "ContainerOutcome",
    "ContainerSpec",
    "ContainerState",
    "Docker",
    "DockerCli",
    "EngineContainer",
    "EngineContainerStillRunning",
    "EngineScanCancelled",
    "EngineImages",
    "EngineLayout",
    "MountStyle",
    "NamedVolumeMounts",
    "PinnedImage",
    "RunReport",
    "check_requested_mounts",
    "confined_path",
    "docker_run_argv",
    "image_target",
    "instance_id",
    "is_loopback_proxy",
    "mount_style",
    "pinned_image",
    "resolve_images",
    "stage1_image_prefix",
    "trivy_db_max_age_days",
    "write_new_file",
]
