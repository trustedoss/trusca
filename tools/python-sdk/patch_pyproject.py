#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Patch the openapi-generator boilerplate out of the generated pyproject.toml.

Targeted line replacements rather than a TOML library round-trip: the
generator's output has a small, predictable set of placeholder lines (author,
description, repository URL, no license), and a round-trip through a TOML
writer would reformat the whole file for a no-content diff on every
regeneration, which is exactly what generate.sh's docstring says this
tooling avoids for the SDK's ~900 other files.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPLACEMENTS = [
    (
        'description = "TRUSCA API"',
        'description = "Official Python client for the TRUSCA REST API, '
        'generated from the published OpenAPI spec."',
    ),
    (
        'authors = [\n  {name = "OpenAPI Generator Community",email = "team@openapitools.org"},\n]',
        'authors = [\n  {name = "TRUSCA contributors"},\n]',
    ),
    (
        'readme = "README.md"',
        'readme = "README.md"\nlicense = "Apache-2.0"',
    ),
    (
        'Repository = "https://github.com/GIT_USER_ID/GIT_REPO_ID"',
        'Repository = "https://github.com/trustedoss/trusca"\n'
        'Documentation = "https://trustedoss.github.io/trusca/docs/reference/api-overview"',
    ),
]


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: patch_pyproject.py <path/to/pyproject.toml>", file=sys.stderr)
        return 1
    path = Path(sys.argv[1])
    text = path.read_text(encoding="utf-8")
    for old, new in REPLACEMENTS:
        if old not in text:
            print(
                f"patch_pyproject: expected text not found (generator output "
                f"shape changed?): {old!r}",
                file=sys.stderr,
            )
            return 1
        text = text.replace(old, new, 1)
    path.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
