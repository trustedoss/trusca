#!/usr/bin/env python3
"""Dump the FastAPI OpenAPI schema to a static JSON file for the docs site.

v2.1 Track B (B4) — API reference hosting. The docs site (Docusaurus +
redocusaurus) renders the API reference from a *committed* static spec at
``docs-site/static/openapi.json`` so the docs build never needs a running
backend. This script regenerates that spec from the live FastAPI ``app``.

Design constraints (single source of truth = the backend's ``app.openapi()``):

* **Read-only on the app.** We import ``main.app`` and call ``app.openapi()``.
  We never touch backend product code — same philosophy as the OpenAPI drift
  gate ``apps/backend/tests/unit/test_openapi_contract.py`` (``from main import
  app``). Keeping this script import-only avoids conflicts with B5 (which edits
  backend code).

* **Dummy runtime env is fine.** Importing ``main`` does not connect to the
  database or Redis (those happen in the FastAPI lifespan, not at import).
  ``app.openapi()`` is pure schema generation. We seed harmless placeholder
  values for the env vars that ``core.config`` reads so the import succeeds in a
  bare CI/host shell without real services.

* **Deterministic output.** ``json.dumps(..., sort_keys=True, indent=2)`` plus a
  trailing newline. This is what makes the freshness gate (regenerate + diff)
  meaningful: an unchanged contract produces a byte-identical file.

Usage::

    python scripts/dump_openapi.py            # write docs-site/static/openapi.json
    python scripts/dump_openapi.py --check     # exit 1 if the committed file is stale
    python scripts/dump_openapi.py -o some.json

The ``--check`` mode is what CI runs (after installing backend deps) to fail a
PR whose committed ``openapi.json`` no longer matches the code.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Repo layout: this file is at <repo>/scripts/dump_openapi.py.
REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = REPO_ROOT / "apps" / "backend"
DEFAULT_OUTPUT = REPO_ROOT / "docs-site" / "static" / "openapi.json"

# Placeholder runtime env so ``import main`` succeeds without real services.
# These are NOT used by ``app.openapi()`` — they only satisfy module-level /
# import-time reads in ``core.config``. The lifespan (which would actually
# connect to Postgres/Redis and validate SECRET_KEY) does not run on import.
_DUMMY_ENV = {
    "SECRET_KEY": "openapi-dump-placeholder-secret-key-min-32-chars",
    "DATABASE_URL": "postgresql+asyncpg://placeholder:placeholder@localhost:5432/placeholder",
    "REDIS_URL": "redis://localhost:6379/0",
    # APP_ENV stays at its default (dev) so CORS validation accepts the
    # permissive defaults without requiring a configured allow-list.
}


def _build_spec() -> dict:
    """Import the FastAPI app (read-only) and return its OpenAPI schema."""
    for key, value in _DUMMY_ENV.items():
        os.environ.setdefault(key, value)

    if str(BACKEND_ROOT) not in sys.path:
        sys.path.insert(0, str(BACKEND_ROOT))

    # Imported here (not at module top) so the dummy env + sys.path are in
    # place first, and so ``--help`` works without backend deps installed.
    from main import app  # noqa: PLC0415  (deliberate late import)

    return app.openapi()


def _serialize(spec: dict) -> str:
    """Deterministic JSON text — stable across unrelated edits."""
    return json.dumps(spec, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output path (default: {DEFAULT_OUTPUT.relative_to(REPO_ROOT)}).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Do not write. Exit 1 if the committed file is missing or stale "
            "(freshness gate). Prints how to regenerate."
        ),
    )
    args = parser.parse_args(argv)

    text = _serialize(_build_spec())

    if args.check:
        if not args.output.exists():
            print(
                f"OpenAPI spec missing: {args.output}\n"
                f"Generate it with: python scripts/dump_openapi.py",
                file=sys.stderr,
            )
            return 1
        committed = args.output.read_text(encoding="utf-8")
        if committed != text:
            print(
                "OpenAPI spec drift — the committed docs-site/static/openapi.json "
                "does not match the backend's app.openapi(). Regenerate and commit:\n"
                "    python scripts/dump_openapi.py",
                file=sys.stderr,
            )
            return 1
        print(f"OpenAPI spec is up to date: {args.output}")
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
    print(f"Wrote {args.output} ({len(text)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
