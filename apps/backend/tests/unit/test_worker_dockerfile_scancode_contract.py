"""The worker image must not ship a scancode that cannot run.

An image built with click 8.5.0 made every scancode call exit 2. The pipeline
treats that as "skipped" and the scan still succeeds, so nothing failed until a
golden fixture showed that no scan detected any license (issue 487). The build
step ``scancode --version`` passes in that state because it reads no options.

This test pins the two things that keep it from recurring: click is pinned in
the same ``pip install`` as scancode, and the same RUN scans one file and
requires the detection, so a broken scancode fails the image build.
"""

from __future__ import annotations

import re
from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[2] / "Dockerfile.worker"


def _scancode_run_block() -> str:
    """The whole ``RUN`` instruction that creates the scancode virtualenv."""
    lines = DOCKERFILE.read_text().splitlines()
    anchor = next(i for i, line in enumerate(lines) if 'python3 -m venv "${SCANCODE_VENV}"' in line)
    start = anchor
    while not lines[start].startswith("RUN "):
        start -= 1
    end = start
    while lines[end].rstrip().endswith("\\"):
        end += 1
    return "\n".join(lines[start : end + 1])


def test_click_is_pinned_next_to_scancode() -> None:
    text = DOCKERFILE.read_text()
    assert re.search(r"^\s+SCANCODE_CLICK_VERSION=\d+\.\d+\.\d+ \\$", text, re.MULTILINE), (
        "SCANCODE_CLICK_VERSION must be an exact version"
    )
    block = _scancode_run_block()
    assert '"scancode-toolkit==${SCANCODE_VERSION}"' in block
    assert '"click==${SCANCODE_CLICK_VERSION}"' in block


def test_the_image_build_scans_a_file_and_requires_the_detection() -> None:
    block = _scancode_run_block()
    version_at = block.index("scancode --version")
    smoke_at = block.index("scancode --license", version_at)
    check_at = block.index("grep -q '\"license_expression\": \"mit\"'", smoke_at)
    assert version_at < smoke_at < check_at
    # `set -e` at the top of the block is what turns a failing step into a
    # failed build; a smoke run in a later RUN would not be tied to the install.
    assert "set -eux" in block.splitlines()[0]
