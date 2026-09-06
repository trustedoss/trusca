#!/usr/bin/env python3
"""Offline checks for mutate.py.

A tool whose whole job is to notice that a mutation did not land has no
business being unverified itself. Each refusal is driven, and so is each
success, because a tool that refuses everything would pass a file of refusal
tests and be useless.

Run:
    python tools/mutate/selftest.py
"""

from __future__ import annotations

import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import mutate  # noqa: E402

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok   {name}")
        return
    FAILURES.append(f"{name}{': ' + detail if detail else ''}")
    print(f"  FAIL {name} {detail}")


def _file(text: str) -> pathlib.Path:
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".py", delete=False, encoding="utf-8"
    )
    handle.write(text)
    handle.close()
    return pathlib.Path(handle.name)


def _refuses(target: pathlib.Path, old: str, new: str) -> str | None:
    """Return the refusal message, or None if it did not refuse."""
    try:
        mutate.apply_mutation(target, old, new)
    except SystemExit as exit_:
        assert exit_.code == 2, f"refusals must exit 2, got {exit_.code}"
        return "refused"
    return None


def test_it_applies_a_real_change() -> None:
    target = _file("alpha\nbeta\ngamma\n")
    result = mutate.apply_mutation(target, "beta", "delta")

    check("applies a real change", target.read_text() == "alpha\ndelta\ngamma\n")
    check("reports what it did", result.startswith("APPLIED"), result)
    check("leaves a backup", mutate.backup_path(target).exists())
    check("leaves sidecar metadata", mutate.meta_path(target).exists())


def test_it_refuses_a_missing_anchor() -> None:
    target = _file("alpha\nbeta\n")
    before = target.read_text()

    check(
        "refuses a missing anchor",
        _refuses(target, "not here", "x") == "refused",
    )
    check("leaves the file alone", target.read_text() == before)
    check(
        "leaves no backup behind",
        not mutate.backup_path(target).exists(),
        "a refusal must not litter",
    )


def test_it_refuses_an_ambiguous_anchor() -> None:
    target = _file("beta\nbeta\n")
    before = target.read_text()

    check(
        "refuses an anchor that appears twice",
        _refuses(target, "beta", "delta") == "refused",
    )
    check("leaves the file alone", target.read_text() == before)


def test_it_refuses_a_replacement_that_changes_nothing() -> None:
    """The case the shell loops missed.

    The anchor matches, the edit runs, and the file is what it was. Reported
    as a mutation, it produces a green run that reads as evidence.
    """
    target = _file("alpha\nbeta\n")

    check(
        "refuses a no-op replacement",
        _refuses(target, "beta", "beta") == "refused",
    )


def test_restore_puts_the_file_back() -> None:
    target = _file("alpha\nbeta\n")
    original = target.read_text()
    mutate.apply_mutation(target, "beta", "delta")
    check("mutated first", target.read_text() != original)

    result = mutate.restore(target)

    check("restores the original bytes", target.read_text() == original)
    check("reports the restore", result.startswith("restored"), result)
    check(
        "removes the backup",
        not mutate.backup_path(target).exists(),
        "a stale backup would restore the wrong thing next time",
    )
    check(
        "removes the sidecar metadata",
        not mutate.meta_path(target).exists(),
        "stale metadata would confuse the next restore",
    )


def test_restore_refuses_without_a_backup() -> None:
    target = _file("alpha\n")
    before = target.read_text()

    try:
        mutate.restore(target)
        check("refuses to restore with no backup", False, "it returned instead")
    except SystemExit as exit_:
        check("refuses to restore with no backup", exit_.code == 2)
    check("leaves the file alone", target.read_text() == before)


def test_restore_preserves_an_unrelated_edit_made_during_the_window() -> None:
    """The bug this rewrite fixes (issue #393).

    A mutation is live, and while it is live a real, unrelated fix lands in
    the same file, on a different line. The old restore copied the pristine
    backup straight over the file and erased that fix along with the
    mutation. Restore must now reverse only the mutated text and leave the
    unrelated edit standing.
    """
    target = _file("alpha\nbeta\ngamma\n")
    mutate.apply_mutation(target, "beta", "delta")

    current = target.read_text()
    target.write_text(current.replace("gamma", "gamma-fixed"))

    result = mutate.restore(target)
    restored = target.read_text()

    check("reverses the mutation", "delta" not in restored)
    check("restores the mutated anchor", "beta" in restored)
    check("preserves the unrelated edit", "gamma-fixed" in restored)
    check(
        "reports a reverse substitution, not a plain overwrite",
        "reverse substitution" in result,
        result,
    )
    check("removes the backup", not mutate.backup_path(target).exists())
    check("removes the sidecar metadata", not mutate.meta_path(target).exists())


def test_restore_refuses_when_the_mutated_text_becomes_ambiguous() -> None:
    """A second copy of the mutated text shows up during the window.

    Reversing "the" occurrence is no longer a single, stated operation, so
    this must refuse rather than guess which one to revert - the same
    ambiguity `apply_mutation` itself refuses on the way in.
    """
    target = _file("alpha\nbeta\ngamma\n")
    mutate.apply_mutation(target, "beta", "delta")

    current = target.read_text()
    target.write_text(current + "delta\n")
    backup_before = mutate.backup_path(target).read_text()
    meta_before = mutate.meta_path(target).read_text()

    try:
        mutate.restore(target)
        check(
            "refuses an ambiguous reverse substitution",
            False,
            "it returned instead",
        )
    except SystemExit as exit_:
        check("refuses an ambiguous reverse substitution", exit_.code == 2)
    check(
        "leaves the backup in place",
        mutate.backup_path(target).read_text() == backup_before,
        "a refusal must not consume the backup",
    )
    check(
        "leaves the sidecar metadata in place",
        mutate.meta_path(target).read_text() == meta_before,
    )


def test_restore_refuses_when_the_mutated_text_disappears() -> None:
    """The mutated line itself gets rewritten during the window.

    The sha256 no longer matches the post-mutation snapshot, but the mutated
    text is also gone, so there is nothing left to reverse. This must refuse
    rather than silently leave the mutation's absence unexplained.
    """
    target = _file("alpha\nbeta\ngamma\n")
    mutate.apply_mutation(target, "beta", "delta")

    current = target.read_text()
    target.write_text(current.replace("delta", "epsilon"))

    try:
        mutate.restore(target)
        check(
            "refuses when the mutated text is gone",
            False,
            "it returned instead",
        )
    except SystemExit as exit_:
        check("refuses when the mutated text is gone", exit_.code == 2)
    check(
        "leaves the backup in place",
        mutate.backup_path(target).exists(),
        "a refusal must not consume the backup",
    )
    check(
        "leaves the sidecar metadata in place",
        mutate.meta_path(target).exists(),
    )


def main() -> int:
    print("mutate selftest")
    for name, function in sorted(globals().items()):
        if name.startswith("test_") and callable(function):
            print(f"\n{name}")
            function()

    print()
    if FAILURES:
        print(f"FAILED ({len(FAILURES)}):")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print("mutate selftest OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
