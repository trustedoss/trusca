#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Print the single head revision of an Alembic versions directory.

Used by upgrade-uat.yml to state, independently of the running container, what
revision the database should be at once the upgrade finishes. Reading it out of
the files rather than asking the container is the point: if the container is
wrong about its own head, this still tells the truth.

The head is the revision no other migration declares as its ``down_revision``.
Exits non-zero when the directory holds no head or more than one, which is
itself worth failing on: a branched chain is how two PRs that each added a
migration collide, and it is exactly what a release must not ship.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Matches `revision = "0075_task_runs"` / `down_revision = None`, single or
# double quoted. Deliberately textual: importing the modules would need the
# app's dependencies, and this runs on a bare runner.
_REVISION_RE = re.compile(r"^revision(?::\s*[^=]+)?\s*=\s*['\"]([^'\"]+)['\"]", re.M)
_DOWN_RE = re.compile(r"^down_revision(?::\s*[^=]+)?\s*=\s*['\"]([^'\"]+)['\"]", re.M)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: alembic_head.py <versions-dir>", file=sys.stderr)
        return 2

    versions = Path(argv[1])
    if not versions.is_dir():
        print(f"not a directory: {versions}", file=sys.stderr)
        return 2

    revisions: set[str] = set()
    parents: set[str] = set()
    for path in sorted(versions.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        match = _REVISION_RE.search(text)
        if not match:
            continue
        revisions.add(match.group(1))
        down = _DOWN_RE.search(text)
        if down:
            parents.add(down.group(1))

    if not revisions:
        print(f"no alembic revisions found in {versions}", file=sys.stderr)
        return 1

    heads = sorted(revisions - parents)
    if len(heads) != 1:
        print(
            f"expected exactly one head, found {len(heads)}: {heads}",
            file=sys.stderr,
        )
        return 1

    print(heads[0])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
