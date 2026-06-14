"""Pluggable executors for the SBOM-generation stage (build-prep + cdxgen).

See :mod:`integrations.scan_executor.base` for the contract. The default
``inprocess`` executor preserves the legacy worker-local behaviour; container
executors (Docker sidecar, Kubernetes Job) are introduced in later increments.
"""

from __future__ import annotations

from integrations.scan_executor.base import (
    CancelCheck,
    PrepHook,
    SbomGenRequest,
    SbomGenResult,
    ScanExecutor,
    StageHook,
)
from integrations.scan_executor.factory import get_executor
from integrations.scan_executor.source_detect import (
    DETECTABLE_ENVS,
    android_compile_sdk,
    detect_language,
    image_for_env,
)

__all__ = [
    "DETECTABLE_ENVS",
    "CancelCheck",
    "PrepHook",
    "SbomGenRequest",
    "SbomGenResult",
    "ScanExecutor",
    "StageHook",
    "android_compile_sdk",
    "detect_language",
    "get_executor",
    "image_for_env",
]
