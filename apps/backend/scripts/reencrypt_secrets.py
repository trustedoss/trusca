# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Move stored ciphertext onto the newest encryption key.

Rotating ``GITHUB_APP_ENCRYPTION_KEY`` is three steps and only the middle one
is this command:

1. Put the new key first in the comma-separated list, keeping the old one.
   Restart. Every new write now uses the new key and both keys still read.
2. Run this until it reports nothing remaining.
3. Remove the old key from the list. Restart.

Doing step 3 early is the failure this exists to prevent. Rows still holding
ciphertext from the old key become unreadable, permanently, and nothing looks
wrong until something reads one. So run with ``MODE=count`` and confirm zero
before removing anything.

    MODE=count  python scripts/reencrypt_secrets.py
    MODE=rewrite python scripts/reencrypt_secrets.py

Both are safe to repeat, and ``rewrite`` is safe to interrupt: it skips rows
already on the newest key, so a second run picks up where the first stopped.

Keep the old key as long as you keep backups taken before the rotation.
Restoring one brings back ciphertext written under it, and a key that is no
longer in the list cannot open it.
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

if TYPE_CHECKING:
    from services.key_rotation_service import RotationReport


def _database_url() -> str:
    return (os.getenv("DATABASE_URL_OWNER") or os.getenv("DATABASE_URL") or "").strip()


def _print_report(report: RotationReport, *, rewrote: bool) -> None:
    for progress in report.columns:
        line = (
            f"  {progress.column.label}: scanned {progress.scanned}, "
            f"on an older key {progress.stale}"
        )
        if rewrote:
            line += f", rewritten {progress.rewritten}"
            if progress.raced:
                # Not an error. Somebody wrote the row between the read and
                # the update, and an application write already uses the newest
                # key, so the row is where it needs to be.
                line += f", changed under us {progress.raced}"
        if progress.unreadable:
            line += f", UNREADABLE {progress.unreadable}"
        print(line)


async def _main() -> int:
    url = _database_url()
    if not url:
        print("DATABASE_URL_OWNER or DATABASE_URL must be set", file=sys.stderr)
        return 2

    mode = os.getenv("MODE", "count").strip().lower()
    if mode not in {"count", "rewrite"}:
        print(f"MODE must be 'count' or 'rewrite', got {mode!r}", file=sys.stderr)
        return 2

    from core.crypto import configured_keys
    from services.key_rotation_service import count_stale, reencrypt

    keys = len(configured_keys())

    engine = create_async_engine(url, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as session:
            report = await (reencrypt if mode == "rewrite" else count_stale)(session)
    finally:
        await engine.dispose()

    print(f"keys configured: {keys}")
    _print_report(report, rewrote=mode == "rewrite")

    if report.unreadable_total:
        print(
            f"\n{report.unreadable_total} row(s) could not be opened by any "
            "configured key. A key they were written under is missing from "
            "GITHUB_APP_ENCRYPTION_KEY. Add it back before doing anything "
            "else; those rows cannot be recovered without it.",
            file=sys.stderr,
        )
        return 1

    if mode == "count":
        if report.stale_total == 0:
            print("\nNothing is on an older key. Removing the oldest key is safe.")
            return 0
        movable = report.stale_total - report.unreadable_total
        print(
            f"\n{report.stale_total} row(s) are still on an older key "
            f"({movable} the rewrite can move). Run with MODE=rewrite. Do NOT "
            "remove a key while this is above zero.",
            file=sys.stderr,
        )
        return 1

    # A rewrite pass reports what is left rather than what it did, because
    # what is left is the number the removal decision turns on.
    remaining = sum(c.remaining for c in report.columns)
    print(f"\nrewrote {report.rewritten_total} row(s)")
    if remaining:
        print(
            f"{remaining} row(s) were not moved. Run MODE=count to see whether "
            "anything is still on an older key.",
            file=sys.stderr,
        )
        return 1
    print("Run MODE=count to confirm before removing the oldest key.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
