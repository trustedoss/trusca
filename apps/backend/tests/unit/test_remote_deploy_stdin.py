# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""deploy/hetzner/remote-deploy.sh runs as ``bash -s < remote-deploy.sh`` over SSH,
so its own stdin is the rest of the script. Any command in it that reads stdin
consumes the tail silently: the deploy stops with no error and the workflow still
reports success. Every command that can read stdin must be redirected."""

from __future__ import annotations

import re
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[4] / "deploy" / "hetzner" / "remote-deploy.sh"


def _logical_lines() -> list[str]:
    lines: list[str] = []
    for raw in SCRIPT.read_text().splitlines():
        text = raw.strip()
        if text and not text.startswith("#"):
            lines.append(text)
    return lines


def _command(needle: str) -> str:
    matches = [line for line in _logical_lines() if re.search(needle, line)]
    assert len(matches) == 1, f"expected exactly one line matching {needle!r}, got {matches}"
    return matches[0]


def test_upgrade_script_does_not_read_the_ssh_stream() -> None:
    assert _command(r"bash scripts/upgrade\.sh").endswith("< /dev/null")


def test_reseed_does_not_read_the_ssh_stream() -> None:
    assert _command(r"scripts\.reset_demo").endswith("< /dev/null")
