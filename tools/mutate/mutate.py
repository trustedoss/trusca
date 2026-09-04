#!/usr/bin/env python3
"""Apply one mutation to one file, or refuse to do anything.

Testing standards rules 7 and 8 ask you to break what an assertion guards and
watch it fail, and to check that a surviving mutation actually landed. The
usual way to do that is a shell loop that edits a file, runs the tests, and
restores it. That loop has a failure mode with no symptom: when the edit does
not match, most spellings of it carry on silently, the tests run against
unmodified code, and the green result reads as "the assertion does not catch
this" when the assertion was never given anything to catch.

It happened five times across three sessions in one day. Every time a person
noticed. This is that check written down so noticing is not required.

    python tools/mutate/mutate.py <file> <old> <new>
    python tools/mutate/mutate.py --restore <file>

Refusals, all exit 2, so a `&&` chain stops before the tests run:

  - the anchor does not appear in the file,
  - it appears more than once, so which one changes is not stated,
  - the replacement leaves the file byte-identical.

The last one is the interesting one. An anchor can match and the edit still
change nothing, which is the same false green by another route.

Restoring reads a backup this tool wrote, not git. `git checkout -- <file>`
would also discard uncommitted work in that file, which cost two sessions
their edits on the same day this was written.

Nothing here is required. Rule 7 says to break what you guard, not how; this
is one safe way to do it, and a mutation that needs a different shape should
use a different shape rather than bending to fit this.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

BACKUP_SUFFIX = ".mutate-backup"


def refuse(message: str) -> None:
    """Print and exit non-zero, so a shell chain stops here."""
    print(f"REFUSED: {message}")
    raise SystemExit(2)


def apply_mutation(target: Path, old: str, new: str) -> str:
    """Replace ``old`` with ``new`` once, or refuse. Returns a status line.

    Backs the file up before writing, and only after every check has passed:
    a refusal must not leave a backup behind for a mutation that never
    happened.
    """
    source = target.read_text()

    occurrences = source.count(old)
    if occurrences == 0:
        refuse(f"anchor not found in {target}")
    if occurrences > 1:
        refuse(f"anchor appears {occurrences} times in {target}; make it unique")

    mutated = source.replace(old, new, 1)
    if mutated == source:
        refuse("replacement left the file unchanged")

    shutil.copyfile(target, backup_path(target))
    target.write_text(mutated)
    return f"APPLIED to {target}: {len(source)} -> {len(mutated)} chars"


def backup_path(target: Path) -> Path:
    return target.with_suffix(target.suffix + BACKUP_SUFFIX)


def restore(target: Path) -> str:
    backup = backup_path(target)
    if not backup.exists():
        refuse(f"no backup at {backup}")
    shutil.copyfile(backup, target)
    backup.unlink()
    return f"restored {target}"


def main(argv: list[str]) -> None:
    if argv[:1] == ["--restore"]:
        if len(argv) != 2:
            refuse("usage: mutate.py --restore <file>")
        print(restore(Path(argv[1])))
        return

    if len(argv) != 3:
        refuse("usage: mutate.py <file> <old> <new> | --restore <file>")
    print(apply_mutation(Path(argv[0]), argv[1], argv[2]))


if __name__ == "__main__":
    main(sys.argv[1:])
