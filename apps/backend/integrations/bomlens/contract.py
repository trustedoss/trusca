# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Read a BomLens run's result manifest (``bomlens-result.json``, contract 1.x).

BomLens writes the manifest to its output folder on every run, also when the
run stops early. It says how the run ended, what each step did, and which file
in the folder plays which role, so this module never parses the engine's log
or guesses a file name. The format is BomLens' engine contract
(``docs/reference/engine-contract.md`` and ``schemas/engine-result-1.json`` in
the BomLens repository).

What this module refuses, and why:

* A contract major version other than 1. A major change means a field, step id
  or status changed meaning, or a failure may now read as success, so reading
  it with this code could persist a wrong result. The error says which version
  arrived, so an operator knows to update TRUSCA or pin the older image.
* A run or step status outside the 1.x vocabulary. The contract only adds
  those in a major version, so an unknown one is a malformed manifest.
* An output path that is not a plain file name inside the output folder, or a
  file whose size or SHA-256 differs from what the manifest recorded. The
  manifest is the run's own account of what it wrote; a file that does not
  match it is not that run's output.

What it tolerates, as the contract asks of a 1.x reader: step ids, reasons and
output roles it does not know. Minor versions add those, and a reader ignores
values it does not know.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Final

#: File name the engine writes into its output folder.
MANIFEST_NAME: Final = "bomlens-result.json"

#: ``contract.name`` every manifest carries.
CONTRACT_NAME: Final = "bomlens-engine"

#: The contract major version this reader implements.
SUPPORTED_MAJOR: Final = 1

#: ``run.status`` values of contract 1.x.
RUN_STATUSES: Final = ("succeeded", "succeeded_degraded", "failed", "cancelled")

#: ``steps[].status`` values of contract 1.x.
STEP_STATUSES: Final = ("ok", "degraded", "failed", "skipped", "disabled")

#: ``steps[].reason`` values of contract 1.x (minor versions may add more).
#: ``no_components`` (BomLens #194) is recorded on ``generate`` when the source
#: declares dependencies but the SBOM has no component.
STEP_REASONS: Final = (
    "failed",
    "timeout",
    "not_installed",
    "too_large",
    "fallback",
    "unavailable",
    "budget_exhausted",
    "no_input",
    "no_components",
)

#: ``outputs[].role`` values this reader uses.
ROLE_SBOM_CYCLONEDX: Final = "sbom.cyclonedx"
ROLE_VULNERABILITIES_TRIVY: Final = "vulnerabilities.trivy"
ROLE_LICENSE_SCAN_SCANCODE: Final = "license-scan.scancode"

#: A manifest lists one entry per step and per output file; a few hundred KiB
#: at most. Anything far larger is not a manifest this engine wrote.
MAX_MANIFEST_BYTES: Final = 4 * 1024 * 1024

#: Same ceiling the Trivy adapter puts on a report it loads eagerly
#: (``integrations.trivy._MAX_REPORT_SIZE_BYTES``): a realistic SBOM or Trivy
#: report is tens of MiB, and ``json.load`` materialises the whole document.
MAX_OUTPUT_BYTES: Final = 256 * 1024 * 1024


#: Longest manifest-derived value echoed into an error message.
CLIP_CHARS: Final = 64

#: Longest message a BomLens error carries. It lands in ``scans.error_message``
#: and in logs; a manifest is engine output, and a value the engine copied from
#: the scanned input must not be able to fill either.
MESSAGE_MAX_CHARS: Final = 300


def clip(value: object) -> str:
    """``repr`` of a manifest-derived value, cut to :data:`CLIP_CHARS`."""
    text = repr(value) if not isinstance(value, str) else value
    return text if len(text) <= CLIP_CHARS else text[: CLIP_CHARS - 3] + "..."


class BomLensError(Exception):
    """Base for every reason a BomLens result cannot become a succeeded scan.

    The message is shown to users as the scan's error, so it names roles,
    steps and versions and never a workspace path. Values that came from the
    manifest are clipped where they are formatted (:func:`clip`), and the whole
    message is capped here as well, so no call site can forget.
    """

    def __init__(self, message: str = "") -> None:
        if len(message) > MESSAGE_MAX_CHARS:
            message = message[: MESSAGE_MAX_CHARS - 3] + "..."
        super().__init__(message)


class BomLensContractError(BomLensError, ValueError):
    """The output folder does not hold a result this reader can trust."""


class BomLensRunFailed(BomLensError):
    """The engine reported the run itself as failed or cancelled."""

    def __init__(self, status: str, step: str | None) -> None:
        self.status = status
        self.step = step
        where = f" in step {clip(step)!r}" if step else ""
        super().__init__(f"the BomLens run {clip(status)}{where}")


class BomLensVulnerabilityScanFailed(BomLensError):
    """The run produced an SBOM but no vulnerability result TRUSCA can trust."""


class ManifestMissing(BomLensContractError):
    """No manifest: the engine was stopped before it could write one."""


class OutputTooLarge(BomLensContractError):
    """A file is larger than the limit its reader allows.

    A contract error for the outputs a scan cannot do without. A caller with
    a degradable output (the licence scan) catches it and degrades instead.
    """


class UnsupportedContractVersion(BomLensContractError):
    """The manifest's contract major version is not one this reader implements."""

    def __init__(self, version: str) -> None:
        self.version = version
        super().__init__(
            f"BomLens result manifest uses contract version {clip(version)!r}; this "
            f"TRUSCA release reads contract {SUPPORTED_MAJOR}.x only"
        )


@dataclass(frozen=True)
class Step:
    """One ``steps[]`` entry."""

    id: str
    status: str
    reason: str | None
    stage: int
    started_at: datetime
    ended_at: datetime
    detail: str | None = None
    counts: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class Output:
    """One ``outputs[]`` entry: a file the run wrote, by role."""

    role: str
    path: str
    sha256: str
    bytes: int


@dataclass(frozen=True)
class EngineResult:
    """The parsed manifest."""

    contract_version: str
    engine_version: str | None
    image: str | None
    image_digest: str | None
    input_kind: str
    input_mode: str
    run_status: str
    run_reason: str | None
    exit_code: int
    started_at: datetime
    ended_at: datetime
    steps: tuple[Step, ...]
    outputs: tuple[Output, ...]
    tools: dict[str, str]
    engines: tuple[str, ...]

    def step(self, step_id: str) -> Step | None:
        """The step with ``step_id``, or ``None`` when the run did not record it."""
        return next((s for s in self.steps if s.id == step_id), None)

    def output(self, role: str) -> Output | None:
        """The first output with ``role``, or ``None`` when the run wrote none."""
        return next((o for o in self.outputs if o.role == role), None)


def contract_major(version: str) -> int | None:
    """The major number of a ``MAJOR.MINOR.PATCH[-pre]`` version, or ``None``."""
    head = version.split("-", 1)[0].split("+", 1)[0]
    parts = head.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return None
    return int(parts[0])


def _require(mapping: Any, key: str, where: str) -> Any:
    if not isinstance(mapping, dict) or key not in mapping:
        raise BomLensContractError(f"BomLens result manifest is missing {where}.{key}")
    return mapping[key]


def _str(value: Any, where: str) -> str:
    if not isinstance(value, str):
        raise BomLensContractError(f"BomLens result manifest field {where} is not a string")
    return value


def _opt_str(value: Any, where: str) -> str | None:
    return None if value is None else _str(value, where)


def _when(value: Any, where: str) -> datetime:
    text = _str(value, where)
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BomLensContractError(
            f"BomLens result manifest field {where} is not a timestamp"
        ) from exc
    if moment.tzinfo is None:
        raise BomLensContractError(f"BomLens result manifest field {where} has no time zone")
    return moment


def _parse_step(raw: Any, index: int) -> Step:
    where = f"steps[{index}]"
    step_id = _str(_require(raw, "id", where), f"{where}.id")
    status = _str(_require(raw, "status", where), f"{where}.status")
    if status not in STEP_STATUSES:
        raise BomLensContractError(
            f"BomLens result manifest step {clip(step_id)!r} has unknown status "
            f"{clip(status)!r}"
        )
    reason = _opt_str(_require(raw, "reason", where), f"{where}.reason")
    stage = _require(raw, "stage", where)
    if stage not in (1, 2) or isinstance(stage, bool):
        raise BomLensContractError(f"BomLens result manifest field {where}.stage is not 1 or 2")
    detail = raw.get("detail")
    counts_raw = raw.get("counts") or {}
    counts = (
        {
            str(k): int(v)
            for k, v in counts_raw.items()
            if isinstance(v, int) and not isinstance(v, bool)
        }
        if isinstance(counts_raw, dict)
        else {}
    )
    return Step(
        id=step_id,
        status=status,
        reason=reason,
        stage=int(stage),
        started_at=_when(_require(raw, "started_at", where), f"{where}.started_at"),
        ended_at=_when(_require(raw, "ended_at", where), f"{where}.ended_at"),
        detail=detail if isinstance(detail, str) else None,
        counts=counts,
    )


def _parse_output(raw: Any, index: int) -> Output:
    where = f"outputs[{index}]"
    role = _str(_require(raw, "role", where), f"{where}.role")
    path = _str(_require(raw, "path", where), f"{where}.path")
    sha256 = _str(_require(raw, "sha256", where), f"{where}.sha256")
    size = _require(raw, "bytes", where)
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise BomLensContractError(f"BomLens result manifest field {where}.bytes is not a size")
    return Output(role=role, path=path, sha256=sha256.lower(), bytes=size)


def parse_manifest(document: Any) -> EngineResult:
    """Validate a decoded manifest and return it as an :class:`EngineResult`.

    The contract version is checked first, before any other field, so a
    manifest of a later major version is refused for its version even when its
    shape has also changed.
    """
    contract = _require(document, "contract", "manifest")
    name = _require(contract, "name", "contract")
    if name != CONTRACT_NAME:
        raise BomLensContractError(
            f"not a BomLens engine result manifest (contract.name is {clip(name)!r})"
        )
    version = _str(_require(contract, "version", "contract"), "contract.version")
    if contract_major(version) != SUPPORTED_MAJOR:
        raise UnsupportedContractVersion(version)

    engine = _require(document, "engine", "manifest")
    if not isinstance(engine, dict):
        raise BomLensContractError("BomLens result manifest field engine is not an object")
    run_input = _require(document, "input", "manifest")
    run = _require(document, "run", "manifest")
    run_status = _str(_require(run, "status", "run"), "run.status")
    if run_status not in RUN_STATUSES:
        raise BomLensContractError(
            f"BomLens result manifest has unknown run status {clip(run_status)!r}"
        )
    exit_code = _require(run, "exit_code", "run")
    if not isinstance(exit_code, int) or isinstance(exit_code, bool):
        raise BomLensContractError("BomLens result manifest field run.exit_code is not an integer")
    if run_status in ("succeeded", "succeeded_degraded") and exit_code != 0:
        raise BomLensContractError(
            f"BomLens result manifest says {run_status} with exit code {clip(exit_code)}"
        )

    raw_steps = _require(document, "steps", "manifest")
    raw_outputs = _require(document, "outputs", "manifest")
    if not isinstance(raw_steps, list) or not isinstance(raw_outputs, list):
        raise BomLensContractError("BomLens result manifest steps/outputs are not lists")

    tools_raw = document.get("tools") or {}
    engines_raw = document.get("engines") or []
    return EngineResult(
        contract_version=version,
        engine_version=_opt_str(engine.get("version"), "engine.version"),
        image=_opt_str(engine.get("image"), "engine.image"),
        image_digest=_opt_str(engine.get("image_digest"), "engine.image_digest"),
        input_kind=_str(_require(run_input, "kind", "input"), "input.kind"),
        input_mode=_str(_require(run_input, "mode", "input"), "input.mode"),
        run_status=run_status,
        run_reason=_opt_str(run.get("reason"), "run.reason"),
        exit_code=exit_code,
        started_at=_when(_require(run, "started_at", "run"), "run.started_at"),
        ended_at=_when(_require(run, "ended_at", "run"), "run.ended_at"),
        steps=tuple(_parse_step(s, i) for i, s in enumerate(raw_steps)),
        outputs=tuple(_parse_output(o, i) for i, o in enumerate(raw_outputs)),
        tools=(
            {str(k): str(v) for k, v in tools_raw.items() if isinstance(v, str)}
            if isinstance(tools_raw, dict)
            else {}
        ),
        engines=tuple(str(e) for e in engines_raw if isinstance(e, str))
        if isinstance(engines_raw, list)
        else (),
    )


def _decode_json(data: bytes, what: str) -> Any:
    """Parse ``data``; every way it can fail is a :class:`BomLensContractError`.

    ``RecursionError`` included: a deeply nested document exhausts the parser's
    stack, and that must end the scan as a refused result, not as an unexpected
    error with a traceback.
    """
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BomLensContractError(f"{what} is not valid JSON") from exc
    except RecursionError as exc:
        raise BomLensContractError(f"{what} is nested too deeply to read") from exc
    except ValueError as exc:
        # A number longer than Python's integer digit limit.
        raise BomLensContractError(f"{what} holds a value too large to read") from exc


def _contained(output_dir: Path, name: str, what: str) -> Path:
    """``output_dir / name`` after symlinks, refused unless it stays in the folder."""
    root = output_dir.resolve()
    candidate = (output_dir / name).resolve()
    if candidate.parent != root:
        raise BomLensContractError(f"{what} is not in the output folder")
    return candidate


def _read_capped(path: Path, limit: int, what: str) -> bytes:
    """The file's bytes, read once, refused when larger than ``limit``."""
    with path.open("rb") as fh:
        data = fh.read(limit + 1)
    if len(data) > limit:
        raise OutputTooLarge(f"{what} is larger than this reader allows")
    return data


def load_manifest(output_dir: Path) -> EngineResult:
    """Read and validate ``bomlens-result.json`` from ``output_dir``.

    The manifest must be a regular file inside ``output_dir`` after symlinks,
    the same containment every output gets (:func:`read_output`).
    """
    what = "BomLens result manifest"
    if not (output_dir / MANIFEST_NAME).exists():
        raise ManifestMissing(
            "BomLens wrote no result manifest; the engine was stopped before it finished"
        )
    path = _contained(output_dir, MANIFEST_NAME, what)
    if not path.is_file():
        raise BomLensContractError(f"{what} is not a regular file")
    return parse_manifest(_decode_json(_read_capped(path, MAX_MANIFEST_BYTES, what), what))


@dataclass(frozen=True)
class VerifiedOutput:
    """An output's bytes as read once, and the document parsed from those bytes.

    ``data`` is what was hashed and what was parsed, so a file rewritten after
    the read cannot be persisted in place of what was checked: callers store
    ``data``, never the path again.
    """

    role: str
    name: str
    sha256: str
    data: bytes
    document: dict[str, Any]


def read_output(output_dir: Path, output: Output, *, limit: int | None = None) -> VerifiedOutput:
    """Read ``output`` once and check it against what the manifest recorded.

    The path must be a bare file name (the contract's ``^[^/]+$``, plus no
    backslash, NUL or dot-only name) and resolve inside ``output_dir`` after
    symlinks. The bytes are read once; that one buffer is size-checked,
    hashed against the recorded SHA-256 and parsed.

    The caller must run this only after the engine container has exited. While
    it runs, it still owns the folder and could write a file after it was read;
    reading once makes what TRUSCA checked and what it keeps the same bytes,
    but cannot make a still-running writer's later output part of the check.

    ``limit`` lowers the read ceiling below :data:`MAX_OUTPUT_BYTES` (never
    above it). The file is never read past the ceiling, whatever size the
    manifest declared; a larger file raises :class:`OutputTooLarge`.
    """
    role = clip(output.role)
    what = f"BomLens output for {role}"
    name = output.path
    if not name or name in (".", "..") or "/" in name or "\\" in name or "\x00" in name:
        raise BomLensContractError(f"BomLens output path for {role} is not a file name")
    path = _contained(output_dir, name, what)
    if not path.is_file():
        raise BomLensContractError(f"{what} is not in the output folder")
    ceiling = MAX_OUTPUT_BYTES if limit is None else min(limit, MAX_OUTPUT_BYTES)
    data = _read_capped(path, ceiling, what)
    if len(data) != output.bytes:
        raise BomLensContractError(
            f"{what} is {len(data)} bytes; the manifest recorded {clip(output.bytes)}"
        )
    digest = hashlib.sha256(data).hexdigest()
    if digest != output.sha256:
        raise BomLensContractError(f"{what} does not match the SHA-256 the manifest recorded")
    document = _decode_json(data, what)
    if not isinstance(document, dict):
        raise BomLensContractError(f"{what} is not a JSON object")
    return VerifiedOutput(role=output.role, name=name, sha256=digest, data=data, document=document)


__all__ = [
    "CONTRACT_NAME",
    "MANIFEST_NAME",
    "MAX_MANIFEST_BYTES",
    "MAX_OUTPUT_BYTES",
    "ROLE_LICENSE_SCAN_SCANCODE",
    "ROLE_SBOM_CYCLONEDX",
    "ROLE_VULNERABILITIES_TRIVY",
    "RUN_STATUSES",
    "STEP_REASONS",
    "STEP_STATUSES",
    "SUPPORTED_MAJOR",
    "BomLensContractError",
    "BomLensError",
    "BomLensRunFailed",
    "BomLensVulnerabilityScanFailed",
    "EngineResult",
    "ManifestMissing",
    "OutputTooLarge",
    "Output",
    "Step",
    "UnsupportedContractVersion",
    "contract_major",
    "load_manifest",
    "parse_manifest",
    "VerifiedOutput",
    "clip",
    "read_output",
]
