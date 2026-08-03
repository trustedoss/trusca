# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Malicious-package flagging (#26) — see :mod:`.malicious_catalog`."""

from services.malicious.malicious_catalog import (
    MALICIOUS_STATES,
    MaliciousVerdict,
    build_evaluator,
    load_index,
    stamp_component_version,
)

__all__ = [
    "MALICIOUS_STATES",
    "MaliciousVerdict",
    "build_evaluator",
    "load_index",
    "stamp_component_version",
]
