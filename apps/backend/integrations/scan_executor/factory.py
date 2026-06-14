"""Select the :class:`ScanExecutor` for the current ``SCAN_EXECUTOR`` mode.

Resolved at call time (CLAUDE.md core rule #11). Unknown / unimplemented modes
fall back to the in-process executor with a WARNING so a misconfigured operator
degrades to the safe legacy path rather than failing scans.
"""

from __future__ import annotations

import structlog

from core.config import scan_executor_mode
from integrations.scan_executor.base import ScanExecutor
from integrations.scan_executor.inprocess import InProcessExecutor
from integrations.scan_executor.local_docker import LocalDockerExecutor

log = structlog.get_logger("integrations.scan_executor.factory")


def get_executor(mode: str | None = None) -> ScanExecutor:
    """Return the executor for ``mode`` (defaults to ``scan_executor_mode()``)."""
    resolved = (mode or scan_executor_mode()).lower()

    if resolved == "inprocess":
        return InProcessExecutor()
    if resolved == "local_docker":
        return LocalDockerExecutor()

    # k8s_job is introduced in a later increment. Until then, any other value
    # degrades to the legacy in-process path rather than erroring.
    log.warning(
        "scan_executor_unavailable_fallback_inprocess",
        requested=resolved,
    )
    return InProcessExecutor()


__all__ = ["get_executor"]
