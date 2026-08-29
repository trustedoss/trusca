"""GRANT vs ALTER DEFAULT PRIVILEGES parity contract for the app-role migration.

Testing-standards rule #2 (CLAUDE.md "품질·보안·운영 표준" §2, hardening rule #2):
when the same vocabulary exists in two or more places, an equality/subset
test is mandatory. Here the "vocabulary" is the set of Postgres privileges
the ``trustedoss_app`` runtime role holds on ``public`` schema tables, and
it is declared in two places inside a single migration:

  * a one-time ``GRANT ... ON ALL TABLES IN SCHEMA public`` that applies to
    tables that exist when the migration runs, and
  * an ``ALTER DEFAULT PRIVILEGES ... ON TABLES`` clause that applies to
    tables created afterwards.

Migration ``0014_app_role_grants.py`` intentionally makes the second set
narrower than the first, in that UPDATE/DELETE are withheld from future tables by
default so a new table starts append-only unless a later migration opts it
in explicitly. That gap is a design decision, not a bug (see the
migration's module docstring "Why" section). The defect this test guards
against is that nothing previously enforced the gap staying exactly
{"UPDATE", "DELETE"}; a migration author could widen or narrow either
GRANT list without anything failing.

This test never opens a database connection. It parses the DDL string
embedded in the migration module and compares it against a constant
declared in that same module.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "0014_app_role_grants.py"
)

# Matches the one-time, broad GRANT that applies to tables existing at
# migration time: "GRANT SELECT, INSERT, ... ON ALL TABLES IN SCHEMA public
# TO trustedoss_app". The "ALL TABLES" wording (vs. bare "TABLES" used by
# ALTER DEFAULT PRIVILEGES below) is what disambiguates the two clauses.
_ONE_TIME_TABLE_GRANT_RE = re.compile(
    r"GRANT\s+(?P<privileges>[A-Z][A-Z,\s]*?)\s+"
    r"ON ALL TABLES IN SCHEMA public TO trustedoss_app",
)

# Matches the future-tables clause: "ALTER DEFAULT PRIVILEGES IN SCHEMA
# public GRANT SELECT, INSERT ON TABLES TO trustedoss_app".
_DEFAULT_PRIVILEGE_TABLE_GRANT_RE = re.compile(
    r"ALTER DEFAULT PRIVILEGES IN SCHEMA public\s+"
    r"GRANT\s+(?P<privileges>[A-Z][A-Z,\s]*?)\s+"
    r"ON TABLES TO trustedoss_app",
)


def _load_migration_module() -> ModuleType:
    """Import 0014_app_role_grants.py by file path.

    Alembic version files are not part of an importable package (the
    ``versions`` directory has no meaningful ``__init__.py`` semantics for
    this purpose), so we load the module directly from its path instead of
    using a dotted import.
    """
    spec = importlib.util.spec_from_file_location(
        "app_role_grants_0014", _MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None, (
        f"Could not build an import spec for {_MIGRATION_PATH}"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _parse_privilege_set(pattern: re.Pattern[str], ddl: str) -> set[str]:
    match = pattern.search(ddl)
    assert match is not None, (
        f"Could not find the expected DDL shape via pattern {pattern.pattern!r} "
        "in 0014's _GRANT_DDL. The migration's DDL text changed shape; update "
        "this test's regexes to match, then re-verify the privilege sets by hand."
    )
    raw = match.group("privileges").replace("\n", " ")
    return {piece.strip() for piece in raw.split(",") if piece.strip()}


@pytest.fixture(scope="module")
def migration_module() -> ModuleType:
    return _load_migration_module()


def test_migration_declares_the_intentional_gap_constant(
    migration_module: ModuleType,
) -> None:
    assert hasattr(migration_module, "_INTENTIONAL_DEFAULT_PRIVILEGE_GAP"), (
        "0014_app_role_grants.py no longer defines "
        "_INTENTIONAL_DEFAULT_PRIVILEGE_GAP. This constant is the single "
        "declared source of truth for how the one-time GRANT and the "
        "ALTER DEFAULT PRIVILEGES clause are allowed to differ; restore it."
    )


def test_one_time_grant_is_a_superset_of_default_privileges(
    migration_module: ModuleType,
) -> None:
    ddl = migration_module._GRANT_DDL
    one_time = _parse_privilege_set(_ONE_TIME_TABLE_GRANT_RE, ddl)
    default = _parse_privilege_set(_DEFAULT_PRIVILEGE_TABLE_GRANT_RE, ddl)

    assert default <= one_time, (
        "ALTER DEFAULT PRIVILEGES grants a privilege "
        f"({default - one_time}) that the one-time GRANT on existing "
        "tables does not. Future tables would end up with MORE access "
        "than tables created at migration time, which is backwards from "
        "the intended deny-by-default posture for new tables."
    )


def test_default_privileges_gap_matches_declared_constant(
    migration_module: ModuleType,
) -> None:
    """The core parity assertion.

    This is the comparison point the codebase did not previously have: it
    fails the moment a future edit to 0014 widens or narrows either
    privilege list without updating the declared gap alongside it (e.g. a
    new migration widening the one-time GRANT to include TRUNCATE, or
    narrowing ALTER DEFAULT PRIVILEGES further, without touching the
    constant).
    """
    ddl = migration_module._GRANT_DDL
    one_time = _parse_privilege_set(_ONE_TIME_TABLE_GRANT_RE, ddl)
    default = _parse_privilege_set(_DEFAULT_PRIVILEGE_TABLE_GRANT_RE, ddl)

    actual_gap = one_time - default
    expected_gap = set(migration_module._INTENTIONAL_DEFAULT_PRIVILEGE_GAP)

    assert actual_gap == expected_gap, (
        f"The privileges withheld from ALTER DEFAULT PRIVILEGES "
        f"({sorted(actual_gap)}) no longer match the declared "
        f"_INTENTIONAL_DEFAULT_PRIVILEGE_GAP ({sorted(expected_gap)}) in "
        "0014_app_role_grants.py. If widening or narrowing this gap between "
        "the one-time GRANT and future-table defaults is intentional, "
        "update _INTENTIONAL_DEFAULT_PRIVILEGE_GAP in that migration and "
        "record the reason in a comment there. If it is not intentional, "
        "the migration's GRANT / ALTER DEFAULT PRIVILEGES DDL drifted and "
        "needs to be fixed instead."
    )
