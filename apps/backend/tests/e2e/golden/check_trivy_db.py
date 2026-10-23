#!/usr/bin/env python3
"""Fail a golden shard whose worker did not scan with the pinned Trivy database.

The workflow seeds the database recorded in ``trivy-db.lock.json`` and starts
the worker with its own download turned off. If either link breaks (the flag
is lost, the seed step is skipped, a scan updates the database), the worker
reads a database whose ``UpdatedAt`` differs from the lock, and the run has
compared baselines against advisory data nobody pinned.

Usage (stdin is the worker's ``/var/lib/trivy/db/metadata.json``):
  check_trivy_db.py <expected UpdatedAt>

stdlib only, like run_golden.
"""
from __future__ import annotations

import json
import sys


def mismatch(metadata_text: str, expected_updated_at: str) -> str | None:
    """None if the worker's database is the pinned one, else the reason."""
    if not expected_updated_at:
        return "no expected UpdatedAt (lock has no db_updated_at)"
    try:
        metadata = json.loads(metadata_text)
    except ValueError:
        return f"worker metadata.json is not JSON: {metadata_text[:200]!r}"
    got = metadata.get("UpdatedAt") if isinstance(metadata, dict) else None
    if got != expected_updated_at:
        return (
            f"worker scanned with Trivy database UpdatedAt={got!r}, "
            f"lock pins {expected_updated_at!r}"
        )
    return None


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    reason = mismatch(sys.stdin.read(), argv[1])
    if reason:
        print(f"::error::{reason}")
        return 1
    print(f"worker scanned with the pinned Trivy database (UpdatedAt {argv[1]})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
