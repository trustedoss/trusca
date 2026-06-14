"""Worker-local SBOM generation — the legacy, default executor.

Runs build-prep (injected) and cdxgen as subprocesses inside the Celery worker,
exactly as ``tasks/scan_source.py`` did inline before the executor abstraction.
This path is behaviour-preserving: it delegates to :func:`cdxgen.run_cdxgen`
unchanged and lets that adapter resolve the scan backend (so ``mock`` mode keeps
working transparently).
"""

from __future__ import annotations

import structlog

from integrations import cdxgen as cdxgen_adapter
from integrations._line_streamer import LineCallback
from integrations.scan_executor.base import (
    CancelCheck,
    PrepHook,
    SbomGenRequest,
    SbomGenResult,
    ScanExecutor,
    StageHook,
)

log = structlog.get_logger("integrations.scan_executor.inprocess")


class InProcessExecutor(ScanExecutor):
    """Run prep + cdxgen as worker-local subprocesses."""

    name = "inprocess"

    def generate_sbom(
        self,
        request: SbomGenRequest,
        *,
        prep: PrepHook | None = None,
        stage: StageHook | None = None,
        line_callback: LineCallback | None = None,
        cancel_check: CancelCheck | None = None,
    ) -> SbomGenResult:
        # Reproduce the exact inline order: advance to "prep", run the
        # language-specific lockfile prep, advance to "cdxgen", then cdxgen.
        if stage is not None:
            stage("prep")
        if prep is not None:
            prep()

        if stage is not None:
            stage("cdxgen")

        # Pass timeout only when explicitly set so the adapter applies its own
        # default otherwise — preserving the legacy call that passed none.
        kwargs: dict[str, object] = {
            "source_dir": request.source_dir,
            "output_dir": request.output_dir,
            "line_callback": line_callback,
            "verbose": request.verbose,
        }
        if request.timeout_seconds is not None:
            kwargs["timeout_seconds"] = request.timeout_seconds

        result = cdxgen_adapter.run_cdxgen(**kwargs)  # type: ignore[arg-type]

        return SbomGenResult(
            sbom_path=result.sbom_path,
            sbom=result.sbom,
            executor=self.name,
            image=None,
            detected_env=request.detected_env,
        )


__all__ = ["InProcessExecutor"]
