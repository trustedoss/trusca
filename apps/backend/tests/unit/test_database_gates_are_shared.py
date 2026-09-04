"""Every test module that needs a database says so through one helper.

ER66. This reads the test tree with ``ast`` and answers two questions: does
any module reach the database without gating on it, and how many modules still
carry their own copy of the gate instead of calling ``tests._db_required``.

Four mistakes were made writing this analysis, and they are recorded because
the next person to widen it will be offered all four again:

1. It searched ``ast.dump()`` output for ``"alembic"`` in double quotes. Dump
   writes apostrophes, so it reported zero migration gates. A count of zero is
   never self-evidently right: it means "absent" or "not seen" and the result
   alone does not say which.
2. It counted only ``pytest.skip``/``fail``/``raise`` as reactions, so ten
   sites guarding with ``assert result.returncode == 0`` looked unprotected.
   That would have put correct code on the list of things to change.
3. It judged function by function, so helpers that only parse the URL looked
   like gates that react to nothing, when their caller does the reacting.
4. After the definition widened to include ``core.config.database_url()``,
   ordinary test bodies came into scope and their ordinary asserts were counted
   as gates - eighteen tests, seven of which assert about URL parsing alone.

The rule that follows: when the definition widens, sample what it now catches
before trusting the number. A sample taken before a widening does not carry
over, because a looser condition pulls in things the earlier one excluded.
"""

from __future__ import annotations

import ast
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parent.parent

# Modules that legitimately touch the database machinery without using the
# shared gate, and why each cannot. The reason matters more than the entry: it
# is what tells the next person whether a file may come off this list.
#
# Two kinds so far, and a third would want its own paragraph rather than being
# filed under "special".
#
#   Testing the tool itself. Running the migration is the subject, so gating on
#   it removes what the test exists to check.
#
#   Migrating a different database. The helper migrates the configured
#   DATABASE_URL and caches per URL; a module that builds its own database and
#   migrates that cannot delegate to it. (Note this is not the same as needing
#   the database in a particular state - that a module can arrange for itself,
#   and test_bootstrap_from_empty.py does exactly that, migrating to head
#   through the helper and then truncating.)
EXEMPT: dict[str, str] = {
    "unit/core/test_config_database_url.py": (
        "Tests the tool itself: core.config.database_url is the subject, and it "
        "is exercised by deleting DATABASE_URL from the environment. Gating on "
        "that variable would remove what is being tested."
    ),
    "integration/test_alembic_upgrade.py": (
        "Tests the tool itself: that `alembic upgrade head` succeeds and that "
        "`alembic current` then reports head. The migration running IS the "
        "assertion."
    ),
    "integration/test_scan_dependency_fingerprint_migration.py": (
        "Tests the tool itself: the shape revision 0070 leaves behind, and that "
        "its downgrade() fails loudly instead of silently doing nothing."
    ),
    "integration/test_app_role_grant_matrix.py": (
        "Migrates a different database: it creates a throwaway one, then runs "
        "alembic against it in a subprocess with DATABASE_URL repointed, to "
        "test the role-first-then-migrate ordering. The helper migrates the "
        "configured database, which is not the one under test here."
    ),
}

# DEBT, and now empty: every module that gates on the database calls
# tests._db_required. It stays here because the assertions below still use it,
# and because a future bulk change might need it again - but it starts at zero
# and may only ever be zero. Nothing may be added: a new entry fails this test
# even though the code it describes works. If you are writing a module that
# needs a database, import tests._db_required.
STILL_ROLLING_THEIR_OWN: frozenset[str] = frozenset()


def _string_constants(node: ast.AST) -> set[str]:
    return {
        n.value
        for n in ast.walk(node)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }


def runs_alembic(fn: ast.AST) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "run":
            for arg in node.args:
                if {"alembic", "upgrade"} <= _string_constants(arg):
                    return True
    return False


def reaches_the_database(fn: ast.AST) -> bool:
    """Both ways in: reading DATABASE_URL, and calling the resolver."""
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name == "database_url":
                return True
            if name in {"getenv", "environ"} and any(
                isinstance(a, ast.Constant) and a.value == "DATABASE_URL"
                for a in node.args
            ):
                return True
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and node.slice.value == "DATABASE_URL"
        ):
            return True
    return False


HELPER_CALLS = frozenset({"migrate_to_head", "require_database_url"})


def calls_shared_helper(fn: ast.AST) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            called = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if called in HELPER_CALLS:
                return True
    return False


def gate_reaction(fn: ast.AST) -> set[str]:
    """What this function does when the database is not there.

    An ``assert`` counts only inside a function that runs the migration, where
    it can only be about the return code. Elsewhere an assert is the test's
    subject, not a gate (mistake 4 above).
    """
    acts: set[str] = set()
    for node in ast.walk(fn):
        # Delegating to the shared helper IS the gate. Leaving this out is how
        # the first converted module still read as ungated: the guard knew
        # every old spelling and not the one it exists to promote. Fifth
        # instance of the definition being narrower than the question.
        if isinstance(node, ast.Call):
            called = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if called in HELPER_CALLS:
                acts.add("shared helper")
            del called
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            owner = getattr(node.func.value, "id", "")
            if attr in {"skip", "fail"} and owner == "pytest":
                acts.add(f"pytest.{attr}")
        if isinstance(node, ast.Raise):
            acts.add("raise")
        if isinstance(node, ast.Assert) and runs_alembic(fn):
            acts.add("assert")
    return acts


def uses_the_shared_helper(tree: ast.AST) -> bool:
    """Whether the module imports the one helper rather than writing its own."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.endswith("_db_required"):
                return True
            if any(a.name == "_db_required" for a in node.names):
                return True
        if isinstance(node, ast.Import):
            if any(a.name.endswith("_db_required") for a in node.names):
                return True
    return False


def _survey() -> tuple[set[str], set[str], set[str]]:
    """(modules using the database, modules with a gate, modules with own copy)."""
    uses: set[str] = set()
    gated: set[str] = set()
    own_copy: set[str] = set()
    for path in sorted(TESTS_ROOT.rglob("test_*.py")):
        rel = path.relative_to(TESTS_ROOT).as_posix()
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:  # pragma: no cover - a parse failure is its own problem
            continue
        functions = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
        ]
        for fn in functions:
            # Selection has to be as wide as the question and no wider. A
            # fixture that calls the shared helper touches neither
            # DATABASE_URL nor subprocess, so selecting on those two alone
            # dropped it before its reaction was looked at. Selecting on "has
            # any reaction" instead pulled in 83 modules that skip over a
            # missing binary and never touch a database. The helper call is
            # the third way in, and only it.
            if not (
                reaches_the_database(fn)
                or runs_alembic(fn)
                or calls_shared_helper(fn)
            ):
                continue
            acts = gate_reaction(fn)
            uses.add(rel)
            if acts:
                gated.add(rel)
                if not uses_the_shared_helper(tree):
                    own_copy.add(rel)
    return uses, gated, own_copy


def test_no_module_reaches_the_database_without_gating_on_it() -> None:
    uses, gated, _ = _survey()
    ungated = {m for m in uses - gated if m not in EXEMPT}
    assert not ungated, (
        "these modules use the database but never say what to do when it is "
        f"absent, so they depend on some other module having migrated first: "
        f"{sorted(ungated)}"
    )


def test_the_exemptions_are_all_real() -> None:
    """An exemption for a file that no longer exists is a line nobody removes.

    What an exemption means is "this module may keep its own arrangement", not
    "this module has no gate". The first version asserted the latter, because
    the only exemption then was a module with no gate at all; the three added
    when the last batch landed do gate, with their own copy, for reasons the
    list states. So the check is that the file exists and still uses the
    database - not what shape its gate has.
    """
    uses, _gated, _own = _survey()
    for module in EXEMPT:
        assert (TESTS_ROOT / module).exists(), f"{module} is exempted but does not exist"
        assert module in uses, f"{module} is exempted but does not use the database"
        assert EXEMPT[module].strip(), f"{module} is exempted without a reason"


def test_the_debt_list_only_shrinks() -> None:
    _, _, own_copy = _survey()
    # Exempt modules keep their own gate on purpose; that is what the exemption
    # is for, so they are not debt.
    own_copy = {m for m in own_copy if m not in EXEMPT}
    added = own_copy - STILL_ROLLING_THEIR_OWN
    assert not added, (
        "these modules gate on the database with their own copy instead of "
        f"tests._db_required: {sorted(added)}. Do not add them to "
        "STILL_ROLLING_THEIR_OWN - that list is debt being paid off, not a "
        "place to register new copies."
    )
    stale = STILL_ROLLING_THEIR_OWN - own_copy
    assert not stale, (
        f"already moved, remove from STILL_ROLLING_THEIR_OWN: {sorted(stale)}"
    )


def test_ci_sets_the_flag_on_the_job_that_runs_the_database_tests() -> None:
    """The helper only changes anything where the flag is set.

    Everything else in this file could pass with the flag set nowhere, and the
    suite would go on skipping exactly as before. This reads the workflow so
    that deleting the line is a test failure rather than a silent return to
    the old behaviour.
    """
    import yaml

    workflow = yaml.safe_load(
        (TESTS_ROOT.parent.parent.parent / ".github/workflows/ci.yml").read_text()
    )
    jobs = workflow["jobs"]
    runners = [
        name
        for name, job in jobs.items()
        if any("pytest" in str(step.get("run", "")) for step in job.get("steps", []))
    ]
    assert runners, "no job in ci.yml appears to run pytest"
    for name in runners:
        env = jobs[name].get("env") or {}
        assert str(env.get("TRUSCA_TESTS_REQUIRE_DB", "")).strip() in {"1", "true"}, (
            f"job {name!r} runs the database tests without "
            "TRUSCA_TESTS_REQUIRE_DB, so a broken migration would let them "
            "skip and the job would pass"
        )
