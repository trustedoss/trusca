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

That backup used to be restored by a plain overwrite: copy it back over the
target, no questions asked. That is exact only if nothing else touched the
file during the mutation window. It was not exact once: a real fix landed in
the same file while a mutation was live, and the overwrite restore erased it
along with the mutation. The full test suite happened to catch the loss;
nothing in this tool would have.

Restore now checks first. Alongside the backup, `apply_mutation` writes a
sidecar `<file>.mutate-backup.json` recording the `old`/`new` pair and the
sha256 of the file right after the mutation. `--restore` hashes the current
file against that record:

  - hash unchanged since the mutation -> nothing else touched the file, so
    the plain backup copy is exact and restore proceeds as before;
  - hash changed -> something else edited the file during the window. If
    `new` still appears in the current file exactly once, restore reverses
    just that one substitution (`new` -> `old`) and leaves the rest of the
    file, including whatever landed during the window, untouched;
  - if `new` appears zero times or more than once in the changed file, which
    occurrence to reverse is not stated, so this refuses (exit 2) rather than
    guess, and points at the backup for a manual diff.

Nothing here is required. Rule 7 says to break what you guard, not how; this
is one safe way to do it, and a mutation that needs a different shape should
use a different shape rather than bending to fit this.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import shutil
import sys
from pathlib import Path

BACKUP_SUFFIX = ".mutate-backup"
META_SUFFIX = ".mutate-backup.json"


def refuse(message: str) -> None:
    """Print and exit non-zero, so a shell chain stops here."""
    print(f"REFUSED: {message}")
    raise SystemExit(2)


def apply_mutation(target: Path, old: str, new: str) -> str:
    """Replace ``old`` with ``new`` once, or refuse. Returns a status line.

    Backs the file up before writing, and only after every check has passed:
    a refusal must not leave a backup behind for a mutation that never
    happened. Alongside the backup it writes a sidecar recording ``old``,
    ``new``, and the sha256 of the file right after the mutation, so
    ``restore`` can later tell whether anything else touched the file.
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
    meta_path(target).write_text(
        json.dumps(
            {
                "old": old,
                "new": new,
                "mutated_sha256": _sha256(target),
            }
        )
    )
    return f"APPLIED to {target}: {len(source)} -> {len(mutated)} chars"


def backup_path(target: Path) -> Path:
    return target.with_suffix(target.suffix + BACKUP_SUFFIX)


def meta_path(target: Path) -> Path:
    return target.with_suffix(target.suffix + META_SUFFIX)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _edit_hunks(before: str, after: str) -> int:
    """Count contiguous changed regions between two texts, line by line.

    Used only to describe, in the restore message, how much of what changed
    during the mutation window survived the restore. Not a precision tool -
    an approximate hunk count is enough to say "something else changed here"
    without claiming to enumerate every edit.
    """
    matcher = difflib.SequenceMatcher(None, before.splitlines(), after.splitlines())
    return sum(1 for tag, *_rest in matcher.get_opcodes() if tag != "equal")


def restore(target: Path) -> str:
    backup = backup_path(target)
    if not backup.exists():
        refuse(f"no backup at {backup}")

    meta_file = meta_path(target)
    if not meta_file.exists():
        # A backup with no sidecar metadata predates this check (or the
        # sidecar was removed by hand). There is nothing to compare against,
        # so fall back to the direct-copy restore this tool always did.
        shutil.copyfile(backup, target)
        backup.unlink()
        return f"restored {target}"

    meta = json.loads(meta_file.read_text())
    old, new, mutated_sha256 = meta["old"], meta["new"], meta["mutated_sha256"]

    if _sha256(target) == mutated_sha256:
        # Nothing touched the file since the mutation landed: the backup is
        # an exact pre-mutation copy, so restoring it is exact too.
        shutil.copyfile(backup, target)
        backup.unlink()
        meta_file.unlink()
        return f"restored {target}"

    current = target.read_text()
    occurrences = current.count(new)
    if occurrences != 1:
        state = "does not appear" if occurrences == 0 else f"appears {occurrences} times"
        refuse(
            f"{target} changed during the mutation window and the mutated "
            f"text {state} in it now, so reversing the mutation is not "
            f"stated; backup at {backup}, inspect with: diff {backup} {target}"
        )

    reverted = current.replace(new, old, 1)
    target.write_text(reverted)
    preserved = _edit_hunks(backup.read_text(), reverted)
    backup.unlink()
    meta_file.unlink()
    return (
        f"restored {target} via reverse substitution; "
        f"{preserved} other edit(s) made during the mutation window were preserved"
    )


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
