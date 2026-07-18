"""Executor abstraction for the SBOM-generation stage (build-prep + cdxgen).

The source-scan pipeline (`tasks/scan_source.py`) historically ran build-prep
and cdxgen as worker-local subprocesses. This module hides that single seam
behind a :class:`ScanExecutor` so the *same* "source dir in → SBOM file out"
contract can be served by:

- :class:`~integrations.scan_executor.inprocess.InProcessExecutor` — the legacy
  worker-local behaviour (default, fully backward compatible).
- a Docker sidecar executor (on-prem, model 2 — increment 3), and
- a Kubernetes Job executor (SaaS, model 2 — increment 7).

The executor owns the *prep → cdxgen* sequence because, for the container
executors, build-prep (e.g. ``pip install``) MUST run *inside* the environment
image for transitive dependencies to be enumerated. To keep the dependency
direction one-way (``tasks`` → ``integrations``, never the reverse), the
in-process prep step is *injected* as a callable rather than imported — see the
``prep`` parameter of :meth:`ScanExecutor.generate_sbom`.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from integrations._line_streamer import LineCallback

# A no-argument hook that runs the worker-local, language-specific lockfile /
# dependency-resolution steps before cdxgen reads the tree. Injected by the
# pipeline seam so this package never imports ``tasks.scan_source``. Container
# executors ignore it (they run an equivalent build-prep inside the image).
PrepHook = Callable[[], None]

# Advance the scan to a named stage ("prep", "cdxgen", ...) using the pipeline's
# percent mapping. Injected so the executor can reproduce the exact stage
# transitions the inline code used to emit.
StageHook = Callable[[str], None]

# Returns True when the surrounding scan has been cancelled. Container executors
# poll this to tear down their sidecar/Job promptly (Celery's SIGTERM revoke does
# not propagate to a non-child container). The in-process executor ignores it —
# SIGTERM already reaches its subprocesses.
CancelCheck = Callable[[], bool]


@dataclass(frozen=True)
class SbomGenRequest:
    """Inputs for one SBOM-generation run.

    ``source_dir`` and ``output_dir`` are worker-in-container paths. For a
    container executor they are reinterpreted against the shared workspace
    volume (see increment 3); the contract is unchanged: cdxgen reads
    ``source_dir`` and the resulting SBOM lands under ``output_dir``.
    """

    scan_uuid: uuid.UUID
    source_dir: Path
    output_dir: Path
    detected_env: str = "inprocess"
    spec_version: str = "1.5"
    fetch_license: bool = False
    verbose: bool = False
    # None → let the cdxgen adapter apply its own default timeout (preserves the
    # legacy call which passed no explicit timeout).
    timeout_seconds: int | None = None
    # K-f2: the RESOLVED project root — the directory that ``detected_env`` was
    # detected from. For a git scan the clone lands a level below ``source_dir``
    # (``source/`` vs ``source/repo/``), and language detection / android
    # compileSdk reads are NON-recursive, so they must run on this resolved
    # root, not the outer ``source_dir``. The in-process executor runs cdxgen
    # with ``-r`` from ``source_dir`` (recursion covers the inner repo, so it is
    # unaffected); a container sidecar targets a single directory, so it uses
    # this. None → falls back to ``source_dir`` (the non-git / zip-upload case
    # where the two already coincide). Use :attr:`effective_root`.
    project_root: Path | None = None

    @property
    def effective_root(self) -> Path:
        """The directory a single-target executor should scan — the resolved
        ``project_root`` when set, else ``source_dir``."""
        return self.project_root if self.project_root is not None else self.source_dir


@dataclass(frozen=True)
class SbomGenResult:
    """Output of one SBOM-generation run.

    ``sbom_path`` / ``sbom`` are what the rest of the pipeline (sign / scancode /
    trivy / persist) consume — identical to the legacy ``CdxgenResult``. The
    remaining fields are provenance recorded into ``scan_metadata`` (JSONB, no
    migration).
    """

    sbom_path: Path
    sbom: dict[str, Any]
    executor: str
    image: str | None
    detected_env: str


class ScanExecutor(ABC):
    """Produces a CycloneDX SBOM for a source tree.

    Implementations MUST run ``prep`` (if given) before generating the SBOM and
    MUST advance ``stage`` exactly as the inline pipeline did ("prep" then
    "cdxgen") so the progress/percent contract on the scan row is preserved.
    """

    #: Stable identifier recorded into ``SbomGenResult.executor``.
    name: str = "base"

    @abstractmethod
    def generate_sbom(
        self,
        request: SbomGenRequest,
        *,
        prep: PrepHook | None = None,
        stage: StageHook | None = None,
        line_callback: LineCallback | None = None,
        cancel_check: CancelCheck | None = None,
    ) -> SbomGenResult:
        """Run build-prep + cdxgen for ``request`` and return the SBOM."""
        raise NotImplementedError


__all__ = [
    "CancelCheck",
    "PrepHook",
    "SbomGenRequest",
    "SbomGenResult",
    "ScanExecutor",
    "StageHook",
]
