# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Release-path workflows must pin their actions to commit SHAs (ER18).

A `uses: some/action@v4` reference follows a tag the action's owner can move
at any time, so what runs is whatever that tag points at on the day. In a
workflow that publishes release artifacts and holds `packages: write`, that is
a third party able to change what ships without any change here.

Scope is deliberately the release path rather than every workflow: 135 `uses:`
references exist, and pinning is only worth its upkeep where the blast radius
is a published artifact. A test job that runs on a pull request can follow a
tag. Widening this later is a matter of adding files to WORKFLOWS.

Pinning is safe to maintain because Dependabot updates SHA-pinned actions: its
GitHub Actions updater rewrites the ref after `@` and keeps the trailing
version comment current (dependabot-core `workflow_updater.rb` /
`version_commenter.rb`). Without that, pinning would just be a slower way to
run stale actions.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]

#: Workflows that publish or deploy something. These are the ones where a
#: moved tag reaches users.
WORKFLOWS = (
    ".github/workflows/release.yml",
    ".github/workflows/deploy-hetzner.yml",
)

# `uses:` values, ignoring local composite actions (`./.github/actions/...`),
# which are this repository's own files and move only when we move them.
_USES_RE = re.compile(r"^\s*-?\s*uses:\s*(?P<ref>[^\s#]+)", re.M)
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _external_uses(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return [
        m.group("ref")
        for m in _USES_RE.finditer(text)
        if not m.group("ref").startswith("./")
    ]


def test_every_release_path_action_is_pinned_to_a_sha() -> None:
    unpinned: list[str] = []
    for workflow in WORKFLOWS:
        path = REPO_ROOT / workflow
        assert path.is_file(), f"{workflow} is missing; update WORKFLOWS"
        for ref in _external_uses(path):
            _, _, version = ref.partition("@")
            if not _SHA_RE.match(version):
                unpinned.append(f"{workflow}: {ref}")

    assert not unpinned, (
        "release-path workflows must pin actions to a full commit SHA, not a "
        "movable tag:\n  " + "\n  ".join(unpinned)
    )


def test_each_pin_carries_a_version_comment() -> None:
    """A bare SHA says nothing about what version it is.

    The comment is what a reviewer reads, and Dependabot keeps it current when
    it bumps the pin, so a pin without one loses that.
    """
    missing: list[str] = []
    for workflow in WORKFLOWS:
        for line in (REPO_ROOT / workflow).read_text(encoding="utf-8").splitlines():
            match = _USES_RE.match(line)
            if not match:
                continue
            ref = match.group("ref")
            if ref.startswith("./") or "@" not in ref:
                continue
            if _SHA_RE.match(ref.partition("@")[2]) and "#" not in line:
                missing.append(f"{workflow}: {line.strip()}")

    assert not missing, "each SHA pin needs a trailing version comment:\n  " + "\n  ".join(
        missing
    )
